"""Request/response and real-time message models for the HTTP + WebSocket API."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.domain import (
    Disruption,
    EmergencyDispatch,
    EmergencyVehicleState,
    Incident,
    IncidentType,
    IntersectionState,
    MetricSample,  # noqa: F401 - re-exported for existing importers
    RoadSegmentState,
    Severity,
    TrafficMetrics,
    VehicleState,
)
from app.models.episode import MemoryMode


class RunStatus(StrEnum):
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    ERROR = "error"


class StatusInfo(BaseModel):
    status: RunStatus
    speed: float
    error: str | None = None


class ProviderInfo(BaseModel):
    smart_city: str
    agent: str
    simulator: str


class CityState(BaseModel):
    """Everything the operations-center UI renders, pushed over /ws/state."""

    status: RunStatus
    speed: float
    sim_time: float
    intersections: list[IntersectionState]
    segments: list[RoadSegmentState]
    vehicles: list[VehicleState]
    emergency_vehicles: list[EmergencyVehicleState]
    incidents: list[Incident]
    metrics: TrafficMetrics
    providers: ProviderInfo


class EventLevel(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ALERT = "alert"


class OpsEvent(BaseModel):
    id: int
    timestamp: datetime
    sim_time: float | None
    level: EventLevel
    message: str
    incident_id: str | None = None


class ModelCallStatus(StrEnum):
    PENDING = "pending"  # the request is out; no reply yet
    OK = "ok"
    ERROR = "error"


class ModelCall(BaseModel):
    """One request to a hosted model, from the moment it is sent to its outcome.

    The ops log says what the agent decided; this says whether the model was reached at all, with which
    model id, and what came back. The same ``id`` is published twice (pending, then the outcome), so a
    client replaces the entry rather than appending a second one.
    """

    id: int
    timestamp: datetime
    sim_time: float | None
    provider: str = Field(description="nemotron or claude")
    role: str = Field(description="which job called it: analyst or reviewer")
    model: str = Field(description="exact model id sent to the endpoint")
    purpose: str = Field(description="propose, recommend, decide or review")
    endpoint: str = Field(description="URL the request went to, so it is visible which host answered")
    status: ModelCallStatus
    duration_ms: int | None = None
    attempts: int = Field(1, description="HTTP attempts, more than one when the endpoint was busy and it backed off")
    http_status: int | None = None
    request_chars: int | None = Field(None, description="characters of prompt sent, a rough size for the request")
    response_chars: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    detail: str | None = Field(None, description="a truncated preview of the reply, or the error that ended the call")


class InjectIncidentRequest(BaseModel):
    type: IncidentType = IncidentType.COLLISION
    segment_id: str | None = Field(None, description="Defaults to the scenario's collision location")
    lanes: list[int] | None = Field(None, description="Blocked lane indices, 0 = rightmost")
    position_fraction: float | None = Field(None, ge=0.1, le=0.9)
    severity: Severity | None = None


class InjectIncidentResponse(BaseModel):
    disruption: Disruption
    message: str


class DispatchRequest(BaseModel):
    origin_segment: str | None = Field(None, description="Defaults to the scenario's EMS station")
    destination_segment: str | None = Field(None, description="Defaults to the most recent active incident")


class DispatchResponse(BaseModel):
    dispatch: EmergencyDispatch


class SpeedRequest(BaseModel):
    multiplier: float = Field(gt=0, le=64)


class MapSelectionRequest(BaseModel):
    map_id: str = Field(description="Bundled scenario id: downtown_grid or pittsburgh_oakland")


class DemoStartRequest(BaseModel):
    script: str | None = Field(
        None,
        description=(
            "A demo script id from simulation/scenarios/<scenario>/demos/*.json. "
            "Omit it to arm AUTONOMOUS_SCRIPT, which is what the console's Arm agent button sends."
        ),
    )
    memory_mode: MemoryMode = Field("use", description="use recalled lessons, or ignore them for a control run")


class DemoAnalyzeRequest(BaseModel):
    memory_mode: MemoryMode | None = Field(None, description="Override the episode's memory mode; omitted keeps it")


class ControlResponse(BaseModel):
    status: RunStatus
    speed: float
    sim_time: float
