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
from dataclasses import dataclass
from typing import TypeVar

from app.agent.base import AgentProvider
from app.config import Settings
from app.models.api import (
    CityState,
    EventLevel,
    InjectIncidentRequest,
    MetricSample,
    ModelCall,
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
    IncidentStatus,
    IncidentType,
    NetworkGeometry,
    NetworkState,
    Severity,
    SignalProgram,
    lane_name,
)
from app.models.episode import Episode
from app.models.scenario import ScenarioRun
from app.services.events import EventLog
from app.services.model_log import ModelCallLog
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

MIRROR_MOVE_TOLERANCE_M = 5.0  # a mirrored crash is re-placed only when the report moves further than this
TREND_SAMPLE_S = 5.0  # simulated seconds between trend samples
TREND_SAMPLES = 180  # 15 simulated minutes
MAX_RESPONDERS_EN_ROUTE = 4  # more than any scenario needs: a runaway client cannot fill the city with ambulances


@dataclass(frozen=True)
class _SpeedHold:
    """A live-speed hold: who owns it, the operator's speed to give back, and the speed imposed meanwhile.

    Frozen so that release can tell "the hold I read" from "a hold someone since replaced" by identity.
    """

    owner: str
    previous: float
    imposed: float


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
        model_calls: ModelCallLog,
    ):
        self.settings = settings
        self.scenario = scenario
        self.network = network
        self.smart_city = smart_city
        self.agent = agent
        self.hub = hub
        self.events = EventLog(on_event=self._broadcast_event)
        # Built before this service (the model clients are constructed first), so it is given its
        # broadcaster and the simulation clock here.
        self.model_calls = model_calls
        self.model_calls.attach(self._broadcast_model_call, self._sim_time)
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
        self._speed_hold: _SpeedHold | None = None
        # Serializes hold_speed and release_speed. Releases run as spawned tasks, so without it a finishing
        # analysis's release could interleave with the next analysis's hold and leave the city un-held.
        self._speed_lock = asyncio.Lock()
        self._trend: deque[MetricSample] = deque(maxlen=TREND_SAMPLES)
        self.latest_scenario: ScenarioRun | None = None
        self.latest_episode: Episode | None = None
        self._mirrored_disruptions: dict[str, str] = {}
        self._unmirrorable: set[str] = set()  # reports already warned about, so the warning is logged once
        # Hooks for the learning services: called after an incident is logged, and before the simulation reboots.
        self.incident_listeners: list[IncidentListener] = []
        self.reset_listeners: list[ResetListener] = []
        # Held for a whole reset (listeners and reboot), and by the implementor for a whole apply, so a plan is
        # never installed on a simulation that is about to be, or has just been, replaced.
        self.live_change_lock = asyncio.Lock()
        # Callables that answer "must the live city be left alone right now?" (an open analysis, a working
        # episode). The periodic refresh of an old twin waits while any of them says yes.
        self.refresh_blockers: list[Callable[[], bool]] = []
        self._epoch_start: float | None = None  # sim time of the first frame since the simulation last (re)started
        self._last_frame_time = 0.0
        self._refresh_task: asyncio.Task | None = None

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
        if self._refresh_task is not None:
            self._refresh_task.cancel()
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

    @property
    def live_sim_time(self) -> float:
        """The simulation clock right now. ``sim_time`` is the last published frame, which still belongs to the
        old run for a moment after a reset."""
        return self._runner.sim_time

    def status_json(self) -> str:
        return self.status.model_dump_json()

    def hello_message(self) -> str:
        events = ",".join(e.model_dump_json() for e in self.events.recent())
        model_calls = ",".join(c.model_dump_json() for c in self.model_calls.recent())
        state = self._state.model_dump_json() if self._state else "null"
        network = self.geometry.model_dump_json()
        trend = ",".join(s.model_dump_json() for s in self._trend)
        scenario = self.latest_scenario.model_dump_json() if self.latest_scenario else "null"
        episode = self.latest_episode.model_dump_json() if self.latest_episode else "null"
        return envelope(
            "hello",
            f'{{"status":{self.status_json()},"network":{network},"state":{state},"events":[{events}],"history":[{trend}],'
            f'"model_calls":[{model_calls}],"scenario":{scenario},"episode":{episode}}}',
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
        # the operator's choice ends any hold, even a re-selection of the value the hold imposed, which a
        # comparison on the simulation thread (restore_speed) cannot tell from the hold itself
        self._speed_hold = None
        await self._runner.set_speed(multiplier)

    async def hold_speed(self, multiplier: float, owner: str) -> None:
        """Slow the live city while something heavy runs beside it (branch simulations).

        ``owner`` (an analysis run id) is who may release the hold. Holds do not stack: a hold already in
        force passes to the new owner and keeps the operator's original speed to give back, so a later
        analysis is never left un-held by an earlier one's release. Serialized with ``release_speed``.
        """
        async with self._speed_lock:
            held = self._speed_hold
            if held is None:
                previous = self._runner.speed
                if previous == multiplier:
                    return
                self._speed_hold = _SpeedHold(owner, previous, multiplier)
            else:
                # recorded before the await, so an operator set_speed that lands meanwhile still clears it
                self._speed_hold = _SpeedHold(owner, held.previous, multiplier)
                if held.imposed == multiplier:
                    return
            await self._runner.set_speed(multiplier)
            self.events.add(
                EventLevel.INFO, f"Live simulation slowed to {multiplier:g}x while branches run", self._sim_time()
            )

    async def release_speed(self, owner: str) -> None:
        """Put the speed back if ``owner`` still holds it. The operator always wins.

        A no-op for any other owner, so a stale or repeated release from an earlier analysis cannot lift a
        later analysis's hold. A speed the operator chose while the hold was on is left exactly as they set
        it: ``set_speed`` ends the hold, and as a second guard only the value this service imposed is ever
        replaced (the check runs on the simulation thread). Serialized with ``hold_speed``.
        """
        async with self._speed_lock:
            hold = self._speed_hold
            if hold is None or hold.owner != owner:
                return
            restored = await self._runner.restore_speed(hold.imposed, hold.previous)
            # cleared only now, after the restore is in effect; set_speed may have cleared it while we awaited
            if self._speed_hold is hold:
                self._speed_hold = None
            if restored:
                self.events.add(EventLevel.INFO, f"Live simulation back to {hold.previous:g}x", self._sim_time())

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
            if not self.smart_city.simulation_is_source:
                await self._reconcile_external_incidents(after_reset=True)

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
        blocking = set(range(segment.lanes)) if severity is Severity.CRITICAL else set(lanes)

        def stage(sim: TrafficSimulation) -> Disruption:
            # a second click, or a repeated API call, would stack phantom vehicles on a crash that is already there
            for existing in sim.list_disruptions():
                if existing.segment_id == segment_id and blocking & set(existing.lanes):
                    held = ", ".join(lane_name(lane, segment.lanes) for lane in sorted(existing.lanes))
                    raise Conflict(
                        f"a collision already blocks the {held} of {segment.name} {segment.direction}; "
                        "clear that scene before staging another there"
                    )
            return sim.inject_collision(segment_id, lanes, position, severity)

        disruption = await self._runner.call(stage)
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
        owned = self._disruptions_of(incident)

        def clear(sim: TrafficSimulation) -> list[str]:
            if owned is None:  # the provider cannot say which crash is this incident's: everything on its road
                ids = [d.id for d in sim.list_disruptions() if d.segment_id == segment_id]
            else:  # only this incident's crash: another one on the same road is a separate incident
                ids = [d.id for d in sim.list_disruptions() if d.id in owned]
            for disruption_id in ids:
                sim.clear_disruption(disruption_id)
            return ids

        cleared = await self._runner.call(clear)
        if cleared:  # clearing a scene that is already clear must not log it a second time
            self.events.add(
                EventLevel.INFO, f"{incident_id}: scene cleared, lanes reopened", self._sim_time(), incident_id
            )
        return cleared

    def _disruptions_of(self, incident: Incident) -> set[str] | None:
        """The live crashes that make up this incident, or None when that cannot be told."""
        if self.smart_city.simulation_is_source:
            owned = self.smart_city.disruptions_of(incident.id)
        else:  # an external report is mirrored into the twin as one crash, remembered here
            mirrored = self._mirrored_disruptions.get(incident.id)
            owned = None if mirrored is None else [mirrored]
        return None if owned is None else set(owned)

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
        responders = [
            ev
            for ev in (self._state.emergency_vehicles if self._state else [])
            if ev.status is EmergencyStatus.EN_ROUTE
        ]
        already = next((ev for ev in responders if ev.destination_segment == destination_segment), None)
        if already is not None:
            raise Conflict(f"{already.id} is already en route to that scene")
        if len(responders) >= MAX_RESPONDERS_EN_ROUTE:
            raise Conflict(f"{len(responders)} responders are already en route")
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
        self._note_epoch(state.sim_time)
        self._maybe_refresh(state.sim_time)

    def _note_epoch(self, sim_time: float) -> None:
        """Remember when this run of the simulation began: the first frame, or the first after a time reversal."""
        if self._epoch_start is None or sim_time < self._last_frame_time:
            self._epoch_start = sim_time
        self._last_frame_time = sim_time

    def _maybe_refresh(self, sim_time: float) -> None:
        limit = self.settings.twin_refresh_s
        if limit <= 0 or self._epoch_start is None or self._refresh_task is not None:
            return
        age = sim_time - self._epoch_start
        if age >= limit and self._can_refresh():
            self._refresh_task = asyncio.create_task(self._refresh(age))

    def _can_refresh(self) -> bool:
        """The live city may be reset unasked only while nothing depends on it."""
        if self._status is not RunStatus.RUNNING or self.live_change_lock.locked():
            return False
        if self._state is not None and self._state.incidents:
            return False
        return not any(blocked() for blocked in self.refresh_blockers)

    async def _refresh(self, age_s: float) -> None:
        """Reset an old, idle twin to a clean network (see ``Settings.twin_refresh_s``)."""
        try:
            self.events.add(
                EventLevel.INFO,
                f"Refreshing the twin: {age_s / 3600:.1f} simulated hours since its last reset and nothing depends on it",
                self._sim_time(),
            )
            await self.reset()
        except Exception:  # noqa: BLE001 - the city keeps running; the next frame decides again
            log.exception("twin refresh failed")
        finally:
            # frames queued before the reboot still carry the old time: forget the epoch so they cannot start another
            self._epoch_start = None
            self._refresh_task = None

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
        elif event.kind is SmartCityEventKind.INCIDENT_UPDATED:
            self.events.add(EventLevel.INFO, f"{incident.id} report updated", incident.sim_time, incident.id)
        elif event.kind is SmartCityEventKind.INCIDENT_CLEARED:
            self.events.add(EventLevel.INFO, f"{incident.id} closed", self._sim_time(), incident.id)
        if not self.smart_city.simulation_is_source:
            self._warn_unmirrorable(incident)
            async with self.live_change_lock:
                await self._reconcile_external_incidents()
            refreshed = await self.smart_city.get_incident(incident.id)
            if refreshed is not None:
                event.incident = incident = refreshed
        for listener in self.incident_listeners:
            try:
                await listener(event)
            except Exception:  # noqa: BLE001 - keep the frame pipeline going
                log.exception("incident listener failed")

    def _warn_unmirrorable(self, incident: Incident) -> None:
        """Warn once when a report cannot be staged; VSS re-reports the same incident on every poll."""
        match = incident.location.match
        reason: str | None = None
        if incident.status is IncidentStatus.ACTIVE:
            if incident.type is not IncidentType.COLLISION:
                reason = f"{incident.type.value.replace('_', ' ')} reported; only collisions can be staged"
            elif not match or not incident.location.segment_id:
                reason = match.reason if match and match.reason else "no road segment matched"
        if reason is None:
            self._unmirrorable.discard(incident.id)
            return
        if incident.id in self._unmirrorable:
            return
        self._unmirrorable.add(incident.id)
        self.events.add(EventLevel.WARNING, f"{incident.id} not mirrored: {reason}", self._sim_time(), incident.id)

    async def _reconcile_external_incidents(self, after_reset: bool = False) -> None:
        """Make externally reported, matched collisions and live disruptions agree exactly."""
        active = {incident.id: incident for incident in await self.smart_city.list_incidents()}

        def reconcile(sim: TrafficSimulation) -> list[tuple[str, str, str]]:
            changes: list[tuple[str, str, str]] = []
            disruptions = {item.id: item for item in sim.list_disruptions()}
            for incident_id, disruption_id in list(self._mirrored_disruptions.items()):
                incident = active.get(incident_id)
                valid = (
                    incident is not None
                    and incident.type is IncidentType.COLLISION
                    and incident.location.segment_id is not None
                )
                disruption = disruptions.get(disruption_id)
                if not valid:
                    if disruption is not None:
                        sim.clear_disruption(disruption_id)
                        changes.append((incident_id, disruption_id, "cleared"))
                    self._mirrored_disruptions.pop(incident_id, None)
                    continue
                if disruption is None:
                    self._mirrored_disruptions.pop(incident_id, None)
                    continue
                lanes = incident.affected_lanes or [0]
                position = incident.location.position_m
                # Re-matching every poll jitters the position slightly, so only a real move re-places the
                # crash; re-injecting on jitter would restage the scene and reset its queue each time.
                moved = position is None or abs(disruption.position_m - position) > MIRROR_MOVE_TOLERANCE_M
                if disruption.segment_id != incident.location.segment_id or disruption.lanes != lanes or moved:
                    sim.clear_disruption(disruption_id)
                    self._mirrored_disruptions.pop(incident_id, None)

            disruptions = {item.id: item for item in sim.list_disruptions()}
            for incident_id, incident in active.items():
                location = incident.location
                if (
                    incident.type is not IncidentType.COLLISION
                    or location.segment_id is None
                    or location.position_m is None
                    or location.segment_id not in self.network.segments
                ):
                    continue
                disruption_id = self._mirrored_disruptions.get(incident_id)
                if disruption_id and disruption_id in disruptions:
                    continue
                disruption = sim.inject_collision(
                    location.segment_id,
                    incident.affected_lanes or [0],
                    location.position_m,
                    incident.severity,
                )
                self._mirrored_disruptions[incident_id] = disruption.id
                changes.append((incident_id, disruption.id, "mirrored"))
            return changes

        changes = await self.run_on_live(reconcile)
        for incident_id, disruption_id, action in changes:
            self.smart_city.set_mirrored(incident_id, action == "mirrored")
            if action != "mirrored":
                continue
            incident = active[incident_id]
            segment = self.network.segments[incident.location.segment_id or ""]
            match = incident.location.match
            method = match.method if match and match.method else "unknown"
            distance = f", {match.distance_m:.0f} m" if match and match.distance_m is not None else ""
            lane = "; lane assumed" if not match or match.lane_assumed else ""
            reset_note = " after reset warm-up" if after_reset else ""
            self.events.add(
                EventLevel.WARNING,
                f"{incident_id} mirrored in the twin as {disruption_id} on {segment.name} {segment.direction} "
                f"(matched by {method}{distance}{lane}){reset_note}",
                self._sim_time(),
                incident_id,
            )
        for incident_id in active:
            if incident_id not in self._mirrored_disruptions:
                self.smart_city.set_mirrored(incident_id, False)

    def _broadcast_event(self, event: OpsEvent) -> None:
        self.hub.broadcast(envelope("event", event.model_dump_json()))

    def _broadcast_model_call(self, call: ModelCall) -> None:
        self.hub.broadcast(envelope("model_call", call.model_dump_json()))

    def _sim_time(self) -> float | None:
        return self._state.sim_time if self._state else None
