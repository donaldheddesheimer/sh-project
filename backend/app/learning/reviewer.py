"""Reviewer: turns an episode's scorecard into a lesson for the next incident.

It sees only the scorecard and the condensed episode (the situation, the plans tried, the plan applied), never
the analyst's reasoning, so a lesson rests on measured numbers. The verdict is always the scorecard's outcome,
computed by code; the reviewer writes the prose around it. The mock reviewer uses templates; a model reviewer
asks Claude or Nemotron for the prose and, when configured, falls back to the mock on any failure.
"""

from __future__ import annotations

import json
import logging
import re

from app.agent.chat import ChatClient
from app.learning.store import describe, plan_family
from app.models.domain import CandidateStatus, SimulationCandidate
from app.models.episode import IncidentFeatures, Lesson, PlanSummary, Scorecard
from app.models.scenario import ScenarioRun

log = logging.getLogger(__name__)

LARGE_ERROR_PCT = 20.0  # a prediction this far off is worth a lesson of its own
STALE_S = 60.0


def plan_kinds(c: SimulationCandidate) -> list[str]:
    kinds = (["timing"] if c.policies else []) + (["corridor"] if c.corridor else []) + (["diversion"] if c.reroutes else [])
    return kinds or ["none"]


def plan_summary(c: SimulationCandidate, baseline: SimulationCandidate | None) -> PlanSummary:
    if c.status is CandidateStatus.REJECTED:
        outcome = "rejected by the safety validator: " + "; ".join(c.violations)
    elif c.metrics is None:
        outcome = c.status.value + (f": {c.notes[-1]}" if c.notes else "")
    else:
        m = c.metrics
        ems = f"{m.emergency_vehicle_eta:.0f}s" if m.emergency_vehicle_eta is not None else "n/a"
        outcome = f"predicted delay {m.mean_vehicle_delay:.0f}s, max queue {m.max_queue_length}, EMS {ems}"
        if baseline is not None and baseline.metrics is not None and c is not baseline:
            outcome += f" (baseline {baseline.metrics.mean_vehicle_delay:.0f}s)"
    return PlanSummary(id=c.id, name=c.name or c.id, kinds=plan_kinds(c), outcome=outcome, status=c.status.value)


def condense(run: ScenarioRun, chosen_id: str) -> tuple[PlanSummary, list[PlanSummary]]:
    """The applied plan and every plan tried, one line each."""
    baseline = next((c for c in run.candidates if c.id == "baseline"), None)
    tried = [plan_summary(c, baseline) for c in run.candidates]
    chosen = next(p for p in tried if p.id == chosen_id)
    return chosen, tried


def _numbers(sc: Scorecard) -> str:
    text = f"realised mean delay {sc.realised.mean_delay:.0f}s"
    if sc.predicted_baseline is not None and (pct := sc.realised_vs_baseline.get("delay_pct")) is not None:
        text += f" vs {sc.predicted_baseline.mean_delay:.0f}s predicted for doing nothing ({pct:+.0f}%)"
    if sc.realised.ems_response_s is not None:
        text += f", EMS response {sc.realised.ems_response_s:.0f}s"
    return text


class MockReviewer:
    name = "mock"

    async def review(
        self, incidents: list[IncidentFeatures], chosen: PlanSummary, tried: list[PlanSummary], sc: Scorecard
    ) -> Lesson:
        where = "; ".join(describe(f) for f in incidents)
        family = plan_family(chosen.id)
        numbers = _numbers(sc)
        worked: list[str] = []
        didnt: list[str] = []
        next_time: list[str] = []
        if family == "baseline":
            summary = f"Kept current timing for {where}: {numbers}."
            next_time.append("Keeping the baseline teaches little: simulate a diversion or metering plan before settling on it")
        elif sc.outcome == "effective":
            summary = f"{chosen.name} worked for {where}: {numbers}."
            worked.append(f"{family} ({', '.join(chosen.kinds)}): {numbers}")
            next_time.append(f"Try {family} first for this situation")
        elif sc.outcome == "ineffective":
            summary = f"{chosen.name} did not deliver for {where}: {numbers}."
            didnt.append(f"{family} ({', '.join(chosen.kinds)}): {numbers}")
            alternative = f"; the rubric preferred {sc.best_by_rubric}" if sc.best_by_rubric not in (None, chosen.id) else ""
            next_time.append(f"Deprioritise {family} for this situation{alternative}")
        else:
            summary = f"{chosen.name} for {where}: no material difference measured ({numbers})."
            next_time.append(f"{family} is not decisive here; spend candidates on plans with a larger predicted gain")
        if sc.picked_best is False and sc.best_by_rubric:
            didnt.append(f"the mock rubric would have picked {sc.best_by_rubric} from the same results")
        didnt.extend(f"{p.name}: {p.outcome}" for p in tried if p.status == CandidateStatus.REJECTED.value)
        error = sc.prediction_error.get("delay_pct")
        if error is not None and abs(error) >= LARGE_ERROR_PCT:
            side = "optimistic" if error > 0 else "pessimistic"
            next_time.append(f"The twin was {side} by {abs(error):.0f}% on delay; treat small predicted gains with caution")
        if sc.staleness_s is not None and sc.staleness_s > STALE_S:
            next_time.append(f"The plan went live {sc.staleness_s:.0f}s after its snapshot; decide faster")
        confidence = {"effective": 0.7, "ineffective": 0.6, "inconclusive": 0.3}[sc.outcome]
        if (error is not None and abs(error) >= LARGE_ERROR_PCT) or (sc.staleness_s or 0) > STALE_S:
            confidence -= 0.15
        return Lesson(
            verdict=sc.outcome,
            summary=summary,
            what_worked=worked,
            what_didnt=didnt,
            next_time=next_time,
            confidence=round(min(0.95, max(0.05, confidence)), 2),
            reviewer=self.name,
        )


REVIEWER_PROMPT = """\
You review one incident-response episode of a city traffic operations center. An agent tested
response plans in a SUMO digital twin, one plan was applied to the live city, and the live city was
then measured. Write the lesson the next agent facing a similar crash should read.

The verdict is fixed by code: {verdict}. Do not argue with it; explain it. Use only the numbers given.
mean_delay is seconds lost per vehicle (lower is better); peak_queue counts halted vehicles on the
worst road; realised_vs_baseline compares what happened with what the twin predicted for doing nothing;
prediction_error is realised minus predicted for the applied plan; staleness_s is how long the city
kept changing between the snapshot and the moment the plan went live.

Answer with one JSON object and nothing else:
{{"summary": "one sentence that quotes numbers",
 "what_worked": ["..."], "what_didnt": ["..."],
 "next_time": ["short, actionable advice for this kind of crash"],
 "confidence": 0.0}}
"""


class ModelReviewer:
    def __init__(self, name: str, chat: ChatClient, fallback: MockReviewer | None):
        self.name = name
        self._chat = chat
        self._fallback = fallback

    async def review(
        self, incidents: list[IncidentFeatures], chosen: PlanSummary, tried: list[PlanSummary], sc: Scorecard
    ) -> Lesson:
        draft = await self._fallback.review(incidents, chosen, tried, sc) if self._fallback else None
        episode = {
            "situation": [describe(f) for f in incidents],
            "applied": chosen.model_dump(),
            "tried": [p.model_dump() for p in tried],
            "scorecard": sc.model_dump(exclude={"notes"}),
            "scorecard_notes": sc.notes,
        }
        messages = [
            {"role": "system", "content": REVIEWER_PROMPT.format(verdict=sc.outcome)},
            {"role": "user", "content": json.dumps(episode)},
        ]
        try:
            reply = await self._chat.chat(messages, max_tokens=2048)
            match = re.search(r"\{.*\}", reply.get("content") or "", re.DOTALL)
            data = json.loads(match.group(0)) if match else {}
            return Lesson(
                verdict=sc.outcome,  # code decides the verdict, the model only explains it
                summary=str(data["summary"]),
                what_worked=[str(x) for x in data.get("what_worked", [])][:5],
                what_didnt=[str(x) for x in data.get("what_didnt", [])][:5],
                next_time=[str(x) for x in data.get("next_time", [])][:5],
                confidence=min(0.95, max(0.05, float(data.get("confidence", draft.confidence if draft else 0.3)))),
                reviewer=self.name,
            )
        except Exception as exc:  # noqa: BLE001 - model and parsing failures follow the configured fallback policy
            if draft is None:
                raise
            log.warning("%s review failed, using the mock reviewer: %s", self.name, exc)
            return draft.model_copy(update={"reviewer": f"mock ({self.name} failed: {type(exc).__name__})"})
