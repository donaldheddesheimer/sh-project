"""Adapter boundary for NVIDIA Nemotron via NIM (not implemented yet; milestone 3).

Plan: Nemotron drives the analysis as an MCP client of this backend's scenario
tools at /mcp (app/api/mcp_tools.py). NIM does not speak MCP, so the loop lists
the tools, hands them to the OpenAI-compatible NIM endpoint as ``tools``, runs
each tool call the model makes against /mcp and returns the result:

    start_analysis         -> incident, segments (worst first), every signal's phases
    -> design plans (SignalPolicy timing changes, EmergencyCorridor, RerouteAction)
    -> validate_plan       -> safety findings, before spending a simulation
    -> simulate_plans      -> parallel SUMO branches from one snapshot, baseline included
    -> get_analysis        -> results with deltas against the baseline; refine and repeat
    -> submit_recommendation (a completed candidate and a rationale that quotes numbers)

The workflow text is the MCP server's ``instructions`` and the client snippet is
in docs/specs/scenario-engine-mcp.md. This class keeps the AgentProvider shape
(propose, then recommend) used by the one-shot REST pipeline.

Nemotron only ever returns data: every tool it can call is read-only or runs
inside a SUMO branch, and none of them changes live signals.
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
