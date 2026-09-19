"""Per-step accumulation of TrafficMetrics (simulator-agnostic)."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from app.models.domain import TrafficMetrics


@dataclass
class StepObservation:
    sim_time: float
    step_length: float
    vehicle_time_loss: dict[str, float]  # background vehicles only
    vehicle_speeds: list[float]
    edge_halting: dict[str, int]
    arrived: int
    pending: int


@dataclass
class _Window:
    start: float
    initial_time_loss: dict[str, float]
    first_time_loss: dict[str, float] = field(default_factory=dict)
    last_time_loss: dict[str, float] = field(default_factory=dict)
    pending_vehicle_seconds: float = 0.0
    max_queue: int = 0
    max_queue_segment: str | None = None
    arrivals: int = 0
    speed_sum: float = 0.0
    speed_samples: int = 0


class MetricsCollector:
    def __init__(self, throughput_window_s: float = 300.0):
        self._throughput_window_s = throughput_window_s
        self._arrivals: deque[tuple[float, int]] = deque()
        self._first_time: float | None = None
        self._last: StepObservation | None = None
        self._window: _Window | None = None

    def reset(self) -> None:
        self._arrivals.clear()
        self._first_time = None
        self._last = None
        self._window = None

    def observe(self, obs: StepObservation) -> None:
        if self._first_time is None:
            self._first_time = obs.sim_time
        self._last = obs
        if obs.arrived:
            self._arrivals.append((obs.sim_time, obs.arrived))
        cutoff = obs.sim_time - self._throughput_window_s
        while self._arrivals and self._arrivals[0][0] < cutoff:
            self._arrivals.popleft()

        w = self._window
        if w is None:
            return
        for vid, loss in obs.vehicle_time_loss.items():
            if vid not in w.first_time_loss:
                w.first_time_loss[vid] = w.initial_time_loss.get(vid, 0.0)
            w.last_time_loss[vid] = loss
        w.pending_vehicle_seconds += obs.pending * obs.step_length
        worst = max(obs.edge_halting.items(), key=lambda kv: kv[1], default=(None, 0))
        if worst[1] > w.max_queue:
            w.max_queue, w.max_queue_segment = worst[1], worst[0]
        w.arrivals += obs.arrived
        w.speed_sum += sum(obs.vehicle_speeds)
        w.speed_samples += len(obs.vehicle_speeds)

    # ------------------------------------------------------------------ live

    def live(self, sim_time: float, emergency_eta: float | None) -> TrafficMetrics:
        obs = self._last
        if obs is None:
            return TrafficMetrics(
                sim_time=sim_time, mean_vehicle_delay=0.0, max_queue_length=0, throughput=0.0, mean_speed=0.0
            )
        losses = list(obs.vehicle_time_loss.values())
        worst = max(obs.edge_halting.items(), key=lambda kv: kv[1], default=(None, 0))
        elapsed = max(obs.step_length, min(self._throughput_window_s, obs.sim_time - (self._first_time or obs.sim_time)))
        arrivals = sum(n for _, n in self._arrivals)
        return TrafficMetrics(
            sim_time=sim_time,
            mean_vehicle_delay=sum(losses) / len(losses) if losses else 0.0,
            max_queue_length=worst[1],
            max_queue_segment=worst[0] if worst[1] else None,
            throughput=arrivals * 3600.0 / elapsed,
            mean_speed=sum(obs.vehicle_speeds) / len(obs.vehicle_speeds) if obs.vehicle_speeds else 0.0,
            emergency_vehicle_eta=emergency_eta,
            vehicles_in_network=len(obs.vehicle_time_loss),
            vehicles_waiting_to_enter=obs.pending,
        )

    # --------------------------------------------------------------- horizon

    def begin_window(self, sim_time: float, current_time_loss: dict[str, float]) -> None:
        self._window = _Window(start=sim_time, initial_time_loss=dict(current_time_loss))

    def end_window(self, sim_time: float, emergency_eta: float | None) -> TrafficMetrics:
        w = self._window
        if w is None:
            raise RuntimeError("end_window() called without begin_window()")
        self._window = None
        duration = max(1e-9, sim_time - w.start)
        served = len(w.last_time_loss)
        accrued = sum(max(0.0, w.last_time_loss[v] - w.first_time_loss[v]) for v in w.last_time_loss)
        live = self.live(sim_time, emergency_eta)
        return TrafficMetrics(
            sim_time=sim_time,
            window_s=duration,
            mean_vehicle_delay=(accrued + w.pending_vehicle_seconds) / served if served else 0.0,
            max_queue_length=w.max_queue,
            max_queue_segment=w.max_queue_segment,
            throughput=w.arrivals * 3600.0 / duration,
            mean_speed=w.speed_sum / w.speed_samples if w.speed_samples else 0.0,
            emergency_vehicle_eta=emergency_eta,
            vehicles_in_network=live.vehicles_in_network,
            vehicles_waiting_to_enter=live.vehicles_waiting_to_enter,
        )
