"""MCP server: the scenario engine as tools for an agent (Nemotron, or any MCP client).

Mounted on the FastAPI app at /mcp (streamable HTTP). Tools are read-only or
simulation-only: plans are only ever executed inside SUMO branches, never on
the live signals. Every tool mutates the same ScenarioRun the REST API and
WebSocket expose, so the UI streams agent-driven analyses as they happen.

Spec: docs/specs/scenario-engine-mcp.md
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from app.agent.base import CandidatePlan
from app.models.domain import Recommendation, SimulationCandidate, TrafficMetrics
from app.models.scenario import ScenarioRun
from app.services.city import Conflict, NotReady
from app.services.scenarios import Analysis, ScenarioService

INSTRUCTIONS = """\
You are the analyst in a city traffic operations center. One or more collisions are
blocking traffic. Test candidate responses in a SUMO digital twin before recommending one.

Workflow:
1. start_analysis: freezes the city at this instant and returns the incident(s), road
   segments (worst congestion first), every signal's phases and standing_responses
   (plans already applied to the live city; every plan you simulate starts with them,
   and a plan that changes the same intersections replaces them). Every plan you
   simulate in this analysis starts from that same instant, so results are
   comparable. If several incidents are listed, solve them TOGETHER: one plan may
   combine timing changes, a corridor and reroutes that address all of them.
2. Design plans. A plan combines any of:
   - policies: signal timing changes. They change existing phase durations and/or
     the offset only (phase index -> new seconds). Movements that run together
     never change. Give green to the incident approach downstream to flush the
     queue, or take it away upstream to meter inflow.
   - corridor: EMS green-corridor pre-emption ahead of responders
     (intersection_ids empty = all signals on the route).
   - reroutes: a diversion advisory that avoids given segments, with a compliance
     share of 0-1.
   Every plan needs a unique id, a short name and a description.
3. validate_plan (optional): a cheap safety check. Fix violations before simulating.
4. simulate_plans: validates and simulates plans in parallel. The do-nothing
   baseline is included automatically. Call it again to try refinements.
5. submit_recommendation: the winning completed candidate, with a rationale that
   quotes the numbers.

Reading results. Lower is better for delay_s (time lost per vehicle served,
including waiting to enter), max_queue (halted vehicles on the worst segment) and
ems_response_s (the test ambulance's realised response time; null = it did not
arrive within the horizon). Higher is better for throughput_vph. vs_baseline gives
candidate minus baseline. Never make EMS response clearly worse to gain delay.
Plans that break safety limits are rejected and never simulated.
"""


@contextmanager
def _as_tool_errors() -> Iterator[None]:
    """Turn service errors into MCP tool errors the agent can read and act on."""
    try:
        yield
    except (Conflict, NotReady, ValueError) as exc:
        raise ToolError(str(exc)) from exc
    except KeyError as exc:
        raise ToolError(f"unknown id: {exc.args[0]}") from exc


def _metrics(m: TrafficMetrics | None) -> dict | None:
    if m is None:
        return None
    return {
        "delay_s": round(m.mean_vehicle_delay, 1),
        "max_queue": m.max_queue_length,
        "max_queue_segment": m.max_queue_segment,
        "throughput_vph": round(m.throughput),
        "ems_response_s": round(m.emergency_vehicle_eta) if m.emergency_vehicle_eta is not None else None,
    }


def _candidate(c: SimulationCandidate, baseline: dict | None) -> dict:
    row: dict = {"id": c.id, "name": c.name, "status": c.status.value}
    if (metrics := _metrics(c.metrics)) is not None:
        row.update(metrics)
        if baseline is not None and c.id != "baseline":
            row["vs_baseline"] = {
                key: round(metrics[key] - baseline[key], 1)
                for key in ("delay_s", "max_queue", "throughput_vph", "ems_response_s")
                if metrics[key] is not None and baseline[key] is not None
            }
    if c.violations:
        row["violations"] = c.violations
    if c.notes:
        row["notes"] = c.notes
    return row


def _baseline_metrics(run: ScenarioRun) -> dict | None:
    baseline = next((c for c in run.candidates if c.id == "baseline"), None)
    return _metrics(baseline.metrics) if baseline else None


def _run_summary(run: ScenarioRun, candidates: list[SimulationCandidate] | None = None) -> dict:
    baseline = _baseline_metrics(run)
    summary = {
        "run_id": run.id,
        "status": run.status.value,
        "incident_id": run.incident_id,
        "snapshot_sim_time": run.snapshot_sim_time,
        "horizon_s": run.horizon_s,
        "candidates": [_candidate(c, baseline) for c in (candidates if candidates is not None else run.candidates)],
    }
    if run.recommendation is not None:
        summary["recommendation"] = run.recommendation.model_dump()
    if run.error:
        summary["error"] = run.error
    return summary


def _incident(incident, by_id: dict) -> dict:
    seg = by_id.get(incident.location.segment_id)
    return {
        "id": incident.id,
        "type": incident.type.value,
        "severity": incident.severity.value,
        "description": incident.description,
        "segment_id": incident.location.segment_id,
        "street": f"{seg.name} {seg.direction}" if seg else None,
        "upstream_intersection": seg.source if seg else None,
        "downstream_intersection": seg.destination if seg else None,
        "blocked_lanes": incident.affected_lanes,
        "total_lanes": incident.total_lanes,
    }


def _standing(plan: CandidatePlan) -> dict:
    return {
        "id": plan.id,
        "name": plan.name,
        "policies": [
            {"intersection": p.intersection_id, "phase_durations_s": p.phase_durations, "offset_s": p.offset_s}
            for p in plan.policies
        ],
        "corridor": plan.corridor is not None,
        "reroutes": [{"avoid": r.avoid_segment_ids, "compliance": r.compliance} for r in plan.reroutes],
    }


def _context(a: Analysis, max_candidates: int) -> dict:
    ctx = a.context
    by_id = {s.id: s for s in ctx.segments}
    segments = sorted(ctx.segments, key=lambda s: s.congestion, reverse=True)
    return {
        "run_id": a.run.id,
        "snapshot_sim_time": ctx.sim_time,
        "horizon_s": a.run.horizon_s,
        "candidate_limit": max_candidates,
        # the primary incident (earliest detected), and every incident this analysis must solve together
        "incident": _incident(ctx.incident, by_id),
        "incidents": [_incident(i, by_id) for i in ctx.all_incidents],
        # responses already applied to the live city: every branch starts with them, and a plan that changes
        # the same intersections replaces them
        "standing_responses": [_standing(p) for p in ctx.standing],
        "ems": {
            "probe": bool(a.probes),
            "origin_segment": ctx.ems_origin_segment,
            "en_route": [ev.id for ev in ctx.emergency_vehicles if ev.status.value == "en_route"],
        },
        "segments": [
            {
                "id": s.id,
                "street": f"{s.name} {s.direction}",
                "from": s.source,
                "to": s.destination,
                "level": s.level.value,
                "congestion": round(s.congestion, 2),
                "queue": s.halting_count,
                "speed_mps": round(s.average_speed, 1),
            }
            for s in segments
        ],
        "signals": {
            iid: {
                "program_id": program.program_id,
                "cycle_s": program.cycle_length,
                "phases": [
                    {
                        "index": p.index,
                        "kind": p.kind.value,
                        "duration_s": p.duration,
                        "serves": p.served_approaches,  # compass labels, which can repeat at one junction
                        "serves_segments": p.served_segments,  # the approaches themselves (incoming segment ids)
                    }
                    for p in program.phases
                ],
            }
            for iid, program in sorted(ctx.signal_programs.items())
        },
    }


def build_mcp(get_service: Callable[[], ScenarioService]) -> MCPServer:
    """``get_service`` resolves the ScenarioService at call time (it is built in the app lifespan)."""
    mcp = MCPServer(
        name="traffic-scenarios",
        title="Traffic scenario engine",
        description="Test incident responses in parallel SUMO branches of a live city digital twin.",
        instructions=INSTRUCTIONS,
    )

    @mcp.tool()
    async def start_analysis(
        incident_ids: Annotated[
            list[str] | None, Field(description="Incidents to solve together. Defaults to ALL active incidents")
        ] = None,
        horizon_s: Annotated[float | None, Field(ge=120, le=1800, description="Simulated seconds per plan")] = None,
        agent: Annotated[str, Field(description="Your name, shown to operators")] = "mcp-agent",
    ) -> dict:
        """Freeze the live city for analysis. Returns run_id, the incident(s), road segments (worst first), every
        signal's phases and the responses already in force. Only one analysis can be open at a time. It stays
        open until submit_recommendation."""
        service = get_service()
        with _as_tool_errors():
            if not incident_ids:  # every active incident, so simultaneous crashes are solved together
                incident_ids = [i.id for i in await service.city.smart_city.list_incidents()] or None
            analysis = await service.open(
                None, horizon_s, True, agent, idle_timeout_s=service.settings.scenario_idle_timeout_s,
                incident_ids=incident_ids,
            )
        try:
            await service.capture(analysis)
        except Exception as exc:
            service.fail(analysis, f"{type(exc).__name__}: {exc}")
            raise ToolError(f"could not snapshot the simulation: {exc}") from exc
        return _context(analysis, service.settings.scenario_max_candidates)

    @mcp.tool()
    async def validate_plan(run_id: str, plan: CandidatePlan) -> dict:
        """Check a plan against the signal safety limits without simulating it. An empty violations list means
        it is safe to simulate."""
        service = get_service()
        with _as_tool_errors():
            violations = service.check_plan(service.analysis(run_id), plan)
        return {"plan_id": plan.id, "safe": not violations, "violations": violations}

    @mcp.tool()
    async def simulate_plans(run_id: str, plans: list[CandidatePlan]) -> dict:
        """Validate the plans and simulate the safe ones in parallel from the analysis snapshot, over the run's
        horizon, with a test ambulance dispatched in every branch. The baseline is added on the first call.
        Blocks until every branch finishes (tens of seconds). Returns this round's results with deltas against
        the baseline."""
        service = get_service()
        with _as_tool_errors():
            analysis = service.analysis(run_id)
            candidates = await service.evaluate(analysis, plans)
        summary = _run_summary(analysis.run, candidates)
        summary["candidates_used"] = len(analysis.run.candidates)
        return summary

    @mcp.tool()
    async def get_analysis(run_id: str) -> dict:
        """Every candidate of the analysis so far (or of a finished one), with deltas against the baseline."""
        service = get_service()
        with _as_tool_errors():
            run = service.get(run_id)
            if run.status.value not in ("completed", "failed"):
                service.analysis(run_id)  # counts as activity for the idle timeout
        return _run_summary(run)

    @mcp.tool()
    async def submit_recommendation(
        run_id: str,
        candidate_id: Annotated[str, Field(description="A completed candidate, or 'baseline' to change nothing")],
        summary: Annotated[str, Field(description="One sentence for the operator")],
        rationale: Annotated[list[str], Field(description="Evidence: before/after figures and why the runner-up lost")],
    ) -> dict:
        """Close the analysis with your recommendation. It is shown to operators and written to the ops log.
        Nothing is applied to the live signals."""
        service = get_service()
        with _as_tool_errors():
            analysis = service.analysis(run_id)
            if analysis.busy:
                raise Conflict(f"{run_id} is still simulating")
            run = service.finish(
                analysis, Recommendation(candidate_id=candidate_id, summary=summary, rationale=rationale)
            )
        return _run_summary(run)

    return mcp
