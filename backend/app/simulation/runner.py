"""Runs the live simulation on a dedicated thread, paced against the wall clock.

TraCI connections are blocking and not thread-safe, so the runner thread is
the only code that touches the live simulation. Everything else submits
callables that run between simulation steps, in order.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import TypeVar

from app.models.api import RunStatus
from app.models.domain import NetworkState
from app.simulation.interface import TrafficSimulation

log = logging.getLogger(__name__)
T = TypeVar("T")

MAX_LAG_S = 0.5  # if the simulation falls this far behind real time, drop the backlog instead of racing
PAUSED_HEARTBEAT_S = 1.0


@dataclass
class LiveFrame:
    status: RunStatus
    speed: float
    state: NetworkState | None
    error: str | None = None


class LiveSimulationRunner:
    def __init__(
        self,
        factory: Callable[[], TrafficSimulation],
        *,
        on_frame: Callable[[LiveFrame], None],
        speed: float = 4.0,
        warmup_s: float = 300.0,
        autostart: bool = True,
        broadcast_hz: float = 8.0,
    ):
        self._factory = factory
        self._on_frame = on_frame
        self._speed = speed
        self._warmup_s = warmup_s
        self._want_running = autostart
        self._publish_interval = 1.0 / broadcast_hz
        self._commands: queue.Queue[tuple[Callable[[TrafficSimulation], object], Future]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sim: TrafficSimulation | None = None
        self._status = RunStatus.STARTING
        self._error: str | None = None
        self._dirty = True

    # ------------------------------------------------------------ public API

    @property
    def status(self) -> RunStatus:
        return self._status

    @property
    def speed(self) -> float:
        return self._speed

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="live-simulation", daemon=True)
        self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def submit(self, fn: Callable[[TrafficSimulation], T]) -> Future[T]:
        """Run ``fn(sim)`` on the simulation thread between steps."""
        future: Future[T] = Future()
        self._commands.put((fn, future))
        return future

    async def call(self, fn: Callable[[TrafficSimulation], T]) -> T:
        return await asyncio.wrap_future(self.submit(fn))

    async def set_running(self, running: bool) -> None:
        def apply(_sim: TrafficSimulation) -> None:
            self._want_running = running
            if self._status in (RunStatus.RUNNING, RunStatus.PAUSED):
                self._status = RunStatus.RUNNING if running else RunStatus.PAUSED

        await self.call(apply)

    async def set_speed(self, multiplier: float) -> None:
        def apply(_sim: TrafficSimulation) -> None:
            self._speed = multiplier

        await self.call(apply)

    async def reset(self) -> None:
        """Restart SUMO from a clean network; also the way out of the error state."""

        def reboot(_sim: TrafficSimulation) -> None:
            try:
                self._boot()
            except Exception as exc:
                self._fail(exc)
                raise

        await self.call(reboot)

    # ------------------------------------------------------------ thread body

    def _run(self) -> None:
        try:
            self._boot()
        except Exception as exc:  # noqa: BLE001 - reported to clients; Reset retries
            self._fail(exc)
        next_step = time.monotonic()
        last_publish = 0.0
        stepped = False
        while not self._stop.is_set():
            now = time.monotonic()
            wait = 0.1
            try:
                if self._status is RunStatus.RUNNING:
                    if now >= next_step:
                        self._sim.step()
                        stepped = True
                        next_step += self._sim.step_length / self._speed
                        if now - next_step > MAX_LAG_S:
                            next_step = now
                    wait = max(0.0, next_step - time.monotonic())
                else:
                    next_step = now

                since = now - last_publish
                if self._status is not RunStatus.ERROR and (
                    ((stepped or self._dirty) and since >= self._publish_interval) or since >= PAUSED_HEARTBEAT_S
                ):
                    self._publish()
                    last_publish, stepped = now, False
            except Exception as exc:  # noqa: BLE001 - stop stepping, keep serving commands (Reset recovers)
                self._fail(exc)
            self._drain(wait)
        if self._sim is not None:
            self._sim.close()

    def _fail(self, exc: Exception) -> None:
        log.error("live simulation failed", exc_info=exc)
        self._status = RunStatus.ERROR
        self._error = str(exc)
        self._publish(with_state=False)

    def _boot(self) -> None:
        if self._sim is not None:
            self._sim.close()
            self._sim = None
        self._status = RunStatus.STARTING
        self._error = None
        self._publish(with_state=False)
        sim = self._factory()
        self._sim = sim
        sim.start()
        if self._warmup_s > 0:
            sim.run_for(self._warmup_s)
        self._status = RunStatus.RUNNING if self._want_running else RunStatus.PAUSED
        self._dirty = True

    def _drain(self, wait: float) -> None:
        try:
            fn, future = self._commands.get(timeout=wait) if wait > 0 else self._commands.get_nowait()
        except queue.Empty:
            return
        while True:
            if future.set_running_or_notify_cancel():
                try:
                    future.set_result(fn(self._sim))
                except Exception as exc:  # noqa: BLE001 - returned to the caller
                    future.set_exception(exc)
            self._dirty = True
            try:
                fn, future = self._commands.get_nowait()
            except queue.Empty:
                return

    def _publish(self, with_state: bool = True) -> None:
        state = self._sim.get_network_state() if with_state and self._sim is not None else None
        self._dirty = False
        try:
            self._on_frame(LiveFrame(status=self._status, speed=self._speed, state=state, error=self._error))
        except Exception:  # noqa: BLE001 - a broken consumer must not stop the simulation
            log.exception("frame consumer failed")
