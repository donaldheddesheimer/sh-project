"""Episode memory: one markdown file per remembered episode, a generated playbook, and structured recall.

A completed episode becomes ``<memory_dir>/episodes/EP-NNNN.md``: a JSON front-matter block holding the whole
``Experience`` (so nothing needs a YAML dependency) and a readable body, so people can read and diff it.
``playbook.md`` is a digest of every lesson, regenerated on each save. Recall ranks past episodes by a trust-aware
score, then by recency and by the lesson's confidence. Structured similarity remains explicit in the recall
contract so optional semantic recall can improve ranking later without gaining pruning authority.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable

from app.agent.base import CandidatePlan
from app.learning.embeddings import NimEmbedder
from app.learning.scorecard import MATERIAL_DELAY_PCT, MATERIAL_EMS_S, MATERIAL_QUEUE, PROVISIONAL_CONFIDENCE_CAP
from app.models.domain import Incident
from app.models.episode import (
    Experience,
    IncidentFeatures,
    LearningComparison,
    LearningReport,
    LearningReportEpisode,
    RecalledExperience,
    ResponseCheck,
)
from app.simulation.network import RoadNetwork

log = logging.getLogger(__name__)

# Similarity weights (sum 1.0). Another crash on the same segment scores 1.0, the same street in the other
# direction about 0.65, a crash on a street feeding the same intersection about 0.55.
W_TYPE = 0.15
W_SEGMENT, W_STREET_DIRECTION, W_STREET = 0.35, 0.2, 0.1
W_SEVERITY = 0.1
W_BLOCKED_SHARE = 0.1
W_INTERSECTION = 0.1  # shares the upstream or downstream intersection
W_LANE = 0.1  # the same lane(s) blocked
W_COUNT = 0.1  # as many incidents at once
MIN_SIMILARITY = 0.3  # less alike than this is not worth recalling
TRUSTED_MATCH = 0.75  # only a trusted structured match this close may prune mock candidates
CONFIRMATION_COUNT = 2  # distinct verified replications needed to trust a provisional lesson at recall time
PROVISIONAL_RANKING_FACTOR = 0.75
RECALL_LIMIT = 3
PLAYBOOK_LIMIT = 20
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_EPISODE_FILE = re.compile(r"EP-(\d+)\.md$")


def incident_features(incident: Incident, network: RoadNetwork) -> IncidentFeatures:
    seg = network.segments.get(incident.location.segment_id or "")
    return IncidentFeatures(
        incident_id=incident.id,
        type=incident.type.value,
        severity=incident.severity.value,
        segment_id=incident.location.segment_id or "",
        street=seg.name if seg else "",
        direction=seg.direction if seg else "",
        upstream=seg.source if seg else "",
        downstream=seg.destination if seg else "",
        blocked_lanes=list(incident.affected_lanes),
        total_lanes=incident.total_lanes,
    )


def lane_name(lane: int, total_lanes: int | None) -> str:
    """'right lane', 'left lane' or 'lane 2'. Only the outermost lanes have a name, and which index is the left
    one depends on how wide the road is: on a 3-lane road lane 1 is an interior lane, not the left one."""
    if lane == 0:
        return "right lane"
    if total_lanes and lane == total_lanes - 1:
        return "left lane"
    return f"lane {lane + 1}"


def describe(f: IncidentFeatures) -> str:
    """'collision major, Main St EB, right lane blocked'"""
    if f.total_lanes and len(f.blocked_lanes) >= f.total_lanes:
        blocked = "all lanes"
    else:
        blocked = ", ".join(lane_name(lane, f.total_lanes) for lane in f.blocked_lanes) or "no lane"
    return f"{f.type} {f.severity}, {f.street} {f.direction}, {blocked} blocked"


def _share(f: IncidentFeatures) -> float | None:
    return len(f.blocked_lanes) / f.total_lanes if f.total_lanes else None


def _pair(a: IncidentFeatures, b: IncidentFeatures) -> float:
    score = W_TYPE if a.type == b.type else 0.0
    if a.segment_id == b.segment_id:
        score += W_SEGMENT
    elif a.street == b.street and a.direction == b.direction:
        score += W_STREET_DIRECTION
    elif a.street == b.street:
        score += W_STREET
    score += W_SEVERITY if a.severity == b.severity else 0.0
    share_a, share_b = _share(a), _share(b)
    if share_a is not None and share_b is not None and abs(share_a - share_b) < 0.01:
        score += W_BLOCKED_SHARE
    if {a.upstream, a.downstream} & {b.upstream, b.downstream} - {""}:
        score += W_INTERSECTION
    score += W_LANE if a.blocked_lanes == b.blocked_lanes else 0.0
    return score


def similarity(current: list[IncidentFeatures], past: list[IncidentFeatures]) -> float:
    """0..1: every current incident is matched to its most alike past incident; the incident count counts too."""
    if not current or not past:
        return 0.0
    matched = sum(max(_pair(c, p) for p in past) for c in current) / len(current)
    return round(matched + (W_COUNT if len(current) == len(past) else 0.0), 3)


def plan_family(plan_id: str) -> str:
    """'INC-0002:divert-advisory' -> 'divert-advisory' (plans for several incidents carry the incident prefix)."""
    return plan_id.split(":", 1)[-1]


def _experience_text(exp: Experience) -> str:
    """The durable meaning of an episode, deliberately excluding noisy numeric outcomes."""
    situation = "; ".join(describe(incident) for incident in exp.incidents)
    guidance = " ".join(exp.lesson.next_time)
    return (
        f"Situation: {situation}. Plan family: {plan_family(exp.chosen.id)}. "
        f"Verdict: {exp.lesson.verdict}. Next time: {guidance}"
    )


def _query_text(current: list[IncidentFeatures], standing: list[CandidatePlan]) -> str:
    situation = "; ".join(describe(incident) for incident in current)
    active = ", ".join(plan_family(plan.id) for plan in standing) or "none"
    return f"Situation: {situation}. Standing responses: {active}."


def _input_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validated_vector(value: object) -> list[float] | None:
    if not isinstance(value, list) or not value:
        return None
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError):
        return None
    return vector if all(math.isfinite(item) for item in vector) else None


def _cosine(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right):
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return None
    cosine = sum(a * b for a, b in zip(left, right)) / (left_norm * right_norm)
    return max(0.0, min(1.0, cosine))


def _report_episode(exp: Experience, by_id: dict[str, Experience]) -> LearningReportEpisode:
    recalled_sources = list(exp.recalled)
    transfer = any(source in by_id and by_id[source].script_id != exp.script_id for source in recalled_sources)
    scorecard = exp.scorecard
    return LearningReportEpisode(
        id=exp.id,
        script_id=exp.script_id,
        analyst=exp.analyst,
        memory_mode=exp.memory_mode,
        eligible_for_recall=exp.eligible_for_recall,
        recalled_sources=recalled_sources,
        recall_provenance=exp.recall_provenance,
        warm=bool(recalled_sources),
        transfer=transfer,
        candidate_order=[plan.id for plan in exp.tried],
        rounds=exp.rounds,
        candidates_tried=scorecard.candidates_tried,
        analysis_wall_s=exp.analysis_wall_s,
        selected_plan=exp.chosen.id,
        verdict=exp.lesson.verdict,
        delay_vs_baseline_pct=scorecard.realised_vs_baseline.get("delay_pct"),
        prediction_error=scorecard.prediction_error,
        staleness_s=scorecard.staleness_s,
        checks=scorecard.checks,
        provisional=scorecard.provisional,
    )


def _comparison(warm: Experience, control: Experience, by_id: dict[str, Experience]) -> LearningComparison:
    transfer_sources = [
        by_id[source]
        for source in warm.recalled
        if source in by_id and by_id[source].script_id != warm.script_id
    ]
    behavior: list[str] = []
    for source in transfer_sources:
        family = plan_family(source.chosen.id)
        warm_position = _family_position(warm, family)
        control_position = _family_position(control, family)
        if source.lesson.verdict == "effective" and warm_position is not None and (
            control_position is None or warm_position < control_position
        ):
            behavior.append(f"{family} moved earlier")
        if source.lesson.verdict == "ineffective" and (
            warm_position is None or (control_position is not None and warm_position > control_position)
        ):
            behavior.append(f"{family} moved later or was omitted")
    if warm.scorecard.candidates_tried < control.scorecard.candidates_tried:
        behavior.append("fewer candidates tried")
    if warm.rounds < control.rounds:
        behavior.append("fewer simulation rounds")
    if control.lesson.verdict == "ineffective" and warm.lesson.verdict in ("inconclusive", "effective"):
        behavior.append("recommendation verdict improved")
    delay_improved = _material_delay_improvement(warm, control)
    if delay_improved:
        behavior.append("realised delay improved without a material queue or EMS regression")
    transfer = bool(transfer_sources)
    useful = bool(behavior) if transfer else None
    return LearningComparison(
        script_id=warm.script_id,
        warm_episode_id=warm.id,
        control_episode_id=control.id,
        transfer=transfer,
        useful=useful,
        behavior_changes=behavior,
        deltas={
            "rounds": float(warm.rounds - control.rounds),
            "candidates_tried": float(warm.scorecard.candidates_tried - control.scorecard.candidates_tried),
            "analysis_wall_s": _delta(warm.analysis_wall_s, control.analysis_wall_s),
            "delay_vs_baseline_pct": _delta(
                warm.scorecard.realised_vs_baseline.get("delay_pct"),
                control.scorecard.realised_vs_baseline.get("delay_pct"),
            ),
            "prediction_error_delay_pct": _delta(
                warm.scorecard.prediction_error.get("delay_pct"), control.scorecard.prediction_error.get("delay_pct")
            ),
            "staleness_s": _delta(warm.scorecard.staleness_s, control.scorecard.staleness_s),
        },
    )


def _family_position(exp: Experience, family: str) -> int | None:
    return next((index for index, plan in enumerate(exp.tried) if plan_family(plan.id) == family), None)


def _material_delay_improvement(warm: Experience, control: Experience) -> bool:
    warm_metrics = warm.scorecard.realised_vs_baseline
    control_metrics = control.scorecard.realised_vs_baseline
    delay_delta = _delta(warm_metrics.get("delay_pct"), control_metrics.get("delay_pct"))
    if delay_delta is None or delay_delta > -MATERIAL_DELAY_PCT:
        return False
    queue_delta = _delta(warm_metrics.get("queue"), control_metrics.get("queue"))
    ems_delta = _delta(warm_metrics.get("ems_s"), control_metrics.get("ems_s"))
    return (queue_delta is None or queue_delta < MATERIAL_QUEUE) and (ems_delta is None or ems_delta < MATERIAL_EMS_S)


def _delta(current: float | None, prior: float | None) -> float | None:
    return round(current - prior, 1) if current is not None and prior is not None else None


@dataclass(frozen=True)
class _ScoredExperience:
    experience: Experience
    structured_score: float
    semantic_score: float | None
    combined_score: float
    ranking_score: float
    trusted: bool


def _checks_succeeded(exp: Experience) -> bool:
    checks = exp.scorecard.checks
    return bool(checks) and all(check.ok is True for check in checks)


def _normalize_legacy_trust(exp: Experience) -> Experience:
    """Treat a legacy high-impact lesson without checks as provisional without rewriting its markdown file."""
    if exp.scorecard.checks:
        return exp
    kinds = [kind for kind in ("corridor", "diversion") if kind in exp.chosen.kinds]
    if not kinds:
        return exp
    checks = [ResponseCheck(kind=kind, ok=None, detail="legacy memory has no response evidence") for kind in kinds]
    scorecard = exp.scorecard.model_copy(update={"checks": checks, "provisional": True})
    lesson = exp.lesson.model_copy(update={"confidence": min(exp.lesson.confidence, PROVISIONAL_CONFIDENCE_CAP)})
    return exp.model_copy(update={"scorecard": scorecard, "lesson": lesson})


def _confirmed_by_replication(
    provisional: Experience, scored: list[tuple[float, Experience]]
) -> bool:
    """Trust a provisional lesson only after two close, verified replications support it for this recall query."""
    family = plan_family(provisional.chosen.id)
    supporters = {
        other.id
        for structured, other in scored
        if other.id != provisional.id
        and structured >= TRUSTED_MATCH
        and plan_family(other.chosen.id) == family
        and other.lesson.verdict == provisional.lesson.verdict
        and _checks_succeeded(other)
    }
    return len(supporters) >= CONFIRMATION_COUNT


def _score_recall(
    exp: Experience,
    structured: float,
    semantic: float | None,
    all_scores: list[tuple[float, Experience]],
) -> _ScoredExperience:
    trusted = not exp.scorecard.provisional or _confirmed_by_replication(exp, all_scores)
    combined = structured
    if semantic is not None:
        combined = max(structured, min(0.74, 0.65 * structured + 0.35 * semantic))
    ranking = combined if trusted else combined * PROVISIONAL_RANKING_FACTOR
    return _ScoredExperience(
        experience=exp,
        structured_score=structured,
        semantic_score=semantic,
        combined_score=combined,
        ranking_score=ranking,
        trusted=trusted,
    )


def recalled_view(scored: _ScoredExperience) -> RecalledExperience:
    exp = scored.experience
    sc = exp.scorecard
    return RecalledExperience(
        id=exp.id,
        similarity=scored.ranking_score,
        structured_score=scored.structured_score,
        semantic_score=scored.semantic_score,
        combined_score=scored.combined_score,
        ranking_score=scored.ranking_score,
        provisional=sc.provisional,
        trusted=scored.trusted,
        incidents=[describe(f) for f in exp.incidents],
        chosen=plan_family(exp.chosen.id),
        chosen_name=exp.chosen.name,
        kinds=exp.chosen.kinds,
        verdict=exp.lesson.verdict,
        summary=exp.lesson.summary,
        what_worked=exp.lesson.what_worked,
        what_didnt=exp.lesson.what_didnt,
        next_time=exp.lesson.next_time,
        numbers={
            "realised_delay_s": sc.realised.mean_delay,
            "predicted_delay_s": sc.predicted.mean_delay if sc.predicted else None,
            "baseline_delay_s": sc.predicted_baseline.mean_delay if sc.predicted_baseline else None,
            "delay_vs_baseline_pct": sc.realised_vs_baseline.get("delay_pct"),
            "ems_response_s": sc.realised.ems_response_s,
            "staleness_s": sc.staleness_s,
        },
    )


class ExperienceStore:
    def __init__(
        self,
        directory: Path,
        enabled: bool = True,
        embedder: NimEmbedder | None = None,
        on_embedding_event: Callable[[str], None] | None = None,
    ):
        self.directory = directory
        self.enabled = enabled
        self._episodes = directory / "episodes"
        self._embedder = embedder
        self._on_embedding_event = on_embedding_event
        self._embedding_outage = False

    # ------------------------------------------------------------ read side

    def load(self) -> list[Experience]:
        """Every readable remembered episode, oldest first."""
        if not self._episodes.is_dir():
            return []
        found: list[Experience] = []
        for path in sorted(self._episodes.glob("EP-*.md")):
            match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
            try:
                found.append(_normalize_legacy_trust(Experience.model_validate_json(match.group(1))))
            except (AttributeError, ValueError):  # no front matter, or edited into something invalid
                log.warning("skipping unreadable memory file %s", path)
        return sorted(found, key=lambda e: e.created_at)

    async def recall(
        self,
        current: list[IncidentFeatures],
        standing: list[CandidatePlan] | None = None,
        limit: int = RECALL_LIMIT,
        query_cache: dict[str, list[float]] | None = None,
    ) -> list[RecalledExperience]:
        """Rank eligible memory without ever letting semantic similarity grant pruning authority."""
        if not self.enabled or not current:
            return []
        experiences = [exp for exp in self.load() if exp.eligible_for_recall]
        if not experiences:
            return []
        structured = [(similarity(current, exp.incidents), exp) for exp in experiences]
        semantics = await self._semantic_scores(current, standing or [], experiences, query_cache)
        scored = [_score_recall(exp, score, semantics.get(exp.id), structured) for score, exp in structured]
        scored = [item for item in scored if item.ranking_score >= MIN_SIMILARITY]
        scored.sort(
            key=lambda item: (item.ranking_score, item.experience.created_at, item.experience.lesson.confidence),
            reverse=True,
        )
        return [recalled_view(item) for item in scored[:limit]]

    def playbook(self) -> str:
        path = self.directory / "playbook.md"
        return path.read_text(encoding="utf-8") if self.enabled and path.is_file() else ""

    def next_number(self) -> int:
        """Episode ids continue after the highest remembered one, so files survive restarts."""
        if not self._episodes.is_dir():
            return 1
        numbers = [int(m.group(1)) for p in self._episodes.iterdir() if (m := _EPISODE_FILE.search(p.name))]
        return max(numbers, default=0) + 1

    def stats(self) -> dict:
        episodes = self.load() if self.enabled else []
        return {
            "enabled": self.enabled,
            "directory": str(self.directory),
            "episodes": len(episodes),
            "latest": [e.id for e in reversed(episodes)][:10],
        }

    def report(self) -> LearningReport:
        """Summarise durable episodes and pair warm runs with same-script controls without inventing a conclusion."""
        experiences = self.load() if self.enabled else []
        by_id = {exp.id: exp for exp in experiences}
        rows = [_report_episode(exp, by_id) for exp in experiences]
        controls_by_script: dict[str | None, list[Experience]] = {}
        for exp in experiences:
            if exp.memory_mode == "ignore":
                controls_by_script.setdefault(exp.script_id, []).append(exp)
        comparisons: list[LearningComparison] = []
        for exp in experiences:
            if exp.memory_mode != "use" or not exp.recalled:
                continue
            controls = controls_by_script.get(exp.script_id, [])
            if not controls:
                continue
            control = min(controls, key=lambda other: abs((other.created_at - exp.created_at).total_seconds()))
            comparisons.append(_comparison(exp, control, by_id))
        return LearningReport(episodes=rows, comparisons=comparisons)

    # ----------------------------------------------------------- write side

    async def save(self, exp: Experience) -> Path:
        """Persist the durable markdown first, then best-effort embed it as a passage."""
        path = await asyncio.to_thread(self._save_durable, exp)
        await self._embed_experiences([exp])
        return path

    def _save_durable(self, exp: Experience) -> Path:
        self._episodes.mkdir(parents=True, exist_ok=True)
        path = self._episodes / f"{exp.id}.md"
        path.write_text(f"---\n{exp.model_dump_json(indent=1)}\n---\n{_body(exp)}", encoding="utf-8")
        self._write_playbook()
        return path

    def clear(self) -> int:
        """Forget everything (a cold run). Returns how many episodes were deleted."""
        removed = 0
        if self._episodes.is_dir():
            for path in self._episodes.glob("EP-*.md"):
                path.unlink()
                removed += 1
            for path in self._episodes.glob("EP-*.vec.json"):
                path.unlink()
        (self.directory / "playbook.md").unlink(missing_ok=True)
        return removed

    def _write_playbook(self) -> None:
        experiences = [exp for exp in reversed(self.load()) if exp.eligible_for_recall][:PLAYBOOK_LIMIT]
        lines = [
            "# Playbook",
            "",
            f"Generated from the {len(experiences)} most recent remembered episodes, newest first. Lessons seed the "
            "first round only: every plan is still validated and simulated.",
            "",
        ]
        for exp in experiences:
            vs_base = exp.scorecard.realised_vs_baseline.get("delay_pct")
            effect = f"delay {vs_base:+.0f}% vs doing nothing" if vs_base is not None else "no comparable numbers"
            trust = "provisional" if exp.scorecard.provisional else "trusted"
            lines.append(
                f"- **{exp.id}** · {'; '.join(describe(f) for f in exp.incidents)} · applied "
                f"`{plan_family(exp.chosen.id)}` ({', '.join(exp.chosen.kinds)}) · **{exp.lesson.verdict}** "
                f"({trust}), {effect}."
            )
            lines.extend(f"  - Next time: {tip}" for tip in exp.lesson.next_time[:2])
        (self.directory / "playbook.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --------------------------------------------------------- semantic recall

    async def _semantic_scores(
        self,
        current: list[IncidentFeatures],
        standing: list[CandidatePlan],
        experiences: list[Experience],
        query_cache: dict[str, list[float]] | None,
    ) -> dict[str, float]:
        """Return cosine scores, or no scores at all when optional embeddings are unavailable."""
        if self._embedder is None:
            return {}
        vectors = await self._experience_vectors(experiences)
        if vectors is None:
            return {}
        query = _query_text(current, standing)
        key = _input_hash(query)
        query_vector = (query_cache or {}).get(key)
        if query_vector is None:
            found = await self._request_vectors([query], "query")
            if found is None:
                return {}
            query_vector = found[0]
            if query_cache is not None:
                query_cache[key] = query_vector
        return {
            episode_id: score
            for episode_id, vector in vectors.items()
            if (score := _cosine(query_vector, vector)) is not None
        }

    async def _experience_vectors(self, experiences: list[Experience]) -> dict[str, list[float]] | None:
        cached: dict[str, list[float]] = {}
        stale: list[Experience] = []
        for exp in experiences:
            vector = self._read_vector(exp)
            if vector is None:
                stale.append(exp)
            else:
                cached[exp.id] = vector
        if not stale:
            return cached
        vectors = await self._request_vectors([_experience_text(exp) for exp in stale], "passage")
        if vectors is None:
            return None
        await self._store_vectors(stale, vectors)
        cached.update({exp.id: vector for exp, vector in zip(stale, vectors)})
        return cached

    async def _embed_experiences(self, experiences: list[Experience]) -> None:
        """Best-effort post-save passage embeddings; a failure leaves durable memory usable."""
        if self._embedder is None or not experiences:
            return
        vectors = await self._request_vectors([_experience_text(exp) for exp in experiences], "passage")
        if vectors is not None:
            await self._store_vectors(experiences, vectors)

    async def _store_vectors(self, experiences: list[Experience], vectors: list[list[float]]) -> None:
        try:
            await asyncio.to_thread(self._write_vectors, experiences, vectors)
        except OSError as exc:
            log.warning("could not write embedding sidecars: %s", exc)

    async def _request_vectors(self, texts: list[str], input_type: str) -> list[list[float]] | None:
        try:
            vectors = await self._embedder.embed(texts, input_type) if self._embedder else None
        except Exception as exc:  # noqa: BLE001 - optional network capability cannot fail an analysis
            self._embedding_failed(exc)
            return None
        if vectors is None:
            return None
        self._embedding_recovered()
        return vectors

    def _read_vector(self, exp: Experience) -> list[float] | None:
        if self._embedder is None:
            return None
        path = self._vector_path(exp)
        try:
            sidecar = json.loads(path.read_text(encoding="utf-8"))
            if sidecar.get("model") != self._embedder.model or sidecar.get("input_hash") != _input_hash(_experience_text(exp)):
                return None
            vector = sidecar.get("vector")
            return _validated_vector(vector)
        except (OSError, TypeError, ValueError):
            return None

    def _write_vectors(self, experiences: list[Experience], vectors: list[list[float]]) -> None:
        for exp, vector in zip(experiences, vectors):
            self._episodes.mkdir(parents=True, exist_ok=True)
            sidecar = {"model": self._embedder.model, "input_hash": _input_hash(_experience_text(exp)), "vector": vector}
            self._vector_path(exp).write_text(json.dumps(sidecar, separators=(",", ":")) + "\n", encoding="utf-8")

    def _vector_path(self, exp: Experience) -> Path:
        return self._episodes / f"{exp.id}.vec.json"

    def _embedding_failed(self, exc: Exception) -> None:
        if self._embedding_outage:
            return
        self._embedding_outage = True
        log.warning("embedding recall unavailable: %s", exc)
        self._emit_embedding_event("Embedding recall unavailable; using structured memory only")

    def _embedding_recovered(self) -> None:
        if not self._embedding_outage:
            return
        self._embedding_outage = False
        log.info("embedding recall recovered")
        self._emit_embedding_event("Embedding recall recovered")

    def _emit_embedding_event(self, message: str) -> None:
        if self._on_embedding_event is not None:
            self._on_embedding_event(message)


def _num(value: float | None, unit: str = "", fmt: str = ".0f") -> str:
    return f"{value:{fmt}}{unit}" if value is not None else "n/a"


def _body(exp: Experience) -> str:
    sc, lesson = exp.scorecard, exp.lesson
    lines = [
        f"# {exp.id}: {lesson.summary}",
        "",
        f"**Situation.** {'; '.join(describe(f) for f in exp.incidents)}. Script: {exp.script_id or 'none'}. "
        f"Analyst: {exp.analyst}. Memory mode: {exp.memory_mode} "
        f"({'eligible for recall' if exp.eligible_for_recall else 'control; not eligible for recall'}). "
        f"Lessons recalled: {', '.join(exp.recalled) or 'none'}.",
        "",
        f"**What was tried** ({exp.rounds} round{'s' if exp.rounds != 1 else ''}, "
        f"{sc.candidates_tried} candidates, {sc.rejected} rejected):",
    ]
    lines.extend(f"- {p.name} ({', '.join(p.kinds)}): {p.outcome}" for p in exp.tried)
    lines += [
        "",
        f"**Applied.** {exp.chosen.name} (`{exp.chosen.id}`), {_num(sc.staleness_s, ' s')} after the snapshot. "
        f"Compared over {_num(sc.window_s, ' s')} of simulated time.",
        "",
        "| | mean delay | peak queue | EMS response |",
        "|---|---|---|---|",
        f"| realised | {_num(sc.realised.mean_delay, ' s')} | {sc.realised.peak_queue} | "
        f"{_num(sc.realised.ems_response_s, ' s')} |",
    ]
    for label, stats in (("predicted", sc.predicted), ("predicted baseline", sc.predicted_baseline)):
        if stats is not None:
            lines.append(
                f"| {label} | {_num(stats.mean_delay, ' s')} | {stats.peak_queue} | {_num(stats.ems_response_s, ' s')} |"
            )
    lines += [
        "",
        f"**Lesson ({lesson.verdict}, {'provisional' if sc.provisional else 'trusted'}, "
        f"confidence {lesson.confidence:.2f}, reviewer {lesson.reviewer}).** {lesson.summary}",
    ]
    if sc.checks:
        lines += [
            "",
            "Response checks:",
            *(
                f"- {check.kind}: "
                f"{'passed' if check.ok is True else 'failed' if check.ok is False else 'unknown'} — {check.detail}"
                for check in sc.checks
            ),
        ]
    for title, items in (("What worked", lesson.what_worked), ("What didn't", lesson.what_didnt),
                         ("Next time", lesson.next_time)):
        if items:
            lines += ["", f"{title}:", *(f"- {item}" for item in items)]
    if sc.notes:
        lines += ["", "Scorecard notes:", *(f"- {note}" for note in sc.notes)]
    return "\n".join(lines) + "\n"
