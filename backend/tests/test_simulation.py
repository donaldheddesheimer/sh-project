"""Integration tests against a real SUMO process (no GPU needed)."""

from app.models.domain import CongestionLevel, EmergencyStatus, Severity, SignalPolicy

CRASH_EDGE = "B2_C2"


def test_warm_network_has_traffic_and_live_metrics(make_sim):
    sim = make_sim(warmup_s=300)
    state = sim.get_network_state()
    assert state.sim_time == 300
    assert len(state.vehicles) > 50
    assert state.metrics.vehicles_in_network == len(state.vehicles)
    assert 0 < state.metrics.mean_speed < 20
    assert state.metrics.throughput > 0
    # normal operation stays out of heavy/severe congestion
    assert not [s.id for s in state.segments if s.level in (CongestionLevel.HEAVY, CongestionLevel.SEVERE)]


def test_collision_blocks_lane_and_builds_queue(make_sim, network):
    sim = make_sim(warmup_s=300)
    length = network.segments[CRASH_EDGE].length
    disruption = sim.inject_collision(CRASH_EDGE, [0], 0.55 * length, Severity.MAJOR)
    assert disruption.lanes == [0] and disruption.pass_speed is not None
    assert sim.get_edge_state(CRASH_EDGE).blocked_lanes == [0]

    sim.run_for(360)
    crash_edge = sim.get_edge_state(CRASH_EDGE)
    upstream = sim.get_edge_state("A2_B2")
    assert crash_edge.level is CongestionLevel.SEVERE
    assert max(crash_edge.halting_count, upstream.halting_count) >= 10  # queue spilling back
    assert sim.collect_metrics().mean_vehicle_delay > 40

    sim.clear_disruption(disruption.id)
    sim.step()
    assert sim.list_disruptions() == []
    assert sim.get_edge_state(CRASH_EDGE).blocked_lanes == []
    assert not any(v.startswith(disruption.id) for v in sim.conn.vehicle.getIDList())


def test_emergency_vehicle_reaches_scene(make_sim, network):
    sim = make_sim(warmup_s=120)
    dispatch = sim.spawn_emergency_vehicle("W2_A2", CRASH_EDGE, 100.0, 0)
    sim.run_for(5)
    assert sim.collect_metrics().emergency_vehicle_eta is not None
    metrics = sim.run_for(300)
    assert sim._dispatches[dispatch.id].status is EmergencyStatus.ON_SCENE
    assert metrics.emergency_vehicle_eta is not None and metrics.emergency_vehicle_eta < 300


def test_fresh_branches_from_one_snapshot_are_identical(make_sim, network):
    live = make_sim(warmup_s=300)
    live.inject_collision(CRASH_EDGE, [0], 0.55 * network.segments[CRASH_EDGE].length, Severity.MAJOR)
    live.run_for(60)
    snapshot = live.save_snapshot()

    results = []
    for _ in range(2):
        branch = make_sim()
        branch.restore_snapshot(snapshot)
        assert branch.sim_time == snapshot.sim_time
        assert [d.id for d in branch.list_disruptions()] == [d.id for d in snapshot.disruptions]
        results.append(branch.run_for(120))
    assert results[0] == results[1]


def test_signal_policy_changes_splits(make_sim):
    sim = make_sim(warmup_s=60)
    program_id = sim.apply_signal_policy(SignalPolicy(intersection_id="B2", phase_durations={0: 50, 3: 30}, reason="test"))
    program = sim.get_signal_program("B2")
    assert program.program_id == program_id
    assert [p.duration for p in program.phases] == [50, 3, 2, 30, 3, 2]
    sim.run_for(180)  # keeps running on the new program
    assert sim.get_intersection_state("B2").program_id == program_id
