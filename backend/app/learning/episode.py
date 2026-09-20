"""Autonomous demo episodes: detect → analyze → implement → monitor → review → remember.

The episode service is the only thing that starts an agent. While a demo script is armed, every detected crash
starts or supersedes an episode:

- ``armed``: the script is loaded; the live runner fires scheduled crashes at their simulation time, or an empty
  script waits for an operator-injected collision;
- ``detected`` → ``analyzing``: the analyst tests plans in parallel branches and recommends one;
- ``monitoring``: the recommendation is live (applied by the agent, or by this service if the agent did not) and
  the monitor caches live samples for a fixed number of simulated seconds;
- ``reviewing`` → ``completed``: code builds the scorecard, the reviewer writes the lesson, the lesson is stored,
  and the live simulation is paused (EPISODE_PAUSE_ON_FINISH).

The two-crash rule: a crash detected while an episode is ``detected``, ``analyzing`` or ``monitoring`` supersedes
it. Its agent is cancelled, its analysis abandoned and its monitor stopped; no lesson is stored for it; a new
episode takes over every active incident. A plan it already applied stays on the live signals. An episode
already ``reviewing`` finishes while the new one starts. A reset or a cleared scene aborts the working episode.

The hooks run inside the frame pipeline or a request, so they change state synchronously and spawn tasks for
slow work.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from app.agent.base import CandidatePlan
from app.config import Settings
from app.learning.implementor import Implementor
from app.learning.monitor import LiveMonitor
from app.learning.reviewer import condense
from app.learning.scorecard import PROVISIONAL_CONFIDENCE_CAP, build_scorecard
from app.learning.store import ExperienceStore, incident_features
from app.models.api import EventLevel
from app.models.domain import Incident
from app.models.episode import (
    ACTIVE_STATUSES,
    WORKING_STATUSES,
    DemoInfo,
    Episode,
    EpisodeStatus,
    EpisodeStep,
    Experience,
    Implementation,
    IncidentFeatures,
    Lesson,
    LiveRecord,
    MemoryMode,
    PlanSummary,
    Scorecard,
)
from app.services.city import CityService
from app.services.scenarios import ScenarioService
from app.simulation.scenario import DemoScript
from app.smart_city.base import SmartCityEvent, SmartCityEventKind

log = logging.getLogger(__name__)

HISTORY = 20  # episodes kept for GET /api/episodes


class Analyst(Protocol):
    name: str

    async def run(self, incident_ids: list[str], on_run, memory_mode: MemoryMode = "use") -> str: ...


class Reviewer(Protocol):
    name: str

    async def review(
        self, incidents: list[IncidentFeatures], chosen: PlanSummary, tried: list[PlanSummary], sc: Scorecard
    ) -> Lesson: ...


@dataclass(frozen=True)
class AgentTeam:
    """One selectable analyst/reviewer pair and its optional analyst fallback."""

    analyst: Analyst
    fallback: Analyst | None
    reviewer: Reviewer
    analyst_model: str | None = None
    reviewer_model: str | None = None


class EpisodeService:
    def __init__(
        self,
        *,
        settings: Settings,
        city: CityService,
        scenarios: ScenarioService,
        implementor: Implementor,
        monitor: LiveMonitor,
        store: ExperienceStore,
        teams: dict[str, AgentTeam],
        selected_team: str,
    ):
        self._settings = settings
        self._city = city
        self._scenarios = scenarios
        self._implementor = implementor
        self._monitor = monitor
        self._store = store
        self._teams = teams
        self._analyst: Analyst
        self._fallback: Analyst | None
        self._reviewer: Reviewer
        self._team: AgentTeam
        self._select_team(selected_team)
        self._episodes: deque[Episode] = deque(maxlen=HISTORY)
        self._working: Episode | None = None  # armed, detected, analyzing or monitoring
        self._agent: asyncio.Task | None = None
        self._script: DemoScript | None = None
        self._memory_mode: MemoryMode = "use"
        self._ids = itertools.count(store.next_number())
        self._tasks: set[asyncio.Task] = set()
        city.incident_listeners.append(self._on_incident)
        city.reset_listeners.append(self._on_reset)
        city.add_frame_observer(monitor.observe)
        implementor.listeners.append(self._on_implemented)
        if store.enabled:
            scenarios.lessons_source = self._lessons

    # ------------------------------------------------------------ read side

    def list(self) -> list[Episode]:
        return list(reversed(self._episodes))

    def get(self, episode_id: str) -> Episode:
        for ep in self._episodes:
            if ep.id == episode_id:
                return ep
        raise KeyError(episode_id)

    def info(self) -> DemoInfo:
        return DemoInfo(
            scripts=[
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "crashes_at": [c.at_sim_s for c in s.crashes],
                    "monitor_s": s.monitor_s,
                }
                for s in self._city.scenario.demos.values()
            ],
            armed=self._script.id if self._script else None,
            analyst=self._analyst.name,
            analyst_model=self._team.analyst_model,
            reviewer_model=self._team.reviewer_model,
            analysts=[
                {"id": name, "model": team.analyst_model, "reviewer_model": team.reviewer_model}
                for name, team in self._teams.items()
            ],
            current=self._episodes[-1] if self._episodes else None,
            memory=self._store.stats(),
        )

    # -------------------------------------------------------------- control

    def arm_at_startup(self) -> None:
        """DEMO_SCRIPT: arm before the simulation boots, so the first warm-up already plays the early crashes."""
        if self._settings.demo_script:
            self._load(self._settings.demo_script)
            self._arm()

    async def start_demo(self, script_id: str, memory_mode: MemoryMode = "use") -> Episode:
        """Reset the city and arm ``script_id``: scheduled crashes play, or the operator injects one."""
        self._memory_mode = memory_mode
        script = self._load(script_id)
        await self._city.reset()  # the reset hook aborts the working episode and arms a new one
        if script.speed:
            await self._city.set_speed(script.speed)
        # A finished episode pauses the city (EPISODE_PAUSE_ON_FINISH) and a reset keeps it paused, so without
        # this the script arms but its crashes never come: simulation time would not advance. Run means run.
        await self._city.set_running(True)
        return self._working

    async def shutdown(self) -> None:
        """Stop this service's work and wait for it.

        Awaiting matters for a map switch: a review or a memory write already in flight would otherwise keep
        running against the old city, and its episode id was taken from a counter the replacement has already
        snapshotted, so an unawaited write can overwrite the replacement's first lesson.
        """
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 - a failed episode task must not stop the rest of the shutdown
                log.exception("an episode task failed while shutting down")
        await self._monitor.shutdown()

    async def stop_demo(self) -> DemoInfo:
        """Disarm: no more scripted crashes and no autonomous response. The city keeps running."""
        self._script = None
        self._city.set_scripted_events([])
        if self._working is not None:
            self._abort(self._working, "the demo was stopped")
        return self.info()

    def select_analyst(self, name: str) -> DemoInfo:
        """Select the analyst/reviewer used by future episodes; never switch a live episode mid-run."""
        if name not in self._teams:
            raise KeyError(name)
        if self._working is not None or any(ep.status in ACTIVE_STATUSES for ep in self._episodes):
            raise RuntimeError("stop or finish the current episode before changing the analyst")
        self._select_team(name)
        self._city.events.add(EventLevel.INFO, f"Autonomous analyst changed to {name}", self._city.sim_time)
        return self.info()

    def _load(self, script_id: str) -> DemoScript:
        script = self._city.scenario.demos.get(script_id)
        if script is None:
            raise KeyError(script_id)
        try:
            events = [(crash.at_sim_s, self._city.crash_command(crash)) for crash in script.crashes]
        except KeyError as exc:
            raise ValueError(f"script {script_id} names an unknown segment {exc.args[0]}") from exc
        self._city.set_scripted_events(events)
        self._script = script
        return script

    def _arm(self) -> None:
        script = self._script
        ep = self._new(EpisodeStatus.ARMED)
        self._working = ep
        if script.crashes:
            times = ", ".join(f"{c.at_sim_s:.0f}" for c in script.crashes)
            message = f"armed '{script.name}' (crash at sim {times} s)"
        else:
            message = f"armed '{script.name}'; waiting for the operator to inject a collision"
        self._step(ep, EpisodeStatus.ARMED, message)

    # ---------------------------------------------------------------- hooks

    async def _on_incident(self, event: SmartCityEvent) -> None:
        incident = event.incident
        ep = self._working
        if event.kind is SmartCityEventKind.INCIDENT_CLEARED:
            if ep is not None and ep.status in WORKING_STATUSES and incident.id in ep.incident_ids:
                self._abort(ep, f"{incident.id} was cleared before the episode finished")
            return
        if event.kind is not SmartCityEventKind.INCIDENT_DETECTED or self._script is None:
            return
        active = [i.id for i in sorted(await self._city.smart_city.list_incidents(), key=lambda i: i.timestamp)]
        if ep is not None and ep.status is EpisodeStatus.ARMED:
            ep.incident_ids = active
        else:
            previous = ep if ep is not None and ep.status in WORKING_STATUSES else None
            ep = self._new(EpisodeStatus.DETECTED, active, supersedes=previous.id if previous else None)
            if previous is not None:
                self._supersede(previous, ep, incident.id)
            self._working = ep
        ep.detected_sim_time = incident.sim_time
        self._step(ep, EpisodeStatus.DETECTED, f"{' + '.join(active)} detected; the {self._analyst.name} analyst responds")
        self._agent = self._spawn(self._drive(ep))

    async def _on_reset(self) -> None:
        ep = self._working
        same_script_armed = ep is not None and ep.status is EpisodeStatus.ARMED and self._script is not None and (
            ep.script_id == self._script.id
        )
        if ep is not None and not same_script_armed:
            self._abort(ep, "the simulation was reset")
        # an open analysis branches from a city that is about to disappear
        self._scenarios.abandon("the simulation was reset")
        if same_script_armed:
            self._step(ep, EpisodeStatus.ARMED, "re-armed: the simulation was reset")
        elif self._script is not None:
            self._arm()

    async def _on_implemented(self, impl: Implementation) -> None:
        ep = self._working
        if ep is None or ep.run_id != impl.run_id or ep.status is not EpisodeStatus.ANALYZING:
            return  # an operator applied some other run, or this episode was superseded meanwhile
        ep.implementation = impl
        ep.implemented_sim_time = impl.implemented_at
        ep.monitor_s = self._monitor_s(self._scenarios.get(impl.run_id).horizon_s)
        self._monitor.start_window(
            ep.id,
            impl,
            ep.monitor_s,
            on_progress=lambda elapsed: self._progress(ep, elapsed),
            on_done=lambda record: self._review(ep, record),
        )
        self._step(
            ep,
            EpisodeStatus.MONITORING,
            f"{impl.candidate_name} is live (applied by {impl.implemented_by}); watching it for "
            f"{ep.monitor_s:.0f} simulated seconds",
        )

    # --------------------------------------------------------- the episode

    async def _drive(self, ep: Episode) -> None:
        self._step(ep, EpisodeStatus.ANALYZING, f"the {self._analyst.name} analyst is testing plans in branches")
        started = time.monotonic()
        try:
            run_id = await self._analyze(ep)
            run = self._scenarios.get(run_id)
            ep.analysis_wall_s = round(time.monotonic() - started, 1)
            ep.rounds, ep.candidates, ep.recalled = run.rounds, len(run.candidates), list(run.recalled)
            ep.recall_provenance = list(run.recall_provenance)
            self._city.publish_episode(ep)
            if run.implementation is not None:
                return  # the agent applied it; _on_implemented moved the episode on
            if self._settings.agent_may_implement:
                await self._implementor.implement(run_id, by="coordinator")
            else:
                self._step(
                    ep,
                    EpisodeStatus.ANALYZING,
                    f"{run_id} recommends {run.recommendation.candidate_id}; waiting for an operator to apply it "
                    "(AGENT_MAY_IMPLEMENT is off)",
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reported on the episode and in the ops log
            log.exception("%s failed", ep.id)
            if ep.status in (EpisodeStatus.DETECTED, EpisodeStatus.ANALYZING):
                self._fail(ep, f"{type(exc).__name__}: {exc}")

    async def _analyze(self, ep: Episode) -> str:
        def on_run(run_id: str) -> None:
            ep.run_id = run_id
            self._city.publish_episode(ep)

        try:
            return await self._analyst.run(ep.incident_ids, on_run, ep.memory_mode)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._fallback is None:
                raise
            if ep.run_id is not None and self._run_completed(ep.run_id):
                return ep.run_id  # it failed after recommending: keep the recommendation
            self._scenarios.abandon(f"the {self._analyst.name} analyst failed: {exc}")
            ep.analyst = f"{self._analyst.name}→{self._fallback.name}"
            self._step(
                ep,
                EpisodeStatus.ANALYZING,
                f"the {self._analyst.name} analyst failed ({type(exc).__name__}: {exc}); "
                f"the {self._fallback.name} analyst takes over",
                EventLevel.WARNING,
            )
            return await self._fallback.run(ep.incident_ids, on_run, ep.memory_mode)

    async def _review(self, ep: Episode, record: LiveRecord) -> None:
        if ep.status is not EpisodeStatus.MONITORING:
            return  # superseded or aborted meanwhile
        if self._working is ep:
            self._working = None  # a crash from now on starts a new episode while this one finishes
        self._step(ep, EpisodeStatus.REVIEWING, "monitor window closed; scoring the outcome and writing the lesson")
        try:
            response_notes_available = False
            try:
                ep.implementation.notes = await self._city.run_on_live(lambda sim: sim.response_notes())
                response_notes_available = True
            except Exception as exc:  # noqa: BLE001 - missing notes make response checks unknown, not the review fail
                log.warning("%s could not read live response notes: %s", ep.id, exc)
            run = self._scenarios.get(ep.run_id)
            scorecard = await build_scorecard(
                run,
                ep.implementation,
                record,
                ep.detected_sim_time or record.started_at,
                response_notes_available=response_notes_available,
            )
            ep.scorecard = scorecard
            found = [await self._city.smart_city.get_incident(i) for i in ep.incident_ids]
            incidents = [incident_features(i, self._city.network) for i in found if i is not None]
            chosen, tried = condense(run, ep.implementation.candidate_id)
            lesson = await self._reviewer.review(incidents, chosen, tried, scorecard)
            if scorecard.provisional and lesson.confidence > PROVISIONAL_CONFIDENCE_CAP:
                lesson = lesson.model_copy(update={"confidence": PROVISIONAL_CONFIDENCE_CAP})
            ep.lesson, ep.reviewer = lesson, lesson.reviewer
            if self._store.enabled:
                experience = Experience(
                    id=ep.id,
                    created_at=datetime.now(UTC),
                    script_id=ep.script_id,
                    analyst=ep.analyst,
                    incidents=incidents,
                    chosen=chosen,
                    tried=tried,
                    scorecard=scorecard,
                    lesson=lesson,
                    rounds=run.rounds,
                    memory_mode=ep.memory_mode,
                    eligible_for_recall=ep.memory_mode == "use",
                    recalled=ep.recalled,
                    recall_provenance=ep.recall_provenance,
                    analysis_wall_s=ep.analysis_wall_s,
                )
                ep.memory_path = str(await self._store.save(experience))
        except Exception as exc:  # noqa: BLE001 - reported on the episode and in the ops log
            log.exception("%s review failed", ep.id)
            self._fail(ep, f"review failed: {type(exc).__name__}: {exc}")
            return
        self._finish(ep)

    def _finish(self, ep: Episode) -> None:
        ep.completed_at = datetime.now(UTC)
        stored = f"lesson stored in {Path(ep.memory_path).name}" if ep.memory_path else "memory is off"
        self._step(ep, EpisodeStatus.COMPLETED, f"{ep.lesson.verdict}: {ep.lesson.summary} ({stored})")
        if self._settings.episode_pause_on_finish and self._working is None:
            self._spawn(self._city.set_running(False))  # live collection stops with the episode

    # ------------------------------------------------------ state changes

    def _supersede(self, old: Episode, new: Episode, incident_id: str) -> None:
        was = old.status.value
        self._cancel_agent()
        self._scenarios.abandon(f"superseded by {new.id}: {incident_id} was detected while the agent was working")
        self._monitor.stop(old.id, f"superseded by {new.id}")
        old.superseded_by = new.id
        old.completed_at = datetime.now(UTC)
        standing = " Its plan stays on the live signals." if old.implementation else ""
        self._step(
            old,
            EpisodeStatus.SUPERSEDED,
            f"superseded by {new.id}: {incident_id} was detected while it was {was}; no lesson stored.{standing}",
            EventLevel.WARNING,
        )

    def _abort(self, ep: Episode, reason: str) -> None:
        if ep is self._working:
            self._working = None
            self._cancel_agent()
            if ep.status in (EpisodeStatus.DETECTED, EpisodeStatus.ANALYZING):
                self._scenarios.abandon(f"{ep.id} aborted: {reason}")
        self._monitor.stop(ep.id, reason)
        ep.error = reason
        ep.completed_at = datetime.now(UTC)
        self._step(ep, EpisodeStatus.ABORTED, f"aborted: {reason}", EventLevel.WARNING)

    def _fail(self, ep: Episode, error: str) -> None:
        if ep is self._working:
            self._working = None
        if ep.status in (EpisodeStatus.DETECTED, EpisodeStatus.ANALYZING):
            self._scenarios.abandon(f"{ep.id} failed: {error}")  # release the one-run lock
        self._monitor.stop(ep.id, error)
        ep.error = error
        ep.completed_at = datetime.now(UTC)
        self._step(ep, EpisodeStatus.FAILED, f"failed: {error}", EventLevel.ALERT)

    def _new(
        self, status: EpisodeStatus, incident_ids: list[str] | None = None, supersedes: str | None = None
    ) -> Episode:
        ep = Episode(
            id=f"EP-{next(self._ids):04d}",
            script_id=self._script.id if self._script else None,
            status=status,
            analyst=self._analyst.name,
            created_at=datetime.now(UTC),
            incident_ids=incident_ids or [],
            supersedes=supersedes,
            memory_mode=self._memory_mode,
            monitor_s=self._monitor_s(None),
        )
        self._episodes.append(ep)
        return ep

    def _step(self, ep: Episode, status: EpisodeStatus, message: str, level: EventLevel = EventLevel.INFO) -> None:
        sim_time = self._city.sim_time
        ep.status = status
        ep.steps.append(EpisodeStep(status=status, at=datetime.now(UTC), sim_time=sim_time, message=message))
        self._city.events.add(level, f"{ep.id}: {message}", sim_time, ep.incident_ids[0] if ep.incident_ids else None)
        self._city.publish_episode(ep)

    def _progress(self, ep: Episode, elapsed: float) -> None:
        ep.monitor_progress_s = round(min(elapsed, ep.monitor_s), 1)
        self._city.publish_episode(ep)

    # -------------------------------------------------------------- helpers

    def _monitor_s(self, horizon_s: float | None) -> float:
        script_s = self._script.monitor_s if self._script else None
        return self._settings.episode_monitor_s or script_s or horizon_s or self._settings.scenario_horizon_s

    async def _lessons(
        self, incidents: list[Incident], standing: list[CandidatePlan], query_cache: dict[str, list[float]]
    ) -> list[dict]:
        features = [incident_features(i, self._city.network) for i in incidents]
        recalled = await self._store.recall(features, standing, query_cache=query_cache)
        return [r.model_dump() for r in recalled]

    def _run_completed(self, run_id: str) -> bool:
        from app.models.scenario import ScenarioStatus

        try:
            return self._scenarios.get(run_id).status is ScenarioStatus.COMPLETED
        except KeyError:
            return False

    def _cancel_agent(self) -> None:
        task, self._agent = self._agent, None
        if task is not None and not task.done():
            task.cancel()  # a simulation round already running finishes on its own (the service shields it)

    def _select_team(self, name: str) -> None:
        team = self._teams[name]
        self._team = team
        self._analyst = team.analyst
        self._fallback = team.fallback
        self._reviewer = team.reviewer

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task
