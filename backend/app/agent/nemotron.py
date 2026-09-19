"""Adapter boundary for NVIDIA Nemotron via NIM (not implemented yet).

Planned loop (tool-calling over an OpenAI-compatible NIM endpoint):

    incident detected -> get_city_state / get_incident
    -> formulate hypotheses -> construct candidate SignalPolicies
    -> validate_signal_plan (SafetyValidator) -> simulate_signal_plan / simulate_reroute
       / simulate_emergency_corridor (fresh SUMO branches from one snapshot)
    -> compare_scenarios -> reject poor candidates, refine
    -> Recommendation with quantitative evidence

Nemotron only ever returns data (CandidatePlan / Recommendation). It has no
tool that changes live signals.
"""

from __future__ import annotations

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext, Recommendation
from app.models.domain import SimulationCandidate

_NOT_READY = "NemotronAgentProvider is a placeholder: the NIM client is not implemented yet. Use AGENT_PROVIDER=mock."


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
