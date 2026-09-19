"""Run one candidate response in a fresh simulation branch.

Pure and synchronous: the scenario service calls ``run_branch`` from a worker
thread. Each branch is a brand-new simulation process restored from the
snapshot, because re-loading a snapshot into a process that has already run
diverges (see docs/architecture.md, "Branching and determinism").
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from app.agent.base import CandidatePlan
from app.models.domain import (
    CandidateStatus,
    Incident,
    MetricSample,
    SimulationCandidate,
    SimulationSnapshot,
    TrafficMetrics,
)
from app.simulation.interface import TrafficSimulation

EMS_STAGING_OFFSET_M = 20.0  # responders stop this far behind the crash, shielding the scene


@dataclass(frozen=True)
class ProbeSpec:
    """Where an EMS unit departs from and where it stages at the scene."""

    origin_segment: str
    destination_segment: str
    position_m: float | None
    lane: int


def probe_for_incident(incident: Incident, origin_segment: str) -> ProbeSpec:
    """Stage a responder behind the incident in its blocked lane (live dispatch and branch probes alike)."""
    if incident.location.segment_id is None:
        raise ValueError(f"{incident.id} is not matched to a road segment")
    position = incident.location.position_m
    return ProbeSpec(
        origin_segment=origin_segment,
        destination_segment=incident.location.segment_id,
        position_m=position - EMS_STAGING_OFFSET_M if position is not None else None,
        lane=incident.affected_lanes[0] if incident.affected_lanes else 0,
    )


def candidate_from_plan(plan: CandidatePlan) -> SimulationCandidate:
    return SimulationCandidate(
        id=plan.id,
        name=plan.name,
        description=plan.description,
        policies=plan.policies,
        corridor=plan.corridor,
        reroutes=plan.reroutes,
    )


def _sample(m: TrafficMetrics) -> MetricSample:
    return MetricSample(
        t=m.sim_time,
        delay=m.mean_vehicle_delay,
        queue=m.max_queue_length,
        throughput=m.throughput,
        speed=m.mean_speed,
        vehicles=m.vehicles_in_network,
    )


def apply_plan(sim: TrafficSimulation, plan: CandidatePlan) -> tuple[dict[str, str], int]:
    """Install a plan's policies, corridor and reroutes on ``sim``.

    Returns intersection -> program id now running, and the vehicles diverted at activation. The same steps run
    in a branch and on the live simulation (the implementor), so what was tested is what goes live.
    """
    programs = {policy.intersection_id: sim.apply_signal_policy(policy) for policy in plan.policies}
    if plan.corridor is not None:
        sim.enable_emergency_corridor(plan.corridor)
    diverted = sum(sim.reroute_vehicles(action) for action in plan.reroutes)
    return programs, diverted


def run_branch(
    factory: Callable[[], TrafficSimulation],
    snapshot: SimulationSnapshot,
    plan: CandidatePlan,
    probes: list[ProbeSpec],
    horizon_s: float,
    sample_every_s: float,
    on_start: Callable[[], None] | None = None,
    standing: list[CandidatePlan] | None = None,
) -> SimulationCandidate:
    """Simulate ``plan`` for ``horizon_s`` from ``snapshot``; never raises.

    The probes are dispatched before the plan is applied, so every branch sends
    its responders at the same simulation time. ``standing`` are responses already
    in force on the live city (snapshots capture base signal programs only), re-applied
    first so the branch starts from the live city's real state; ``plan`` is then applied
    on top of them. Failures come back as a ``failed`` candidate with the error in ``notes``.
    """
    candidate = candidate_from_plan(plan)
    candidate.status = CandidateStatus.RUNNING
    wall_start = time.monotonic()
    sim: TrafficSimulation | None = None
    try:
        if on_start:
            on_start()
        sim = factory()
        sim.start()
        sim.restore_snapshot(snapshot)
        for probe in probes:
            sim.spawn_emergency_vehicle(probe.origin_segment, probe.destination_segment, probe.position_m, probe.lane)
        for response in standing or []:
            apply_plan(sim, response)
        apply_plan(sim, plan)
        candidate.metrics = sim.run_for(
            horizon_s,
            on_sample=lambda m: candidate.timeline.append(_sample(m)),
            sample_every_s=sample_every_s,
        )
        candidate.notes.extend(sim.response_notes())
        candidate.status = CandidateStatus.COMPLETED
    except Exception as exc:  # noqa: BLE001 - one failed branch must not sink the run
        candidate.status = CandidateStatus.FAILED
        candidate.notes.append(f"{type(exc).__name__}: {exc}")
    finally:
        if sim is not None:
            sim.close()
        candidate.wall_time_s = round(time.monotonic() - wall_start, 2)
    return candidate
