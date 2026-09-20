"""Autonomous demo-episode records: detect -> analyze -> implement -> monitor -> review -> remember.

One episode = one agent working one set of active incidents. If another incident is detected while the
agent is still working, the episode is superseded by a new one that carries every active incident.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class EpisodeStatus(StrEnum):
    ARMED = "armed"  # script loaded, waiting for the crash to be detected
    DETECTED = "detected"  # incident(s) reported; the agent has been handed the state
    ANALYZING = "analyzing"  # agent is testing alternatives in parallel branches
    MONITORING = "monitoring"  # the chosen plan is applied to the live sim; live data is being cached
    REVIEWING = "reviewing"  # window closed; reviewer agent is condensing the data
    COMPLETED = "completed"  # lesson stored in memory, live collection stopped
    SUPERSEDED = "superseded"  # another crash arrived mid-response; a new episode took over
    ABORTED = "aborted"  # reset, or the scene was cleared before the window closed
    FAILED = "failed"  # agent, implementor or reviewer error


ACTIVE_STATUSES = (
    EpisodeStatus.DETECTED,
    EpisodeStatus.ANALYZING,
    EpisodeStatus.MONITORING,
    EpisodeStatus.REVIEWING,
)
# the statuses in which the agent or the implementor is still working (a new crash supersedes these)
WORKING_STATUSES = (EpisodeStatus.DETECTED, EpisodeStatus.ANALYZING, EpisodeStatus.MONITORING)


class LiveSample(BaseModel):
    """One cached observation of the live city."""

    t: float = Field(description="Simulation time")
    delay: float
    queue: int
    throughput: float
    speed: float
    vehicles: int
    incident_queue: int = Field(description="Halted vehicles summed over the incident segments")
    incident_speed: float = Field(description="Mean speed over the incident segments (m/s)")


class Implementation(BaseModel):
    """A recommended plan applied to the live simulation."""

    run_id: str
    candidate_id: str
    candidate_name: str
    implemented_by: str = Field(description="agent | operator | coordinator")
    incident_ids: list[str]
    implemented_at: float = Field(description="Simulation time the plan went live")
    snapshot_sim_time: float | None = None
    staleness_s: float | None = Field(None, description="Live time that passed between the branch snapshot and the apply")
    policies: dict[str, str] = Field(default_factory=dict, description="Intersection -> program id now running")
    corridor: bool = False
    diverted: int = Field(0, description="Vehicles diverted synchronously when this response was applied")
    ems_dispatch_ids: list[str] = Field(default_factory=list, description="Responders dispatched with the plan")
    ems_en_route_ids: list[str] = Field(
        default_factory=list, description="Responders already on the way at the snapshot; the branches timed them too"
    )
    notes: list[str] = Field(default_factory=list)


class LiveRecord(BaseModel):
    """What the monitor cached around an implementation."""

    run_id: str
    incident_ids: list[str]
    started_at: float
    ended_at: float
    monitor_s: float
    pre: list[LiveSample] = Field(default_factory=list, description="Live samples before the plan went live")
    post: list[LiveSample] = Field(default_factory=list, description="Live samples while the plan was in force")
    ems_response_s: float | None = Field(None, description="Realised response time; None if a responder never arrived")
    ems_timed_ids: list[str] = Field(default_factory=list, description="Responders this window timed")
    complete: bool = True
    abort_reason: str | None = None


class WindowStats(BaseModel):
    """Aggregates of a set of samples (same definition for predicted and realised)."""

    samples: int
    mean_delay: float
    end_delay: float
    peak_queue: int
    mean_throughput: float
    mean_speed_mps: float
    incident_queue_end: int = 0
    ems_response_s: float | None = None


Outcome = Literal["effective", "ineffective", "inconclusive"]
ResponseKind = Literal["corridor", "diversion"]
MemoryMode = Literal["use", "ignore"]


class ResponseCheck(BaseModel):
    """Evidence that an applied response actually exercised its intended control."""

    kind: ResponseKind
    ok: bool | None = Field(description="True when exercised, false when explicitly idle/disabled, null when unknown")
    detail: str


class Scorecard(BaseModel):
    """Deterministic numbers the reviewer interprets. Computed by code, never by the LLM."""

    candidate_id: str
    candidate_name: str
    window_s: float = Field(description="Sim seconds compared (shorter of the monitor and branch horizons)")
    realised: WindowStats
    predicted: WindowStats | None = None
    predicted_baseline: WindowStats | None = None
    pre: WindowStats | None = Field(None, description="The unmanaged period before the plan went live")
    delay_slope_pre_per_min: float | None = None
    delay_slope_post_per_min: float | None = None
    queue_slope_pre_per_min: float | None = None
    queue_slope_post_per_min: float | None = None
    predicted_gain: dict[str, float | None] = Field(
        default_factory=dict, description="Predicted plan vs predicted baseline: delay_pct, queue, ems_s"
    )
    prediction_error: dict[str, float | None] = Field(
        default_factory=dict, description="Realised minus predicted for the chosen plan: delay_pct, queue, ems_s"
    )
    realised_vs_baseline: dict[str, float | None] = Field(
        default_factory=dict, description="Realised minus the predicted do-nothing baseline: delay_pct, queue, ems_s"
    )
    staleness_s: float | None = None
    candidates_tried: int = 0
    rejected: int = 0
    picked_best: bool | None = Field(None, description="Did the recommendation match the rubric's pick among the simulated plans")
    best_by_rubric: str | None = None
    material: bool = Field(False, description="The predicted gain exceeded the noise thresholds")
    outcome: Outcome = "inconclusive"
    checks: list[ResponseCheck] = Field(default_factory=list)
    provisional: bool = Field(False, description="An applicable response check was unsuccessful or unavailable")
    notes: list[str] = Field(default_factory=list)


class Lesson(BaseModel):
    verdict: Outcome
    summary: str
    what_worked: list[str] = Field(default_factory=list)
    what_didnt: list[str] = Field(default_factory=list)
    next_time: list[str] = Field(default_factory=list)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    reviewer: str = "mock"


class IncidentFeatures(BaseModel):
    """What memory recall matches on."""

    incident_id: str
    type: str
    severity: str
    segment_id: str
    street: str
    direction: str
    upstream: str
    downstream: str
    blocked_lanes: list[int] = Field(default_factory=list)
    total_lanes: int | None = None


class PlanSummary(BaseModel):
    id: str
    name: str
    kinds: list[str] = Field(description="timing | corridor | diversion | none")
    outcome: str = Field(description="One line of predicted numbers or the rejection reason")
    status: str


class Experience(BaseModel):
    """One remembered episode: what was faced, what was tried, what happened, what was learned."""

    id: str
    created_at: datetime
    script_id: str | None = None
    analyst: str
    incidents: list[IncidentFeatures]
    chosen: PlanSummary
    tried: list[PlanSummary] = Field(default_factory=list)
    scorecard: Scorecard
    lesson: Lesson
    rounds: int = 0
    memory_mode: MemoryMode = "use"
    eligible_for_recall: bool = True
    recalled: list[str] = Field(default_factory=list, description="Remembered episodes the analyst was given")
    recall_provenance: list[RecalledExperience] = Field(
        default_factory=list, description="Scores and trust state for every remembered episode given to the analyst"
    )
    analysis_wall_s: float | None = Field(None, description="Wall-clock seconds from detection to recommendation")


class RecalledExperience(BaseModel):
    """The compact view of an Experience injected into the agent's context."""

    id: str
    similarity: float = Field(description="Backward-compatible alias for ranking_score")
    structured_score: float = 0.0
    semantic_score: float | None = None
    combined_score: float = 0.0
    ranking_score: float = 0.0
    provisional: bool = False
    trusted: bool = True
    incidents: list[str] = Field(description="One line per incident, e.g. 'collision major, Main St EB, right lane blocked'")
    chosen: str = Field(description="Plan family that was applied, e.g. 'divert-advisory' (incident prefix removed)")
    chosen_name: str = ""
    kinds: list[str]
    verdict: Outcome
    summary: str
    what_worked: list[str] = Field(default_factory=list)
    what_didnt: list[str] = Field(default_factory=list)
    next_time: list[str] = Field(default_factory=list)
    numbers: dict[str, float | None] = Field(default_factory=dict)


class LearningReportEpisode(BaseModel):
    """One durable episode in the read-only learning report."""

    id: str
    script_id: str | None = None
    analyst: str
    memory_mode: MemoryMode = "use"
    eligible_for_recall: bool = True
    recalled_sources: list[str] = Field(default_factory=list)
    recall_provenance: list[RecalledExperience] = Field(default_factory=list)
    warm: bool = False
    transfer: bool = False
    candidate_order: list[str] = Field(default_factory=list)
    rounds: int = 0
    candidates_tried: int = 0
    analysis_wall_s: float | None = None
    selected_plan: str
    verdict: Outcome
    delay_vs_baseline_pct: float | None = None
    prediction_error: dict[str, float | None] = Field(default_factory=dict)
    staleness_s: float | None = None
    checks: list[ResponseCheck] = Field(default_factory=list)
    provisional: bool = False


class LearningComparison(BaseModel):
    """A warm episode compared with a no-memory control of the same script."""

    script_id: str | None = None
    warm_episode_id: str
    control_episode_id: str
    transfer: bool
    useful: bool | None = Field(None, description="Only evaluated for a cross-script recall comparison")
    behavior_changes: list[str] = Field(default_factory=list)
    deltas: dict[str, float | None] = Field(default_factory=dict)


class LearningReport(BaseModel):
    episodes: list[LearningReportEpisode] = Field(default_factory=list)
    comparisons: list[LearningComparison] = Field(default_factory=list)


class EpisodeStep(BaseModel):
    status: EpisodeStatus
    at: datetime
    sim_time: float | None = None
    message: str


class Episode(BaseModel):
    id: str = Field(description='e.g. "EP-0001"')
    script_id: str | None = None
    status: EpisodeStatus = EpisodeStatus.ARMED
    analyst: str = ""
    reviewer: str = ""
    created_at: datetime
    completed_at: datetime | None = None
    incident_ids: list[str] = Field(default_factory=list)
    supersedes: str | None = Field(None, description="Episode this one took over from when a new crash arrived")
    superseded_by: str | None = None
    run_id: str | None = None
    rounds: int = 0
    candidates: int = Field(0, description="Candidates in the analysis, baseline included")
    memory_mode: MemoryMode = "use"
    analysis_wall_s: float | None = Field(None, description="Wall-clock seconds from detection to recommendation")
    detected_sim_time: float | None = None
    implemented_sim_time: float | None = None
    monitor_s: float = 600.0
    monitor_progress_s: float = 0.0
    implementation: Implementation | None = None
    scorecard: Scorecard | None = None
    lesson: Lesson | None = None
    recalled: list[str] = Field(default_factory=list, description="Ids of remembered episodes given to the agent")
    recall_provenance: list[RecalledExperience] = Field(
        default_factory=list, description="Scores and trust state for every remembered episode given to the analyst"
    )
    memory_path: str | None = None
    error: str | None = None
    steps: list[EpisodeStep] = Field(default_factory=list)


class DemoInfo(BaseModel):
    scripts: list[dict] = Field(description="Every demos/*.json script: id, name, description, crash times, monitor_s")
    armed: str | None = Field(None, description="Script currently armed (autonomous response is on while one is)")
    analyst: str = Field("", description="Analyst new episodes use: mock or nemotron")
    current: Episode | None = None
    memory: dict
