"""Scenario analysis ("Analyze Response") records: one run = one snapshot of the
live network branched into a baseline plus candidate responses.

Frozen milestone-2 contract (docs/milestone-2/MASTER.md): the scenario engine
produces these, the UI renders them, the agent and simulation never see them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.domain import Recommendation, SimulationCandidate


class ScenarioStatus(StrEnum):
    QUEUED = "queued"  # accepted; waiting for the live runner to take the snapshot
    PROPOSING = "proposing"  # agent is generating candidate plans
    SIMULATING = "simulating"  # validated candidates are running in SUMO branches
    RECOMMENDING = "recommending"  # agent is comparing outcomes
    COMPLETED = "completed"
    FAILED = "failed"


class ScenarioRunRequest(BaseModel):
    incident_id: str | None = Field(None, description="Defaults to the most recent active incident")
    horizon_s: float = Field(600.0, ge=120.0, le=1800.0, description="Simulated seconds per branch")
    ems_probe: bool = Field(True, description="Dispatch an EMS unit in every branch unless one is already en route")


class ScenarioRun(BaseModel):
    id: str = Field(description='e.g. "SCN-0001"')
    incident_id: str
    status: ScenarioStatus
    agent: str = Field(description="AgentProvider that proposed the candidates")
    created_at: datetime
    completed_at: datetime | None = None
    snapshot_sim_time: float | None = Field(None, description="Live simulation time the branches start from")
    horizon_s: float
    ems_probe: bool
    candidates: list[SimulationCandidate] = Field(
        default_factory=list, description='Agent order; always includes id "baseline"'
    )
    recommendation: Recommendation | None = None
    error: str | None = None
