"""Core domain models shared by the simulation, providers, API and (later) MCP tools.

Units: distances in metres, speeds in m/s, durations and simulation times in
seconds. Wall-clock timestamps are timezone-aware UTC datetimes.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class GeoPoint(BaseModel):
    lat: float
    lon: float


# --------------------------------------------------------------------------
# Live network state
# --------------------------------------------------------------------------


class SignalColor(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class CongestionLevel(StrEnum):
    FREE = "free"
    MODERATE = "moderate"
    HEAVY = "heavy"
    SEVERE = "severe"


class IntersectionState(BaseModel):
    id: str
    name: str
    location: GeoPoint
    signalized: bool
    current_phase: int | None = None
    phase_label: str | None = Field(None, description='Human-readable phase, e.g. "N-S green"')
    phase_remaining: float | None = Field(None, description="Seconds until the next phase change")
    cycle_length: float | None = None
    program_id: str | None = None
    approach_signals: dict[str, SignalColor] = Field(
        default_factory=dict, description="Through-movement signal per approach, keyed by incoming segment id"
    )
    queue_lengths: dict[str, int] = Field(
        default_factory=dict, description="Halting vehicles per approach, keyed by incoming segment id"
    )
    average_speed: float = Field(0.0, description="Mean speed on the approaches (m/s)")
    vehicle_count: int = Field(0, description="Vehicles on the approaches")
    congestion: float = Field(0.0, ge=0.0, le=1.0)


class RoadSegmentState(BaseModel):
    id: str
    name: str
    source: str
    destination: str
    direction: str = Field(description="Travel direction: NB/SB/EB/WB")
    average_speed: float
    speed_limit: float
    vehicle_count: int
    halting_count: int = Field(description="Vehicles with speed < 0.1 m/s (queue)")
    occupancy: float = Field(ge=0.0, le=1.0)
    congestion: float = Field(ge=0.0, le=1.0, description="Smoothed congestion index")
    level: CongestionLevel
    blocked_lanes: list[int] = Field(default_factory=list)


class VehicleKind(StrEnum):
    CAR = "car"
    TRUCK = "truck"
    BUS = "bus"
    EMERGENCY = "emergency"
    DISABLED = "disabled"


class VehicleState(BaseModel):
    id: str
    lat: float
    lon: float
    angle: float = Field(description="Heading in degrees, 0 = north, clockwise")
    speed: float
    kind: VehicleKind


class EmergencyStatus(StrEnum):
    EN_ROUTE = "en_route"
    ON_SCENE = "on_scene"
    COMPLETED = "completed"


class EmergencyVehicleState(BaseModel):
    id: str
    status: EmergencyStatus
    location: GeoPoint | None
    speed: float
    origin_segment: str
    destination_segment: str
    dispatched_at: float = Field(description="Simulation time of dispatch")
    arrived_at: float | None = None
    eta_s: float | None = Field(None, description="Estimated seconds to scene (None once arrived)")


class MetricSample(BaseModel):
    """One point of a metric trend (live trend or a candidate's horizon timeline)."""

    t: float = Field(description="Simulation time")
    delay: float
    queue: int
    throughput: float
    speed: float
    vehicles: int


class EmergencyResponse(BaseModel):
    """One responder's realised response time within a measured window.

    Produced per candidate branch by the twin alongside the aggregate ``TrafficMetrics.emergency_vehicle_eta``,
    so a plan that helps one responder and hurts another is visible instead of averaged away.
    """

    vehicle_id: str
    # the scene it was sent to: identifies the incident (responder ids differ between a branch and the live city)
    destination_segment: str
    dispatched_at: float
    arrived_at: float | None = None
    response_s: float | None = Field(
        None, description="arrived_at - max(window start, dispatched_at); None until it arrives"
    )


class TrafficMetrics(BaseModel):
    """Network performance indicators.

    Live metrics (window_s is None) describe the current instant:
      mean_vehicle_delay = mean accumulated time loss of vehicles now in the network.
    Horizon metrics (window_s set) summarise a simulated run of that length:
      mean_vehicle_delay = time loss accrued during the window per vehicle served,
      including time spent waiting to enter the network.
    """

    sim_time: float
    window_s: float | None = None
    mean_vehicle_delay: float = Field(description="Seconds")
    max_queue_length: int = Field(description="Vehicles halted on the worst segment")
    max_queue_segment: str | None = None
    throughput: float = Field(description="Completed trips per hour")
    mean_speed: float = Field(description="m/s")
    emergency_vehicle_eta: float | None = Field(
        None,
        description=(
            "Seconds. Measured window: the realised response time of the last responder to reach its scene "
            "(None if no responder mattered to the window or any has not arrived). "
            "Live metrics: the soonest estimated time to scene over en-route responders (None if there are none)."
        ),
    )
    # measured window: each responder of that same window, the same set the ETA above is taken over. Live metrics
    # never fill it, so it is always empty there.
    emergency_responses: list[EmergencyResponse] = Field(default_factory=list)
    vehicles_in_network: int = 0
    vehicles_waiting_to_enter: int = 0


# --------------------------------------------------------------------------
# Incidents
# --------------------------------------------------------------------------


class IncidentType(StrEnum):
    COLLISION = "collision"
    STALLED_VEHICLE = "stalled_vehicle"
    WRONG_WAY = "wrong_way"
    CONGESTION = "congestion"


class Severity(StrEnum):
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    ACTIVE = "active"
    CLEARED = "cleared"


class IncidentMatch(BaseModel):
    """How an external report was associated with the simulation network."""

    method: Literal["geometry", "place", "sensor"] | None = None
    distance_m: float | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    notes: list[str] = Field(default_factory=list)
    lane_assumed: bool = True
    mirrored: bool = False
    reason: str | None = None


class IncidentLocation(BaseModel):
    segment_id: str | None = Field(None, description="Road segment in the simulation network (map-matched)")
    intersection_id: str | None = Field(None, description="Nearest intersection")
    position_m: float | None = Field(None, description="Distance along the segment")
    point: GeoPoint | None = None
    description: str
    match: IncidentMatch | None = None


class Incident(BaseModel):
    """A traffic incident as reported by a SmartCityProvider.

    Field choices mirror NVIDIA VSS incident documents: sensor_ids <- sensorId,
    object_ids <- objectIds, confidence <- info/analyticsModule.info confidence,
    timestamp/cleared_at <- timestamp/end (older start is accepted by the mapper).
    """

    id: str
    type: IncidentType
    status: IncidentStatus = IncidentStatus.ACTIVE
    severity: Severity
    location: IncidentLocation
    timestamp: datetime = Field(description="When the incident was detected (UTC)")
    sim_time: float | None = Field(None, description="Simulation time of detection")
    affected_lanes: list[int] = Field(default_factory=list, description="0 = rightmost lane")
    total_lanes: int | None = None
    description: str
    source: str = Field(description="Provider that reported the incident")
    external_id: str | None = Field(None, description="Provider-native id retained for traceability")
    external_category: str | None = None
    type_mapping_note: str | None = None
    sensor_ids: list[str] = Field(default_factory=list)
    object_ids: list[str] = Field(default_factory=list)
    confidence: float | None = None
    vlm_confirmed: bool | None = None
    cleared_at: datetime | None = None


# --------------------------------------------------------------------------
# Signal control and candidate evaluation
# --------------------------------------------------------------------------


class PhaseKind(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    ALL_RED = "all_red"


class SignalPhase(BaseModel):
    index: int
    duration: float
    state: str = Field(description="SUMO signal state string, one char per controlled link")
    kind: PhaseKind
    label: str
    served_approaches: list[str] = Field(
        default_factory=list, description="Compass labels (NB/SB/EB/WB) of the served approaches; display only"
    )
    served_segments: list[str] = Field(
        default_factory=list, description="Approaches whose through movement runs, as incoming segment ids"
    )


class SignalProgram(BaseModel):
    intersection_id: str
    program_id: str
    phases: list[SignalPhase]

    @property
    def cycle_length(self) -> float:
        return sum(p.duration for p in self.phases)


class SignalPolicy(BaseModel):
    """A proposed timing change for one intersection.

    Policies only change timing (splits via phase durations, and offset). Phase
    states - which movements run together - always come from the base program,
    so a policy can never create conflicting green movements.
    """

    intersection_id: str
    phase_durations: dict[int, float] = Field(default_factory=dict, description="Phase index -> new duration (s)")
    offset_s: float | None = Field(None, description="New cycle offset (s)")
    reason: str


class EmergencyCorridor(BaseModel):
    """Signal pre-emption ahead of en-route emergency vehicles (a "green corridor").

    Applies to every emergency vehicle en route in the simulation, including
    ones dispatched after the corridor is enabled. The controller only ever
    reaches a responder's green through the running phase's own yellow and
    all-red clearance, never by skipping them.
    """

    intersection_ids: list[str] = Field(
        default_factory=list, description="Signals that may pre-empt; empty = every signal on a responder's route"
    )
    detection_distance_m: float = Field(150.0, description="Pre-empt when a responder is this close to the stop line")
    min_served_green_s: float = Field(12.0, description="A running green is never cut before it has run this long")
    max_hold_s: float = Field(60.0, description="Longest a pre-empted green is held for one responder")
    reason: str


class RerouteAction(BaseModel):
    """A diversion advisory (DMS sign / navigation-app alert) for traffic headed into given segments."""

    avoid_segment_ids: list[str]
    compliance: float = Field(ge=0.0, le=1.0, description="Share of affected drivers who divert")
    reason: str


class CandidateStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REJECTED = "rejected"
    FAILED = "failed"


class SimulationCandidate(BaseModel):
    """One candidate response and, once simulated, its outcome over the analysis horizon."""

    id: str = Field(description='Stable plan id; "baseline" is the do-nothing reference')
    name: str = ""
    description: str
    policies: list[SignalPolicy] = Field(default_factory=list, description="Signal timing changes")
    corridor: EmergencyCorridor | None = None
    reroutes: list[RerouteAction] = Field(default_factory=list)
    status: CandidateStatus = CandidateStatus.PENDING
    metrics: TrafficMetrics | None = Field(None, description="Horizon metrics (window_s set) once completed")
    timeline: list[MetricSample] = Field(default_factory=list, description="Live metrics sampled during the horizon")
    violations: list[str] = Field(default_factory=list, description="Safety-validator findings (status rejected)")
    notes: list[str] = Field(default_factory=list)
    wall_time_s: float | None = Field(None, description="Wall-clock seconds the branch took to simulate")


class Recommendation(BaseModel):
    candidate_id: str
    summary: str
    rationale: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Simulation ground truth (what the digital twin is modelling)
# --------------------------------------------------------------------------


class Disruption(BaseModel):
    """A physical disruption modelled in the simulation (e.g. crashed vehicles)."""

    id: str
    kind: IncidentType
    segment_id: str
    lanes: list[int]
    total_lanes: int
    position_m: float
    severity: Severity
    started_at: float = Field(description="Simulation time")
    point: GeoPoint
    vehicle_ids: list[str] = Field(default_factory=list)
    pass_speed: float | None = Field(None, description="Speed limit past the scene on open lanes (m/s)")


class EmergencyDispatch(BaseModel):
    id: str
    origin_segment: str
    destination_segment: str
    destination_position: float
    destination_lane: int
    dispatched_at: float
    arrived_at: float | None = None
    status: EmergencyStatus = EmergencyStatus.EN_ROUTE


class ProgramLogic(BaseModel):
    """A signal program installed at runtime (e.g. a timing policy applied to the live city)."""

    tls_id: str
    program_id: str
    phases: list[tuple[float, str]] = Field(description="(duration s, SUMO state string) per phase")


class SimulationSnapshot(BaseModel):
    """A saved simulation state that candidate runs can branch from."""

    id: str
    sim_time: float
    path: str
    created_at: datetime
    disruptions: list[Disruption] = Field(default_factory=list)
    dispatches: list[EmergencyDispatch] = Field(default_factory=list)
    custom_programs: list[ProgramLogic] = Field(
        default_factory=list,
        description="Programs installed at runtime; SUMO refuses to load a state naming a program it does not know, "
        "so a branch re-creates these before loading",
    )


class NetworkState(BaseModel):
    sim_time: float
    intersections: list[IntersectionState]
    segments: list[RoadSegmentState]
    vehicles: list[VehicleState]
    emergency_vehicles: list[EmergencyVehicleState]
    metrics: TrafficMetrics
    disruptions: list[Disruption] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Static network geometry (sent once to map clients)
# --------------------------------------------------------------------------


class SegmentGeometry(BaseModel):
    id: str
    name: str
    source: str
    destination: str
    direction: str
    lanes: int
    length: float
    speed_limit: float
    coordinates: list[tuple[float, float]] = Field(description="[lon, lat] centreline of the road")


class ApproachGeometry(BaseModel):
    segment_id: str
    approach: str
    signal_point: tuple[float, float] = Field(description="[lon, lat] of the signal head marker")


class IntersectionGeometry(BaseModel):
    id: str
    name: str
    lon: float
    lat: float
    signalized: bool
    approaches: list[ApproachGeometry]


class StationGeometry(BaseModel):
    id: str
    name: str
    segment_id: str
    lon: float
    lat: float


class NetworkGeometry(BaseModel):
    id: str
    name: str
    attribution: str | None = Field(None, description="Data credit to show on the map")
    center: tuple[float, float]
    bounds: tuple[tuple[float, float], tuple[float, float]]
    segments: list[SegmentGeometry]
    intersections: list[IntersectionGeometry]
    stations: list[StationGeometry]
