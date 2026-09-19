"""Decision-agent boundary.

An AgentProvider proposes candidate responses and, given simulated outcomes,
recommends one. It never controls signals: candidates are data (SignalPolicy)
that must pass the SafetyValidator and are only ever executed inside
simulation branches by the scenario service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.models.domain import (
    Incident,
    IntersectionState,
    RoadSegmentState,
    SignalPolicy,
    SignalProgram,
    SimulationCandidate,
)


class IncidentContext(BaseModel):
    incident: Incident
    sim_time: float
    segments: list[RoadSegmentState]
    intersections: list[IntersectionState]
    signal_programs: dict[str, SignalProgram] = Field(description="Active program per signalized intersection")


class CandidatePlan(BaseModel):
    id: str
    name: str
    description: str
    policies: list[SignalPolicy] = Field(default_factory=list, description="Empty = do-nothing baseline")


class Recommendation(BaseModel):
    candidate_id: str
    summary: str
    rationale: list[str] = Field(default_factory=list)


class AgentProvider(ABC):
    name: str

    @abstractmethod
    async def propose_candidates(self, context: IncidentContext) -> list[CandidatePlan]: ...

    @abstractmethod
    async def recommend(self, context: IncidentContext, results: list[SimulationCandidate]) -> Recommendation: ...
