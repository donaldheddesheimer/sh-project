"""Eclipse SUMO implementation of TrafficSimulation, via TraCI.

One instance owns one SUMO process and one TraCI connection. Instances are
independent, so the live simulation and candidate branches (restored from a
snapshot) can run side by side. An instance is not thread-safe: drive it from
a single thread.
"""

from __future__ import annotations

import itertools
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import sumolib
import traci
from traci import constants as tc

from app.models.domain import (
    CongestionLevel,
    Disruption,
    EmergencyCorridor,
    EmergencyDispatch,
    EmergencyResponse,
    EmergencyStatus,
    EmergencyVehicleState,
    IncidentType,
    IntersectionState,
    NetworkState,
    ProgramLogic,
    RerouteAction,
    RoadSegmentState,
    Severity,
    SignalColor,
    SignalPolicy,
    SignalProgram,
    SimulationSnapshot,
    TrafficMetrics,
    VehicleKind,
    VehicleState,
)
from app.simulation.interface import TrafficSimulation
from app.simulation.metrics import MetricsCollector, StepObservation
from app.simulation.network import PhaseKind, RoadNetwork, phase_kind
from app.simulation.preemption import (
    PreemptionController,
    ResponderApproach,
    SetPhase,
    TlsObservation,
    UnsafeTransition,
    check_transition,
)
from app.simulation.reroute import DiversionAdvisory
from app.simulation.scenario import Scenario

log = logging.getLogger(__name__)

# VAR_LANE_ID and VAR_LANEPOSITION ride along so the per-step bookkeeping below (rubbernecking, the
# responder walk, the wait diagnostics) needs no per-vehicle TraCI round trip. A subscription result is
# the state after the last step, which is exactly what a getter called between steps returns.
VEHICLE_VARS = (
    tc.VAR_POSITION,
    tc.VAR_ANGLE,
    tc.VAR_SPEED,
    tc.VAR_TYPE,
    tc.VAR_TIMELOSS,
    tc.VAR_LANE_ID,
    tc.VAR_LANEPOSITION,
)
EDGE_VARS = (
    tc.LAST_STEP_MEAN_SPEED,
    tc.LAST_STEP_VEHICLE_NUMBER,
    tc.LAST_STEP_VEHICLE_HALTING_NUMBER,
    tc.LAST_STEP_OCCUPANCY,
)
TLS_VARS = (tc.TL_CURRENT_PHASE, tc.TL_RED_YELLOW_GREEN_STATE, tc.TL_NEXT_SWITCH, tc.TL_CURRENT_PROGRAM)
SIM_VARS = (tc.VAR_TIME, tc.VAR_DEPARTED_VEHICLES_IDS, tc.VAR_ARRIVED_VEHICLES_IDS, tc.VAR_PENDING_VEHICLES)

CRASH_TYPE = "crash"
EMS_TYPE = "ems"

# Speed of traffic squeezing past a crash on the lanes that stay open (modelling
# assumption). "major" is an active scene with responders waving traffic past at
# walking pace: the open lane passes roughly a third of the busiest link's normal
# volume, so queues build steadily. "critical" closes every lane.
PASS_SPEED = {Severity.MINOR: 2.5, Severity.MAJOR: 0.6, Severity.CRITICAL: None}
RUBBERNECK_UPSTREAM_M = 40.0
RUBBERNECK_DOWNSTREAM_M = 8.0
CRASH_HOLD_S = 1e6  # crashed vehicles stay until the disruption is cleared
EMS_ON_SCENE_S = 900.0
COLLISION_VEHICLE_GAP_M = 6.5

# Congestion is averaged over one signal cycle: queues that form on red and clear
# on green cancel out, leaving only persistent queuing (e.g. behind a crash).
CONGESTION_WINDOW_S = 90.0
CRITICAL_DENSITY = 0.04  # veh per lane-metre at which a slow segment counts as fully congested
SPEED_TAU_S = 20.0  # smoothing of observed segment speeds used for responder ETAs
RESPONDER_SPEED_TAU_S = 15.0  # smoothing of a responder's own speed (detects it being stuck in a queue)
# Below walking pace a responder is held up rather than slowing for a turn: the threshold that makes
# "stopped" in the diagnostics mean what an operator watching the map would call stopped.
RESPONDER_STALL_SPEED_MS = 1.0
# A responder braking for its scene, or standing a few seconds at a red, is not "held up": shorter stalls are
# still recorded but get no note line, so a line means something.
RESPONDER_NOTE_MIN_STALL_S = 3.0
EXPECTED_SIGNAL_WAIT_S = 10.0  # mean wait at a fixed-time signal: P(red) ~0.5 x half of a ~40 s red

# traci.start's own connect loop hard-codes a 1 s wait between attempts (traci/main.py: start -> init ->
# connect(..., waitBetweenRetries=1)), and the first attempt is always made before SUMO is listening, so
# every process paid about a second. SUMO is started here instead and the port is polled.
CONNECT_POLL_S = 0.02
CONNECT_TIMEOUT_S = 60.0  # generous: a large net (Oakland) is loaded before the remote port opens

_start_lock = threading.Lock()  # traci registers a labelled connection in module-level state
# Ports handed to a SUMO that has not bound them yet. getFreeSocketPort() only says a port is free *now*, and
# SUMO binds it after loading its network, so two branches starting together could be given the same one.
# (traci.start used to be called with the lock held until SUMO was listening, which hid this.)
_claimed_ports: set[int] = set()
REAP_TIMEOUT_S = 5.0  # how long a killed SUMO gets to be collected; a kill is prompt, so this only bounds a stuck OS


def _kill_and_reap(process: subprocess.Popen) -> None:
    """Kill a SUMO that start() gave up on and collect it; never raises.

    kill() alone leaves the child unreaped (a zombie on POSIX, an open handle on Windows) until the Popen is
    garbage collected. Waiting also covers a process that already exited, where it returns at once. Cleanup must
    not mask the failure that got us here, so a stuck wait is logged and dropped.
    """
    try:
        process.kill()
    except OSError:
        pass  # already gone
    try:
        process.wait(timeout=REAP_TIMEOUT_S)
    except (subprocess.TimeoutExpired, OSError):
        log.warning("SUMO process %s was not reaped within %.0fs of being killed", process.pid, REAP_TIMEOUT_S)


def _discard_connection(connection: traci.connection.Connection | None) -> None:
    """Unregister and close a TraCI connection whose handshake failed; best effort, never raises.

    traci.connect() writes a labelled connection into traci's pool as soon as the socket connects, before
    getVersion() proves SUMO answers. If that then fails, nothing owns the connection: the label stays taken
    (every retry would fail with "Connection ... already active") and the socket stays open. Connection.close()
    is no use, it sends CMD_CLOSE over the very socket that failed and raises before it unregisters anything.
    traci has no public unregister, so this is the one place that touches its private ``_socket`` and
    ``_connections``. Only ``connection`` itself is removed, never another live connection that has this label.
    """
    if connection is None:
        return
    try:
        sock = connection._socket
        connection._socket = None  # a later stray command then fails as "Connection already closed"
        if sock is not None:
            sock.close()
    except Exception:  # noqa: BLE001 - cleanup must not mask the original failure
        pass
    try:
        label = connection.getLabel()
        if label is not None and traci.connection.has(label) and traci.connection.get(label) is connection:
            del traci.connection._connections[label]
    except Exception:  # noqa: BLE001
        pass


def resolve_sumo_binary(explicit: str | None = None, gui: bool = False) -> str:
    if explicit:
        return explicit
    name = "sumo-gui" if gui else "sumo"
    candidates = []
    try:
        import sumo  # type: ignore  # eclipse-sumo wheel

        candidates.append(Path(sumo.SUMO_HOME) / "bin" / name)
    except ImportError:
        pass
    if os.environ.get("SUMO_HOME"):
        candidates.append(Path(os.environ["SUMO_HOME"]) / "bin" / name)
    for candidate in candidates:
        for path in (candidate, candidate.with_name(candidate.name + ".exe")):  # ".exe" on Windows
            if path.exists():
                return str(path)
    found = shutil.which(name)
    if found:
        return found
    raise RuntimeError(f"Cannot find the SUMO binary '{name}'. Run `pip install eclipse-sumo` or set SUMO_BINARY.")


def vehicle_kind(type_id: str) -> VehicleKind:
    if type_id == EMS_TYPE:
        return VehicleKind.EMERGENCY
    if type_id == CRASH_TYPE:
        return VehicleKind.DISABLED
    if "truck" in type_id:
        return VehicleKind.TRUCK
    if "bus" in type_id:
        return VehicleKind.BUS
    return VehicleKind.CAR


@dataclass
class _ResponderWait:
    """Why one responder was slow: how long it sat still, where, and what was in front of it."""

    stalled_s: float = 0.0
    by_segment: dict[str, float] = field(default_factory=dict)
    max_ahead: int = 0


class SumoSimulation(TrafficSimulation):
    def __init__(
        self,
        scenario: Scenario,
        network: RoadNetwork,
        *,
        label: str,
        snapshot_dir: Path,
        sumo_binary: str | None = None,
        gui: bool = False,
        fail_safe_preemption: bool = False,
    ):
        self.scenario = scenario
        self.network = network
        self.label = label
        self._snapshot_dir = snapshot_dir
        self._binary = resolve_sumo_binary(sumo_binary, gui)
        self._gui = gui
        self._conn: traci.connection.Connection | None = None
        self._step_length = 0.5
        self._time = 0.0
        self._collector = MetricsCollector()
        self._veh: dict[str, dict] = {}
        self._edges: dict[str, dict] = {}
        self._tls: dict[str, dict] = {}
        self._pending: tuple[str, ...] = ()
        self._congestion: dict[str, float] = {}
        self._congestion_window: dict[str, deque[float]] = {}
        self._speed_ema: dict[str, float] = {}
        self._disruptions: dict[str, Disruption] = {}
        self._dispatches: dict[str, EmergencyDispatch] = {}
        self._responder_speed: dict[str, float] = {}
        self._responder_waits: dict[str, _ResponderWait] = {}
        self._rubbernecking: set[str] = set()
        self._pending_offsets: dict[str, float] = {}
        self._preemption: PreemptionController | None = None
        # a corridor that was dropped (reverted, or refused as unsafe), kept only so response_notes() can still say
        # what it did in total and per responder; it is never stepped again
        self._dropped_preemption: PreemptionController | None = None
        self._preemption_failures: list[str] = []
        # The live twin degrades instead of stopping: an unsafe pre-emption drops the corridor and the city
        # keeps running. A branch must still fail loudly, because a candidate that needs an unsafe signal
        # change must never be measured as if it were safe, let alone recommended.
        self._fail_safe_preemption = fail_safe_preemption
        self._diversion: DiversionAdvisory | None = None
        self._base_program_ids: dict[str, str] = {}  # intersection -> program running before the first policy
        self._programs: dict[tuple[str, str], SignalProgram] = {}  # active program per (tls, program id)
        self._custom_programs: dict[tuple[str, str], list[tuple[float, str]]] = {}  # installed at runtime
        self._seq = itertools.count(1)

    # ------------------------------------------------------------ lifecycle

    @property
    def conn(self) -> traci.connection.Connection:
        if self._conn is None:
            raise RuntimeError("simulation not started")
        return self._conn

    def start(self) -> None:
        # RNG state + high precision make a snapshot reproduce the same future in any fresh process
        cmd = [self._binary, "-c", str(self.scenario.sumocfg), "--save-state.rng", "true", "--save-state.precision", "8"]
        if self._gui:
            cmd += ["--start", "--quit-on-end", "--delay", "0"]
        with _start_lock:
            port = sumolib.miscutils.getFreeSocketPort()
            while port in _claimed_ports:  # another start was given this port and SUMO has not bound it yet
                port = sumolib.miscutils.getFreeSocketPort()
            _claimed_ports.add(port)
        try:
            # Same steps traci.start() takes (Popen with --remote-port, then connect), minus its fixed 1 s wait
            # between connection attempts. stderr is inherited, as traci.start leaves it.
            process = subprocess.Popen(cmd + ["--remote-port", str(port)], stdout=subprocess.DEVNULL)
            try:
                self._conn = self._connect(port, process)
            except BaseException:
                # Without a connection nothing would ever close this SUMO. This also covers a SUMO that already
                # exited (_connect raised because of it): killing it is harmless and wait() returns at once.
                _kill_and_reap(process)
                raise
        finally:
            _claimed_ports.discard(port)  # connected, SUMO holds the port; or failed, and nobody will use it
        try:
            self._step_length = self.conn.simulation.getDeltaT()
            self._subscribe_all()
            self._read_state()
        except BaseException:
            # A caller that sees start() fail cannot rely on close(): the live runner keeps the half-started
            # simulation until the next Reset, and close() itself gives up before unregistering when the link
            # is what broke. So a failed start releases its own SUMO, the connection and the label.
            connection, self._conn = self._conn, None
            _discard_connection(connection)
            _kill_and_reap(process)
            raise

    def _connect(self, port: int, process: subprocess.Popen) -> traci.connection.Connection:
        """Poll ``port`` until SUMO is listening, then return the labelled connection.

        ``numRetries=0`` makes each ``traci.connect`` a single attempt, so the waiting happens here and can
        be short. The connection is registered under ``self.label`` exactly as ``traci.start`` registered it,
        and ``process`` is handed over so ``Connection.close()`` reaps it.
        """
        deadline = time.monotonic() + CONNECT_TIMEOUT_S
        while True:
            with _start_lock:  # a labelled connection is written into traci's module-level pool
                connection: traci.connection.Connection | None = None  # stays None if the socket was refused
                try:
                    connection = traci.connect(port, numRetries=0, proc=process, label=self.label)
                    connection.getVersion()  # the handshake traci.start does through init()
                    return connection
                except BaseException as exc:
                    # traci registered the label when the socket connected, so a failed handshake must give it
                    # back before the next attempt, or every retry fails as "already active" until the timeout.
                    _discard_connection(connection)
                    if not isinstance(exc, (traci.FatalTraCIError, traci.TraCIException)):
                        raise
                    last = exc
            if process.poll() is not None:
                raise RuntimeError(f"SUMO exited with code {process.returncode} before accepting TraCI: {last}")
            if time.monotonic() >= deadline:
                raise RuntimeError(f"SUMO did not accept TraCI on port {port} within {CONNECT_TIMEOUT_S:.0f}s: {last}")
            time.sleep(CONNECT_POLL_S)

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001 - SUMO may already be gone
                pass
            self._conn = None

    @property
    def sim_time(self) -> float:
        return self._time

    @property
    def step_length(self) -> float:
        return self._step_length

    def step(self) -> None:
        self.conn.simulationStep()
        self._after_step()

    def run_for(
        self,
        seconds: float,
        speed_multiplier: float | None = None,
        on_sample: Callable[[TrafficMetrics], None] | None = None,
        sample_every_s: float = 30.0,
    ) -> TrafficMetrics:
        start_time = self._time
        self._collector.begin_window(start_time, self._background_time_loss())
        steps = max(1, int(round(seconds / self._step_length)))
        sample_steps = max(1, int(round(sample_every_s / self._step_length)))
        wall_start = time.monotonic()
        for i in range(steps):
            self.step()
            if on_sample and (i + 1) % sample_steps == 0:
                on_sample(self.collect_metrics())
            if speed_multiplier:
                delay = wall_start + (i + 1) * self._step_length / speed_multiplier - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
        responses = self._emergency_responses(start_time)
        return self._collector.end_window(self._time, _aggregate_response(responses), responses)

    # --------------------------------------------------------- per-step work

    def _subscribe_all(self) -> None:
        """(Re)create all subscriptions; SUMO drops every subscription when a state is loaded."""
        c = self.conn
        c.simulation.subscribe(SIM_VARS)
        for edge_id in self.network.segments:
            c.edge.subscribe(edge_id, EDGE_VARS)
        for info in self.network.intersections.values():
            if info.tls_id:
                c.trafficlight.subscribe(info.tls_id, TLS_VARS)
        for vid in c.vehicle.getIDList():
            c.vehicle.subscribe(vid, VEHICLE_VARS)

    def _read_state(self) -> dict:
        c = self.conn
        sim = c.simulation.getSubscriptionResults()
        self._time = sim[tc.VAR_TIME]
        self._pending = tuple(sim.get(tc.VAR_PENDING_VEHICLES, ()))
        self._veh = c.vehicle.getAllSubscriptionResults()
        self._edges = c.edge.getAllSubscriptionResults()
        self._tls = c.trafficlight.getAllSubscriptionResults()
        return sim

    def _after_step(self) -> None:
        c = self.conn
        sim = self._read_state()
        departed = sim.get(tc.VAR_DEPARTED_VEHICLES_IDS, ())
        arrived = sim.get(tc.VAR_ARRIVED_VEHICLES_IDS, ())
        for vid in departed:
            c.vehicle.subscribe(vid, VEHICLE_VARS)
        if departed:
            self._veh = c.vehicle.getAllSubscriptionResults()
        if self._diversion is not None:
            self._diversion.on_departed(departed)

        self._update_congestion()
        self._apply_rubbernecking()
        self._update_dispatches(arrived)
        self._apply_pending_offsets()
        if self._preemption is not None:  # after offsets: the last setPhaseDuration on a signal wins
            self._preempt_signals()

        speeds = []
        time_loss = {}
        for vid, r in self._veh.items():
            if r[tc.VAR_TYPE] in (CRASH_TYPE, EMS_TYPE):
                continue
            time_loss[vid] = r[tc.VAR_TIMELOSS]
            speeds.append(r[tc.VAR_SPEED])
        self._collector.observe(
            StepObservation(
                sim_time=self._time,
                step_length=self._step_length,
                vehicle_time_loss=time_loss,
                vehicle_speeds=speeds,
                edge_halting={e: r[tc.LAST_STEP_VEHICLE_HALTING_NUMBER] for e, r in self._edges.items()},
                arrived=sum(1 for v in arrived if not v.startswith(("EMS-", "C-"))),
                pending=len(self._pending),
            )
        )

    def _background_time_loss(self) -> dict[str, float]:
        return {
            vid: r[tc.VAR_TIMELOSS] for vid, r in self._veh.items() if r[tc.VAR_TYPE] not in (CRASH_TYPE, EMS_TYPE)
        }

    def _update_congestion(self) -> None:
        alpha = 1.0 - math.exp(-self._step_length / SPEED_TAU_S)
        window = max(1, int(round(CONGESTION_WINDOW_S / self._step_length)))
        for edge_id, r in self._edges.items():
            seg = self.network.segments[edge_id]
            n = r[tc.LAST_STEP_VEHICLE_NUMBER]
            speed = r[tc.LAST_STEP_MEAN_SPEED] if n else seg.speed_limit
            density = n / (seg.length * seg.lanes)
            deficit = max(0.0, 1.0 - speed / seg.speed_limit)
            samples = self._congestion_window.setdefault(edge_id, deque(maxlen=window))
            samples.append(deficit * min(1.0, density / CRITICAL_DENSITY))
            self._congestion[edge_id] = sum(samples) / len(samples)
            prev_speed = self._speed_ema.get(edge_id, seg.speed_limit)
            self._speed_ema[edge_id] = prev_speed + alpha * (speed - prev_speed)

    def _apply_rubbernecking(self) -> None:
        """Slow traffic squeezing past a crash on the lanes that remain open.

        The vehicles and their positions come from the subscription, not from a lane query plus a
        getLanePosition per vehicle: the same set at the same positions (both are the state after the last
        step), without the ~20k round trips a post-crash branch used to spend here.
        """
        c = self.conn
        # lane id -> the disruption whose open lane it is. Built in disruption order, so where two crashes on
        # one segment leave the same lane open the later one's pass speed wins, as it did before.
        open_lanes: dict[str, Disruption] = {
            f"{d.segment_id}_{lane}": d
            for d in self._disruptions.values()
            if d.pass_speed is not None
            for lane in range(d.total_lanes)
            if lane not in d.lanes
        }
        targets: dict[str, float] = {}
        if open_lanes:
            for vid, r in self._veh.items():
                d = open_lanes.get(r[tc.VAR_LANE_ID])
                if d is None or r[tc.VAR_TYPE] == EMS_TYPE:
                    continue
                pos = r[tc.VAR_LANEPOSITION]
                if d.position_m - RUBBERNECK_UPSTREAM_M <= pos <= d.position_m + RUBBERNECK_DOWNSTREAM_M:
                    targets[vid] = d.pass_speed
        for vid, speed in targets.items():
            if vid not in self._rubbernecking:
                c.vehicle.setSpeed(vid, speed)
        for vid in self._rubbernecking - targets.keys():
            if vid in self._veh:
                c.vehicle.setSpeed(vid, -1)  # hand control back to the car-following model
        self._rubbernecking = set(targets)

    def _update_dispatches(self, arrived: tuple[str, ...]) -> None:
        alpha = 1.0 - math.exp(-self._step_length / RESPONDER_SPEED_TAU_S)
        lanes: dict[str, list[float]] | None = None  # built once per step, and only if someone is stalled
        for d in self._dispatches.values():
            if d.status is EmergencyStatus.COMPLETED:
                continue
            if (r := self._veh.get(d.id)) is not None:
                prev = self._responder_speed.get(d.id, r[tc.VAR_SPEED])
                self._responder_speed[d.id] = prev + alpha * (r[tc.VAR_SPEED] - prev)
            if d.id in arrived:
                d.status = EmergencyStatus.COMPLETED
                if d.arrived_at is None:
                    d.arrived_at = self._time
            elif d.status is EmergencyStatus.EN_ROUTE and r is not None:
                if self.conn.vehicle.isStopped(d.id):
                    d.status = EmergencyStatus.ON_SCENE
                    d.arrived_at = self._time
                elif r[tc.VAR_SPEED] < RESPONDER_STALL_SPEED_MS:
                    # only a unit still on its way is held up: the step it stops at its scene is arrival, not a wait
                    if lanes is None:
                        lanes = self._lane_positions()
                    self._record_stall(d.id, r, lanes)

    def _lane_positions(self) -> dict[str, list[float]]:
        """Lane id -> the lane position of every vehicle on it, from the subscription (no TraCI round trip)."""
        lanes: dict[str, list[float]] = {}
        for r in self._veh.values():
            lanes.setdefault(r[tc.VAR_LANE_ID], []).append(r[tc.VAR_LANEPOSITION])
        return lanes

    def _record_stall(self, responder_id: str, r: dict, lanes: dict[str, list[float]]) -> None:
        """Charge this step to a stalled responder: total, the segment it happened on, and the queue ahead."""
        wait = self._responder_waits.setdefault(responder_id, _ResponderWait())
        wait.stalled_s += self._step_length
        lane_id = r[tc.VAR_LANE_ID]
        segment = lane_id.rsplit("_", 1)[0]  # SUMO lane ids are "<edge>_<index>"
        if segment in self.network.segments:  # an internal lane means it is inside a junction, not held on a road
            wait.by_segment[segment] = wait.by_segment.get(segment, 0.0) + self._step_length
        pos = r[tc.VAR_LANEPOSITION]
        wait.max_ahead = max(wait.max_ahead, sum(1 for p in lanes.get(lane_id, ()) if p > pos))

    def _apply_pending_offsets(self) -> None:
        """Offsets are applied by stretching a green phase, never by cutting clearance intervals."""
        if not self._pending_offsets:
            return
        for tls_id, shift in list(self._pending_offsets.items()):
            r = self._tls.get(tls_id)
            if r and phase_kind(r[tc.TL_RED_YELLOW_GREEN_STATE]) is PhaseKind.GREEN:
                remaining = r[tc.TL_NEXT_SWITCH] - self._time
                self.conn.trafficlight.setPhaseDuration(tls_id, remaining + shift)
                del self._pending_offsets[tls_id]

    def _preempt_signals(self) -> None:
        try:
            # the controller checks every command before returning any, so nothing was issued if this raises
            commands = self._preemption.step(self._time, self._responder_approaches(), self._observe_tls)
        except UnsafeTransition as exc:
            if not self._fail_safe_preemption:
                raise  # a branch fails loudly: an unsafe candidate must not be measured as if it were safe
            log.error("pre-emption refused an unsafe transition; EMS corridor disabled", exc_info=exc)
            self._drop_preemption()
            # this exact prefix is what the episode's corridor response check looks for (docs/milestone-4). The
            # corridor's own note, kept by _drop_preemption, can still count an activation whose command was
            # refused, which is why the failure is stated on its own line rather than left to be inferred.
            self._preemption_failures.append(f"pre-emption disabled: an unsafe transition was refused ({exc})")
            return
        for command in commands:
            tls_id = self._tls_id(command.intersection_id)
            if isinstance(command, SetPhase):
                self.conn.trafficlight.setPhase(tls_id, command.phase_index)
            else:
                self.conn.trafficlight.setPhaseDuration(tls_id, command.seconds)

    def _responder_approaches(self) -> list[ResponderApproach]:
        """Signalized approaches still ahead of every en-route responder (a stopped one is on scene)."""
        v = self.conn.vehicle
        approaches = []
        for vid, r in self._veh.items():
            if r[tc.VAR_TYPE] != EMS_TYPE:
                continue
            try:
                if v.isStopped(vid):
                    continue
                route = v.getRoute(vid)
                index = v.getRouteIndex(vid)
            except traci.TraCIException:
                continue  # arrived after the subscriptions were read
            # an internal lane (":junction_n_m") means the responder is on the junction, as getRoadID said
            on_junction = r[tc.VAR_LANE_ID].startswith(":")
            lane_pos = r[tc.VAR_LANEPOSITION]
            if index < 0:
                continue
            for a in self.network.signalized_approaches_ahead(route, index, lane_pos, on_junction):
                approaches.append(ResponderApproach(vid, a.intersection_id, a.segment_id, a.distance_m))
        return approaches

    def _observe_tls(self, intersection_id: str) -> TlsObservation:
        tls_id = self._tls_id(intersection_id)
        r = self._tls[tls_id]
        key = (tls_id, r[tc.TL_CURRENT_PROGRAM])
        program = self._programs.get(key)
        if program is None:
            program = self._programs[key] = self.get_signal_program(intersection_id)
        return TlsObservation(
            state=r[tc.TL_RED_YELLOW_GREEN_STATE],
            phase_index=r[tc.TL_CURRENT_PHASE],
            remaining_s=max(0.0, r[tc.TL_NEXT_SWITCH] - self._time),
            program=program,
        )

    # ------------------------------------------------------------ observation

    def get_network_state(self) -> NetworkState:
        to_ll = self.network.projector.to_lonlat
        vehicles = []
        for vid, r in self._veh.items():
            lon, lat = to_ll(*r[tc.VAR_POSITION])
            vehicles.append(
                VehicleState(
                    id=vid,
                    lat=lat,
                    lon=lon,
                    angle=r[tc.VAR_ANGLE],
                    speed=r[tc.VAR_SPEED],
                    kind=vehicle_kind(r[tc.VAR_TYPE]),
                )
            )
        emergency = [self._emergency_state(d) for d in self._dispatches.values()]
        return NetworkState(
            sim_time=self._time,
            intersections=[self.get_intersection_state(i) for i in self.network.intersections],
            segments=[self.get_edge_state(e) for e in self.network.segments],
            vehicles=vehicles,
            emergency_vehicles=emergency,
            metrics=self.collect_metrics(),
            disruptions=self.list_disruptions(),
        )

    def collect_metrics(self) -> TrafficMetrics:
        etas = [
            eta
            for d in self._dispatches.values()
            if d.status is EmergencyStatus.EN_ROUTE and (eta := self._estimate_eta(d)) is not None
        ]
        return self._collector.live(self._time, min(etas) if etas else None)

    def get_edge_state(self, segment_id: str) -> RoadSegmentState:
        seg = self.network.segments[segment_id]
        r = self._edges.get(segment_id, {})
        n = r.get(tc.LAST_STEP_VEHICLE_NUMBER, 0)
        congestion = min(1.0, max(0.0, self._congestion.get(segment_id, 0.0)))
        return RoadSegmentState(
            id=seg.id,
            name=seg.name,
            source=seg.source,
            destination=seg.destination,
            direction=seg.direction,
            average_speed=r.get(tc.LAST_STEP_MEAN_SPEED, seg.speed_limit) if n else seg.speed_limit,
            speed_limit=seg.speed_limit,
            vehicle_count=n,
            halting_count=r.get(tc.LAST_STEP_VEHICLE_HALTING_NUMBER, 0),
            occupancy=min(1.0, max(0.0, r.get(tc.LAST_STEP_OCCUPANCY, 0.0) / 100.0)),  # SUMO can report -1e-20
            congestion=congestion,
            level=_congestion_level(congestion),
            blocked_lanes=sorted({lane for d in self._disruptions.values() if d.segment_id == segment_id for lane in d.lanes}),
        )

    def get_intersection_state(self, intersection_id: str) -> IntersectionState:
        info = self.network.intersections[intersection_id]
        tls = self._tls.get(info.tls_id) if info.tls_id else None
        signals: dict[str, SignalColor] = {}
        queues: dict[str, int] = {}
        vehicles = 0
        speed_sum = 0.0
        congestion = 0.0
        for segment_id, approach in info.approaches_by_segment.items():
            r = self._edges.get(segment_id, {})
            n = r.get(tc.LAST_STEP_VEHICLE_NUMBER, 0)
            queues[segment_id] = r.get(tc.LAST_STEP_VEHICLE_HALTING_NUMBER, 0)
            vehicles += n
            speed_sum += r.get(tc.LAST_STEP_MEAN_SPEED, 0.0) * n
            congestion = max(congestion, self._congestion.get(segment_id, 0.0))
            if tls:
                state = tls[tc.TL_RED_YELLOW_GREEN_STATE]
                chars = [state[i] for i in (approach.through_link_indices or approach.link_indices) if i < len(state)]
                if any(ch in "Gg" for ch in chars):
                    signals[segment_id] = SignalColor.GREEN
                elif any(ch in "yY" for ch in chars):
                    signals[segment_id] = SignalColor.YELLOW
                else:
                    signals[segment_id] = SignalColor.RED
        phase = phase_label = remaining = cycle = program_id = None
        if tls:
            phase = tls[tc.TL_CURRENT_PHASE]
            program_id = tls[tc.TL_CURRENT_PROGRAM]
            phase_label = self.network.describe_phase(intersection_id, phase, 0.0, tls[tc.TL_RED_YELLOW_GREEN_STATE]).label
            remaining = max(0.0, tls[tc.TL_NEXT_SWITCH] - self._time)
            cycle = self._cycle_length(info.tls_id, program_id)
        return IntersectionState(
            id=info.id,
            name=info.name,
            location=self.network.projector.to_point(info.x, info.y),
            signalized=info.tls_id is not None,
            current_phase=phase,
            phase_label=phase_label,
            phase_remaining=remaining,
            cycle_length=cycle,
            program_id=program_id,
            approach_signals=signals,
            queue_lengths=queues,
            average_speed=speed_sum / vehicles if vehicles else 0.0,
            vehicle_count=vehicles,
            congestion=min(1.0, congestion),
        )

    def get_signal_program(self, intersection_id: str) -> SignalProgram:
        tls_id = self._tls_id(intersection_id)
        current = self.conn.trafficlight.getProgram(tls_id)
        logic = next(lg for lg in self.conn.trafficlight.getAllProgramLogics(tls_id) if lg.programID == current)
        return SignalProgram(
            intersection_id=intersection_id,
            program_id=current,
            phases=[
                self.network.describe_phase(intersection_id, i, p.duration, p.state) for i, p in enumerate(logic.phases)
            ],
        )

    def _cycle_length(self, tls_id: str, program_id: str) -> float | None:
        base = self.network.base_program(tls_id)
        if base and base.program_id == program_id:
            return base.cycle_length
        return self.get_signal_program(tls_id).cycle_length

    # --------------------------------------------------------- signal control

    def _tls_id(self, intersection_id: str) -> str:
        info = self.network.intersections.get(intersection_id)
        if info is None or info.tls_id is None:
            raise ValueError(f"{intersection_id} is not a signalized intersection")
        return info.tls_id

    def set_signal_phase(self, intersection_id: str, phase_index: int) -> None:
        self.conn.trafficlight.setPhase(self._tls_id(intersection_id), phase_index)

    def set_phase_duration(self, intersection_id: str, seconds: float) -> None:
        self.conn.trafficlight.setPhaseDuration(self._tls_id(intersection_id), seconds)

    def apply_signal_policy(self, policy: SignalPolicy) -> str:
        tls_id = self._tls_id(policy.intersection_id)
        c = self.conn
        program = self.get_signal_program(policy.intersection_id)
        unknown = [i for i in policy.phase_durations if not 0 <= i < len(program.phases)]
        if unknown:
            raise ValueError(f"{policy.intersection_id} has no phase(s) {unknown}")

        program_id = program.program_id
        if policy.phase_durations:
            # the first policy's program is the one revert_response() goes back to; a second policy on the
            # same intersection replaces a program this simulation installed, not the intersection's base
            self._base_program_ids.setdefault(policy.intersection_id, program.program_id)
            phase_index = c.trafficlight.getPhase(tls_id)
            elapsed = program.phases[phase_index].duration - (c.trafficlight.getNextSwitch(tls_id) - self._time)
            phases = [
                traci.trafficlight.Phase(policy.phase_durations.get(p.index, p.duration), p.state) for p in program.phases
            ]
            program_id = f"policy-{next(self._seq)}"
            c.trafficlight.setProgramLogic(tls_id, traci.trafficlight.Logic(program_id, 0, phase_index, phases))
            self._custom_programs[(tls_id, program_id)] = [(p.duration, p.state) for p in phases]
            c.trafficlight.setPhase(tls_id, phase_index)
            # keep the running phase's progress; a shortened green ends promptly rather than restarting
            c.trafficlight.setPhaseDuration(tls_id, max(1.0, phases[phase_index].duration - elapsed))

        if policy.offset_s is not None:
            cycle = sum(policy.phase_durations.get(p.index, p.duration) for p in program.phases)
            phase_index = c.trafficlight.getPhase(tls_id)
            elapsed_in_phase = program.phases[phase_index].duration - (c.trafficlight.getNextSwitch(tls_id) - self._time)
            position = sum(p.duration for p in program.phases[:phase_index]) + elapsed_in_phase
            desired = (self._time - policy.offset_s) % cycle
            shift = (position - desired) % cycle
            if shift > 0.5:
                self._pending_offsets[tls_id] = shift
        return program_id

    # ------------------------------------------------ disruptions / responders

    def inject_collision(self, segment_id: str, lanes: list[int], position_m: float, severity: Severity) -> Disruption:
        seg = self.network.segments.get(segment_id)
        if seg is None:
            raise ValueError(f"unknown segment {segment_id}")
        if severity is Severity.CRITICAL:
            lanes = list(range(seg.lanes))
        lanes = sorted(set(lanes))
        if not lanes or any(not 0 <= lane < seg.lanes for lane in lanes):
            raise ValueError(f"{segment_id} has lanes 0..{seg.lanes - 1}")
        position_m = min(max(position_m, 20.0), seg.length - 20.0)

        c = self.conn
        disruption_id = f"C-{uuid4().hex[:6]}"
        route_id = f"route_{disruption_id}"
        c.route.add(route_id, [segment_id])
        vehicle_ids = []
        for n, lane in enumerate(lanes):
            offsets = (0.0, -COLLISION_VEHICLE_GAP_M) if n == 0 else (0.0,)  # two-car collision + debris
            for k, offset in enumerate(offsets):
                pos = position_m + offset
                vid = f"{disruption_id}-{lane}{k}"
                c.vehicle.add(vid, route_id, typeID=CRASH_TYPE, depart="now", departLane=str(lane),
                              departPos=f"{pos:.2f}", departSpeed="0")
                c.vehicle.setStop(vid, segment_id, pos=pos + 1.0, laneIndex=lane, duration=CRASH_HOLD_S,
                                  startPos=max(0.0, pos - 3.0))
                vehicle_ids.append(vid)

        x, y = self.network.point_along(segment_id, position_m, lateral_m=seg.lanes * 3.2 * 0.5)
        disruption = Disruption(
            id=disruption_id,
            kind=IncidentType.COLLISION,
            segment_id=segment_id,
            lanes=lanes,
            total_lanes=seg.lanes,
            position_m=position_m,
            severity=severity,
            started_at=self._time,
            point=self.network.projector.to_point(x, y),
            vehicle_ids=vehicle_ids,
            pass_speed=None if len(lanes) == seg.lanes else PASS_SPEED[severity],
        )
        self._disruptions[disruption_id] = disruption
        return disruption

    def list_disruptions(self) -> list[Disruption]:
        return [d.model_copy() for d in self._disruptions.values()]

    def clear_disruption(self, disruption_id: str) -> None:
        disruption = self._disruptions.pop(disruption_id, None)
        if disruption is None:
            raise KeyError(disruption_id)
        for vid in disruption.vehicle_ids:
            try:
                self.conn.vehicle.remove(vid, tc.REMOVE_VAPORIZED)
            except traci.TraCIException:
                pass  # already gone

    def spawn_emergency_vehicle(
        self,
        origin_segment: str,
        destination_segment: str,
        destination_position: float | None = None,
        destination_lane: int = 0,
    ) -> EmergencyDispatch:
        c = self.conn
        for segment in (origin_segment, destination_segment):
            if segment not in self.network.segments:
                raise ValueError(f"unknown segment {segment}")
        route = c.simulation.findRoute(origin_segment, destination_segment, vType=EMS_TYPE)
        if not route.edges:
            raise ValueError(f"no route from {origin_segment} to {destination_segment}")
        dest = self.network.segments[destination_segment]
        position = destination_position if destination_position is not None else dest.length - 10.0
        position = min(max(position, 5.0), dest.length - 1.0)
        lane = min(max(destination_lane, 0), dest.lanes - 1)

        dispatch_id = f"EMS-{next(self._seq)}"
        c.route.add(f"route_{dispatch_id}", list(route.edges))
        c.vehicle.add(dispatch_id, f"route_{dispatch_id}", typeID=EMS_TYPE, depart="now",
                      departLane="best", departSpeed="max")
        c.vehicle.setStop(dispatch_id, destination_segment, pos=position, laneIndex=lane, duration=EMS_ON_SCENE_S)
        dispatch = EmergencyDispatch(
            id=dispatch_id,
            origin_segment=origin_segment,
            destination_segment=destination_segment,
            destination_position=position,
            destination_lane=lane,
            dispatched_at=self._time,
        )
        self._dispatches[dispatch_id] = dispatch
        return dispatch

    def _estimate_eta(self, d: EmergencyDispatch) -> float | None:
        """Remaining travel time: smoothed speeds on the rest of the route plus signal waits.

        On its current segment the responder's own recent speed caps the estimate,
        so a unit stuck in an incident queue shows a growing ETA instead of the
        free-flow time of the road it is stuck on.
        """
        r = self._veh.get(d.id)
        if r is None:
            return None
        c = self.conn
        route = c.vehicle.getRoute(d.id)
        index = c.vehicle.getRouteIndex(d.id)
        on_junction = r[tc.VAR_LANE_ID].startswith(":")
        lane_pos = r[tc.VAR_LANEPOSITION]
        try:
            dest_index = len(route) - 1 - list(reversed(route)).index(d.destination_segment)
        except ValueError:
            return None
        total = 0.0
        for i in range(index + (1 if on_junction else 0), dest_index + 1):
            seg = self.network.segments[route[i]]
            current = i == index and not on_junction
            start = lane_pos if current else 0.0
            end = d.destination_position if i == dest_index else seg.length
            speed = max(1.5, self._speed_ema.get(seg.id, seg.speed_limit))
            if current:
                speed = max(0.3, min(speed, self._responder_speed.get(d.id, speed)))
            total += max(0.0, end - start) / speed
            junction = self.network.intersections.get(seg.destination)
            if i < dest_index and junction is not None and junction.tls_id is not None:
                total += EXPECTED_SIGNAL_WAIT_S
        return total

    def _emergency_responses(self, window_start: float) -> list[EmergencyResponse]:
        """One entry per responder that matters to this window, in dispatch order.

        A responder that reached its scene before the window began did not respond *during* it and is left
        out. Each one's response time starts at the later of the window and its own dispatch, so a unit sent
        mid-window is not charged for the time before it existed. ``_aggregate_response`` reduces this list
        to ``TrafficMetrics.emergency_vehicle_eta``, so the two can never disagree.
        """
        return [
            EmergencyResponse(
                vehicle_id=d.id,
                destination_segment=d.destination_segment,
                dispatched_at=d.dispatched_at,
                arrived_at=d.arrived_at,
                response_s=None if d.arrived_at is None else d.arrived_at - max(window_start, d.dispatched_at),
            )
            for d in self._dispatches.values()
            if d.arrived_at is None or d.arrived_at >= window_start
        ]

    def _emergency_state(self, d: EmergencyDispatch) -> EmergencyVehicleState:
        r = self._veh.get(d.id)
        location = self.network.projector.to_point(*r[tc.VAR_POSITION]) if r else None
        return EmergencyVehicleState(
            id=d.id,
            status=d.status,
            location=location,
            speed=r[tc.VAR_SPEED] if r else 0.0,
            origin_segment=d.origin_segment,
            destination_segment=d.destination_segment,
            dispatched_at=d.dispatched_at,
            arrived_at=d.arrived_at,
            eta_s=self._estimate_eta(d) if d.status is EmergencyStatus.EN_ROUTE else None,
        )

    # -------------------------------------------------------- incident responses

    def enable_emergency_corridor(self, corridor: EmergencyCorridor) -> None:
        """Pre-empt signals ahead of every en-route EMS vehicle for the rest of the run.

        Responders are found by scanning the vehicles each step, so ones dispatched later
        are covered too. Enabling a second corridor replaces the first: the latest wins.
        """
        for intersection_id in corridor.intersection_ids:
            self._tls_id(intersection_id)  # raises for an unknown or unsignalized intersection
        self._preemption = PreemptionController(corridor, self._step_length)
        self._dropped_preemption = None  # the replaced corridor's record goes with it
        self._preemption_failures = []

    def reroute_vehicles(self, action: RerouteAction) -> int:
        for segment_id in action.avoid_segment_ids:
            if segment_id not in self.network.segments:
                raise ValueError(f"unknown segment {segment_id}")
        if self._diversion is None:
            self._diversion = DiversionAdvisory(self.conn, self._vehicle_type)
        return self._diversion.activate(action, list(self._veh))

    def response_notes(self) -> list[str]:
        corridor = self._preemption or self._dropped_preemption
        notes = corridor.notes() if corridor is not None else []
        notes += self._preemption_failures
        if self._diversion is not None:
            notes += self._diversion.notes()
        notes += self._responder_notes()
        return notes

    def _responder_notes(self) -> list[str]:
        """One line per responder that was held up, or that a corridor was in force for, in dispatch order.

        The corridor and diversion notes above say what the response did overall; these say what happened
        to each unit, which is what turns a disappointing EMS number into an explanation. They are added
        to the existing notes, never in place of them.
        """
        notes: list[str] = []
        corridor = self._preemption or self._dropped_preemption  # a dropped corridor still says what it did
        for d in self._dispatches.values():
            served, hold = corridor.responder_record(d.id) if corridor is not None else ([], 0.0)
            wait = self._responder_waits.get(d.id)
            if wait is not None and wait.stalled_s < RESPONDER_NOTE_MIN_STALL_S:
                wait = None  # braking for its scene or a red light, not held up
            if wait is None and not served:
                continue  # it was never held up and no signal was pre-empted for it: nothing to explain
            parts = []
            if wait is not None:
                # the segment it lost the most time on; ties go to the first in id order (determinism)
                worst = max(sorted(wait.by_segment.items()), key=lambda kv: kv[1], default=None)
                where = f" on {worst[0]}" if worst is not None else ""
                n = wait.max_ahead
                parts.append(
                    f"stopped for {wait.stalled_s:.0f}s{where}, "
                    f"up to {n} vehicle{'' if n == 1 else 's'} ahead"
                )
            if corridor is not None:
                k = len(served)
                parts.append(
                    f"{k} signal{'' if k == 1 else 's'} pre-empted on its route ({', '.join(served)}), "
                    f"longest hold {hold:.0f}s"
                    if served
                    else "no signals pre-empted on its route"
                )
            notes.append(f"{d.id}: {'; '.join(parts)}")
        return notes

    # ------------------------------------------------------------------ reverting

    def revert_response(self) -> list[str]:
        """Take every response back off this simulation (see ``TrafficSimulation.revert_response``)."""
        notes = self._revert_signals()
        if self._pending_offsets:
            # an offset that never found a green to stretch has changed nothing yet, so dropping it is the
            # whole revert; one already applied lives in the signal's clock and cannot be undone safely
            n = len(self._pending_offsets)
            self._pending_offsets = {}
            notes.append(f"{n} offset shift{'' if n == 1 else 's'} dropped before taking effect")
        if self._preemption is not None:
            self._drop_preemption()
            notes.append("EMS corridor disabled; each signal carries on with its own program")
        if self._diversion is not None and self._diversion.active:
            cleared = self._diversion.deactivate(self._veh)
            notes.append(
                f"diversion advisory stopped; travel-time overrides cleared on {cleared} "
                f"vehicle{'' if cleared == 1 else 's'} (routes already changed are left alone)"
            )
        return notes

    def _drop_preemption(self) -> None:
        """Abandon the corridor, keeping the controller (never stepped again) for ``response_notes``.

        A service in progress is simply forgotten, which is safe: the last command the controller can have
        sent is either an extension of a running green (it changes no light and the green now ends into the
        program's own yellow) or a jump taken from a fully served all-red. Nothing is left pending that
        could skip a clearance - the same position a timing policy applied mid-service leaves.
        """
        if self._preemption is None:
            return
        self._dropped_preemption = self._preemption
        self._preemption = None

    def _revert_signals(self) -> list[str]:
        """Put every intersection a timing policy changed back on the program it was running before.

        The running phase keeps its index, its state and its remaining time, so no light on the street
        changes at this step and the clearance that follows is the base program's own. An intersection now
        running a program this simulation did not install is left alone: mapping a phase index into an
        unknown program is the kind of guess ``check_transition`` exists to catch.
        """
        notes: list[str] = []
        c = self.conn.trafficlight
        for intersection_id, base_id in sorted(self._base_program_ids.items()):
            tls_id = self._tls_id(intersection_id)
            current_id = c.getProgram(tls_id)
            if current_id == base_id:
                self._base_program_ids.pop(intersection_id)
                continue  # already back: a restore, or a second revert
            if (tls_id, current_id) not in self._custom_programs:
                notes.append(f"{intersection_id}: left on program {current_id}, which this simulation did not install")
                self._base_program_ids.pop(intersection_id)
                continue
            base = next((lg for lg in c.getAllProgramLogics(tls_id) if lg.programID == base_id), None)
            phase_index = c.getPhase(tls_id)
            if base is None or phase_index >= len(base.phases):
                notes.append(f"{intersection_id}: left on program {current_id}; program {base_id} does not match it")
                self._base_program_ids.pop(intersection_id)
                continue
            # a policy only ever re-times the base program's phases, so this is the same state: asserted, not assumed
            check_transition(c.getRedYellowGreenState(tls_id), base.phases[phase_index].state)
            remaining = max(0.0, c.getNextSwitch(tls_id) - self._time)
            c.setProgram(tls_id, base_id)  # switches to wherever that program's own clock stood...
            c.setPhase(tls_id, phase_index)  # ...so put the running phase back, with its progress
            c.setPhaseDuration(tls_id, remaining)
            self._base_program_ids.pop(intersection_id)
            notes.append(
                f"{intersection_id}: timing reverted to program {base_id} "
                f"(phase {phase_index}, {remaining:.0f}s left)"
            )
        return notes

    def _vehicle_type(self, vehicle_id: str) -> str | None:
        r = self._veh.get(vehicle_id)
        return r[tc.VAR_TYPE] if r else None

    # ---------------------------------------------------------------- branching

    def save_snapshot(self) -> SimulationSnapshot:
        self._snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_id = f"snap-{uuid4().hex[:8]}"
        path = self._snapshot_dir / f"{snapshot_id}.xml"
        self.conn.simulation.saveState(str(path))
        return SimulationSnapshot(
            id=snapshot_id,
            sim_time=self._time,
            path=str(path),
            created_at=datetime.now(UTC),
            disruptions=[d.model_copy(deep=True) for d in self._disruptions.values()],
            dispatches=[d.model_copy(deep=True) for d in self._dispatches.values()],
            custom_programs=[
                ProgramLogic(tls_id=tls_id, program_id=program_id, phases=phases)
                for (tls_id, program_id), phases in self._custom_programs.items()
            ],
        )

    def restore_snapshot(self, snapshot: SimulationSnapshot) -> None:
        """Load a snapshot saved by any instance running the same scenario.

        For comparable candidate runs, restore into a freshly started instance:
        fresh processes restored from one snapshot evolve identically, whereas
        re-loading into a process that has already run carries over internal
        SUMO state and diverges. Signal programs installed at runtime come back
        where they were (see below); emergency corridors, diversions and pending
        offsets live in Python and must be re-applied.
        """
        # SUMO saves the state of every program variant by id and refuses to load an id it does not know, so a
        # program installed at runtime (a timing policy applied to the live city) is re-created first. loadState
        # then restores each program's phase and switches every signal back to the program it was running.
        for logic in snapshot.custom_programs:
            phases = [traci.trafficlight.Phase(duration, state) for duration, state in logic.phases]
            self.conn.trafficlight.setProgramLogic(logic.tls_id, traci.trafficlight.Logic(logic.program_id, 0, 0, phases))
        self.conn.simulation.loadState(snapshot.path)
        self._custom_programs = {(p.tls_id, p.program_id): list(p.phases) for p in snapshot.custom_programs}
        self._disruptions = {d.id: d.model_copy(deep=True) for d in snapshot.disruptions}
        self._dispatches = {d.id: d.model_copy(deep=True) for d in snapshot.dispatches}
        self._responder_speed = {}
        self._responder_waits = {}  # a branch measures the delay it causes, not the live city's history
        self._rubbernecking = set()
        self._pending_offsets = {}
        self._preemption = None
        self._dropped_preemption = None
        self._preemption_failures = []
        self._diversion = None
        self._base_program_ids = {}  # the snapshot restores each signal to the program it was running
        self._programs = {}
        self._collector.reset()
        # ids stay unique: new EMS units and policy programs number on from the restored ones
        used = [int(m.group(1)) for d in self._dispatches.values() if (m := re.match(r"EMS-(\d+)$", d.id))]
        used += [int(m.group(1)) for _, pid in self._custom_programs if (m := re.match(r"policy-(\d+)$", pid))]
        self._seq = itertools.count(max(used, default=0) + 1)
        self._subscribe_all()
        self._read_state()
        self._apply_rubbernecking()  # per-vehicle speed overrides are not part of SUMO's saved state


def _aggregate_response(responses: list[EmergencyResponse]) -> float | None:
    """The window's single EMS figure: the last responder to reach its scene.

    None if no responder mattered to the window, or if any of them still has not arrived (with one
    responder this is simply its response time).
    """
    if not responses or any(r.response_s is None for r in responses):
        return None
    return max(r.response_s for r in responses)


def _congestion_level(value: float) -> CongestionLevel:
    # Calibrated on the downtown grid: normal cycle-averaged operation stays below
    # ~0.5, while links queued behind a crash reach 0.95+.
    if value >= 0.80:
        return CongestionLevel.SEVERE
    if value >= 0.55:
        return CongestionLevel.HEAVY
    if value >= 0.30:
        return CongestionLevel.MODERATE
    return CongestionLevel.FREE
