"""NVIDIA Nemotron via NIM: chat-backed REST proposals plus the episode's MCP analyst.

Nemotron drives an episode's analysis as an MCP client of this backend's scenario tools
(``app/learning/analysts.py``). NIM does not speak MCP, so the analyst lists the tools, hands them to the
OpenAI-compatible NIM endpoint as ``tools`` and runs each tool call the model makes:

    start_analysis         -> incident(s), segments (worst first), every signal's phases, experience
    -> design plans (SignalPolicy timing changes, EmergencyCorridor, RerouteAction)
    -> validate_plan       -> safety findings, before spending a simulation
    -> simulate_plans      -> parallel SUMO branches from one snapshot, baseline included
    -> get_analysis        -> results with deltas against the baseline; refine and repeat
    -> submit_recommendation (a completed candidate and a rationale that quotes numbers)
    -> implement_recommendation (the gated implementor applies it to the live city)

The one-shot REST pipeline (``AGENT_PROVIDER=nemotron``) uses the same NIM client through
``NemotronAgentProvider``. It returns plans as data only; ScenarioService keeps validation,
branch simulation, completed-candidate checks and its explicit mock fallback.
"""

from __future__ import annotations

import json

import httpx2
from pydantic import ValidationError

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext, Recommendation
from app.api.mcp_tools import INSTRUCTIONS, _candidate, _metrics
from app.models.domain import SimulationCandidate

class NimError(RuntimeError):
    pass


class NimClient:
    """Minimal OpenAI-compatible chat-completions client for NIM (hosted at integrate.api.nvidia.com, or self-hosted)."""

    def __init__(self, base_url: str, model: str, api_key: str | None, timeout_s: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._timeout_s = timeout_s

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 4096) -> dict:
        """One completion; returns the assistant message (``content``, and ``tool_calls`` when it calls tools)."""
        # Nemotron 3 model cards recommend temperature 1.0 / top_p 0.95 for every task, tool calling included.
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": 1.0,
            "top_p": 0.95,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        async with httpx2.AsyncClient(timeout=self._timeout_s) as http:
            response = await http.post(f"{self.base_url}/chat/completions", json=body, headers=headers)
        if response.status_code >= 400:
            raise NimError(f"NIM returned {response.status_code}: {response.text[:300]}")
        try:
            return response.json()["choices"][0]["message"]
        except (KeyError, IndexError, ValueError) as exc:
            raise NimError(f"unexpected NIM response: {response.text[:300]}") from exc


class NemotronAgentProvider(AgentProvider):
    name = "nemotron"

    def __init__(self, base_url: str, model: str, api_key: str | None, candidate_limit: int):
        self._nim = NimClient(base_url, model, api_key)
        self._candidate_limit = candidate_limit
        self._diagnostics: list[str] = []

    async def propose_candidates(self, context: IncidentContext) -> list[CandidatePlan]:
        self._diagnostics.clear()
        budget = max(0, self._candidate_limit - 1)  # baseline is added by ScenarioService, never by the model
        prompt = {
            "candidate_budget": budget,
            "context": _proposal_context(context),
            "response_schema": {"type": "array", "items": CandidatePlan.model_json_schema()},
        }
        messages = [
            {
                "role": "system",
                "content": (
                    INSTRUCTIONS
                    + "\nReturn only a JSON array of CandidatePlan objects. "
                    "Plans are data, never commands: choose safe timing, corridor or reroute ideas for later validation "
                    "and branch simulation. Respect candidate_budget, do not include the baseline, and use lessons only "
                    "as advice."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, separators=(",", ":"))},
        ]
        for attempt in range(2):
            message = await self._nim.chat(messages)
            plans = self._validated_plans(message.get("content"), budget)
            if plans:
                return plans
            if attempt == 0:
                self._diagnostics.append("Nemotron returned no valid candidates; retrying once")
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
        if not isinstance(parsed, list):
            self._diagnostics.append("Nemotron proposal was not a JSON array")
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
    baseline_metrics = _metrics(baseline.metrics) if baseline is not None else None
    return [_candidate(candidate, baseline_metrics) for candidate in results]
