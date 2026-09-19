"""NVIDIA Nemotron via NIM: the chat client the episode's analyst and reviewer use.

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

The one-shot REST pipeline (AGENT_PROVIDER, "Analyze Response") still uses the AgentProvider shape, which
Nemotron does not implement: ``NemotronAgentProvider`` stays a stub.
"""

from __future__ import annotations

import httpx2

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext, Recommendation
from app.models.domain import SimulationCandidate

_NOT_READY = (
    "The REST Analyze Response pipeline does not run Nemotron; use AGENT_PROVIDER=mock. Nemotron drives analyses "
    "in autonomous episodes (EPISODE_ANALYST=nemotron) through the MCP tools."
)


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

    def __init__(self, base_url: str, model: str | None, api_key: str | None):
        self._base_url = base_url
        self._model = model
        self._api_key = api_key

    async def propose_candidates(self, context: IncidentContext) -> list[CandidatePlan]:
        raise NotImplementedError(_NOT_READY)

    async def recommend(self, context: IncidentContext, results: list[SimulationCandidate]) -> Recommendation:
        raise NotImplementedError(_NOT_READY)
