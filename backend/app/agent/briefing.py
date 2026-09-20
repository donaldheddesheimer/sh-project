"""What an analyst is told, and how simulated results are shown to it.

Both analyst paths need the same two things: the workflow prompt and a compact, model-readable row per
candidate. The MCP server (``app/api/mcp_tools.py``) serves them to any MCP client; the REST provider
(``app/agent/nemotron.py``) puts them straight in a chat message. They live here so the agent layer never
has to reach into the API layer for them.

Nothing here touches MCP, FastAPI or a service: these are pure views over domain models.
"""

from __future__ import annotations

from app.models.domain import SimulationCandidate, TrafficMetrics

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
   `experience`, when present, holds lessons from earlier episodes (a playbook and
   the most similar past incidents); recall_experience returns more. Use lessons to
   choose what to simulate first; they never replace simulating.
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
6. implement_recommendation (when enabled): applies your recommendation to the LIVE
   city. It is re-validated against the live signals first; nothing else can be
   applied. The live result is measured afterwards and becomes the next lesson.

Reading results. Lower is better for delay_s (time lost per vehicle served,
including waiting to enter), max_queue (halted vehicles on the worst segment) and
ems_response_s (the test ambulance's realised response time; null = it did not
arrive within the horizon). Higher is better for throughput_vph. vs_baseline gives
candidate minus baseline. Never make EMS response clearly worse to gain delay.
Plans that break safety limits are rejected and never simulated.
"""

# The plan-design and result-reading halves of INSTRUCTIONS, without the tool-calling workflow. The one-shot
# REST provider has no tools, so telling it to call start_analysis would describe steps it cannot take.
PLAN_DESIGN = """\
You are the analyst in a city traffic operations center. One or more collisions are
blocking traffic. Propose candidate responses; they are simulated in a SUMO digital
twin before any of them is recommended, and you never apply anything yourself.

If several incidents are listed, solve them TOGETHER: one plan may combine timing
changes, a corridor and reroutes that address all of them. A plan combines any of:
- policies: signal timing changes. They change existing phase durations and/or the
  offset only (phase index -> new seconds). Movements that run together never
  change. Give green to the incident approach downstream to flush the queue, or
  take it away upstream to meter inflow.
- corridor: EMS green-corridor pre-emption ahead of responders (intersection_ids
  empty = all signals on the route).
- reroutes: a diversion advisory that avoids given segments, with a compliance
  share of 0-1.
Every plan needs a unique id, a short name and a description. standing_responses are
plans already applied to the live city; every plan simulated starts with them, and a
plan that changes the same intersections replaces them.

Reading results. Lower is better for delay_s (time lost per vehicle served,
including waiting to enter), max_queue (halted vehicles on the worst segment) and
ems_response_s (the test ambulance's realised response time; null = it did not
arrive within the horizon). Higher is better for throughput_vph. vs_baseline gives
candidate minus baseline. Never make EMS response clearly worse to gain delay.
Plans that break safety limits are rejected and never simulated.
"""


def metrics_row(m: TrafficMetrics | None) -> dict | None:
    if m is None:
        return None
    return {
        "delay_s": round(m.mean_vehicle_delay, 1),
        "max_queue": m.max_queue_length,
        "max_queue_segment": m.max_queue_segment,
        "throughput_vph": round(m.throughput),
        "ems_response_s": round(m.emergency_vehicle_eta) if m.emergency_vehicle_eta is not None else None,
    }


def candidate_row(c: SimulationCandidate, baseline: dict | None) -> dict:
    row: dict = {"id": c.id, "name": c.name, "status": c.status.value}
    if (metrics := metrics_row(c.metrics)) is not None:
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
