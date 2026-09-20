"""Scenario analysis ("Analyze Response").

An analysis snapshots the live network once, then evaluates candidate plans in
parallel fresh SUMO branches from that instant: unsafe plans are rejected by
the SafetyValidator and never simulated. Nothing here touches live signals.

Two drivers share the same steps (open → capture → evaluate → finish):
- the mock pipeline (``start_run``, POST /api/scenarios/run): the
  AgentProvider proposes every plan up front and recommends one;
- an external agent over MCP (``app/api/mcp_tools.py``): it proposes plans in as
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
from collections import Counter, deque
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from app.agent.base import AgentProvider, CandidatePlan, IncidentContext
from app.agent.mock import MockAgentProvider
from app.config import Settings
from app.models.api import EventLevel, RunStatus
from app.models.domain import (
    CandidateStatus,
    EmergencyStatus,
    Incident,
    IncidentStatus,
    NetworkState,
    Recommendation,
    SignalProgram,
    SimulationCandidate,
    SimulationSnapshot,
)
from app.models.episode import MemoryMode, RecalledExperience
from app.models.scenario import ScenarioRun, ScenarioRunRequest, ScenarioStatus
from app.safety.validator import SafetyValidator
from app.services.city import CityService, Conflict, NotReady
from app.simulation.branching import ProbeSpec, candidate_from_plan, probe_for_incident, run_branch
from app.simulation.interface import TrafficSimulation
from app.simulation.network import RoadNetwork

log = logging.getLogger(__name__)

BASELINE = CandidatePlan(id="baseline", name="Baseline", description="Continue current signal timing unchanged.")


def _safe_error(exc: Exception) -> str:
    """Keep provider diagnostics useful to operators without exposing a full upstream response."""
    return " ".join(str(exc).split())[:240] or type(exc).__name__


def validation_findings(
    plan: CandidatePlan, programs: dict[str, SignalProgram], network: RoadNetwork, validator: SafetyValidator
) -> list[str]:
    """Human-readable reasons the plan must not be simulated; empty = safe."""
    findings: list[str] = []
    for iid, n in Counter(p.intersection_id for p in plan.policies).items():
        if n > 1:  # policies apply one after another, so their combination would go unvalidated
            findings.append(f"{iid}: {n} policies for one intersection; combine them into one")
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


@dataclass
class Analysis:
    """Working state of the one open run: its snapshot and everything branches need."""

    run: ScenarioRun
    incident: Incident  # the primary incident
    incidents: list[Incident] = field(default_factory=list)  # everything analyzed together (includes the primary)
    idle_timeout_s: float | None = None  # agent-driven runs are failed after this long without a call
    snapshot: SimulationSnapshot | None = None
    programs: dict[str, SignalProgram] = field(default_factory=dict)
    context: IncidentContext | None = None
    probes: list[ProbeSpec] = field(default_factory=list)  # one EMS probe per incident that has no responder yet
    standing: list[CandidatePlan] = field(default_factory=list)  # responses already in force on the live city
    busy: bool = False
    closed: bool = False
    branch_tasks: list[asyncio.Task] = field(default_factory=list)
    touched: float = field(default_factory=time.monotonic)
    embedding_cache: dict[str, list[float]] = field(default_factory=dict)


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
        # Wired by the learning services (None/empty = nothing implemented on the live city, no memory).
        self.standing_source: Callable[[], list[CandidatePlan]] = list
        self.lessons_source: Callable[[list[Incident], list[CandidatePlan], dict[str, list[float]]], Awaitable[list[dict]]] | None = None

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
        analysis = await self.open(
            request.incident_id,
            horizon,
            request.ems_probe,
            self.agent.name,
            memory_mode=request.memory_mode,
            incident_ids=request.incident_ids,
        )
        self._spawn(self.run_pipeline(analysis))
        return analysis.run

    async def _propose(self, a: Analysis, agent: AgentProvider) -> list[CandidatePlan]:
        """The mock agent's proposals. With several incidents: a combined plan first, then each incident's own."""
        if len(a.incidents) == 1:
            plans = await agent.propose_candidates(a.context)
            self._publish_agent_diagnostics(a, agent)
            return plans
        per_incident: list[CandidatePlan] = []
        for incident in a.incidents:
            context = a.context.model_copy(update={"incident": incident})
            proposed = await agent.propose_candidates(context)
            self._publish_agent_diagnostics(a, agent)
            for plan in proposed:
                if plan.id == BASELINE.id or plan.id.startswith("aggressive"):
                    continue  # the unsafe validator demo belongs to the single-incident story
                per_incident.append(
                    plan.model_copy(update={"id": f"{incident.id}:{plan.id}", "name": f"{incident.id} {plan.name}"})
                )
        return [*self._combined_plans(per_incident), *per_incident]

    def _publish_agent_diagnostics(self, a: Analysis, agent: AgentProvider) -> None:
        drain = getattr(agent, "drain_diagnostics", None)
        if not callable(drain):
            return
        for diagnostic in drain():
            self.city.events.add(EventLevel.WARNING, f"{a.run.agent}: {diagnostic}", self.city.sim_time, a.incident.id)

    @staticmethod
    def _combined_plans(plans: list[CandidatePlan]) -> list[CandidatePlan]:
        """Merge one incident's timing/corridor/diversion ideas into plans that address every incident at once.

        Each intersection gets at most one policy (the first proposed), so the merged plan never has two
        policies for one intersection (the validator's rule).
        """
        def family(plan: CandidatePlan) -> str:
            return plan.id.split(":", 1)[1]

        combined: list[CandidatePlan] = []
        timing = [p for p in plans if family(p) == "meter-upstream"]
        diversion = [p for p in plans if family(p) == "divert-advisory"]
        corridor = next((p for p in plans if family(p) == "ems-corridor"), None)
        if timing:
            policies, seen = [], set()
            for plan in timing:
                for policy in plan.policies:
                    if policy.intersection_id not in seen:
                        seen.add(policy.intersection_id)
                        policies.append(policy)
            combined.append(
                CandidatePlan(
                    id="combined-metering",
                    name="Meter inflow toward every blocked link",
                    description="Upstream metering for each incident in one plan.",
                    policies=policies,
                )
            )
        if diversion:
            combined.append(
                CandidatePlan(
                    id="combined-diversion",
                    name="Divert around every blocked link",
                    description="One diversion advisory that avoids all blocked segments.",
                    reroutes=[a for p in diversion for a in p.reroutes],
                )
            )
        if corridor is not None and (timing or diversion):
            combined.append(
                CandidatePlan(
                    id="combined-all",
                    name="Corridor + metering + diversion for every incident",
                    description="EMS green corridor, upstream metering and diversion together.",
                    policies=[p for c in combined for p in c.policies],
                    corridor=corridor.corridor,
                    reroutes=[a for c in combined for a in c.reroutes],
                )
            )
        return combined

    async def run_pipeline(self, a: Analysis, agent: AgentProvider | None = None) -> None:
        """Capture, propose, simulate and recommend with an AgentProvider; failures fail the run.

        Awaited by the episode's mock analyst (so cancelling the analyst cancels the pipeline) or spawned by
        ``start_run``. An episode may supply its team's provider; REST uses the configured provider.
        """
        try:
            await self.capture(a)
            selected = agent or self.agent
            try:
                plans = await self._propose(a, selected)
            except Exception as exc:
                selected = self._fallback_agent(a, selected, "proposal", exc)
                plans = await self._propose(a, selected)
            plans = [BASELINE, *(p for p in plans if p.id != BASELINE.id)][: self.settings.scenario_max_candidates]
            await self.evaluate(a, plans, then=ScenarioStatus.RECOMMENDING)
            try:
                recommendation = await selected.recommend(a.context, a.run.candidates)
            except Exception as exc:
                selected = self._fallback_agent(a, selected, "recommendation", exc)
                recommendation = await selected.recommend(a.context, a.run.candidates)
            self.finish(a, self._checked(recommendation, a.run, selected.name))
        except Exception as exc:  # noqa: BLE001 - reported on the run and in the ops log
            log.exception("scenario run %s failed", a.run.id)
            self.fail(a, f"{type(exc).__name__}: {exc}")

    def _fallback_agent(self, a: Analysis, agent: AgentProvider, stage: str, exc: Exception) -> AgentProvider:
        """Switch a REST NIM run to the deterministic provider once, or preserve its configured failure."""
        if agent.name != "nemotron" or a.run.agent == "nemotron→mock" or not self.settings.agent_fallback_to_mock:
            raise exc
        message = _safe_error(exc)
        a.run.agent = "nemotron→mock"
        self.city.publish_scenario(a.run)
        self.city.events.add(
            EventLevel.WARNING,
            f"Nemotron {stage} failed; mock continues this run ({message})",
            self.city.sim_time,
            a.incident.id,
        )
        return MockAgentProvider()

    # ------------------------------------------------------------ the steps

    async def open(
        self,
        incident_id: str | None,
        horizon_s: float | None,
        ems_probe: bool,
        driver: str,
        idle_timeout_s: float | None = None,
        *,
        memory_mode: MemoryMode = "use",
        incident_ids: list[str] | None = None,
    ) -> Analysis:
        """Guard, resolve the incident(s) and create the run (status queued). One open run at a time.

        ``incident_ids`` analyzes several incidents together (the first, by detection time, is the primary);
        otherwise ``incident_id`` (default: the most recent active one) is analyzed alone.
        """
        if self.city.status.status in (RunStatus.STARTING, RunStatus.ERROR):
            raise NotReady(f"simulation is {self.city.status.status.value}")
        self.city.state  # raises NotReady before the first frame
        self._ensure_no_open_run()
        incidents = await self._resolve_incidents(incident_id, incident_ids)
        self._ensure_no_open_run()  # another start may have claimed it while we awaited
        incident = incidents[0]
        run = ScenarioRun(
            id=f"SCN-{next(self._ids):04d}",
            incident_id=incident.id,
            incident_ids=[i.id for i in incidents],
            status=ScenarioStatus.QUEUED,
            agent=driver,
            created_at=datetime.now(UTC),
            horizon_s=horizon_s if horizon_s is not None else self.settings.scenario_horizon_s,
            ems_probe=ems_probe,
            memory_mode=memory_mode,
        )
        analysis = Analysis(run=run, incident=incident, incidents=incidents, idle_timeout_s=idle_timeout_s)
        self._open = analysis
        self._runs.append(run)
        self.city.publish_scenario(run)
        if idle_timeout_s:
            self._spawn(self._watch_idle(analysis))
        # Branches and the live city share the machine. Keep the live snapshot inside the
        # branch horizon by default; an operator speed change in the console still wins.
        await self.city.hold_speed(1.0, run.id)
        return analysis

    async def capture(self, a: Analysis) -> None:
        """Snapshot the live network, its state and every signal program at one instant (status proposing)."""
        a.standing = self.standing_source()  # what is in force on the live city right now
        a.snapshot, a.programs, state = await self.city.run_on_live(self._capture)
        if a.closed:  # abandoned while the snapshot was being taken
            Path(a.snapshot.path).unlink(missing_ok=True)
            raise Conflict(f"{a.run.id} was abandoned")
        a.run.snapshot_sim_time = a.snapshot.sim_time
        a.context = await self._context(a, state)
        a.run.recalled = [lesson["id"] for lesson in a.context.lessons if "id" in lesson]
        a.run.recall_provenance = [RecalledExperience.model_validate(lesson) for lesson in a.context.lessons]
        a.probes = self._probes(a)
        self._set_status(a.run, ScenarioStatus.PROPOSING)

    async def evaluate(
        self, a: Analysis, plans: list[CandidatePlan], then: ScenarioStatus = ScenarioStatus.PROPOSING
    ) -> list[SimulationCandidate]:
        """Validate ``plans`` and simulate the safe ones in parallel branches from the run's snapshot.

        The baseline is added (and simulated) on the first round if missing.
        Returns this round's candidates, in order; the run keeps all rounds and
        moves to ``then`` (proposing: the agent may try another round).
        """
        if a.closed:  # abandoned or failed meanwhile: its snapshot may already be gone
            raise Conflict(f"{a.run.id} is {a.run.status.value}; start a new analysis")
        if a.busy:
            raise Conflict(f"{a.run.id} is already simulating")
        existing = {c.id for c in a.run.candidates}
        if BASELINE.id not in existing:  # the reference is always the canonical do-nothing plan
            plans = [BASELINE, *(p for p in plans if p.id != BASELINE.id)]
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
            f"Analyzing {' + '.join(i.id for i in a.incidents)}: {len(plans)} candidate{'s' if len(plans) != 1 else ''}, "
            f"{rejected} rejected by safety validator",
            a.snapshot.sim_time,
            a.incident.id,
        )

        a.busy = True
        a.run.rounds += 1
        self._set_status(a.run, ScenarioStatus.SIMULATING)
        # The round belongs to the service, not the caller: if a tool call is cancelled, the branches
        # still finish and are recorded, and the run stays busy until they have.
        await asyncio.shield(self._spawn(self._run_round(a, start, plans, then)))
        return a.run.candidates[start:]

    async def _run_round(self, a: Analysis, start: int, plans: list[CandidatePlan], then: ScenarioStatus) -> None:
        try:
            accepted = [i for i in range(start, len(a.run.candidates)) if a.run.candidates[i].status is CandidateStatus.PENDING]
            a.branch_tasks = [asyncio.ensure_future(self._simulate(a, i, plans[i - start])) for i in accepted]
            # a cancelled branch (the analysis was abandoned) is not an error of this round
            await asyncio.gather(*a.branch_tasks, return_exceptions=True)
        finally:
            a.busy = False
            a.touched = time.monotonic()
            if a.closed:
                self._drop_snapshot(a)  # the run was closed while branches still read it
        if a.run.status is ScenarioStatus.SIMULATING:  # not failed meanwhile
            self._set_status(a.run, then)

    def finish(self, a: Analysis, recommendation: Recommendation) -> ScenarioRun:
        """Complete the run with a recommendation for a completed candidate."""
        if a.closed:  # abandoned (e.g. superseded by a new crash) while the agent was still deciding
            raise Conflict(f"{a.run.id} is {a.run.status.value}; its recommendation is discarded")
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
        if a.closed:  # already finished, failed or abandoned
            return
        a.run.error = error
        a.run.completed_at = datetime.now(UTC)
        self._set_status(a.run, ScenarioStatus.FAILED)
        self.city.events.add(EventLevel.ALERT, f"Analysis {a.run.id} failed: {error}", self.city.sim_time, a.incident.id)
        self._close(a)

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        # cancel queued branches and wait for running ones, which still read the snapshot
        await asyncio.to_thread(self._executor.shutdown, wait=True, cancel_futures=True)
        if self._open is not None:
            self._close(self._open)  # drop the open run's snapshot file
        self._executor.shutdown(wait=False, cancel_futures=True)

    # ------------------------------------------------------------- helpers

    def _ensure_no_open_run(self) -> None:
        if self._open is not None:
            raise Conflict(f"{self._open.run.id} is still running")

    async def _resolve_incidents(self, incident_id: str | None, incident_ids: list[str] | None) -> list[Incident]:
        """The incidents to analyze, earliest first. Explicit ids must all be active and map-matched."""
        wanted = list(dict.fromkeys(incident_ids)) if incident_ids else ([incident_id] if incident_id else [])
        found: list[Incident] = []
        for wanted_id in wanted:
            incident = await self.city.smart_city.get_incident(wanted_id)
            if incident is None:
                raise KeyError(wanted_id)
            if incident.status is not IncidentStatus.ACTIVE:
                raise Conflict(f"{wanted_id} is {incident.status.value}")
            found.append(incident)
        if not found:
            active = await self.city.smart_city.list_incidents()
            if not active:
                raise Conflict("no active incident to analyze")
            found = [max(active, key=lambda i: i.timestamp)]
        for incident in found:
            if incident.location.segment_id not in self.city.network.segments:
                raise Conflict(f"{incident.id} is not matched to a road segment")
            if (
                not self.city.smart_city.simulation_is_source
                and (incident.location.match is None or not incident.location.match.mirrored)
            ):
                raise Conflict(f"{incident.id} is not mirrored in the digital twin")
        return sorted(found, key=lambda i: i.timestamp)

    def _capture(self, sim: TrafficSimulation) -> tuple[SimulationSnapshot, dict[str, SignalProgram], NetworkState]:
        """Snapshot, signal programs and network state from one instant (runs on the live simulation thread)."""
        snapshot = sim.save_snapshot()
        try:
            programs = {iid: sim.get_signal_program(iid) for iid, info in self.city.network.intersections.items() if info.tls_id}
            return snapshot, programs, sim.get_network_state()
        except Exception:
            Path(snapshot.path).unlink(missing_ok=True)
            raise

    async def _context(self, a: Analysis, state: NetworkState) -> IncidentContext:
        stations = self.city.scenario.ems_stations
        lessons = []
        if a.run.memory_mode == "use" and self.lessons_source is not None:
            lessons = await self.lessons_source(a.incidents, a.standing, a.embedding_cache)
        return IncidentContext(
            incident=a.incident,
            incidents=a.incidents,
            sim_time=a.snapshot.sim_time,
            segments=state.segments,
            intersections=state.intersections,
            signal_programs=a.programs,
            emergency_vehicles=state.emergency_vehicles,
            ems_origin_segment=stations[0].edge if stations else None,
            standing=a.standing,
            lessons=lessons,
        )

    def _probes(self, a: Analysis) -> list[ProbeSpec]:
        """One EMS probe per incident that has no responder on the way yet (every branch dispatches the same ones)."""
        stations = self.city.scenario.ems_stations
        if not a.run.ems_probe or not stations:
            return []
        # a live responder already en route to an incident's segment is measured by every branch as it is
        covered = {d.destination_segment for d in a.snapshot.dispatches if d.status is EmergencyStatus.EN_ROUTE}
        return [
            probe_for_incident(incident, stations[0].edge)
            for incident in a.incidents
            if incident.location.segment_id not in covered
        ]

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
            a.probes,
            run.horizon_s,
            self.settings.scenario_sample_s,
            on_start,
            a.standing,
        )
        run.candidates[index] = result
        self.city.publish_scenario(run)

    def _mark_running(self, run: ScenarioRun, index: int) -> None:
        candidate = run.candidates[index]
        if candidate.status is CandidateStatus.PENDING:
            candidate.status = CandidateStatus.RUNNING
            self.city.publish_scenario(run)

    def _checked(self, recommendation: Recommendation, run: ScenarioRun, agent_name: str) -> Recommendation:
        """Mock path: fall back to the baseline if the agent picked a candidate that did not complete."""
        chosen = next((c for c in run.candidates if c.id == recommendation.candidate_id), None)
        if chosen is not None and chosen.status is CandidateStatus.COMPLETED:
            return recommendation
        return Recommendation(
            candidate_id=BASELINE.id,
            summary="Keep current signal timing.",
            rationale=[f"{agent_name} agent recommended '{recommendation.candidate_id}', which did not complete; "
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

    def abandon(self, reason: str) -> str | None:
        """Fail the open analysis so another can start (e.g. a new crash changed the situation).

        Branches still queued are dropped; ones already running finish on their own. Returns the run id.
        """
        a = self._open
        if a is None:
            return None
        self.fail(a, reason)
        return a.run.id

    def _close(self, a: Analysis) -> None:
        a.closed = True
        if self._open is a:
            self._open = None
        # a task, not an await: _close is called from the synchronous finish/fail paths, and giving the
        # speed back must never be able to hold up completing the run
        self._spawn(self.city.release_speed(a.run.id))
        for task in a.branch_tasks:
            task.cancel()  # queued branches never start; a branch already running finishes and is discarded
        if not a.busy:
            self._drop_snapshot(a)  # otherwise the round's last branch deletes it (see _run_round)

    @staticmethod
    def _drop_snapshot(a: Analysis) -> None:
        if a.snapshot is None:
            return
        try:
            Path(a.snapshot.path).unlink(missing_ok=True)
        except OSError:  # a branch that was already running may still hold the file open (Windows)
            log.warning("could not delete snapshot %s", a.snapshot.path)

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _set_status(self, run: ScenarioRun, status: ScenarioStatus) -> None:
        run.status = status
        self.city.publish_scenario(run)
