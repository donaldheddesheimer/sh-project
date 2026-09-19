"""Scenario analysis ("Analyze Response").

One run snapshots the live network, asks the agent for candidate responses,
rejects unsafe ones with the SafetyValidator, simulates the baseline and every
safe candidate in parallel fresh SUMO branches, and asks the agent to
recommend one. Nothing here touches the live signals.

Threading: branches run on a worker pool (``run_branch`` is synchronous), but
the ``ScenarioRun`` is only ever mutated on the event loop, so broadcasting it
never races with a worker.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext
from app.config import Settings
from app.models.api import EventLevel, RunStatus
from app.models.domain import (
    CandidateStatus,
    EmergencyStatus,
    Incident,
    IncidentStatus,
    Recommendation,
    SignalProgram,
    SimulationSnapshot,
)
from app.models.scenario import ScenarioRun, ScenarioRunRequest, ScenarioStatus
from app.safety.validator import SafetyValidator
from app.services.city import CityService, Conflict, NotReady
from app.simulation.branching import ProbeSpec, candidate_from_plan, probe_for_incident, run_branch
from app.simulation.interface import TrafficSimulation
from app.simulation.network import RoadNetwork

log = logging.getLogger(__name__)

TERMINAL = (ScenarioStatus.COMPLETED, ScenarioStatus.FAILED)


def validation_findings(
    plan: CandidatePlan, programs: dict[str, SignalProgram], network: RoadNetwork, validator: SafetyValidator
) -> list[str]:
    """Human-readable reasons the plan must not be simulated; empty = safe."""
    findings: list[str] = []
    for policy in plan.policies:
        program = programs.get(policy.intersection_id)
        if program is None:
            findings.append(f"{policy.intersection_id}: no signal program at this intersection")
            continue
        for v in validator.validate(policy, program).violations:
            where = f" phase {v.phase_index}" if v.phase_index is not None else ""
            findings.append(f"{policy.intersection_id}{where}: {v.message}")
    if plan.corridor is not None:
        findings.extend(f"corridor: {v.message}" for v in validator.validate_corridor(plan.corridor, programs))
    for action in plan.reroutes:
        unknown = [s for s in action.avoid_segment_ids if s not in network.segments]
        if unknown:
            findings.append(f"reroute: unknown segment {', '.join(unknown)}")
    return findings


def _mmss(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, secs = divmod(int(round(seconds)), 60)
    return f"{minutes}:{secs:02d}"


class ScenarioService:
    def __init__(
        self,
        *,
        settings: Settings,
        city: CityService,
        agent: AgentProvider,
        validator: SafetyValidator,
        branch_factory: Callable[[], TrafficSimulation],
    ):
        self.settings = settings
        self.city = city
        self.agent = agent
        self.validator = validator
        self._branch_factory = branch_factory
        self._executor = ThreadPoolExecutor(settings.scenario_workers, thread_name_prefix="scenario-branch")
        self._runs: deque[ScenarioRun] = deque(maxlen=settings.scenario_history)
        self._ids = itertools.count(1)
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------ read side

    def runs(self) -> list[ScenarioRun]:
        return list(reversed(self._runs))

    def get(self, run_id: str) -> ScenarioRun:
        for run in self._runs:
            if run.id == run_id:
                return run
        raise KeyError(run_id)

    # ------------------------------------------------------------- commands

    async def start_run(self, request: ScenarioRunRequest) -> ScenarioRun:
        """Validate the request, queue the run and return it; the analysis continues in the background."""
        if self.city.status.status in (RunStatus.STARTING, RunStatus.ERROR):
            raise NotReady(f"simulation is {self.city.status.status.value}")
        self.city.state  # raises NotReady before the first frame
        if self._runs and self._runs[-1].status not in TERMINAL:
            raise Conflict(f"{self._runs[-1].id} is still running")
        incident = await self._resolve_incident(request.incident_id)

        horizon = request.horizon_s if "horizon_s" in request.model_fields_set else self.settings.scenario_horizon_s
        run = ScenarioRun(
            id=f"SCN-{next(self._ids):04d}",
            incident_id=incident.id,
            status=ScenarioStatus.QUEUED,
            agent=self.agent.name,
            created_at=datetime.now(UTC),
            horizon_s=horizon,
            ems_probe=request.ems_probe,
        )
        self._runs.append(run)
        self.city.publish_scenario(run)
        self._task = asyncio.create_task(self._execute(run, incident))
        return run

    def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- pipeline

    async def _resolve_incident(self, incident_id: str | None) -> Incident:
        if incident_id is not None:
            incident = await self.city.smart_city.get_incident(incident_id)
            if incident is None:
                raise KeyError(incident_id)
            if incident.status is not IncidentStatus.ACTIVE:
                raise Conflict(f"{incident_id} is {incident.status.value}")
        else:
            active = await self.city.smart_city.list_incidents()
            if not active:
                raise Conflict("no active incident to analyze")
            incident = max(active, key=lambda i: i.timestamp)
        if incident.location.segment_id not in self.city.network.segments:
            raise Conflict(f"{incident.id} is not matched to a road segment")
        return incident

    async def _execute(self, run: ScenarioRun, incident: Incident) -> None:
        snapshot: SimulationSnapshot | None = None
        try:
            snapshot, programs = await self.city.run_on_live(self._capture)
            run.snapshot_sim_time = snapshot.sim_time
            self._set_status(run, ScenarioStatus.PROPOSING)

            context = self._context(incident, snapshot, programs)
            plans = await self.agent.propose_candidates(context)
            if not any(p.id == "baseline" for p in plans):
                plans.insert(0, CandidatePlan(id="baseline", name="Baseline", description="Continue current signal timing unchanged."))
            plans = plans[: self.settings.scenario_max_candidates]

            run.candidates = [candidate_from_plan(p) for p in plans]
            for candidate, plan in zip(run.candidates, plans):
                if findings := validation_findings(plan, programs, self.city.network, self.validator):
                    candidate.status = CandidateStatus.REJECTED
                    candidate.violations = findings
            rejected = sum(c.status is CandidateStatus.REJECTED for c in run.candidates)
            self.city.events.add(
                EventLevel.INFO,
                f"Analyzing {incident.id}: {len(plans)} candidates, {rejected} rejected by safety validator",
                snapshot.sim_time,
                incident.id,
            )
            self._set_status(run, ScenarioStatus.SIMULATING)

            probe = self._probe(run, incident, snapshot)
            accepted = [i for i, c in enumerate(run.candidates) if c.status is CandidateStatus.PENDING]
            await asyncio.gather(*(self._simulate(run, i, plans[i], snapshot, probe) for i in accepted))

            self._set_status(run, ScenarioStatus.RECOMMENDING)
            run.recommendation = self._checked(await self.agent.recommend(context, run.candidates), run)
            run.completed_at = datetime.now(UTC)
            self._set_status(run, ScenarioStatus.COMPLETED)
            self.city.events.add(EventLevel.INFO, self._summary(run), self.city.sim_time, incident.id)
        except Exception as exc:  # noqa: BLE001 - reported on the run and in the ops log
            log.exception("scenario run %s failed", run.id)
            run.error = f"{type(exc).__name__}: {exc}"
            run.completed_at = datetime.now(UTC)
            self._set_status(run, ScenarioStatus.FAILED)
            self.city.events.add(EventLevel.ALERT, f"Analysis {run.id} failed: {run.error}", self.city.sim_time, incident.id)
        finally:
            if snapshot is not None:
                Path(snapshot.path).unlink(missing_ok=True)

    def _capture(self, sim: TrafficSimulation) -> tuple[SimulationSnapshot, dict[str, SignalProgram]]:
        """Snapshot and signal programs from one instant (runs on the live simulation thread)."""
        snapshot = sim.save_snapshot()
        return snapshot, {iid: sim.get_signal_program(iid) for iid in self.city.network.intersections}

    def _context(
        self, incident: Incident, snapshot: SimulationSnapshot, programs: dict[str, SignalProgram]
    ) -> IncidentContext:
        state = self.city.state
        stations = self.city.scenario.ems_stations
        return IncidentContext(
            incident=incident,
            sim_time=snapshot.sim_time,
            segments=state.segments,
            intersections=state.intersections,
            signal_programs=programs,
            emergency_vehicles=state.emergency_vehicles,
            ems_origin_segment=stations[0].edge if stations else None,
        )

    def _probe(self, run: ScenarioRun, incident: Incident, snapshot: SimulationSnapshot) -> ProbeSpec | None:
        stations = self.city.scenario.ems_stations
        if not run.ems_probe or not stations:
            return None
        if any(d.status is EmergencyStatus.EN_ROUTE for d in snapshot.dispatches):
            return None  # a live responder is already en route; every branch measures that one
        return probe_for_incident(incident, stations[0].edge)

    async def _simulate(
        self, run: ScenarioRun, index: int, plan: CandidatePlan, snapshot: SimulationSnapshot, probe: ProbeSpec | None
    ) -> None:
        loop = asyncio.get_running_loop()

        def on_start() -> None:  # worker thread
            loop.call_soon_threadsafe(self._mark_running, run, index)

        result = await loop.run_in_executor(
            self._executor,
            run_branch,
            self._branch_factory,
            snapshot,
            plan,
            probe,
            run.horizon_s,
            self.settings.scenario_sample_s,
            on_start,
        )
        run.candidates[index] = result
        self.city.publish_scenario(run)

    def _mark_running(self, run: ScenarioRun, index: int) -> None:
        candidate = run.candidates[index]
        if candidate.status is CandidateStatus.PENDING:
            candidate.status = CandidateStatus.RUNNING
            self.city.publish_scenario(run)

    def _checked(self, recommendation: Recommendation, run: ScenarioRun) -> Recommendation:
        chosen = next((c for c in run.candidates if c.id == recommendation.candidate_id), None)
        if chosen is not None and chosen.status is CandidateStatus.COMPLETED:
            return recommendation
        return Recommendation(
            candidate_id="baseline",
            summary="Keep current signal timing.",
            rationale=[f"{self.agent.name} agent recommended '{recommendation.candidate_id}', which did not complete; "
                       "falling back to the baseline"],
        )

    def _summary(self, run: ScenarioRun) -> str:
        rec = run.recommendation
        chosen = next(c for c in run.candidates if c.id == rec.candidate_id)
        baseline = next((c for c in run.candidates if c.id == "baseline"), None)
        text = f"Recommendation: {chosen.name or chosen.id}"
        if chosen.metrics and baseline and baseline.metrics and chosen is not baseline:
            b, m = baseline.metrics, chosen.metrics
            text += (
                f" (delay {b.mean_vehicle_delay:.0f}s → {m.mean_vehicle_delay:.0f}s, "
                f"EMS {_mmss(b.emergency_vehicle_eta)} → {_mmss(m.emergency_vehicle_eta)})"
            )
        return text

    def _set_status(self, run: ScenarioRun, status: ScenarioStatus) -> None:
        run.status = status
        self.city.publish_scenario(run)
