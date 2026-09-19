import asyncio
from datetime import UTC, datetime

from app.agent.base import IncidentContext
from app.agent.mock import MockAgentProvider
from app.models.domain import (
    CongestionLevel,
    GeoPoint,
    Incident,
    IncidentLocation,
    IncidentType,
    RoadSegmentState,
    Severity,
    SignalPolicy,
)
from app.safety.validator import RuleBasedSafetyValidator


def test_validator_accepts_split_shift(network):
    program = network.base_program("B2")
    policy = SignalPolicy(intersection_id="B2", phase_durations={0: 55, 3: 25}, reason="shift")
    assert RuleBasedSafetyValidator().validate(policy, program).ok


def test_validator_rejects_unsafe_timings(network):
    program = network.base_program("B2")
    validator = RuleBasedSafetyValidator()
    cases = {
        "min_green": {0: 5},
        "yellow_clearance": {1: 2},
        "all_red_clearance": {2: 0.5},
        "unknown_phase": {9: 30},
        "cycle_length": {0: 90, 3: 90},
    }
    for code, durations in cases.items():
        result = validator.validate(SignalPolicy(intersection_id="B2", phase_durations=durations, reason=code), program)
        assert not result.ok
        assert code in {v.code for v in result.violations}, code


def _incident_on(segment_id: str) -> Incident:
    return Incident(
        id="INC-TEST",
        type=IncidentType.COLLISION,
        severity=Severity.MAJOR,
        location=IncidentLocation(segment_id=segment_id, point=GeoPoint(lat=0, lon=0), description="test"),
        timestamp=datetime.now(UTC),
        description="test",
        source="test",
    )


def test_mock_agent_candidates_pass_safety_validation(network):
    seg = network.segments["B2_C2"]
    context = IncidentContext(
        incident=_incident_on(seg.id),
        sim_time=0,
        segments=[
            RoadSegmentState(
                id=seg.id, name=seg.name, source=seg.source, destination=seg.destination, direction=seg.direction,
                average_speed=1, speed_limit=seg.speed_limit, vehicle_count=20, halting_count=15, occupancy=0.5,
                congestion=0.9, level=CongestionLevel.SEVERE,
            )
        ],
        intersections=[],
        signal_programs={i: network.base_program(i) for i in network.intersections},
    )
    plans = asyncio.run(MockAgentProvider().propose_candidates(context))
    assert plans[0].id == "baseline" and not plans[0].policies
    assert {p.id for p in plans} >= {"flush-downstream", "meter-upstream"}
    validator = RuleBasedSafetyValidator()
    for plan in plans:
        for policy in plan.policies:
            result = validator.validate(policy, network.base_program(policy.intersection_id))
            assert result.ok, (plan.id, result.violations)
