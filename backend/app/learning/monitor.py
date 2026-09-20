"""Live monitor: a ring of live samples, and windows that watch a plan applied to the live city.

A frame observer samples the live city every few simulated seconds, all the time, so the unmanaged period between
detection and implementation is already on record when a plan goes live. A window then collects samples for a
fixed number of *simulated* seconds (pausing the simulation pauses it) and hands a ``LiveRecord`` to its callback.
Samples use the live-metric definitions of a branch's timeline (``collect_metrics``), so predicted and realised
numbers can be compared directly.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.models.domain import NetworkState
from app.models.episode import Implementation, LiveRecord, LiveSample

log = logging.getLogger(__name__)

SAMPLE_S = 5.0  # simulated seconds between samples (the live trend's interval)
RING_S = 1800.0  # simulated seconds of history kept


@dataclass
class _Window:
    record: LiveRecord
    on_progress: Callable[[float], None]
    on_done: Callable[[LiveRecord], Awaitable[None]]
    arrivals: dict[str, float | None] = field(default_factory=dict)  # responder -> response time (None: not yet)
    origin: float = 0.0  # responders are timed from here or from their dispatch, whichever is later


class LiveMonitor:
    def __init__(self, sample_s: float = SAMPLE_S, ring_s: float = RING_S):
        self._sample_s = sample_s
        self._ring: deque[LiveSample] = deque(maxlen=int(ring_s / sample_s))
        self._windows: dict[str, _Window] = {}
        self._tasks: set[asyncio.Task] = set()

    def start_window(
        self,
        key: str,
        implementation: Implementation,
        monitor_s: float,
        on_progress: Callable[[float], None],
        on_done: Callable[[LiveRecord], Awaitable[None]],
    ) -> None:
        """Watch the live city from the moment ``implementation`` went live for ``monitor_s`` simulated seconds.

        Timed responders are the ones the branches timed: the plan's own dispatches plus those already on the way
        when the snapshot was taken. A branch measures from its window start (the snapshot) or from the dispatch,
        whichever is later, so the live side uses the same origin: a responder dispatched with the plan is timed
        from the apply, one that was already running from the snapshot.
        """
        started = implementation.implemented_at
        timed = [*implementation.ems_dispatch_ids, *implementation.ems_en_route_ids]
        record = LiveRecord(
            run_id=implementation.run_id,
            incident_ids=implementation.incident_ids,
            started_at=started,
            ended_at=started,
            monitor_s=monitor_s,
            pre=[s for s in self._ring if s.t <= started],
            ems_timed_ids=timed,
        )
        origin = implementation.snapshot_sim_time if implementation.snapshot_sim_time is not None else started
        window = _Window(record, on_progress, on_done, {d: None for d in timed}, origin)
        self._windows[key] = window

    def stop(self, key: str, reason: str) -> LiveRecord | None:
        """Abort a window (superseded, reset, scene cleared); nothing is reported to its callback."""
        window = self._windows.pop(key, None)
        if window is None:
            return None
        window.record.complete = False
        window.record.abort_reason = reason
        return window.record

    async def shutdown(self) -> None:
        """Cancel queued completion callbacks and wait for them.

        ``on_done`` runs the review and the durable memory write. A discarded monitor (a map switch) must not
        finish one against a city that no longer exists, so these are awaited, not just cancelled.
        """
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - cleanup must not raise into a map switch
                log.exception("a monitor completion task failed while shutting down")

    async def observe(self, state: NetworkState) -> None:
        if self._ring and state.sim_time < self._ring[-1].t:  # the simulation was reset
            self._ring.clear()
            for key in list(self._windows):
                self.stop(key, "simulation reset")
        if self._ring and state.sim_time - self._ring[-1].t < self._sample_s:
            return
        sample = _sample(state)
        self._ring.append(sample)
        for key, window in list(self._windows.items()):
            record = window.record
            if sample.t < record.started_at:
                continue
            record.post.append(sample)
            record.ended_at = sample.t
            for ev in state.emergency_vehicles:
                if ev.id in window.arrivals and ev.arrived_at is not None:
                    window.arrivals[ev.id] = ev.arrived_at - max(window.origin, ev.dispatched_at)
            elapsed = sample.t - record.started_at
            window.on_progress(elapsed)
            if elapsed >= record.monitor_s:
                del self._windows[key]
                times = list(window.arrivals.values())
                # the last responder to reach its scene; unknown while any has not arrived
                record.ems_response_s = max(times) if times and None not in times else None
                task = asyncio.create_task(window.on_done(record))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)


def _sample(state: NetworkState) -> LiveSample:
    m = state.metrics
    blocked = {d.segment_id for d in state.disruptions}
    segments = [s for s in state.segments if s.id in blocked]
    return LiveSample(
        t=state.sim_time,
        delay=m.mean_vehicle_delay,
        queue=m.max_queue_length,
        throughput=m.throughput,
        speed=m.mean_speed,
        vehicles=m.vehicles_in_network,
        incident_queue=sum(s.halting_count for s in segments),
        incident_speed=sum(s.average_speed for s in segments) / len(segments) if segments else 0.0,
    )
