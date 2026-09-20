"""Demo readiness that needs no SUMO: the scripted crashes name real roads, and lessons survive a round trip."""

import asyncio
from datetime import UTC, datetime

import pytest

from app.learning.store import PROVISIONAL_RANKING_FACTOR, ExperienceStore
from app.models.domain import Severity
from app.models.episode import Experience, IncidentFeatures, Lesson, PlanSummary, Scorecard, WindowStats


def test_demo_scripts_name_real_segments_and_lanes(scenario, network):
    defaults = scenario.default_collision
    assert "crash-ahead" in scenario.demos
    for script in scenario.demos.values():
        for crash in script.crashes:
            # unset fields fall back to the scenario's default collision, as CityService._collision_args fills them
            segment_id = crash.segment_id or defaults.edge
            assert segment_id in network.segments, (script.id, segment_id)
            severity = crash.severity or defaults.severity
            lanes = crash.lanes if crash.lanes is not None else defaults.lanes
            if severity is not Severity.CRITICAL:  # a critical crash closes every lane, whatever it names
                lane_count = network.segments[segment_id].lanes
                assert lanes and all(0 <= lane < lane_count for lane in lanes), (script.id, segment_id, lanes)


def _features(**overrides) -> IncidentFeatures:
    fields = dict(
        incident_id="INC-0001", type="collision", severity="major", segment_id="B2_C2", street="Main St",
        direction="EB", upstream="B2", downstream="C2", blocked_lanes=[0], total_lanes=2,
    )
    return IncidentFeatures(**{**fields, **overrides})


def _experience(number: int, incidents: list[IncidentFeatures]) -> Experience:
    window = WindowStats(
        samples=1, mean_delay=10.0, end_delay=10.0, peak_queue=3, mean_throughput=100.0, mean_speed_mps=5.0
    )
    return Experience(
        id=f"EP-{number:04d}",
        created_at=datetime.now(UTC),
        analyst="mock",
        incidents=incidents,
        chosen=PlanSummary(
            id="divert-advisory", name="Divert", kinds=["diversion"], outcome="predicted delay 10s", status="completed"
        ),
        scorecard=Scorecard(candidate_id="divert-advisory", candidate_name="Divert", window_s=120.0, realised=window),
        lesson=Lesson(verdict="effective", summary="Diverting worked.", next_time=["Try divert-advisory first"]),
    )


def test_memory_store_round_trip(tmp_path):
    store = ExperienceStore(tmp_path / "memory")
    assert store.load() == [] and store.next_number() == 1

    assert asyncio.run(store.save(_experience(7, [_features()]))).is_file()
    loaded = store.load()
    assert [e.id for e in loaded] == ["EP-0007"]
    assert loaded[0].chosen.id == "divert-advisory" and loaded[0].lesson.summary == "Diverting worked."
    assert store.next_number() == 8
    assert store.stats()["episodes"] == 1
    assert "EP-0007" in store.playbook()

    # the same crash again recalls the lesson at full similarity; another kind of crash somewhere else does not
    recalled = asyncio.run(store.recall([_features()]))
    assert [r.id for r in recalled] == ["EP-0007"]
    assert recalled[0].structured_score == pytest.approx(1.0)
    # a diversion lesson carrying no response evidence is provisional, so its ranking score is discounted
    assert recalled[0].provisional and not recalled[0].trusted
    assert recalled[0].similarity == pytest.approx(1.0 * PROVISIONAL_RANKING_FACTOR)
    assert recalled[0].chosen == "divert-advisory" and recalled[0].verdict == "effective"
    elsewhere = _features(
        incident_id="INC-0002", type="stall", severity="minor", segment_id="A1_A2", street="Oak St", direction="NB",
        upstream="A1", downstream="A2", blocked_lanes=[1], total_lanes=None,
    )
    assert asyncio.run(store.recall([elsewhere])) == []

    assert store.clear() == 1
    assert store.load() == [] and store.next_number() == 1 and store.playbook() == ""
