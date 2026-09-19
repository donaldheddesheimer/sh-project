"""Deterministic, rule-based stand-in for the Nemotron agent.

Proposes the classic incident-management responses for a blocked link (signal
timing changes, an EMS green corridor, a diversion advisory) and recommends the
best simulated outcome. No LLM involved. Remembered episodes (``context.lessons``)
reorder or prune the proposals, so a warm run tries what worked first.
"""

from __future__ import annotations

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext, Recommendation
from app.models.domain import (
    CandidateStatus,
    EmergencyCorridor,
    EmergencyStatus,
    PhaseKind,
    RerouteAction,
    RoadSegmentState,
    SignalPolicy,
    SignalProgram,
    SimulationCandidate,
    TrafficMetrics,
)

SPLIT_SHIFT_S = 15.0
STRONG_SHIFT_S = 25.0
AGGRESSIVE_CROSS_GREEN_S = 8.0  # deliberately below the safety validator's green floor
DIVERT_COMPLIANCE = 0.3
EMS_ETA_TOLERANCE = 1.10  # a plan may not slow responders by more than 10%
EMS_GAIN_PREFERRED_S = 60.0  # an EMS improvement this large outweighs a small delay penalty...
DELAY_TRADEOFF = 1.05  # ...as long as mean delay stays within 5% of the best eligible candidate
CLOSE_MATCH = 0.75  # a remembered episode this similar (same segment and blockage) may prune the plan set...
LOOSE_MATCH = 0.5  # ...one this similar only reorders it
KEEP_WITH_LESSON = 2  # other plans still tried next to the one a close lesson found effective


def _green_phase(program: SignalProgram, approach: str) -> int | None:
    return next(
        (p.index for p in program.phases if p.kind is PhaseKind.GREEN and approach in p.served_approaches), None
    )


def _cross_green_phase(program: SignalProgram, approach: str) -> int | None:
    return next(
        (
            p.index
            for p in program.phases
            if p.kind is PhaseKind.GREEN and p.served_approaches and approach not in p.served_approaches
        ),
        None,
    )


def _shift(program: SignalProgram, give_to: int, take_from: int, seconds: float, reason: str) -> SignalPolicy:
    durations = {p.index: p.duration for p in program.phases}
    return SignalPolicy(
        intersection_id=program.intersection_id,
        phase_durations={give_to: durations[give_to] + seconds, take_from: durations[take_from] - seconds},
        reason=reason,
    )


def _cross_street(context: IncidentContext, node: str, approach: str) -> str | None:
    """Name of the street crossing ``approach`` at ``node``, read from the segments that touch it."""
    perpendicular = ("NB", "SB") if approach in ("EB", "WB") else ("EB", "WB")
    return next((s.name for s in context.segments if node in (s.source, s.destination) and s.direction in perpendicular), None)


def _parallel_streets(context: IncidentContext, segment: RoadSegmentState) -> list[str]:
    return sorted({s.name for s in context.segments if s.direction == segment.direction and s.name != segment.name})


def _mss(seconds: float) -> str:
    total = int(seconds + 0.5)
    return f"{total // 60}:{total % 60:02d}"


def _change(before: float, after: float) -> str:
    return f" ({round(100 * (after - before) / before):+d}%)" if before else ""


def _outcome_lines(base: TrafficMetrics, m: TrafficMetrics) -> list[str]:
    lines = []
    if base.emergency_vehicle_eta is not None and m.emergency_vehicle_eta is not None:
        b, a = base.emergency_vehicle_eta, m.emergency_vehicle_eta
        lines.append(f"EMS response {_mss(b)} -> {_mss(a)}{_change(b, a)}")
    elif m.emergency_vehicle_eta is not None:
        lines.append(
            f"EMS response {_mss(m.emergency_vehicle_eta)} (the baseline responder did not reach the scene within the horizon)"
        )
    lines.append(
        f"Mean delay {base.mean_vehicle_delay:.0f}s -> {m.mean_vehicle_delay:.0f}s"
        f"{_change(base.mean_vehicle_delay, m.mean_vehicle_delay)}"
    )
    lines.append(f"Max queue {base.max_queue_length} -> {m.max_queue_length} vehicles")
    lines.append(f"Throughput {base.throughput:.0f} -> {m.throughput:.0f} veh/h")
    return lines


def _by_plan(lessons: list[dict], verdict: str, min_similarity: float) -> dict[str, str]:
    """Plan id -> the most similar lesson (lessons arrive most similar first) that found it ``verdict``."""
    found: dict[str, str] = {}
    for lesson in lessons:
        if lesson.get("verdict") == verdict and lesson.get("similarity", 0.0) >= min_similarity:
            found.setdefault(lesson.get("chosen", ""), lesson["id"])
    return found


def _noted(plan: CandidatePlan, note: str) -> CandidatePlan:
    return plan.model_copy(update={"description": f"{plan.description} {note}"})


def _apply_lessons(plans: list[CandidatePlan], lessons: list[dict]) -> list[CandidatePlan]:
    """Use remembered episodes the way an operator uses experience: try what worked first, skip what did not.

    Only a close match prunes: a plan it found ineffective is dropped, and a plan it found effective is simulated
    with just ``KEEP_WITH_LESSON`` others (one wave of branches instead of two). A looser match only reorders.
    Every plan that is left is still validated and simulated.
    """
    baseline, rest = plans[0], plans[1:]
    rest = [p for p in rest if p.id not in _by_plan(lessons, "ineffective", CLOSE_MATCH)]
    worked = _by_plan(lessons, "effective", CLOSE_MATCH)
    first = next((p for p in rest if p.id in worked), None)
    if first is not None:
        others = [p for p in rest if p is not first][:KEEP_WITH_LESSON]
        return [baseline, _noted(first, f"Tried first: {worked[first.id]} found it effective here."), *others]
    liked = _by_plan(lessons, "effective", LOOSE_MATCH)
    disliked = _by_plan(lessons, "ineffective", LOOSE_MATCH)
    front = [_noted(p, f"Tried early: {liked[p.id]} found it effective in a similar situation.") for p in rest if p.id in liked]
    back = [
        _noted(p, f"Tried last: {disliked[p.id]} found it ineffective in a similar situation.")
        for p in rest
        if p.id in disliked and p.id not in liked
    ]
    middle = [p for p in rest if p.id not in liked and p.id not in disliked]
    return [baseline, *front, *middle, *back]


def _why_lost(chosen: SimulationCandidate, other: SimulationCandidate, other_eligible: bool) -> str:
    c, o = chosen.metrics, other.metrics
    gap = o.mean_vehicle_delay - c.mean_vehicle_delay
    if gap >= 0.5:
        return f"{other.id} has {gap:.0f}s more delay"
    lead = f"{other.id} has {-gap:.0f}s less delay" if gap <= -0.5 else f"{other.id} has the same delay"
    o_eta, c_eta = o.emergency_vehicle_eta, c.emergency_vehicle_eta
    if o_eta is None and c_eta is not None:
        return f"{lead} but the responder never reaches the scene within the horizon"
    if o_eta is not None and c_eta is not None and o_eta > c_eta:
        limit = f" (beyond the {round((EMS_ETA_TOLERANCE - 1) * 100)}% EMS limit)" if not other_eligible else ""
        return f"{lead} but leaves the responder {o_eta - c_eta:.0f}s slower{limit}"
    return f"{lead} but ranks lower on tie-breaks"


class MockAgentProvider(AgentProvider):
    name = "mock"

    async def propose_candidates(self, context: IncidentContext) -> list[CandidatePlan]:
        incident = context.incident
        segment = next((s for s in context.segments if s.id == incident.location.segment_id), None)
        plans = [CandidatePlan(id="baseline", name="Baseline", description="Continue current signal timing unchanged.")]
        if segment is None:
            return plans
        approach = segment.direction
        upstream = context.signal_programs.get(segment.source)
        downstream = context.signal_programs.get(segment.destination)
        ems_present = context.ems_origin_segment is not None or any(
            v.status is EmergencyStatus.EN_ROUTE for v in context.emergency_vehicles
        )
        aggressive: CandidatePlan | None = None
        meter: CandidatePlan | None = None

        if downstream and (flow := _green_phase(downstream, approach)) is not None:
            cross = _cross_green_phase(downstream, approach)
            if cross is not None:
                plans.append(
                    CandidatePlan(
                        id="flush-downstream",
                        name=f"Extend {approach} green at {segment.destination}",
                        description=f"Give the incident approach {SPLIT_SHIFT_S:.0f}s more green downstream to discharge vehicles past the scene.",
                        policies=[_shift(downstream, flow, cross, SPLIT_SHIFT_S, "discharge traffic past the incident")],
                    )
                )
                cross_green = next(p.duration for p in downstream.phases if p.index == cross)
                unsafe = _shift(
                    downstream, flow, cross, cross_green - AGGRESSIVE_CROSS_GREEN_S, "clear the incident queue as fast as possible"
                )
                cross_name = _cross_street(context, segment.destination, approach) or "the cross street"
                aggressive = CandidatePlan(
                    id="aggressive-flush",
                    name=f"Maximum {approach} green at {segment.destination}",
                    description=f"Give {segment.name} {approach} {unsafe.phase_durations[flow]:.0f}s of green at "
                    f"{segment.destination}, leaving {cross_name} {AGGRESSIVE_CROSS_GREEN_S:.0f}s. Deliberately unsafe: "
                    "the cross street falls below the minimum green, so the safety validator must reject it "
                    "(it shows the validator gates agent output).",
                    policies=[unsafe],
                )

        if upstream and (feed := _green_phase(upstream, approach)) is not None:
            cross = _cross_green_phase(upstream, approach)
            if cross is not None:
                meter = CandidatePlan(
                    id="meter-upstream",
                    name=f"Meter {approach} inflow at {segment.source}",
                    description=f"Move {SPLIT_SHIFT_S:.0f}s of {approach} green to the cross street upstream so the queue "
                    "stays off the intersection and cross traffic keeps moving.",
                    policies=[_shift(upstream, cross, feed, SPLIT_SHIFT_S, "meter inflow toward the blocked link")],
                )
                plans.append(meter)
                plans.append(
                    CandidatePlan(
                        id="relieve-cross-street",
                        name=f"Favour cross street at {segment.source}",
                        description=f"Stronger metering: shift {STRONG_SHIFT_S:.0f}s to the cross street at the upstream intersection.",
                        policies=[_shift(upstream, cross, feed, STRONG_SHIFT_S, "protect cross-street flow from spillback")],
                    )
                )

        if ems_present:
            # The unsafe demo plan travels with the EMS plans (Analyze Response always carries an EMS probe), so a
            # routine proposal set without responders stays fully valid: every plan there passes the validator.
            if aggressive is not None:
                plans.append(aggressive)
            reason = f"pre-empt signals ahead of EMS responding to {incident.id}"
            plans.append(
                CandidatePlan(
                    id="ems-corridor",
                    name="EMS green corridor",
                    description="Pre-empt each signal on the responder's route to green ahead of the EMS unit, "
                    "through full yellow and all-red clearance.",
                    corridor=EmergencyCorridor(reason=reason),
                )
            )
            if meter is not None:
                meter_at = meter.policies[0].intersection_id
                plans.append(
                    CandidatePlan(
                        id="corridor-plus-meter",
                        name=f"EMS corridor + meter at {meter_at}",
                        description=f"Green corridor for the responder, then keep metering {approach} inflow at {meter_at}.",
                        policies=[p.model_copy(deep=True) for p in meter.policies],
                        corridor=EmergencyCorridor(reason=reason),
                    )
                )

        parallels = _parallel_streets(context, segment)
        src = _cross_street(context, segment.source, approach) or segment.source
        dst = _cross_street(context, segment.destination, approach) or segment.destination
        blocked = "road" if incident.total_lanes and len(incident.affected_lanes) >= incident.total_lanes else "lane"
        plans.append(
            CandidatePlan(
                id="divert-advisory",
                name=f"Divert via {' / '.join(parallels)}" if parallels else f"Divert around {segment.name} {approach}",
                description=f"Advise {DIVERT_COMPLIANCE:.0%} of drivers headed through the blocked segment to divert "
                "(DMS sign + navigation alert).",
                reroutes=[
                    RerouteAction(
                        avoid_segment_ids=[segment.id],
                        compliance=DIVERT_COMPLIANCE,
                        reason=f"{blocked} blocked on {segment.name} {approach} between {src} and {dst}",
                    )
                ],
            )
        )
        if context.lessons and len(context.all_incidents) == 1:  # several incidents: combined plans lead instead
            plans = _apply_lessons(plans, context.lessons)
        return plans

    async def recommend(self, context: IncidentContext | None, results: list[SimulationCandidate]) -> Recommendation:
        """Pick one candidate with a deterministic rule.

        The choice depends only on the simulated outcomes, so ``context`` may be None (the learning
        scorecard reuses this rule as its "what should have won" reference).

        1. Only ``completed`` candidates with metrics count. None -> keep the baseline.
        2. EMS filter: a candidate is eligible only if the realised EMS response (``emergency_vehicle_eta``) is at
           most ``EMS_ETA_TOLERANCE`` x the baseline's. If the baseline responder never arrived (None) there is
           nothing to compare, so nobody is filtered; if only the candidate's responder failed to arrive, the
           candidate is ineligible. The baseline itself is always eligible, as the fallback.
        3. Take the eligible candidate with the lowest mean delay. Unless another eligible candidate (or that one)
           improves EMS response by >= ``EMS_GAIN_PREFERRED_S`` and has delay within ``DELAY_TRADEOFF`` of the best;
           then the biggest EMS improvement wins (ties: lower delay, then candidate order). If the baseline responder
           never arrived, its response counts as the whole horizon, so a candidate that gets it there scores a gain.
        4. The rationale gives before/after figures against the baseline and one line on why the runner-up
           (lowest-delay other completed candidate) lost.
        """
        done = [r for r in results if r.status is CandidateStatus.COMPLETED and r.metrics is not None]
        if not done:
            return Recommendation(candidate_id="baseline", summary="No candidate completed; keep current timing.")
        baseline = next((r for r in done if r.id == "baseline"), None)
        base_eta = baseline.metrics.emergency_vehicle_eta if baseline is not None else None

        def eligible(r: SimulationCandidate) -> bool:
            if r is baseline or base_eta is None:
                return True
            eta = r.metrics.emergency_vehicle_eta
            return eta is not None and eta <= base_eta * EMS_ETA_TOLERANCE

        # a baseline responder that never arrived took at least the whole horizon (a lower bound on the gain)
        reference = base_eta if base_eta is not None else baseline.metrics.window_s if baseline is not None else None

        def ems_gain(r: SimulationCandidate) -> float:
            eta = r.metrics.emergency_vehicle_eta
            return reference - eta if reference is not None and eta is not None else 0.0

        pool = [r for r in done if eligible(r)]
        best = min(pool, key=lambda r: r.metrics.mean_vehicle_delay)
        fast = [
            r
            for r in pool
            if ems_gain(r) >= EMS_GAIN_PREFERRED_S
            and r.metrics.mean_vehicle_delay <= best.metrics.mean_vehicle_delay * DELAY_TRADEOFF
        ]
        chosen = max(fast, key=lambda r: (ems_gain(r), -r.metrics.mean_vehicle_delay)) if fast else best
        others = sorted((r for r in done if r is not chosen), key=lambda r: r.metrics.mean_vehicle_delay)

        m = chosen.metrics
        if chosen is baseline:
            ems = f", EMS response {_mss(m.emergency_vehicle_eta)}" if m.emergency_vehicle_eta is not None else ""
            lead = "No candidate beat keeping current timing" if others else "No alternative candidate completed"
            rationale = [
                f"{lead} (mean delay {m.mean_vehicle_delay:.0f}s, max queue {m.max_queue_length} vehicles, "
                f"throughput {m.throughput:.0f} veh/h{ems})"
            ]
            summary = (
                "Keep current signal timing; no candidate beat it." if others else "No alternative completed; keep current timing."
            )
        else:
            summary = chosen.description
            if baseline is not None:
                rationale = _outcome_lines(baseline.metrics, m)
            else:
                rationale = [
                    f"No baseline completed; ranked on mean delay alone ({m.mean_vehicle_delay:.0f}s, "
                    f"max queue {m.max_queue_length} vehicles, throughput {m.throughput:.0f} veh/h)"
                ]
        if others:
            rationale.append(_why_lost(chosen, others[0], eligible(others[0])))
        return Recommendation(candidate_id=chosen.id, summary=summary, rationale=rationale)
