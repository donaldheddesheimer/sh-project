"""NVIDIA Nemotron via NIM: structured proposals and recommendations for the guarded pipeline.

The built-in Nemotron episode uses ``NemotronAgentProvider`` through ``PipelineAnalyst``. The model makes
the two judgment calls—propose candidate plans, then recommend from completed results—as compact JSON.
Application code owns the stateful workflow:

    incident context -> propose plans -> validate -> parallel SUMO branches
    -> completed results -> recommend -> checked implementor

The public MCP scenario tools remain available for external agents, while the bounded built-in path avoids
the hosted endpoint's fragile, growing assistant/tool transcript. The REST Analyze Response path uses the
same provider and safety pipeline.
"""

from __future__ import annotations

import asyncio
import json

import httpx2
from pydantic import ValidationError

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext, Recommendation
from app.agent.briefing import PLAN_DESIGN, candidate_row, metrics_row
from app.models.domain import SimulationCandidate


class NimError(RuntimeError):
    pass


class NimClient:
    """Minimal OpenAI-compatible chat-completions client for NIM (hosted at integrate.api.nvidia.com, or self-hosted)."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str | None,
        timeout_s: float = 120.0,
        *,
        json_mode: bool = False,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._json_mode = json_mode

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 4096) -> dict:
        """One completion; returns the assistant message (``content``, and ``tool_calls`` when it calls tools)."""
        # Nemotron 3 defaults; JSON mode below follows NVIDIA's deterministic structured-output example.
        body: dict = {
            "model": self.model,
            # ``is_error`` is an internal hint used by the Claude adapter; OpenAI-compatible tool messages
            # represent failures in their content and reject that extra field.
            "messages": [{k: v for k, v in message.items() if k != "is_error"} for message in messages],
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": max_tokens,
        }
        if "nemotron-3" in self.model.lower():
            # Structured output does not benefit from a visible reasoning trace. Keeping it off
            # also prevents reasoning text from competing with the schema-constrained payload.
            body["chat_template_kwargs"] = {"enable_thinking": False}
        if tools and "nemotron-3-super" in self.model.lower():
            body["chat_template_kwargs"]["force_nonempty_content"] = True
        if self._json_mode and not tools:
            body["response_format"] = {"type": "json_object"}
            body["temperature"] = 0.0
            body.pop("top_p", None)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        async with httpx2.AsyncClient(timeout=self._timeout_s) as http:
            for attempt in range(2):
                response = await http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
                if response.status_code not in {500, 502, 503, 504} or attempt == 1:
                    break
                await asyncio.sleep(0.5)
        if response.status_code >= 400:
            raise NimError(f"NIM returned {response.status_code}: {response.text[:300]}")
        try:
            return response.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise NimError(f"unexpected NIM response: {response.text[:300]}") from exc


class NemotronAgentProvider(AgentProvider):
    name = "nemotron"

    def __init__(self, base_url: str, model: str, api_key: str | None, candidate_limit: int):
        self._nim = NimClient(base_url, model, api_key, json_mode=True)
        self._candidate_limit = candidate_limit
        self._diagnostics: list[str] = []

    async def propose_candidates(self, context: IncidentContext) -> list[CandidatePlan]:
        self._diagnostics.clear()
        budget = max(0, self._candidate_limit - 1)  # baseline is added by ScenarioService, never by the model
        prompt = {
            "candidate_budget": budget,
            "context": _proposal_context(context),
            "response_schema": {
                "type": "object",
                "properties": {"plans": {"type": "array", "items": CandidatePlan.model_json_schema()}},
                "required": ["plans"],
            },
        }
        messages = [
            {
                "role": "system",
                "content": (
                    PLAN_DESIGN
                    + '\nReturn only one JSON object shaped as {"plans":[CandidatePlan,...]}. '
                    "Plans are data, never commands: choose safe timing, corridor or reroute ideas for later validation "
                    "and branch simulation. Respect candidate_budget, do not include the baseline, and use lessons only "
                    "as advice."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
        ]
        for attempt in range(2):
            message = await self._nim.chat(messages)
            reported = len(self._diagnostics)
            plans = self._validated_plans(message.get("content"), budget)
            if plans:
                return plans
            if attempt == 0:
                # The retry has to show the model what was wrong with its reply. Re-sending the identical two
                # messages only resamples the same prompt, which is a NIM call spent on nothing.
                faults = self._diagnostics[reported:] or ["the reply contained no CandidatePlan entries"]
                raw = message.get("content")
                messages += [
                    {"role": "assistant", "content": raw if isinstance(raw, str) else ""},
                    {
                        "role": "user",
                        "content": (
                            f"That reply was rejected: {'; '.join(faults[:4])}. "
                            'Return only one JSON object shaped as {"plans":[CandidatePlan,...]}.'
                        ),
                    },
                ]
                self._diagnostics.append("Nemotron returned no valid candidates; retrying once with the errors")
        diagnostics = "; ".join(self._diagnostics[:4])
        raise NimError(f"Nemotron returned no valid CandidatePlan entries after one retry: {diagnostics[:400]}")

    async def recommend(self, context: IncidentContext, results: list[SimulationCandidate]) -> Recommendation:
        prompt = {"context": _recommendation_context(context), "results": _result_table(results)}
        messages = [
            {
                "role": "system",
                "content": (
                    "Choose one completed candidate from the simulated results. Return only a JSON object with "
                    "candidate_id, summary and rationale (an array of evidence statements). Never invent a result."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
        ]
        parsed = _json_value((await self._nim.chat(messages)).get("content"))
        if not isinstance(parsed, dict):
            raise NimError("Nemotron recommendation was not a JSON object")
        try:
            return Recommendation.model_validate(parsed)
        except ValidationError as exc:
            raise NimError(f"Nemotron recommendation did not match the required schema: {_validation_detail(exc)}") from exc

    def drain_diagnostics(self) -> list[str]:
        diagnostics, self._diagnostics = self._diagnostics, []
        return diagnostics

    def _validated_plans(self, raw: object, budget: int) -> list[CandidatePlan]:
        parsed = _json_value(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("plans")
        if not isinstance(parsed, list):
            self._diagnostics.append("Nemotron proposal did not contain a plans array")
            return []
        plans: list[CandidatePlan] = []
        ids: set[str] = set()
        for index, value in enumerate(parsed):
            try:
                plan = CandidatePlan.model_validate(value)
            except ValidationError as exc:
                self._diagnostics.append(f"candidate {index + 1} dropped: {_validation_detail(exc)}")
                continue
            if plan.id == "baseline" or plan.id in ids:
                self._diagnostics.append(f"candidate {index + 1} dropped: duplicate or reserved id '{plan.id}'")
                continue
            ids.add(plan.id)
            plans.append(plan)
            if len(plans) == budget:
                break
        return plans


def _json_value(raw: object) -> object | None:
    """Extract the first JSON value from a fenced or conversational model reply."""
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.startswith("```"):
        newline = text.find("\n")
        text = text[newline + 1 :].rsplit("```", 1)[0].strip() if newline >= 0 else ""
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
            return value
        except json.JSONDecodeError:
            continue
    return None


def _validation_detail(exc: ValidationError) -> str:
    error = exc.errors(include_url=False)[0]
    location = ".".join(str(item) for item in error["loc"])
    return f"{location}: {error['msg']}"


def _incident_view(context: IncidentContext) -> list[dict]:
    return [
        {
            "id": incident.id,
            "type": incident.type.value,
            "severity": incident.severity.value,
            "segment_id": incident.location.segment_id,
            "blocked_lanes": incident.affected_lanes,
            "total_lanes": incident.total_lanes,
        }
        for incident in context.all_incidents
    ]


def _signals(context: IncidentContext) -> dict[str, dict]:
    return {
        intersection_id: {
            "program_id": program.program_id,
            "phases": [
                {"index": phase.index, "duration_s": phase.duration, "serves_segments": phase.served_segments}
                for phase in program.phases
            ],
        }
        for intersection_id, program in context.signal_programs.items()
    }


def _proposal_context(context: IncidentContext) -> dict:
    return {
        "incidents": _incident_view(context),
        "worst_segments": [
            {
                "id": segment.id,
                "street": f"{segment.name} {segment.direction}",
                "congestion": round(segment.congestion, 2),
                "queue": segment.halting_count,
            }
            for segment in sorted(context.segments, key=lambda segment: segment.congestion, reverse=True)[:12]
        ],
        "signals": _signals(context),
        "ems_origin_segment": context.ems_origin_segment,
        "standing_responses": [plan.id for plan in context.standing],
        "lessons": [
            {key: lesson.get(key) for key in ("id", "chosen", "verdict", "summary", "next_time")}
            for lesson in context.lessons
        ],
    }


def _recommendation_context(context: IncidentContext) -> dict:
    return {"incidents": _incident_view(context), "standing_responses": [plan.id for plan in context.standing]}


def _result_table(results: list[SimulationCandidate]) -> list[dict]:
    baseline = next((candidate for candidate in results if candidate.id == "baseline" and candidate.metrics), None)
    baseline_metrics = metrics_row(baseline.metrics) if baseline is not None else None
    return [candidate_row(candidate, baseline_metrics) for candidate in results]
