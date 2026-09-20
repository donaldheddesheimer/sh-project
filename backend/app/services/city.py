"""Operations-center orchestration.

Joins the live simulation, the Smart City provider and the event log into the
CityState the UI renders, and executes operator commands. It depends only on
the provider interfaces, never on a concrete mock or NVIDIA implementation.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import TypeVar

from app.agent.base import AgentProvider
from app.config import Settings
from app.models.api import (
    CityState,
    EventLevel,
    InjectIncidentRequest,
    MetricSample,
    OpsEvent,
    ProviderInfo,
    RunStatus,
    StatusInfo,
)
from app.models.domain import (
    Disruption,
    EmergencyDispatch,
    EmergencyStatus,
    Incident,
    IncidentType,
    NetworkGeometry,
    NetworkState,
    Severity,
    SignalProgram,
)
from app.models.episode import Episode
from app.models.scenario import ScenarioRun
from app.services.events import EventLog
from app.simulation.branching import probe_for_incident
from app.simulation.interface import TrafficSimulation
from app.simulation.network import RoadNetwork
from app.simulation.runner import LiveFrame, LiveSimulationRunner
from app.simulation.scenario import DemoCrash, Scenario
from app.smart_city.base import SmartCityEvent, SmartCityEventKind, SmartCityProvider
from app.websocket.hub import ConnectionHub

log = logging.getLogger(__name__)

FrameObserver = Callable[[NetworkState], Awaitable[None]]
IncidentListener = Callable[[SmartCityEvent], Awaitable[None]]
ResetListener = Callable[[], Awaitable[None]]
T = TypeVar("T")

TREND_SAMPLE_S = 5.0  # simulated seconds between trend samples
TREND_SAMPLES = 180  # 15 simulated minutes


class NotReady(RuntimeError):
    pass


class Conflict(RuntimeError):
    pass


def envelope(kind: str, payload_json: str) -> str:
    return f'{{"type":"{kind}","data":{payload_json}}}'


class CityService:
    def __init__(
        self,
        *,
        settings: Settings,
        scenario: Scenario,
        network: RoadNetwork,
        sim_factory: Callable[[], TrafficSimulation],
        smart_city: SmartCityProvider,
        agent: AgentProvider,
        frame_observers: list[FrameObserver],
        hub: ConnectionHub,
    ):
        self.settings = settings
        self.scenario = scenario
        self.network = network
        self.smart_city = smart_city
        self.agent = agent
        self.hub = hub
        self.events = EventLog(on_event=self._broadcast_event)
        self.providers = ProviderInfo(smart_city=smart_city.name, agent=agent.name, simulator="Eclipse SUMO")
        self.geometry: NetworkGeometry = network.geometry()
        self._observers = frame_observers
        self._runner = LiveSimulationRunner(
            sim_factory,
            on_frame=self._on_frame_threadsafe,
            speed=settings.sim_speed,
            warmup_s=settings.sim_warmup_s,
            autostart=settings.sim_autostart,
            broadcast_hz=settings.broadcast_hz,
        )
        self._loop: asyncio.AbstractEventLoop | None = None
        self._frames: asyncio.Queue[LiveFrame] | None = None
        self._consumer: asyncio.Task | None = None
        self._state: CityState | None = None
        self._status = RunStatus.STARTING
        self._error: str | None = None
        self._ems_status: dict[str, EmergencyStatus] = {}
        self._speed_hold: tuple[float, float] | None = None  # (speed before the hold, speed we imposed)
        self._trend: deque[MetricSample] = deque(maxlen=TREND_SAMPLES)
        self.latest_scenario: ScenarioRun | None = None
        self.latest_episode: Episode | None = None
        # Hooks for the learning services: called after an incident is logged, and before the simulation reboots.
        self.incident_listeners: list[IncidentListener] = []
        self.reset_listeners: list[ResetListener] = []
        # Held for a whole reset (listeners and reboot), and by the implementor for a whole apply, so a plan is
        # never installed on a simulation that is about to be, or has just been, replaced.
        self.live_change_lock = asyncio.Lock()

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._frames = asyncio.Queue(maxsize=2)
        await self.smart_city.start(self._on_smart_city_event)
        self._consumer = asyncio.create_task(self._consume_frames())
        self._runner.start()
        self.events.add(
            EventLevel.INFO,
            f"Loading {self.scenario.name}: {sum(1 for i in self.network.intersections.values() if i.tls_id)} signalized intersections, "
            f"{self.settings.sim_warmup_s:.0f}s warm-up",
        )

    async def stop(self) -> None:
        await asyncio.to_thread(self._runner.shutdown)
        if self._consumer:
            self._consumer.cancel()
        await self.smart_city.stop()

    # ------------------------------------------------------------ read side

    @property
    def state(self) -> CityState:
        if self._state is None:
            raise NotReady("simulation is still starting")
        return self._state

    @property
    def status(self) -> StatusInfo:
        return StatusInfo(status=self._runner.status, speed=self._runner.speed, error=self._error)

    @property
    def sim_time(self) -> float:
        return self._state.sim_time if self._state else 0.0

    def status_json(self) -> str:
        return self.status.model_dump_json()

    def hello_message(self) -> str:
        events = ",".join(e.model_dump_json() for e in self.events.recent())
        state = self._state.model_dump_json() if self._state else "null"
        trend = ",".join(s.model_dump_json() for s in self._trend)
        scenario = self.latest_scenario.model_dump_json() if self.latest_scenario else "null"
        episode = self.latest_episode.model_dump_json() if self.latest_episode else "null"
        return envelope(
            "hello",
            f'{{"status":{self.status_json()},"state":{state},"events":[{events}],"history":[{trend}],'
            f'"scenario":{scenario},"episode":{episode}}}',
        )

    async def incidents(self, include_cleared: bool = False) -> list[Incident]:
        return await self.smart_city.list_incidents(include_cleared=include_cleared)

    async def signal_program(self, intersection_id: str) -> SignalProgram:
        if intersection_id not in self.network.intersections:
            raise KeyError(intersection_id)
        return await self._runner.call(lambda sim: sim.get_signal_program(intersection_id))

    async def run_on_live(self, fn: Callable[[TrafficSimulation], T]) -> T:
        """Run ``fn(sim)`` on the live simulation thread, between steps."""
        return await self._runner.call(fn)

    def publish_scenario(self, run: ScenarioRun) -> None:
        self.latest_scenario = run
        self.hub.broadcast(envelope("scenario", run.model_dump_json()))

    def publish_episode(self, episode: Episode) -> None:
        # an older episode still reviewing must not replace a newer one in the hello message
        if self.latest_episode is None or episode.created_at >= self.latest_episode.created_at:
            self.latest_episode = episode
        self.hub.broadcast(envelope("episode", episode.model_dump_json()))

    def add_frame_observer(self, observer: FrameObserver) -> None:
        self._observers.append(observer)

    def set_scripted_events(self, events: list[tuple[float, Callable[[TrafficSimulation], object]]]) -> None:
        """Commands the live runner fires at their simulation time on every boot, inside the warm-up (a scripted
        crash that has already happened) or while running. A new script takes effect at the next reset."""
        self._runner.set_scripted_events(events)

    def crash_command(self, crash: DemoCrash) -> Callable[[TrafficSimulation], Disruption]:
        """A scripted crash as a command for the live simulation thread."""
        segment_id, lanes, position, severity = self._collision_args(
            crash.segment_id, crash.lanes, crash.position_fraction, crash.severity
        )
        return lambda sim: sim.inject_collision(segment_id, lanes, position, severity)

    # ------------------------------------------------------------- commands

    async def set_running(self, running: bool) -> None:
        await self._runner.set_running(running)
        self.events.add(EventLevel.INFO, "Simulation resumed" if running else "Simulation paused", self._sim_time())

    async def set_speed(self, multiplier: float) -> None:
        await self._runner.set_speed(multiplier)

    async def hold_speed(self, multiplier: float) -> None:
        """Slow the live city while something heavy runs beside it (branch simulations).

        Holds do not stack: the first one owns the restore, so overlapping callers cannot lose the
        operator's original speed. A no-op once a hold is in force.
        """
        if self._speed_hold is not None:
            return
        previous = self._runner.speed
        if previous == multiplier:
            return
        self._speed_hold = (previous, multiplier)
        await self._runner.set_speed(multiplier)
        self.events.add(EventLevel.INFO, f"Live simulation slowed to {multiplier:g}x while branches run", self._sim_time())

    async def release_speed(self) -> None:
        """Put the speed back if the hold is still in force. The operator always wins.

        A speed the operator chose while the hold was on is left exactly as they set it: only the value
        this service imposed is replaced, and the check runs on the simulation thread.
        """
        hold, self._speed_hold = self._speed_hold, None
        if hold is None:
            return
        previous, imposed = hold
        if await self._runner.restore_speed(imposed, previous):
            self.events.add(EventLevel.INFO, f"Live simulation back to {previous:g}x", self._sim_time())

    async def reset(self) -> None:
        async with self.live_change_lock:  # wait for an apply in flight, and keep the next one out until we reboot
            self.events.add(EventLevel.INFO, "Resetting simulation to a clean network", self._sim_time())
            self._ems_status.clear()
            for listener in self.reset_listeners:
                try:
                    await listener()
                except Exception:  # noqa: BLE001 - a broken listener must not block the reset
                    log.exception("reset listener failed")
            await self._runner.reset()

    def _collision_args(
        self, segment_id: str | None, lanes: list[int] | None, position_fraction: float | None, severity: Severity | None
    ) -> tuple[str, list[int], float, Severity]:
        """Fill a collision's unset fields from the scenario defaults (operator injection and scripted crashes)."""
        defaults = self.scenario.default_collision
        segment_id = segment_id or defaults.edge
        segment = self.network.segments.get(segment_id)
        if segment is None:
            raise KeyError(segment_id)
        return (
            segment_id,
            lanes if lanes is not None else defaults.lanes,
            (position_fraction or defaults.position_fraction) * segment.length,
            severity or defaults.severity,
        )

    async def inject_incident(self, request: InjectIncidentRequest) -> Disruption:
        if request.type is not IncidentType.COLLISION:
            raise ValueError(f"injecting '{request.type}' is not supported yet; use 'collision'")
        segment_id, lanes, position, severity = self._collision_args(
            request.segment_id, request.lanes, request.position_fraction, request.severity
        )
        segment = self.network.segments[segment_id]
        disruption = await self._runner.call(lambda sim: sim.inject_collision(segment_id, lanes, position, severity))
        self.events.add(
            EventLevel.WARNING,
            f"Collision staged in simulation on {segment.name} {segment.direction} ({severity.value}); "
            "awaiting detection",
            disruption.started_at,
        )
        return disruption

    async def clear_incident(self, incident_id: str) -> list[str]:
        incident = await self.smart_city.get_incident(incident_id)
        if incident is None:
            raise KeyError(incident_id)
        segment_id = incident.location.segment_id

        def clear(sim: TrafficSimulation) -> list[str]:
            ids = [d.id for d in sim.list_disruptions() if d.segment_id == segment_id]
            for disruption_id in ids:
                sim.clear_disruption(disruption_id)
            return ids

        cleared = await self._runner.call(clear)
        self.events.add(EventLevel.INFO, f"{incident_id}: scene cleared, lanes reopened", self._sim_time(), incident_id)
        return cleared

    async def dispatch_emergency(self, origin_segment: str | None, destination_segment: str | None) -> EmergencyDispatch:
        station = self.scenario.ems_stations[0] if self.scenario.ems_stations else None
        origin = origin_segment or (station.edge if station else None)
        if origin is None:
            raise ValueError("no EMS station configured; pass origin_segment")
        position: float | None = None
        lane = 0
        incident_id = None
        if destination_segment is None:
            active = await self.smart_city.list_incidents()
            if not active:
                raise Conflict("no active incident to respond to")
            incident = max(active, key=lambda i: i.timestamp)
            incident_id = incident.id
            probe = probe_for_incident(incident, origin)
            destination_segment, position, lane = probe.destination_segment, probe.position_m, probe.lane
        if destination_segment not in self.network.segments:
            raise KeyError(destination_segment)
        dispatch = await self._runner.call(
            lambda sim: sim.spawn_emergency_vehicle(origin, destination_segment, position, lane)
        )
        target = self.network.segments[destination_segment]
        self.events.add(
            EventLevel.INFO,
            f"{dispatch.id} dispatched from {station.name if station else origin} to {target.name} {target.direction}",
            dispatch.dispatched_at,
            incident_id,
        )
        return dispatch

    # ------------------------------------------------------ frame pipeline

    def _on_frame_threadsafe(self, frame: LiveFrame) -> None:
        loop = self._loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(self._offer_frame, frame)

    def _offer_frame(self, frame: LiveFrame) -> None:
        if self._frames.full():
            self._frames.get_nowait()  # keep only the freshest frames
        self._frames.put_nowait(frame)

    async def _consume_frames(self) -> None:
        while True:
            frame = await self._frames.get()
            try:
                await self._handle_frame(frame)
            except Exception:  # noqa: BLE001 - keep serving frames
                log.exception("failed to handle simulation frame")

    async def _handle_frame(self, frame: LiveFrame) -> None:
        if frame.status is not self._status or frame.error != self._error:
            self._status, self._error = frame.status, frame.error
            if frame.status is RunStatus.ERROR:
                self.events.add(EventLevel.ALERT, f"Simulation error: {frame.error}")
            self.hub.broadcast(envelope("status", self.status_json()))
        if frame.state is None:
            return

        for observe in self._observers:
            await observe(frame.state)
        self._track_responders(frame.state)
        self._record_trend(frame.state)
        state = frame.state
        self._state = CityState(
            status=frame.status,
            speed=frame.speed,
            sim_time=state.sim_time,
            intersections=state.intersections,
            segments=state.segments,
            vehicles=state.vehicles,
            emergency_vehicles=state.emergency_vehicles,
            incidents=await self.smart_city.list_incidents(),
            metrics=state.metrics,
            providers=self.providers,
        )
        self.hub.broadcast(envelope("state", self._state.model_dump_json()))

    def _record_trend(self, state: NetworkState) -> None:
        if self._trend and state.sim_time < self._trend[-1].t:
            self._trend.clear()  # simulation was reset
        if self._trend and state.sim_time - self._trend[-1].t < TREND_SAMPLE_S:
            return
        m = state.metrics
        self._trend.append(
            MetricSample(
                t=state.sim_time,
                delay=m.mean_vehicle_delay,
                queue=m.max_queue_length,
                throughput=m.throughput,
                speed=m.mean_speed,
                vehicles=m.vehicles_in_network,
            )
        )

    def _track_responders(self, state: NetworkState) -> None:
        for ev in state.emergency_vehicles:
            previous = self._ems_status.get(ev.id)
            self._ems_status[ev.id] = ev.status
            if previous is EmergencyStatus.EN_ROUTE and ev.status is EmergencyStatus.ON_SCENE:
                minutes, seconds = divmod(int((ev.arrived_at or state.sim_time) - ev.dispatched_at), 60)
                self.events.add(
                    EventLevel.INFO, f"{ev.id} on scene (response time {minutes}:{seconds:02d})", state.sim_time
                )

    async def _on_smart_city_event(self, event: SmartCityEvent) -> None:
        incident = event.incident
        if event.kind is SmartCityEventKind.INCIDENT_DETECTED:
            cameras = f" via {', '.join(incident.sensor_ids)}" if incident.sensor_ids else ""
            self.events.add(
                EventLevel.ALERT,
                f"{incident.id} {incident.type.value.replace('_', ' ')} detected{cameras}: {incident.description}",
                incident.sim_time,
                incident.id,
            )
        elif event.kind is SmartCityEventKind.INCIDENT_CLEARED:
            self.events.add(EventLevel.INFO, f"{incident.id} closed", self._sim_time(), incident.id)
        for listener in self.incident_listeners:
            try:
                await listener(event)
            except Exception:  # noqa: BLE001 - keep the frame pipeline going
                log.exception("incident listener failed")

    def _broadcast_event(self, event: OpsEvent) -> None:
        self.hub.broadcast(envelope("event", event.model_dump_json()))

    def _sim_time(self) -> float | None:
        return self._state.sim_time if self._state else None
