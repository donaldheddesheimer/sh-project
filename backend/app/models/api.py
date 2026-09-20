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
