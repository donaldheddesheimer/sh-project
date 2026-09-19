"""Deterministic safety validation of signal timing policies.

Every candidate signal change - whoever proposed it (operator, mock agent,
Nemotron) - must pass a SafetyValidator before it reaches a simulation or,
eventually, a real controller. The validator is deliberately independent of
any agent code.

MVP limits are synthetic placeholders. A production validator should load
per-intersection timing sheets (MUTCD / local agency standards): pedestrian
walk + flashing-don't-walk per crosswalk length, yellow from approach speed
and grade (ITE formula), all-red from intersection width, and the controller's
conflict monitor (MMU) card for incompatible movements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.models.domain import EmergencyCorridor, PhaseKind, SignalPolicy, SignalProgram


class TimingLimits(BaseModel):
    min_green_s: float = 7.0
    max_green_s: float = 90.0
    min_yellow_s: float = 3.0
    min_all_red_s: float = 1.0
    min_pedestrian_green_s: float = 12.0  # placeholder: walk + clearance for a ~15 m crossing
    min_cycle_s: float = 50.0
    max_cycle_s: float = 150.0
    min_corridor_hold_s: float = 5.0  # placeholder: a pre-empted green held for less than this cannot clear a responder


class Violation(BaseModel):
    code: str
    message: str
    phase_index: int | None = None


class ValidationResult(BaseModel):
    intersection_id: str
    ok: bool
    violations: list[Violation] = Field(default_factory=list)


class SafetyValidator(ABC):
    @abstractmethod
    def validate(self, policy: SignalPolicy, base_program: SignalProgram) -> ValidationResult: ...

    @abstractmethod
    def validate_corridor(self, corridor: EmergencyCorridor, programs: dict[str, SignalProgram]) -> list[Violation]:
        """Check a pre-emption request against the signals it may control (``programs``: every signalized
        intersection, keyed by id). Empty list = safe to simulate."""


class RuleBasedSafetyValidator(SafetyValidator):
    """Synthetic-limit validator for the MVP.

    Incompatible movements cannot arise from a SignalPolicy: policies change
    durations and offsets only, and phase states always come from the
    certified base program. Clearance intervals may be lengthened but never
    shortened.

    The same holds for an EmergencyCorridor: pre-emption only ever changes
    WHEN a phase ends, never a phase's state string, and it never shortens a
    yellow or all-red clearance. A running green is cut only after
    ``min_served_green_s``, and the responder's green is reached through the
    program's own clearance, so ``validate_corridor`` bounds those timing
    parameters (and that a clearance exists to end each green through) rather
    than any phase content.
    """

    def __init__(self, limits: TimingLimits | None = None):
        self.limits = limits or TimingLimits()

    def validate(self, policy: SignalPolicy, base_program: SignalProgram) -> ValidationResult:
        lim = self.limits
        violations: list[Violation] = []
        if policy.intersection_id != base_program.intersection_id:
            violations.append(Violation(code="wrong_program", message="policy and program are for different intersections"))

        phases = {p.index: p for p in base_program.phases}
        for index, duration in policy.phase_durations.items():
            phase = phases.get(index)
            if phase is None:
                violations.append(Violation(code="unknown_phase", message=f"no phase {index}", phase_index=index))
                continue
            if phase.kind is PhaseKind.GREEN:
                floor = max(lim.min_green_s, lim.min_pedestrian_green_s)
                if duration < floor:
                    violations.append(
                        Violation(
                            code="min_green",
                            message=f"green {duration:.0f}s < {floor:.0f}s (vehicle/pedestrian minimum)",
                            phase_index=index,
                        )
                    )
                if duration > lim.max_green_s:
                    violations.append(
                        Violation(code="max_green", message=f"green {duration:.0f}s > {lim.max_green_s:.0f}s", phase_index=index)
                    )
            elif phase.kind is PhaseKind.YELLOW:
                if duration < max(lim.min_yellow_s, phase.duration):
                    violations.append(
                        Violation(code="yellow_clearance", message="yellow clearance cannot be shortened", phase_index=index)
                    )
            elif duration < max(lim.min_all_red_s, phase.duration):
                violations.append(
                    Violation(code="all_red_clearance", message="all-red clearance cannot be shortened", phase_index=index)
                )

        cycle = sum(policy.phase_durations.get(p.index, p.duration) for p in base_program.phases)
        if not lim.min_cycle_s <= cycle <= lim.max_cycle_s:
            violations.append(
                Violation(
                    code="cycle_length",
                    message=f"cycle {cycle:.0f}s outside {lim.min_cycle_s:.0f}-{lim.max_cycle_s:.0f}s",
                )
            )
        if policy.offset_s is not None and not 0 <= policy.offset_s < cycle:
            violations.append(Violation(code="offset", message=f"offset must be within [0, {cycle:.0f})s"))

        return ValidationResult(intersection_id=policy.intersection_id, ok=not violations, violations=violations)

    def validate_corridor(self, corridor: EmergencyCorridor, programs: dict[str, SignalProgram]) -> list[Violation]:
        lim = self.limits
        violations: list[Violation] = []
        unknown = [i for i in corridor.intersection_ids if i not in programs]
        if unknown:
            violations.append(Violation(code="unknown_signal", message=f"no signal program for {', '.join(unknown)}"))
        duplicates = sorted({i for i in corridor.intersection_ids if corridor.intersection_ids.count(i) > 1})
        if duplicates:
            violations.append(Violation(code="duplicate_signal", message=f"listed more than once: {', '.join(duplicates)}"))

        # Bounds are written as negated comparisons so a NaN parameter fails them instead of slipping through.
        floor = max(lim.min_green_s, lim.min_pedestrian_green_s)
        if not corridor.min_served_green_s >= floor:
            violations.append(
                Violation(
                    code="min_green",
                    message=f"pre-emption may cut a green after {corridor.min_served_green_s:.0f}s "
                    f"< {floor:.0f}s (vehicle/pedestrian minimum)",
                )
            )
        if corridor.min_served_green_s > lim.max_green_s:
            violations.append(
                Violation(
                    code="min_green_range",
                    message=f"minimum served green {corridor.min_served_green_s:.0f}s exceeds the {lim.max_green_s:.0f}s maximum green",
                )
            )
        if not corridor.max_hold_s <= lim.max_green_s:
            violations.append(
                Violation(code="max_green", message=f"hold {corridor.max_hold_s:.0f}s > {lim.max_green_s:.0f}s")
            )
        if corridor.max_hold_s < lim.min_corridor_hold_s:
            violations.append(
                Violation(
                    code="min_hold",
                    message=f"hold {corridor.max_hold_s:.0f}s < {lim.min_corridor_hold_s:.0f}s (too short to clear a responder)",
                )
            )
        if not 30.0 <= corridor.detection_distance_m <= 400.0:
            violations.append(Violation(code="detection_distance", message="detection distance must be 30-400 m"))

        # Pre-emption ends a running green early and relies on the program's own clearance to follow it.
        for intersection_id in corridor.intersection_ids or programs:
            phases = programs[intersection_id].phases if intersection_id in programs else []
            for i, phase in enumerate(phases):
                if phase.kind is PhaseKind.GREEN and phases[(i + 1) % len(phases)].kind is PhaseKind.GREEN:
                    violations.append(
                        Violation(
                            code="no_clearance",
                            message=f"{intersection_id} phase {phase.index}: green is followed directly by another green "
                            "(no yellow/all-red to end it through)",
                            phase_index=phase.index,
                        )
                    )
        return violations
