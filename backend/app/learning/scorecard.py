"""Scorecard: what was predicted for an applied plan, what really happened, and how far apart they were.

Computed by code, never by the model; the reviewer only interprets it. Predicted and realised numbers are compared
on absolute simulation time over [implemented_at, min(implemented_at + monitor_s, snapshot + horizon)]. The
branches started at the snapshot, so over that stretch the predicted baseline is what doing nothing would have
given, while the plan went live later than in its branch: that delay (staleness) shows up as prediction error.
There is only one live timeline, hence no realised do-nothing counterfactual: "better" means better than the
predicted baseline.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.agent.mock import MockAgentProvider
from app.models.domain import CandidateStatus, MetricSample, SimulationCandidate
from app.models.episode import Implementation, LiveRecord, LiveSample, Outcome, ResponseCheck, Scorecard, WindowStats
from app.models.scenario import ScenarioRun

# Differences smaller than these are noise, not a lesson (timing plans move delay by about 1%).
MATERIAL_DELAY_PCT = 5.0
MATERIAL_QUEUE = 5
MATERIAL_EMS_S = 30.0
STALE_S = 60.0  # a plan applied this long after its snapshot was predicted for a noticeably different city
PROVISIONAL_CONFIDENCE_CAP = 0.4

_CORRIDOR_SUCCESS = re.compile(r"^(?P<count>[1-9]\d*) pre-emptions? \(.+\); longest hold \d+s$")
_DIVERSION_RESULT = re.compile(r"^(?P<count>\d+) vehicles? diverted over the horizon$")

_EMPTY = WindowStats(samples=0, mean_delay=0.0, end_delay=0.0, peak_queue=0, mean_throughput=0.0, mean_speed_mps=0.0)


def _corridor_check(notes: list[str], available: bool) -> ResponseCheck:
    disabled = next((note for note in notes if note.startswith("pre-emption disabled")), None)
    if disabled is not None:
        return ResponseCheck(kind="corridor", ok=False, detail=disabled)
    if "no pre-emptions" in notes:
        return ResponseCheck(kind="corridor", ok=False, detail="no pre-emptions")
    success = next((note for note in notes if _CORRIDOR_SUCCESS.fullmatch(note)), None)
    if success is not None:
        return ResponseCheck(kind="corridor", ok=True, detail=success)
    detail = "response notes did not contain corridor evidence" if available else "response notes unavailable"
    return ResponseCheck(kind="corridor", ok=None, detail=detail)


def _diversion_check(implementation: Implementation, available: bool) -> ResponseCheck:
    result = next((match for note in implementation.notes if (match := _DIVERSION_RESULT.fullmatch(note))), None)
    if result is not None:
        count = int(result.group("count"))
        return ResponseCheck(kind="diversion", ok=count > 0, detail=result.group(0))
    if implementation.diverted > 0:
        count = implementation.diverted
        return ResponseCheck(
            kind="diversion",
            ok=True,
            detail=f"{count} vehicle{'s' if count != 1 else ''} diverted when the response was applied",
        )
    if available:
        return ResponseCheck(kind="diversion", ok=False, detail="0 vehicles diverted")
    return ResponseCheck(kind="diversion", ok=None, detail="response notes unavailable")


def _response_checks(chosen: SimulationCandidate, implementation: Implementation, available: bool) -> list[ResponseCheck]:
    checks: list[ResponseCheck] = []
    if chosen.corridor is not None:
        checks.append(_corridor_check(implementation.notes, available))
    if chosen.reroutes:
        checks.append(_diversion_check(implementation, available))
    return checks


def _stats(samples: Sequence[MetricSample | LiveSample], ems: float | None) -> WindowStats | None:
    if not samples:
        return None
    n, last = len(samples), samples[-1]
    return WindowStats(
        samples=n,
        mean_delay=round(sum(s.delay for s in samples) / n, 1),
        end_delay=round(last.delay, 1),
        peak_queue=max(s.queue for s in samples),
        mean_throughput=round(sum(s.throughput for s in samples) / n, 1),
        mean_speed_mps=round(sum(s.speed for s in samples) / n, 2),
        incident_queue_end=getattr(last, "incident_queue", 0),  # branch timelines do not carry it
        ems_response_s=ems,
    )


def _slope_per_min(points: list[tuple[float, float]]) -> float | None:
    """Least-squares slope per simulated minute."""
    if len(points) < 2:
        return None
    n = len(points)
    mean_t = sum(t for t, _ in points) / n
    mean_v = sum(v for _, v in points) / n
    var = sum((t - mean_t) ** 2 for t, _ in points)
    if var == 0:
        return None
    return round(60.0 * sum((t - mean_t) * (v - mean_v) for t, v in points) / var, 2)


def _versus(a: WindowStats | None, b: WindowStats | None) -> dict[str, float | None]:
    """``a`` minus ``b``: delay in percent of b, peak queue in vehicles, EMS response in seconds."""
    if a is None or b is None or not a.samples or not b.samples:
        return {}
    ems = None
    if a.ems_response_s is not None and b.ems_response_s is not None:
        ems = round(a.ems_response_s - b.ems_response_s, 1)
    return {
        "delay_pct": round(100.0 * (a.mean_delay - b.mean_delay) / b.mean_delay, 1) if b.mean_delay else None,
        "queue": float(a.peak_queue - b.peak_queue),
        "ems_s": ems,
    }


def _beyond(d: dict[str, float | None], sign: int) -> bool:
    """Does any metric differ by a material amount in the given direction (-1 better, +1 worse)?"""
    limits = {"delay_pct": MATERIAL_DELAY_PCT, "queue": MATERIAL_QUEUE, "ems_s": MATERIAL_EMS_S}
    return any(d.get(key) is not None and sign * d[key] >= limit for key, limit in limits.items())


def _eta(candidate: SimulationCandidate | None) -> float | None:
    return candidate.metrics.emergency_vehicle_eta if candidate and candidate.metrics else None


async def build_scorecard(
    run: ScenarioRun,
    implementation: Implementation,
    record: LiveRecord,
    detected_at: float,
    *,
    response_notes_available: bool = False,
) -> Scorecard:
    chosen = next(c for c in run.candidates if c.id == implementation.candidate_id)
    baseline = next((c for c in run.candidates if c.id == "baseline"), None)
    start = implementation.implemented_at
    end = start + record.monitor_s
    if run.snapshot_sim_time is not None:
        end = min(end, run.snapshot_sim_time + run.horizon_s)

    def within(samples: Sequence[MetricSample | LiveSample]) -> list:
        return [s for s in samples if start <= s.t <= end]

    compared = within(record.post)
    realised = _stats(compared, record.ems_response_s) or _stats(record.post, record.ems_response_s) or _EMPTY
    predicted = _stats(within(chosen.timeline), _eta(chosen))
    predicted_baseline = _stats(within(baseline.timeline), _eta(baseline)) if baseline else None
    pre_samples = [s for s in record.pre if detected_at <= s.t <= start]

    predicted_gain = _versus(predicted, predicted_baseline)
    realised_vs_baseline = _versus(realised, predicted_baseline) if compared else {}
    material = _beyond(predicted_gain, -1)
    best = await MockAgentProvider().recommend(None, run.candidates)
    checks = _response_checks(chosen, implementation, response_notes_available)

    notes: list[str] = []
    staleness = implementation.staleness_s
    if staleness is not None and staleness > STALE_S:
        notes.append(f"the plan went live {staleness:.0f}s after the snapshot, so the branches predicted an earlier city")
    if compared and end - start < record.monitor_s:
        notes.append(f"compared over {end - start:.0f}s: the branches' horizon ended before the monitor window did")

    outcome: Outcome
    if not record.complete:
        outcome = "inconclusive"
        notes.append(f"monitor window incomplete: {record.abort_reason}")
    elif chosen.id == "baseline":
        outcome = "inconclusive"
        notes.append("doing nothing has no live counterfactual; the prediction error is the lesson")
    elif not realised_vs_baseline:
        outcome = "inconclusive"
        notes.append("no predicted baseline overlaps the monitor window, so nothing could be compared")
    elif _beyond(realised_vs_baseline, -1) and not _beyond(realised_vs_baseline, +1):
        outcome = "effective"
    elif material or _beyond(realised_vs_baseline, +1):
        outcome = "ineffective"
    else:
        outcome = "inconclusive"

    return Scorecard(
        candidate_id=chosen.id,
        candidate_name=chosen.name or chosen.id,
        window_s=round(max(0.0, end - start), 1),
        realised=realised,
        predicted=predicted,
        predicted_baseline=predicted_baseline,
        pre=_stats(pre_samples, None),
        delay_slope_pre_per_min=_slope_per_min([(s.t, s.delay) for s in pre_samples]),
        delay_slope_post_per_min=_slope_per_min([(s.t, s.delay) for s in record.post]),
        queue_slope_pre_per_min=_slope_per_min([(s.t, float(s.queue)) for s in pre_samples]),
        queue_slope_post_per_min=_slope_per_min([(s.t, float(s.queue)) for s in record.post]),
        predicted_gain=predicted_gain,
        prediction_error=_versus(realised, predicted) if compared else {},
        realised_vs_baseline=realised_vs_baseline,
        staleness_s=staleness,
        candidates_tried=len(run.candidates),
        rejected=sum(c.status is CandidateStatus.REJECTED for c in run.candidates),
        picked_best=best.candidate_id == chosen.id,
        best_by_rubric=best.candidate_id,
        material=material,
        outcome=outcome,
        checks=checks,
        provisional=any(check.ok is not True for check in checks),
        notes=notes,
    )
