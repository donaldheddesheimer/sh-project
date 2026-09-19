"""Decision-agent boundary.

An AgentProvider proposes candidate responses and, given simulated outcomes,
recommends one. It never controls signals: candidates are data (SignalPolicy,
EmergencyCorridor, RerouteAction) that must pass the SafetyValidator and are
only ever executed inside simulation branches by the scenario service.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.models.domain import (
    EmergencyCorridor,
    EmergencyVehicleState,
    Incident,
    IntersectionState,
    Recommendation,
    RerouteAction,
    RoadSegmentState,
    SignalPolicy,
    SignalProgram,
    SimulationCandidate,
)

__all__ = ["AgentProvider", "CandidatePlan", "IncidentContext", "Recommendation"]


class IncidentContext(BaseModel):
    incident: Incident
    sim_time: float
    segments: list[RoadSegmentState]
    intersections: list[IntersectionState]
    signal_programs: dict[str, SignalProgram] = Field(description="Active program per signalized intersection")
    emergency_vehicles: list[EmergencyVehicleState] = Field(default_factory=list, description="Responders in the network")
    ems_origin_segment: str | None = Field(None, description="Segment an EMS probe/dispatch departs from")


class CandidatePlan(BaseModel):
    id: str
    name: str
    description: str
    policies: list[SignalPolicy] = Field(default_factory=list)
    corridor: EmergencyCorridor | None = None
    reroutes: list[RerouteAction] = Field(default_factory=list)
    # a plan with no policies, corridor or reroutes is the do-nothing baseline


class AgentProvider(ABC):
    name: str

    @abstractmethod
    async def propose_candidates(self, context: IncidentContext) -> list[CandidatePlan]: ...

    @abstractmethod
    async def recommend(self, context: IncidentContext, results: list[SimulationCandidate]) -> Recommendation: ...
