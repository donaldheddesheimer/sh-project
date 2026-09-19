"""The traffic-simulation contract.

Everything above this layer (live runner, scenario branching, REST API, and
later an MCP server exposing tools to Nemotron) talks to this interface only.
It deliberately contains no decision-making logic: callers decide *what* to
try; the simulation only executes and measures it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.domain import (
    Disruption,
    EmergencyDispatch,
    IntersectionState,
    NetworkState,
    RoadSegmentState,
    Severity,
    SignalPolicy,
    SignalProgram,
    SimulationSnapshot,
    TrafficMetrics,
)


class TrafficSimulation(ABC):
    # lifecycle ---------------------------------------------------------------
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @property
    @abstractmethod
    def sim_time(self) -> float: ...

    @property
    @abstractmethod
    def step_length(self) -> float: ...

    @abstractmethod
    def step(self) -> None:
        """Advance one simulation step."""

    @abstractmethod
    def run_for(self, seconds: float, speed_multiplier: float | None = None) -> TrafficMetrics:
        """Advance ``seconds`` of simulated time; returns metrics for that window.

        ``speed_multiplier`` paces execution against the wall clock; ``None``
        runs as fast as possible (used for candidate evaluation).
        """

    # observation -------------------------------------------------------------
    @abstractmethod
    def get_network_state(self) -> NetworkState: ...

    @abstractmethod
    def get_intersection_state(self, intersection_id: str) -> IntersectionState: ...

    @abstractmethod
    def get_edge_state(self, segment_id: str) -> RoadSegmentState: ...

    @abstractmethod
    def get_signal_program(self, intersection_id: str) -> SignalProgram: ...

    @abstractmethod
    def collect_metrics(self) -> TrafficMetrics:
        """Live network metrics at the current instant."""

    # signal control ------------------------------------------------------------
    @abstractmethod
    def set_signal_phase(self, intersection_id: str, phase_index: int) -> None: ...

    @abstractmethod
    def set_phase_duration(self, intersection_id: str, seconds: float) -> None:
        """Set the remaining duration of the current phase."""

    @abstractmethod
    def apply_signal_policy(self, policy: SignalPolicy) -> str:
        """Install a timing policy; returns the new program id. Callers must validate first."""

    # disruptions and responders ------------------------------------------------
    @abstractmethod
    def inject_collision(
        self,
        segment_id: str,
        lanes: list[int],
        position_m: float,
        severity: Severity,
    ) -> Disruption: ...

    @abstractmethod
    def list_disruptions(self) -> list[Disruption]: ...

    @abstractmethod
    def clear_disruption(self, disruption_id: str) -> None: ...

    @abstractmethod
    def spawn_emergency_vehicle(
        self,
        origin_segment: str,
        destination_segment: str,
        destination_position: float | None = None,
        destination_lane: int = 0,
    ) -> EmergencyDispatch: ...

    # branching -------------------------------------------------------------------
    @abstractmethod
    def save_snapshot(self) -> SimulationSnapshot: ...

    @abstractmethod
    def restore_snapshot(self, snapshot: SimulationSnapshot) -> None: ...
