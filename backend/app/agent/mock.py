"""Deterministic, rule-based stand-in for the Nemotron agent.

Proposes the classic incident-management signal responses for a blocked
link and recommends the best simulated outcome. No LLM involved.
"""

from __future__ import annotations

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext, Recommendation
from app.models.domain import CandidateStatus, PhaseKind, SignalPolicy, SignalProgram, SimulationCandidate

SPLIT_SHIFT_S = 15.0
STRONG_SHIFT_S = 25.0
EMS_ETA_TOLERANCE = 1.10  # a plan may not slow responders by more than 10%


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

        if upstream and (feed := _green_phase(upstream, approach)) is not None:
            cross = _cross_green_phase(upstream, approach)
            if cross is not None:
                plans.append(
                    CandidatePlan(
                        id="meter-upstream",
                        name=f"Meter {approach} inflow at {segment.source}",
                        description=f"Move {SPLIT_SHIFT_S:.0f}s of {approach} green to the cross street upstream so the queue "
                        "stays off the intersection and cross traffic keeps moving.",
                        policies=[_shift(upstream, cross, feed, SPLIT_SHIFT_S, "meter inflow toward the blocked link")],
                    )
                )
                plans.append(
                    CandidatePlan(
                        id="relieve-cross-street",
                        name=f"Favour cross street at {segment.source}",
                        description=f"Stronger metering: shift {STRONG_SHIFT_S:.0f}s to the cross street at the upstream intersection.",
                        policies=[_shift(upstream, cross, feed, STRONG_SHIFT_S, "protect cross-street flow from spillback")],
                    )
                )
        return plans

    async def recommend(self, context: IncidentContext, results: list[SimulationCandidate]) -> Recommendation:
        done = [r for r in results if r.status is CandidateStatus.COMPLETED and r.metrics is not None]
        baseline = next((r for r in done if r.id == "baseline"), None)
        if not done:
            return Recommendation(candidate_id="baseline", summary="No candidate completed; keep current timing.")

        def acceptable(r: SimulationCandidate) -> bool:
            if baseline is None or baseline.metrics.emergency_vehicle_eta is None:
                return True
            eta = r.metrics.emergency_vehicle_eta
            return eta is not None and eta <= baseline.metrics.emergency_vehicle_eta * EMS_ETA_TOLERANCE

        best = min((r for r in done if acceptable(r)), key=lambda r: r.metrics.mean_vehicle_delay, default=baseline)
        rationale = []
        if baseline is not None and best is not baseline:
            b, m = baseline.metrics, best.metrics
            rationale.append(f"Mean delay {b.mean_vehicle_delay:.0f}s -> {m.mean_vehicle_delay:.0f}s")
            rationale.append(f"Max queue {b.max_queue_length} -> {m.max_queue_length} vehicles")
            rationale.append(f"Throughput {b.throughput:.0f} -> {m.throughput:.0f} veh/h")
        return Recommendation(candidate_id=best.id, summary=best.description, rationale=rationale)
