"""Episode memory: one markdown file per remembered episode, a generated playbook, and structured recall.

A completed episode becomes ``<memory_dir>/episodes/EP-NNNN.md``: a JSON front-matter block holding the whole
``Experience`` (so nothing needs a YAML dependency) and a readable body, so people can read and diff it.
``playbook.md`` is a digest of every lesson, regenerated on each save. Recall ranks past episodes by how similar
the situation is, then by recency and by the lesson's confidence. Embedding-based recall (an NVIDIA embedding NIM)
can later replace ``similarity`` behind the same ``recall`` call.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.models.domain import Incident
from app.models.episode import Experience, IncidentFeatures, RecalledExperience
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


def recalled_view(exp: Experience, score: float) -> RecalledExperience:
    sc = exp.scorecard
    return RecalledExperience(
        id=exp.id,
        similarity=score,
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
    def __init__(self, directory: Path, enabled: bool = True):
        self.directory = directory
        self.enabled = enabled
        self._episodes = directory / "episodes"

    # ------------------------------------------------------------ read side

    def load(self) -> list[Experience]:
        """Every readable remembered episode, oldest first."""
        if not self._episodes.is_dir():
            return []
        found: list[Experience] = []
        for path in sorted(self._episodes.glob("EP-*.md")):
            match = _FRONT_MATTER.match(path.read_text(encoding="utf-8"))
            try:
                found.append(Experience.model_validate_json(match.group(1)))
            except (AttributeError, ValueError):  # no front matter, or edited into something invalid
                log.warning("skipping unreadable memory file %s", path)
        return sorted(found, key=lambda e: e.created_at)

    def recall(self, current: list[IncidentFeatures], limit: int = RECALL_LIMIT) -> list[RecalledExperience]:
        """The most similar remembered episodes, then the most recent, then the most confident."""
        if not self.enabled or not current:
            return []
        scored = [(similarity(current, e.incidents), e) for e in self.load()]
        scored = [(s, e) for s, e in scored if s >= MIN_SIMILARITY]
        scored.sort(key=lambda se: (se[0], se[1].created_at, se[1].lesson.confidence), reverse=True)
        return [recalled_view(e, s) for s, e in scored[:limit]]

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

    # ----------------------------------------------------------- write side

    def save(self, exp: Experience) -> Path:
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
        (self.directory / "playbook.md").unlink(missing_ok=True)
        return removed

    def _write_playbook(self) -> None:
        experiences = list(reversed(self.load()))[:PLAYBOOK_LIMIT]
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
            lines.append(
                f"- **{exp.id}** · {'; '.join(describe(f) for f in exp.incidents)} · applied "
                f"`{plan_family(exp.chosen.id)}` ({', '.join(exp.chosen.kinds)}) · **{exp.lesson.verdict}**, {effect}."
            )
            lines.extend(f"  - Next time: {tip}" for tip in exp.lesson.next_time[:2])
        (self.directory / "playbook.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _num(value: float | None, unit: str = "", fmt: str = ".0f") -> str:
    return f"{value:{fmt}}{unit}" if value is not None else "n/a"


def _body(exp: Experience) -> str:
    sc, lesson = exp.scorecard, exp.lesson
    lines = [
        f"# {exp.id}: {lesson.summary}",
        "",
        f"**Situation.** {'; '.join(describe(f) for f in exp.incidents)}. Script: {exp.script_id or 'none'}. "
        f"Analyst: {exp.analyst}. Lessons recalled: {', '.join(exp.recalled) or 'none'}.",
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
        f"**Lesson ({lesson.verdict}, confidence {lesson.confidence:.2f}, reviewer {lesson.reviewer}).** {lesson.summary}",
    ]
    for title, items in (("What worked", lesson.what_worked), ("What didn't", lesson.what_didnt),
                         ("Next time", lesson.next_time)):
        if items:
            lines += ["", f"{title}:", *(f"- {item}" for item in items)]
    if sc.notes:
        lines += ["", "Scorecard notes:", *(f"- {note}" for note in sc.notes)]
    return "\n".join(lines) + "\n"
