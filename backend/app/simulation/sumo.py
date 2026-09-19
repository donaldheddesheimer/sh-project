"""Eclipse SUMO implementation of TrafficSimulation, via TraCI.

One instance owns one SUMO process and one TraCI connection. Instances are
independent, so the live simulation and candidate branches (restored from a
snapshot) can run side by side. An instance is not thread-safe: drive it from
a single thread.
"""

from __future__ import annotations

import itertools
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable
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
    EmergencyStatus,
    EmergencyVehicleState,
    IncidentType,
    IntersectionState,
    NetworkState,
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
from app.simulation.preemption import PreemptionController, ResponderApproach, SetPhase, TlsObservation
from app.simulation.reroute import DiversionAdvisory
from app.simulation.scenario import Scenario

VEHICLE_VARS = (tc.VAR_POSITION, tc.VAR_ANGLE, tc.VAR_SPEED, tc.VAR_TYPE, tc.VAR_TIMELOSS)
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
EXPECTED_SIGNAL_WAIT_S = 10.0  # mean wait at a fixed-time signal: P(red) ~0.5 x half of a ~40 s red

_start_lock = threading.Lock()  # traci.start mutates module-level connection state


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
        self._rubbernecking: set[str] = set()
        self._pending_offsets: dict[str, float] = {}
        self._preemption: PreemptionController | None = None
        self._diversion: DiversionAdvisory | None = None
        self._programs: dict[tuple[str, str], SignalProgram] = {}  # active program per (tls, program id)
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
            traci.start(
                cmd,
                port=sumolib.miscutils.getFreeSocketPort(),
                label=self.label,
                doSwitch=False,
                stdout=subprocess.DEVNULL,
            )
            self._conn = traci.getConnection(self.label)
        self._step_length = self.conn.simulation.getDeltaT()
        self._subscribe_all()
        self._read_state()

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
        return self._collector.end_window(self._time, self._realised_emergency_eta(start_time))

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
        """Slow traffic squeezing past a crash on the lanes that remain open."""
        c = self.conn
        targets: dict[str, float] = {}
        for d in self._disruptions.values():
            if d.pass_speed is None:
                continue
            for lane in range(d.total_lanes):
                if lane in d.lanes:
                    continue
                for vid in c.lane.getLastStepVehicleIDs(f"{d.segment_id}_{lane}"):
                    r = self._veh.get(vid)
                    if r is None or r[tc.VAR_TYPE] == EMS_TYPE:
                        continue
                    pos = c.vehicle.getLanePosition(vid)
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
            elif d.status is EmergencyStatus.EN_ROUTE and d.id in self._veh and self.conn.vehicle.isStopped(d.id):
                d.status = EmergencyStatus.ON_SCENE
                d.arrived_at = self._time

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
        commands = self._preemption.step(self._time, self._responder_approaches(), self._observe_tls)
        for command in commands:  # UnsafeTransition from the controller propagates on purpose
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
                on_junction = v.getRoadID(vid).startswith(":")
                lane_pos = v.getLanePosition(vid)
            except traci.TraCIException:
                continue  # arrived after the subscriptions were read
            if index < 0:
                continue
            for a in self.network.signalized_approaches_ahead(route, index, lane_pos, on_junction):
                approaches.append(ResponderApproach(vid, a.intersection_id, a.direction, a.distance_m))
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
        for direction, approach in info.approaches.items():
            r = self._edges.get(approach.segment_id, {})
            n = r.get(tc.LAST_STEP_VEHICLE_NUMBER, 0)
            queues[direction] = r.get(tc.LAST_STEP_VEHICLE_HALTING_NUMBER, 0)
            vehicles += n
            speed_sum += r.get(tc.LAST_STEP_MEAN_SPEED, 0.0) * n
            congestion = max(congestion, self._congestion.get(approach.segment_id, 0.0))
            if tls:
                state = tls[tc.TL_RED_YELLOW_GREEN_STATE]
                chars = [state[i] for i in (approach.through_link_indices or approach.link_indices) if i < len(state)]
                if any(ch in "Gg" for ch in chars):
                    signals[direction] = SignalColor.GREEN
                elif any(ch in "yY" for ch in chars):
                    signals[direction] = SignalColor.YELLOW
                else:
                    signals[direction] = SignalColor.RED
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
            phase_index = c.trafficlight.getPhase(tls_id)
            elapsed = program.phases[phase_index].duration - (c.trafficlight.getNextSwitch(tls_id) - self._time)
            phases = [
                traci.trafficlight.Phase(policy.phase_durations.get(p.index, p.duration), p.state) for p in program.phases
            ]
            program_id = f"policy-{next(self._seq)}"
            c.trafficlight.setProgramLogic(tls_id, traci.trafficlight.Logic(program_id, 0, phase_index, phases))
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
        if d.id not in self._veh:
            return None
        c = self.conn
        route = c.vehicle.getRoute(d.id)
        index = c.vehicle.getRouteIndex(d.id)
        on_junction = c.vehicle.getRoadID(d.id).startswith(":")
        lane_pos = c.vehicle.getLanePosition(d.id)
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

    def _realised_emergency_eta(self, window_start: float) -> float | None:
        """Response time of the responders that matter to this window: the last one to reach its scene.

        Responders that reached their scene before the window began are not counted. None if none did, or if
        any responder still has not arrived (with one responder this is simply its response time).
        """
        relevant = [d for d in self._dispatches.values() if d.arrived_at is None or d.arrived_at >= window_start]
        if not relevant or any(d.arrived_at is None for d in relevant):
            return None
        return max(d.arrived_at - max(window_start, d.dispatched_at) for d in relevant)

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

    def reroute_vehicles(self, action: RerouteAction) -> int:
        for segment_id in action.avoid_segment_ids:
            if segment_id not in self.network.segments:
                raise ValueError(f"unknown segment {segment_id}")
        if self._diversion is None:
            self._diversion = DiversionAdvisory(self.conn, self._vehicle_type)
        return self._diversion.activate(action, list(self._veh))

    def response_notes(self) -> list[str]:
        notes = self._preemption.notes() if self._preemption is not None else []
        if self._diversion is not None:
            notes += self._diversion.notes()
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
        )

    def restore_snapshot(self, snapshot: SimulationSnapshot) -> None:
        """Load a snapshot saved by any instance running the same scenario.

        For comparable candidate runs, restore into a freshly started instance:
        fresh processes restored from one snapshot evolve identically, whereas
        re-loading into a process that has already run carries over internal
        SUMO state and diverges. Snapshots capture base signal programs only;
        programs installed by apply_signal_policy(), emergency corridors and
        diversions must be re-applied.
        """
        self.conn.simulation.loadState(snapshot.path)
        self._disruptions = {d.id: d.model_copy(deep=True) for d in snapshot.disruptions}
        self._dispatches = {d.id: d.model_copy(deep=True) for d in snapshot.dispatches}
        self._responder_speed = {}
        self._rubbernecking = set()
        self._pending_offsets = {}
        self._preemption = None
        self._diversion = None
        self._programs = {}
        self._collector.reset()
        used = [int(m.group(1)) for d in self._dispatches.values() if (m := re.match(r"EMS-(\d+)$", d.id))]
        self._seq = itertools.count(max(used, default=0) + 1)
        self._subscribe_all()
        self._read_state()
        self._apply_rubbernecking()  # per-vehicle speed overrides are not part of SUMO's saved state


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
