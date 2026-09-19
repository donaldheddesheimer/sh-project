"""EMS green-corridor pre-emption: pure logic, no TraCI and no sumolib.

The simulation adapter feeds ``PreemptionController.step`` once per simulation step and
executes the commands it returns. Per step the adapter passes

* ``responders``: one ``ResponderApproach`` per (en-route responder, signalized approach
  still ahead of it, at any distance; ``RoadNetwork.signalized_approaches_ahead`` builds
  them). Responders that are stopped, on scene, arrived or gone are simply omitted;
* ``observe(intersection_id) -> TlsObservation``: the signal's current state, phase
  index, seconds the phase stays in force (``TL_NEXT_SWITCH - sim_time``, never negative;
  0 means the next step already runs the following phase) and its ACTIVE
  ``SignalProgram``. It is called only for junctions a responder is close to, so it may
  memoize the program per (tls, program id).

and executes ``SetRemaining`` (``trafficlight.setPhaseDuration``) and ``SetPhase``
(``trafficlight.setPhase``) commands, in order. ``notes()`` is valid at any time.

Per intersection the controller runs one service, a small state machine driven by what the
signal is showing:

* idle: no responder within ``detection_distance_m`` (nothing is observed or commanded);
* waiting-min-green: a conflicting green runs and has not yet served ``min_served_green_s``;
* clearing: the conflicting green was ended early (or is yellow / all-red) on the way to the
  target green. The program's own yellow and all-red run untouched;
* holding: the target green is in force and is topped up past its natural end while a
  responder is still approaching, for at most ``max_hold_s``;
* released: every responder has crossed (or the hold hit its cap). The target green keeps
  what it owes (its natural end, or ``min_served_green_s``) and the program carries on.

How it stays safe: only a GREEN phase is ever shortened, into the program's own next phase
(yellow); yellow and all-red phases are never shortened. A jump into a green happens only
at the last step of an all-red that has run its full scheduled duration, and only when the
program's next phase is a non-target green. Every command that shortens a phase or jumps
is first passed through ``check_transition``, which is the one place the rule lives; a
violation raises ``UnsafeTransition`` so the candidate fails loudly.

Known limits: a service is not pre-empted by a closer responder wanting another phase
(that one waits its turn), and a timing policy installed mid-service abandons the service.
Everything is deterministic: sorted iteration, no randomness.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.models.domain import EmergencyCorridor, PhaseKind, SignalProgram

EPS = 1e-3
HOLD_REFRESH_BELOW_S = 2.0  # a held green is topped up when less than this is left...
HOLD_EXTENSION_S = 4.0  # ...to this much

_GREEN, _YELLOW, _RED = "Gg", "yY", "rR"


class UnsafeTransition(RuntimeError):
    """A commanded signal change would break the clearance rule."""


def check_transition(prev_state: str, next_state: str) -> None:
    """Raise ``UnsafeTransition`` unless ``prev_state`` -> ``next_state`` is a safe signal change.

    Per link: green (G/g) may go to green or yellow only, never straight to red; yellow may
    go to yellow or red only; red may stay red, and may turn green only when the WHOLE
    previous state is all-red (no G/g/y anywhere). The states must be the same length.
    Every transition of the certified base programs (green, yellow, all-red, green)
    satisfies this.
    """
    if len(prev_state) != len(next_state):
        raise UnsafeTransition(f"state lengths differ: {prev_state!r} -> {next_state!r}")
    unknown = set(prev_state + next_state) - set(_GREEN + _YELLOW + _RED)
    if unknown:
        raise UnsafeTransition(f"unsupported signal characters {sorted(unknown)}: {prev_state!r} -> {next_state!r}")
    all_red = all(c in _RED for c in prev_state)
    for link, (a, b) in enumerate(zip(prev_state, next_state)):
        if a in _GREEN:
            safe = b in _GREEN or b in _YELLOW
        elif a in _YELLOW:
            safe = b in _YELLOW or b in _RED
        else:
            safe = b in _RED or (b in _GREEN and all_red)
        if not safe:
            raise UnsafeTransition(f"link {link} goes {a}->{b}: {prev_state!r} -> {next_state!r}")


@dataclass(frozen=True)
class ResponderApproach:
    """A responder heading for a signalized approach (``distance_m`` to the stop line)."""

    responder_id: str
    intersection_id: str
    segment_id: str  # the approach the responder is on (its incoming segment)
    distance_m: float


@dataclass(frozen=True)
class TlsObservation:
    state: str
    phase_index: int
    remaining_s: float
    program: SignalProgram  # the ACTIVE program


@dataclass(frozen=True)
class SetRemaining:
    """Set the running phase's remaining duration (setPhaseDuration); 0 ends it at the next step."""

    intersection_id: str
    seconds: float


@dataclass(frozen=True)
class SetPhase:
    """Jump to a phase now (setPhase). Only ever issued from a fully served all-red."""

    intersection_id: str
    phase_index: int


Command = SetRemaining | SetPhase


@dataclass
class _PhaseClock:
    """When the running phase started being in force. SUMO does not report elapsed time."""

    key: tuple[str, int]  # (program id, phase index)
    start: float
    last_seen: float


@dataclass
class _Service:
    """Pre-emption of one intersection for the responders in ``responders``."""

    target: int  # phase index of the green being served
    program_id: str
    responders: set[str] = field(default_factory=set)
    hold_started: float | None = None
    extended: bool = False  # the green was topped up beyond its natural end


class PreemptionController:
    def __init__(self, corridor: EmergencyCorridor, step_length: float = 0.5):
        self.corridor = corridor
        self._dt = step_length
        self._scope = frozenset(corridor.intersection_ids)  # empty = every signal
        self._clocks: dict[str, _PhaseClock] = {}
        self._services: dict[str, _Service] = {}
        self._activated: set[tuple[str, str]] = set()  # (responder, intersection): at most one activation each
        self._now = 0.0
        self._activations = 0
        self._preempted: list[str] = []
        self._longest_hold_s = 0.0
        self._capped: list[str] = []

    def notes(self) -> list[str]:
        if not self._activations:
            return ["no pre-emptions"]
        ongoing = [self._now - s.hold_started for s in self._services.values() if s.hold_started is not None]
        longest = max([self._longest_hold_s, *ongoing])
        n = self._activations
        notes = [f"{n} pre-emption{'s' if n != 1 else ''} ({', '.join(self._preempted)}); longest hold {longest:.0f}s"]
        if self._capped:
            notes.append(f"hold capped at {self.corridor.max_hold_s:.0f}s at {', '.join(self._capped)}")
        return notes

    def step(
        self,
        now: float,
        responders: Sequence[ResponderApproach],
        observe: Callable[[str], TlsObservation],
    ) -> list[Command]:
        """Advance every intersection with a responder nearby or a service running.

        Raises ``UnsafeTransition`` if a command it would emit fails ``check_transition``.
        """
        self._now = now
        by_junction: dict[str, list[ResponderApproach]] = {}
        for a in sorted(responders, key=lambda a: (a.distance_m, a.responder_id)):
            if not self._scope or a.intersection_id in self._scope:
                by_junction.setdefault(a.intersection_id, []).append(a)
        commands: list[Command] = []
        for intersection_id in sorted(by_junction.keys() | self._services.keys()):
            self._step_junction(intersection_id, by_junction.get(intersection_id, []), observe, commands)
        return commands

    # ------------------------------------------------------------ per intersection

    def _step_junction(
        self,
        iid: str,
        approaches: list[ResponderApproach],
        observe: Callable[[str], TlsObservation],
        out: list[Command],
    ) -> None:
        service = self._services.get(iid)
        if service is not None:  # a responder no longer ahead has crossed (or is gone)
            service.responders.intersection_update(a.responder_id for a in approaches)
        candidates = [
            a
            for a in approaches
            if a.distance_m <= self.corridor.detection_distance_m and (a.responder_id, iid) not in self._activated
        ]
        if service is None and not candidates:
            return

        obs = observe(iid)
        elapsed = self._elapsed_in_phase(iid, obs)
        if service is not None and service.program_id != obs.program.program_id:
            del self._services[iid]  # targets are program indices: abandon rather than guess
            service = None

        # nearest first: the closest responder picks the target, others join only if it serves them too
        for a in candidates:
            if service is None:
                target = _pick_target(obs.program, obs.phase_index, a.segment_id)
                if target is None:
                    continue
                service = self._services[iid] = _Service(target, obs.program.program_id)
            elif a.segment_id not in obs.program.phases[service.target].served_segments:
                continue
            service.responders.add(a.responder_id)
            self._activate(a.responder_id, iid)
        if service is None:
            return
        if not service.responders:
            self._release(iid, service, obs, elapsed, out)
            return

        phase = obs.program.phases[obs.phase_index]
        if obs.phase_index == service.target:
            self._hold(iid, service, obs, out)
        elif phase.kind is PhaseKind.GREEN:
            # waiting-min-green, then end it: the program's own yellow and all-red follow
            if obs.remaining_s > EPS and elapsed + EPS >= self.corridor.min_served_green_s:
                self._set_remaining(iid, obs, 0.0, out)
        elif phase.kind is PhaseKind.ALL_RED:
            self._jump_from_all_red(iid, service, obs, out)
        # yellow: clearing, the program's own interval runs untouched

    def _activate(self, responder_id: str, iid: str) -> None:
        self._activated.add((responder_id, iid))
        self._activations += 1
        if iid not in self._preempted:
            self._preempted.append(iid)

    def _hold(self, iid: str, service: _Service, obs: TlsObservation, out: list[Command]) -> None:
        if obs.remaining_s >= HOLD_REFRESH_BELOW_S:
            return
        if service.hold_started is None:
            service.hold_started = self._now
        grant = min(HOLD_EXTENSION_S, service.hold_started + self.corridor.max_hold_s - self._now)
        if grant <= obs.remaining_s + EPS:  # the cap leaves nothing to add: let the program proceed
            del self._services[iid]
            self._longest_hold_s = max(self._longest_hold_s, self._now - service.hold_started)
            if iid not in self._capped:
                self._capped.append(iid)
            return
        self._set_remaining(iid, obs, grant, out)  # an extension: changes no light
        service.extended = True

    def _jump_from_all_red(self, iid: str, service: _Service, obs: TlsObservation, out: list[Command]) -> None:
        """Only for programs whose all-red is followed by a non-target green.

        Waits for the last step of the all-red (its full scheduled duration has then been
        shown) and takes the target green instead of the next phase at that very step.
        """
        following = (obs.phase_index + 1) % len(obs.program.phases)
        if (
            obs.remaining_s <= EPS
            and following != service.target
            and obs.program.phases[following].kind is PhaseKind.GREEN
        ):
            check_transition(obs.state, obs.program.phases[service.target].state)
            out.append(SetPhase(iid, service.target))

    def _release(self, iid: str, service: _Service, obs: TlsObservation, elapsed: float, out: list[Command]) -> None:
        del self._services[iid]
        if service.hold_started is not None:
            self._longest_hold_s = max(self._longest_hold_s, self._now - service.hold_started)
        if service.extended and obs.phase_index == service.target:
            # the green owes only its natural end or the minimum served green, whichever is later
            natural = obs.program.phases[service.target].duration - elapsed
            keep = max(natural, self.corridor.min_served_green_s - elapsed, 0.0)
            if abs(keep - obs.remaining_s) > EPS:
                self._set_remaining(iid, obs, keep, out)

    # -------------------------------------------------------------------- commands

    def _set_remaining(self, iid: str, obs: TlsObservation, seconds: float, out: list[Command]) -> None:
        if seconds < obs.remaining_s - EPS:  # shortening: only a green, only into the program's next phase
            phase = obs.program.phases[obs.phase_index]
            if phase.kind is not PhaseKind.GREEN:
                raise UnsafeTransition(f"{iid}: refusing to shorten {phase.kind.value} phase {obs.phase_index}")
            following = obs.program.phases[(obs.phase_index + 1) % len(obs.program.phases)]
            check_transition(obs.state, following.state)
        out.append(SetRemaining(iid, seconds))

    def _elapsed_in_phase(self, iid: str, obs: TlsObservation) -> float:
        key = (obs.program.program_id, obs.phase_index)
        clock = self._clocks.get(iid)
        if clock is None or clock.key != key or self._now - clock.last_seen > 1.5 * self._dt:
            duration = obs.program.phases[obs.phase_index].duration
            start = self._now - max(0.0, duration - obs.remaining_s)  # first sight: assume unmodified
            clock = self._clocks[iid] = _PhaseClock(key, start, self._now)
        clock.last_seen = self._now
        return self._now - clock.start


def _pick_target(program: SignalProgram, current_index: int, segment_id: str) -> int | None:
    """The running phase if it is a green serving approach ``segment_id``, else the next such green in cycle order."""
    n = len(program.phases)
    for k in range(n):
        index = (current_index + k) % n
        phase = program.phases[index]
        if phase.kind is PhaseKind.GREEN and segment_id in phase.served_segments:
            return index
    return None
