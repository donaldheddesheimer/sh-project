"""Scenario analysis ("Analyze Response") records: one run = one snapshot of the
live network branched into a baseline plus candidate responses.

The scenario-analysis contract, introduced in milestone 2 (docs/milestone-2/MASTER.md)
and mirrored in frontend/src/api/types.ts: the scenario engine produces these, the UI
renders them, the agent and simulation never see them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.domain import PlanRoutes, Recommendation, SimulationCandidate
from app.models.episode import Implementation, MemoryMode, RecalledExperience


class ScenarioStatus(StrEnum):
    QUEUED = "queued"  # accepted; waiting for the live runner to take the snapshot
    PROPOSING = "proposing"  # agent is generating candidate plans
    SIMULATING = "simulating"  # validated candidates are running in SUMO branches
    RECOMMENDING = "recommending"  # agent is comparing outcomes
    COMPLETED = "completed"
    FAILED = "failed"


class ScenarioRunRequest(BaseModel):
    incident_id: str | None = Field(None, description="Defaults to the most recent active incident")
    incident_ids: list[str] | None = Field(None, description="Analyze several incidents together (overrides incident_id)")
    horizon_s: float = Field(600.0, ge=120.0, le=1800.0, description="Simulated seconds per branch")
    ems_probe: bool = Field(True, description="Dispatch an EMS unit in every branch unless one is already en route")
    memory_mode: MemoryMode = Field("use", description="use recalled lessons, or ignore them for a control run")


class ScenarioRun(BaseModel):
    id: str = Field(description='e.g. "SCN-0001"')
    incident_id: str = Field(description="The primary incident (the first of incident_ids)")
    incident_ids: list[str] = Field(default_factory=list, description="Every incident analyzed together in this run")
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
    routes: dict[str, PlanRoutes] = Field(default_factory=dict, description="Candidate id -> paths it acts on")
    recommendation: Recommendation | None = None
    error: str | None = None
    rounds: int = Field(0, description="Simulation rounds run (the deterministic agent runs one; an MCP agent may run several)")
    memory_mode: MemoryMode = "use"
    recalled: list[str] = Field(default_factory=list, description="Remembered episodes given to the agent as lessons")
    recall_provenance: list[RecalledExperience] = Field(
        default_factory=list, description="Scores and trust state for every remembered episode given to the agent"
    )
    implementation: Implementation | None = Field(
        None, description="Set once the recommended plan was applied to the live simulation"
    )
