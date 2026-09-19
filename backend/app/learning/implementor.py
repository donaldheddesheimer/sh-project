"""Implementor: applies an analysis' recommended plan to the live simulation.

This is the one gated exception to "agents never touch live signals". It takes no plan payload: only the
recommended, completed candidate of a finished run, re-validated against the *live* signal programs first (they
may have changed since the snapshot), and refused if an incident of the run is no longer active. The operator
(REST), the agent (MCP ``implement_recommendation``, when AGENT_MAY_IMPLEMENT) and the episode service share this
code path. The plan is installed with the same ``apply_plan`` a branch runs, so what was tested is what goes live.

Applied plans stay in force until a reset ("standing responses"). Every branch of a later analysis replays them
in order, because corridors and diversions live in Python and are not part of a SUMO snapshot.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.agent.base import CandidatePlan
from app.models.api import EventLevel
from app.models.domain import CandidateStatus, EmergencyStatus, IncidentStatus
from app.models.episode import Implementation
from app.models.scenario import ScenarioStatus
from app.safety.validator import SafetyValidator
from app.services.city import CityService, Conflict
from app.services.scenarios import ScenarioService, validation_findings
from app.simulation.branching import apply_plan, probe_for_incident
from app.simulation.interface import TrafficSimulation

log = logging.getLogger(__name__)

ImplementationListener = Callable[[Implementation], Awaitable[None]]


class PlanRejected(ValueError):
    """The plan no longer passes the validator against the live signal programs."""


class Implementor:
    def __init__(self, *, city: CityService, scenarios: ScenarioService, validator: SafetyValidator):
        self._city = city
        self._scenarios = scenarios
        self._validator = validator
        self._standing: list[CandidatePlan] = []  # in the order they went live
        self._busy: set[str] = set()
        self._tasks: set[asyncio.Task] = set()
        self.listeners: list[ImplementationListener] = []
        scenarios.standing_source = self.standing_plans
        city.reset_listeners.append(self._on_reset)

    def standing_plans(self) -> list[CandidatePlan]:
        """Plans in force on the live city, oldest first (a later plan on the same signals replaces an earlier one
        when replayed in order, and the latest corridor wins, exactly as on the live simulation)."""
        return list(self._standing)

    async def implement(self, run_id: str, by: str) -> Implementation:
        """Apply ``run_id``'s recommendation to the live simulation. ``by``: agent | operator | coordinator."""
        run = self._scenarios.get(run_id)  # KeyError: unknown run
        if run.status is not ScenarioStatus.COMPLETED or run.recommendation is None:
            raise Conflict(f"{run.id} is {run.status.value}; only a completed analysis can be implemented")
        if run.implementation is not None or run.id in self._busy:
            raise Conflict(f"{run.id} was already implemented")
        chosen = next((c for c in run.candidates if c.id == run.recommendation.candidate_id), None)
        if chosen is None or chosen.status is not CandidateStatus.COMPLETED:
            raise Conflict(f"{run.id} recommends '{run.recommendation.candidate_id}', which did not complete")
        self._busy.add(run.id)
        # Once started, the apply and its bookkeeping finish even if the caller is cancelled (an agent superseded
        # mid-apply): a plan that reached the live signals must be registered as standing.
        task = asyncio.create_task(self._apply(run, chosen, by))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        task.add_done_callback(lambda t: self._busy.discard(run.id) or t.cancelled() or t.exception())  # retrieved
        return await asyncio.shield(task)

    async def _apply(self, run, chosen, by: str) -> Implementation:
        incidents = []
        for incident_id in run.incident_ids or [run.incident_id]:
            incident = await self._city.smart_city.get_incident(incident_id)
            if incident is None or incident.status is not IncidentStatus.ACTIVE:
                raise Conflict(f"{incident_id} is no longer active; start a new analysis")
            incidents.append(incident)
        plan = CandidatePlan(
            id=chosen.id,
            name=chosen.name or chosen.id,
            description=chosen.description,
            policies=chosen.policies,
            corridor=chosen.corridor,
            reroutes=chosen.reroutes,
        )
        # the probe every branch dispatched: one per incident without a responder already on the way
        stations = self._city.scenario.ems_stations
        en_route = {ev.destination_segment for ev in self._city.state.emergency_vehicles if ev.status is EmergencyStatus.EN_ROUTE}
        probes = (
            [probe_for_incident(i, stations[0].edge) for i in incidents if i.location.segment_id not in en_route]
            if run.ems_probe and stations
            else []
        )
        network, validator = self._city.network, self._validator

        def apply(sim: TrafficSimulation) -> tuple[float, dict[str, str], int, list[str]]:
            # one command on the live thread: validation, dispatch and install happen between the same two steps
            programs = {iid: sim.get_signal_program(iid) for iid in network.intersections}
            if findings := validation_findings(plan, programs, network, validator):
                raise PlanRejected("; ".join(findings))
            dispatched = [
                sim.spawn_emergency_vehicle(p.origin_segment, p.destination_segment, p.position_m, p.lane)
                for p in probes
            ]
            installed, diverted = apply_plan(sim, plan)
            return sim.sim_time, installed, diverted, [d.id for d in dispatched]

        now, installed, diverted, dispatch_ids = await self._city.run_on_live(apply)
        staleness = round(now - run.snapshot_sim_time, 1) if run.snapshot_sim_time is not None else None
        implementation = Implementation(
            run_id=run.id,
            candidate_id=chosen.id,
            candidate_name=chosen.name or chosen.id,
            implemented_by=by,
            incident_ids=[i.id for i in incidents],
            implemented_at=now,
            snapshot_sim_time=run.snapshot_sim_time,
            staleness_s=staleness,
            policies=installed,
            corridor=plan.corridor is not None,
            diverted=diverted,
            ems_dispatch_ids=dispatch_ids,
        )
        if plan.policies or plan.corridor or plan.reroutes:
            self._standing.append(plan)
        run.implementation = implementation
        self._city.publish_scenario(run)
        self._city.events.add(EventLevel.WARNING, _describe(implementation), now, run.incident_id)
        for listener in self.listeners:
            try:
                await listener(implementation)
            except Exception:  # noqa: BLE001 - the plan is live either way
                log.exception("implementation listener failed")
        return implementation

    async def _on_reset(self) -> None:
        self._standing.clear()  # a reboot restores the base programs and drops corridors and diversions


def _describe(impl: Implementation) -> str:
    parts = [f"{i}: {p}" for i, p in sorted(impl.policies.items())]
    if impl.corridor:
        parts.append("EMS green corridor on")
    if impl.diverted:
        parts.append(f"{impl.diverted} vehicles diverted")
    if impl.ems_dispatch_ids:
        parts.append(f"{', '.join(impl.ems_dispatch_ids)} dispatched")
    what = "; ".join(parts) or "no signal change (keep current timing)"
    stale = f", {impl.staleness_s:.0f}s after the snapshot" if impl.staleness_s is not None else ""
    return f"{impl.implemented_by.capitalize()} applied {impl.candidate_name} ({impl.run_id}) to the live city{stale}: {what}"
