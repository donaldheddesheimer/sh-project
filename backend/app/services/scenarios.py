"""Scenario analysis ("Analyze Response").

An analysis snapshots the live network once, then evaluates candidate plans in
parallel fresh SUMO branches from that instant: unsafe plans are rejected by
the SafetyValidator and never simulated. Nothing here touches live signals.

Two drivers share the same steps (open → capture → evaluate → finish):
- the mock pipeline (``start_run``, POST /api/scenarios/run): the
  AgentProvider proposes every plan up front and recommends one;
- an external agent over MCP (``app/api/mcp.py``): it proposes plans in as
  many ``evaluate`` rounds as it likes and submits the recommendation itself.

Threading: branches run on a worker pool (``run_branch`` is synchronous), but
the ``ScenarioRun`` is only ever mutated on the event loop, so broadcasting it
never races with a worker.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
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
    SimulationCandidate,
    SimulationSnapshot,
)
from app.models.scenario import ScenarioRun, ScenarioRunRequest, ScenarioStatus
from app.safety.validator import SafetyValidator
from app.services.city import CityService, Conflict, NotReady
from app.simulation.branching import ProbeSpec, candidate_from_plan, probe_for_incident, run_branch
from app.simulation.interface import TrafficSimulation
from app.simulation.network import RoadNetwork

log = logging.getLogger(__name__)

BASELINE = CandidatePlan(id="baseline", name="Baseline", description="Continue current signal timing unchanged.")


def validation_findings(
    plan: CandidatePlan, programs: dict[str, SignalProgram], network: RoadNetwork, validator: SafetyValidator
) -> list[str]:
    """Human-readable reasons the plan must not be simulated; empty = safe."""
    findings: list[str] = []
    seen_intersections: set[str] = set()
    for policy in plan.policies:
        if policy.intersection_id in seen_intersections:
            findings.append(f"{policy.intersection_id}: multiple policies target this intersection")
            continue
        seen_intersections.add(policy.intersection_id)
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


@dataclass
class Analysis:
    """Working state of the one open run: its snapshot and everything branches need."""

    run: ScenarioRun
    incident: Incident
    idle_timeout_s: float | None = None  # agent-driven runs are failed after this long without a call
    snapshot: SimulationSnapshot | None = None
    programs: dict[str, SignalProgram] = field(default_factory=dict)
    context: IncidentContext | None = None
    probe: ProbeSpec | None = None
    busy: bool = False
    touched: float = field(default_factory=time.monotonic)


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
        self._open: Analysis | None = None
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ read side

    def runs(self) -> list[ScenarioRun]:
        return list(reversed(self._runs))

    def get(self, run_id: str) -> ScenarioRun:
        for run in self._runs:
            if run.id == run_id:
                return run
        raise KeyError(run_id)

    def analysis(self, run_id: str) -> Analysis:
        """The open analysis with this id; raises Conflict if it is finished, KeyError if unknown."""
        if self._open is not None and self._open.run.id == run_id:
            self._open.touched = time.monotonic()
            return self._open
        run = self.get(run_id)
        raise Conflict(f"{run.id} is {run.status.value}; start a new analysis")

    def check_plan(self, analysis: Analysis, plan: CandidatePlan) -> list[str]:
        return validation_findings(plan, analysis.programs, self.city.network, self.validator)

    # ------------------------------------------------ mock pipeline (REST)

    async def start_run(self, request: ScenarioRunRequest) -> ScenarioRun:
        """Queue a full mock-agent analysis and return it; the work continues in the background."""
        horizon = request.horizon_s if "horizon_s" in request.model_fields_set else None
        analysis = await self.open(request.incident_id, horizon, request.ems_probe, self.agent.name)
        self._spawn(self._run_pipeline(analysis))
        return analysis.run

    async def _run_pipeline(self, a: Analysis) -> None:
        try:
            await self.capture(a)
            plans = await self.agent.propose_candidates(a.context)
            if not any(p.id == BASELINE.id for p in plans):
                plans.insert(0, BASELINE)
            await self.evaluate(a, plans[: self.settings.scenario_max_candidates], then=ScenarioStatus.RECOMMENDING)
            recommendation = await self.agent.recommend(a.context, a.run.candidates)
            self.finish(a, self._checked(recommendation, a.run))
        except Exception as exc:  # noqa: BLE001 - reported on the run and in the ops log
            log.exception("scenario run %s failed", a.run.id)
            self.fail(a, f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------ the steps

    async def open(
        self,
        incident_id: str | None,
        horizon_s: float | None,
        ems_probe: bool,
        driver: str,
        idle_timeout_s: float | None = None,
    ) -> Analysis:
        """Guard, resolve the incident and create the run (status queued). One open run at a time."""
        if self.city.status.status in (RunStatus.STARTING, RunStatus.ERROR):
            raise NotReady(f"simulation is {self.city.status.status.value}")
        self.city.state  # raises NotReady before the first frame
        if self._open is not None:
            raise Conflict(f"{self._open.run.id} is still running")
        incident = await self._resolve_incident(incident_id)
        run = ScenarioRun(
            id=f"SCN-{next(self._ids):04d}",
            incident_id=incident.id,
            status=ScenarioStatus.QUEUED,
            agent=driver,
            created_at=datetime.now(UTC),
            horizon_s=horizon_s if horizon_s is not None else self.settings.scenario_horizon_s,
            ems_probe=ems_probe,
        )
        analysis = Analysis(run=run, incident=incident, idle_timeout_s=idle_timeout_s)
        self._open = analysis
        self._runs.append(run)
        self.city.publish_scenario(run)
        if idle_timeout_s:
            self._spawn(self._watch_idle(analysis))
        return analysis

    async def capture(self, a: Analysis) -> None:
        """Snapshot the live network and read every signal program at one instant (status proposing)."""
        a.snapshot, a.programs = await self.city.run_on_live(self._capture)
        a.run.snapshot_sim_time = a.snapshot.sim_time
        a.context = self._context(a)
        a.probe = self._probe(a)
        self._set_status(a.run, ScenarioStatus.PROPOSING)

    async def evaluate(
        self, a: Analysis, plans: list[CandidatePlan], then: ScenarioStatus = ScenarioStatus.PROPOSING
    ) -> list[SimulationCandidate]:
        """Validate ``plans`` and simulate the safe ones in parallel branches from the run's snapshot.

        The baseline is added (and simulated) on the first round if missing.
        Returns this round's candidates, in order; the run keeps all rounds and
        moves to ``then`` (proposing: the agent may try another round).
        """
        if a.busy:
            raise Conflict(f"{a.run.id} is already simulating")
        existing = {c.id for c in a.run.candidates}
        if not any(c.id == BASELINE.id for c in a.run.candidates) and not any(p.id == BASELINE.id for p in plans):
            plans = [BASELINE, *plans]
        ids = [p.id for p in plans]
        if duplicates := sorted({i for i in ids if ids.count(i) > 1 or i in existing}):
            raise ValueError(f"plan ids already used in {a.run.id}: {', '.join(duplicates)}")
        room = self.settings.scenario_max_candidates - len(a.run.candidates)
        if len(plans) > room:
            raise ValueError(f"{a.run.id} has room for {room} more candidates (limit {self.settings.scenario_max_candidates})")

        start = len(a.run.candidates)
        a.run.candidates.extend(candidate_from_plan(p) for p in plans)
        for candidate, plan in zip(a.run.candidates[start:], plans):
            if findings := self.check_plan(a, plan):
                candidate.status = CandidateStatus.REJECTED
                candidate.violations = findings
        rejected = sum(c.status is CandidateStatus.REJECTED for c in a.run.candidates[start:])
        self.city.events.add(
            EventLevel.INFO,
            f"Analyzing {a.incident.id}: {len(plans)} candidate{'s' if len(plans) != 1 else ''}, "
            f"{rejected} rejected by safety validator",
            a.snapshot.sim_time,
            a.incident.id,
        )

        a.busy = True
        self._set_status(a.run, ScenarioStatus.SIMULATING)
        try:
            accepted = [i for i in range(start, len(a.run.candidates)) if a.run.candidates[i].status is CandidateStatus.PENDING]
            await asyncio.gather(*(self._simulate(a, i, plans[i - start]) for i in accepted))
        finally:
            a.busy = False
            a.touched = time.monotonic()
        if a.run.status is ScenarioStatus.SIMULATING:  # not failed meanwhile
            self._set_status(a.run, then)
        return a.run.candidates[start:]

    def finish(self, a: Analysis, recommendation: Recommendation) -> ScenarioRun:
        """Complete the run with a recommendation for a completed candidate."""
        chosen = next((c for c in a.run.candidates if c.id == recommendation.candidate_id), None)
        if chosen is None or chosen.status is not CandidateStatus.COMPLETED:
            state = chosen.status.value if chosen else "unknown"
            raise ValueError(f"cannot recommend '{recommendation.candidate_id}' ({state}); pick a completed candidate")
        a.run.recommendation = recommendation
        a.run.completed_at = datetime.now(UTC)
        self._set_status(a.run, ScenarioStatus.COMPLETED)
        self.city.events.add(EventLevel.INFO, self._summary(a.run), self.city.sim_time, a.incident.id)
        self._close(a)
        return a.run

    def fail(self, a: Analysis, error: str) -> None:
        a.run.error = error
        a.run.completed_at = datetime.now(UTC)
        self._set_status(a.run, ScenarioStatus.FAILED)
        self.city.events.add(EventLevel.ALERT, f"Analysis {a.run.id} failed: {error}", self.city.sim_time, a.incident.id)
        self._close(a)

    def shutdown(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._open is not None:
            self._close(self._open)  # drop the open run's snapshot file
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- helpers

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

    def _capture(self, sim: TrafficSimulation) -> tuple[SimulationSnapshot, dict[str, SignalProgram]]:
        """Snapshot and signal programs from one instant (runs on the live simulation thread)."""
        snapshot = sim.save_snapshot()
        return snapshot, {iid: sim.get_signal_program(iid) for iid in self.city.network.intersections}

    def _context(self, a: Analysis) -> IncidentContext:
        state = self.city.state
        stations = self.city.scenario.ems_stations
        return IncidentContext(
            incident=a.incident,
            sim_time=a.snapshot.sim_time,
            segments=state.segments,
            intersections=state.intersections,
            signal_programs=a.programs,
            emergency_vehicles=state.emergency_vehicles,
            ems_origin_segment=stations[0].edge if stations else None,
        )

    def _probe(self, a: Analysis) -> ProbeSpec | None:
        stations = self.city.scenario.ems_stations
        if not a.run.ems_probe or not stations:
            return None
        if any(d.status is EmergencyStatus.EN_ROUTE for d in a.snapshot.dispatches):
            return None  # a live responder is already en route; every branch measures that one
        return probe_for_incident(a.incident, stations[0].edge)

    async def _simulate(self, a: Analysis, index: int, plan: CandidatePlan) -> None:
        loop = asyncio.get_running_loop()
        run = a.run

        def on_start() -> None:  # worker thread
            loop.call_soon_threadsafe(self._mark_running, run, index)

        result = await loop.run_in_executor(
            self._executor,
            run_branch,
            self._branch_factory,
            a.snapshot,
            plan,
            a.probe,
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
        """Mock path: fall back to the baseline if the agent picked a candidate that did not complete."""
        chosen = next((c for c in run.candidates if c.id == recommendation.candidate_id), None)
        if chosen is not None and chosen.status is CandidateStatus.COMPLETED:
            return recommendation
        return Recommendation(
            candidate_id=BASELINE.id,
            summary="Keep current signal timing.",
            rationale=[f"{self.agent.name} agent recommended '{recommendation.candidate_id}', which did not complete; "
                       "falling back to the baseline"],
        )

    def _summary(self, run: ScenarioRun) -> str:
        rec = run.recommendation
        chosen = next(c for c in run.candidates if c.id == rec.candidate_id)
        baseline = next((c for c in run.candidates if c.id == BASELINE.id), None)
        text = f"Recommendation: {chosen.name or chosen.id}"
        if chosen.metrics and baseline and baseline.metrics and chosen is not baseline:
            b, m = baseline.metrics, chosen.metrics
            text += (
                f" (delay {b.mean_vehicle_delay:.0f}s → {m.mean_vehicle_delay:.0f}s, "
                f"EMS {_mmss(b.emergency_vehicle_eta)} → {_mmss(m.emergency_vehicle_eta)})"
            )
        return text

    async def _watch_idle(self, a: Analysis) -> None:
        """Fail an agent-driven run nobody has touched for ``idle_timeout_s``, releasing the lock."""
        while self._open is a:
            await asyncio.sleep(min(5.0, a.idle_timeout_s))
            if self._open is a and not a.busy and time.monotonic() - a.touched > a.idle_timeout_s:
                self.fail(a, f"agent abandoned the run (idle > {a.idle_timeout_s:.0f}s)")

    def _close(self, a: Analysis) -> None:
        if self._open is a:
            self._open = None
        if a.snapshot is not None:
            Path(a.snapshot.path).unlink(missing_ok=True)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _set_status(self, run: ScenarioRun, status: ScenarioStatus) -> None:
        run.status = status
        self.city.publish_scenario(run)
