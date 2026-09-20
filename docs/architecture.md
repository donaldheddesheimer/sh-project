# Architecture and design notes

## Components and boundaries

| Layer | Module | Knows about | Never knows about |
|---|---|---|---|
| UI | `frontend/` | REST + WebSocket payloads | SUMO, providers |
| API | `app/api/routes.py` | `CityService`, `ScenarioService` | TraCI |
| Agent tools | `app/api/mcp_tools.py` (MCP at `/mcp`) | `ScenarioService`, `Implementor`, `ExperienceStore` | signal states (only the implementor applies a run's recommendation) |
| Episodes | `app/learning/` | `CityService`, `ScenarioService`, the analysts and reviewer | SUMO specifics |
| Orchestration | `app/services/city.py` | provider **interfaces**, `TrafficSimulation`, runner | mock vs NVIDIA, SUMO specifics |
| Scenario analysis | `app/services/scenarios.py` | `CityService`, `AgentProvider`, `SafetyValidator`, a branch factory | SUMO specifics, which agent drives it |
| Branching | `app/simulation/branching.py` | `TrafficSimulation`, `CandidatePlan` | agents, the run it belongs to |
| Incidents | `app/smart_city/` | its data source | how incidents are used |
| Decisions | `app/agent/` | `IncidentContext` → `CandidatePlan` / `Recommendation` | signal hardware, TraCI |
| Safety | `app/safety/validator.py` | `SignalPolicy` / `EmergencyCorridor` + base `SignalProgram` | who proposed the plan |
| Simulation | `app/simulation/` | SUMO / TraCI | agents, LLMs, providers |

## Live data flow

```
SUMO ──TraCI──► SumoSimulation.step()          (runner thread, paced to the time scale)
                   │ subscriptions: vehicles, edges, signals, departures/arrivals
                   ▼
        LiveSimulationRunner._publish()  ≤ BROADCAST_HZ ── LiveFrame(NetworkState)
                   │ loop.call_soon_threadsafe
                   ▼
        CityService._handle_frame()      (asyncio)
           ├─ frame observers (MockSmartCityProvider.observe → incident events)
           ├─ trend sampling, responder tracking, ops events
           └─ CityState ──► ConnectionHub.broadcast() ──► /ws/state clients
```

- TraCI is blocking and not thread-safe, so **one thread owns the live connection**.
  API commands are closures queued to that thread and run between steps
  (`runner.call(lambda sim: sim.inject_collision(...))`), which gives ordered,
  race-free control.
- The WebSocket hub gives each client a small drop-oldest buffer, so a slow browser can
  never stall the simulation or other clients.
- New clients get a `hello` with network geometry, current state, the recent ops log and the
  metric trend, so a reload mid-incident still shows the pre-incident baseline. Selecting a
  different bundled map replaces the service graph and sends every connected client a fresh
  `hello`, clearing the previous map's local state without disconnecting the WebSocket.

## Analyze Response pipeline

One run is one snapshot of the live twin, branched into a baseline plus candidate
responses. `ScenarioService` (`app/services/scenarios.py`) owns it, one run at a time, and
broadcasts the `ScenarioRun` as a `scenario` WebSocket message on every change. Nothing in
the pipeline touches live signals: plans only ever execute inside SUMO branches. Applying a
recommendation to the live city is a separate, gated step (see
[Autonomous episode](#autonomous-episode-applearning)).

```
live twin ─► detect ─► trigger ─► capture ─► propose ─► validate ─► simulate ×N ─► recommend ─► UI
  SUMO       Smart     REST or    snapshot    agent      safety     fresh SUMO     agent       advisory
             City      MCP                                          processes
```

| # | Stage | Code | Input | Output |
|---|---|---|---|---|
| 0 | Live twin | `simulation/sumo.py`, `simulation/runner.py` | network, demand, operator commands (inject, dispatch, speed) | `NetworkState` frames (≤ 8 Hz) → `CityState` on `/ws/state` |
| 1 | Detect | `smart_city/mock.py` | the disruptions in each frame (ground truth) | `Incident` (`INC-0001`: segment, lanes, position, severity, cameras) after a 4 s detection delay |
| 2 | Trigger | `POST /api/scenarios/run` → `ScenarioService.open`, or MCP `start_analysis` | `ScenarioRunRequest {incident_id?, horizon_s=600, ems_probe=true}` | `ScenarioRun` `SCN-0001`, status `queued` (HTTP 202). 409 without an active, map-matched incident or while a run is open; 404 for an unknown incident; 503 before the simulation is ready. The live speed is held at 1× while the run is open and restored when it ends (finish, fail or abandon); a speed change by the operator wins and ends the hold |
| 3 | Capture | `ScenarioService.capture`, on the live thread | the live simulation at one instant | `SimulationSnapshot` (SUMO state + RNG, disruptions, dispatches), every `SignalProgram`, the agent's `IncidentContext`, and an EMS `ProbeSpec` (Fire Station 3 → 20 m behind the crash) unless a live responder is already en route. Status `proposing` |
| 4 | Propose | `AgentProvider.propose_candidates` (`agent/mock.py`) | `IncidentContext {incident, segments, intersections, signal_programs, emergency_vehicles, ems_origin_segment}` | `CandidatePlan[]`, each any mix of `SignalPolicy` timing changes, one `EmergencyCorridor` and `RerouteAction`s. Baseline forced first; at most 9 (`SCENARIO_MAX_CANDIDATES`, baseline included) |
| 5 | Validate | `validation_findings` → `RuleBasedSafetyValidator` | each plan and the captured programs | `violations[]`; a plan with any is `rejected` and never simulated |
| 6 | Simulate | `branching.run_branch` on a 4-worker pool | snapshot, plan, probe, horizon | `SimulationCandidate`: `completed` with horizon `TrafficMetrics`, a 30 s `timeline`, `notes` and `wall_time_s`, or `failed` with the error in `notes`. Status `simulating` |
| 7 | Recommend | `AgentProvider.recommend` | the context and every candidate | `Recommendation {candidate_id, summary, rationale[]}` naming a completed candidate (else the baseline). Status `completed`, a summary in the ops log, the snapshot file deleted |
| 8 | Present | `frontend/src/components/plans/` | `scenario` messages and `hello.data.scenario` | plan cards with deltas against the baseline, rejection reasons, a KPI comparison, a horizon chart, map overlays. Advisory until applied |

**A branch** (stage 6) is a brand-new SUMO process: restore the snapshot, dispatch the EMS
probe (before the plan, so every branch sends it at the same moment), apply the timing
policies, enable the corridor, activate the diversion, then run the horizon. A failure
marks that candidate `failed` and never sinks the run.

**Horizon metrics.** Mean delay is the time lost per vehicle served during the window,
including time spent waiting to enter the network. Max queue counts halted vehicles on
the worst segment. Throughput is completed trips per hour. EMS response is the probe's
realised dispatch-to-arrival time, or `null` if it did not arrive within the horizon.

**Safety runs twice.** Before simulation, the validator rejects plans (stage 5). Inside a
branch, the pre-emption controller passes every signal command through
`check_transition` (`simulation/preemption.py`), and a violation fails the branch. On the live
twin a refused pre-emption drops the corridor and records a note starting `pre-emption disabled:`
instead of failing. `revert_response()` takes a whole response back off the live twin (no caller yet).

**Mock recommendation rule.** Drop candidates whose EMS response is more than 10% slower
than the baseline's, then take the lowest mean delay. A candidate that improves EMS
response by 60 s or more, with delay within 5% of the best, wins instead.

**Two drivers, same steps.** The REST pipeline runs stages 2–7 once with the configured
`AgentProvider`. An MCP agent runs them itself (see [MCP tools](#mcp-tools-for-agents)) and
may simulate several rounds from the same snapshot before it recommends.

**Measured on main** (2026-09-19, analysis started about 75 s after the crash): a full
mock run took 26 s of wall time, with 7 branches simulated on 4 workers at 10–16 s each.

## Simulation model (`simulation/`)

**Network.** `build_network.py` writes plain node/edge XML with real street names and
runs `netconvert`. The grid is 3×3 with 250 m blocks and 200 m approach roads. Main St
and Central Ave have 2 lanes per direction at 50 km/h; the other streets have 1 lane at
40 km/h. All 9 junctions are signalized with a 90 s fixed-time plan (40 s green, 3 s
yellow, 2 s all-red). Offsets form an **eastbound green wave** (18 s per block),
computed from each program's actual phase order, because the phase index serving Main St
differs by junction.

**Demand.** There are 132 Poisson-like flows between all boundary roads (about 3,100
veh/h). 70% of trips go straight through, and Main St eastbound (750 veh/h) is the peak
direction. The baseline is healthy: about 30 s mean delay, no teleports, and every queue
clears within a cycle.

**Habitual routing.** `device.rerouting.adaptation-interval = 0`. By default SUMO routes
each new trip with live travel times, which amounts to perfect-information diversion
that dissolves incidents instantly. Drivers here take habitual free-flow routes, and
diversion is an explicit response: a `RerouteAction` advises a share of the drivers
headed into the blocked segment to divert, now and for later departures
(`simulation/reroute.py`).

**Collision model.** `inject_collision()` inserts two stopped `crash` vehicles in the
blocked lane (held by a SUMO stop, so it survives snapshots). Traffic on the open lanes
within −40 m…+8 m of the scene is slowed to a *pass speed* (major: 0.6 m/s, meaning
responders waving traffic past, so the open lane carries about a third of the link's
750 veh/h). The effect is re-derived every step from the disruption registry, so
restored branches reproduce it. With these settings the crash link goes severe in about
2 min and spills back two blocks in about 5 min, and network delay roughly doubles.

**Congestion index.** Per segment, raw = speed deficit × min(1, density / 0.04 veh/m),
**averaged over one 90 s signal cycle**. Normal red-light queues cancel out, while
persistent incident queues stand out. Measured on this grid, the baseline 99th
percentile is 0.39 and crash links reach 0.95+. Thresholds: free < 0.30 ≤ moderate
< 0.55 ≤ heavy < 0.80 ≤ severe.

**Metrics** (`TrafficMetrics`):
- *Live* values describe the current instant: mean accumulated time loss of vehicles in
  the network, worst halting queue, trips completed over the last 5 min as veh/h, and
  mean speed.
- *Horizon* values (from `run_for`) cover a window: time loss accrued in the window per
  vehicle served, **including time spent waiting to enter the network**, so a policy
  can't look good by blocking entries. They also include the realized EMS response time
  (`emergency_vehicle_eta`: the last responder to arrive) and `emergency_responses`, one entry
  per responder. On live metrics `emergency_vehicle_eta` is instead the soonest estimated ETA
  among en-route responders, and `emergency_responses` is always empty.

**EMS.** `spawn_emergency_vehicle()` routes an `ems` vehicle to a stop 20 m behind the
crash in the blocked lane. The live ETA uses smoothed segment speeds, the responder's own
recent speed on its current segment (so being stuck shows), and 10 s expected wait per
signal ahead.

**Branching and determinism.** `save_snapshot()` writes SUMO state with the RNG state
and 8-digit precision, and records disruptions and dispatches alongside it. Verified
behavior:
- Fresh SUMO processes restored from one snapshot evolve bit-identically (test:
  `test_fresh_branches_from_one_snapshot_are_identical`).
- Re-loading into a process that has already run diverges, because internal SUMO state
  leaks.
- SUMO saves the state of every signal program variant by id, and `loadState` refuses an
  id the process does not know (`Unknown program`). A timing policy applied to the live city
  installs such a program (`policy-N`), so the snapshot records every runtime-installed
  program (`custom_programs`) and `restore_snapshot` re-creates them before loading. The
  branch then starts with the live signals exactly as they were. Corridors, diversions and
  pending offsets live in Python and are replayed from the standing responses instead. This
  fix was checked against the SUMO source, not by a run.

**So every candidate must run in a fresh process**, which also makes parallel candidate
evaluation natural. A 600 s horizon of the healthy network takes under 1 s headless, but
the post-crash network takes 6–8 s alone and 10–16 s with 4 branches in parallel. About
half of that was `_apply_rubbernecking`, which made a TraCI position lookup per vehicle per
step on the crash link's open lanes; lane id and lane position now ride on the existing
vehicle subscription, so that per-step bookkeeping makes no extra round trip. The figures
above are the ones measured before the change; **the speed-up has not been timed**.

## Provider boundaries

**Smart City.** `SmartCityProvider` is `start(emit)`, `list_incidents`, `get_incident`,
`list_cameras` and `status`; `simulation_is_source` says whether reports originate in the twin.
- `MockSmartCityProvider` stands in for video analytics. It watches the disruptions the
  simulation models (ground truth) and reports each as an `Incident` after a detection
  delay. It fills in named location text, sensor ids of the adjacent intersection
  cameras, and object ids. It receives frames through a factory-registered observer, so
  `CityService` doesn't know it exists.
- `NvidiaSmartCityProvider` polls the **VSS Video Analytics MCP server** through the MCP SDK,
  or a development-only VSS-shaped replay file on simulation time. `vss_mapping.py` contains
  every document-field assumption; `matching.py` resolves geometry first, then normalized
  place names and registered sensors, with an explicit confidence and lane-0 assumption.
  `CityService` reconciles each active matched external collision to exactly one linked twin
  disruption through the runner thread, clears it with the report, and restores it once after
  reset warm-up. Non-collisions and unmatched reports remain visible but cannot be analyzed.
  Provider failures back off without stopping frames; `/api/smart-city/status` exposes health.
  This was checked against the published VSS 3.2 tool reference, not a running endpoint.

**Agent.** `AgentProvider` is `propose_candidates(IncidentContext)` and
`recommend(context, results)`. For a blocked link the mock proposes:
- timing: downstream flush, upstream metering and cross-street relief;
- `ems-corridor`, `corridor-plus-divert` and `corridor-plus-meter` (pre-emption, at a 350 m
  detection distance), when an EMS origin or responder exists;
- `divert-advisory`, a 30% compliance diversion around the blocked segment;
- `aggressive-flush`, which is deliberately unsafe (an 8 s green), so the demo always shows
  the validator rejecting a plan.

With lessons in the context (see [Autonomous episode](#autonomous-episode-applearning)),
the mock prunes or reorders these proposals. `NemotronAgentProvider` drives REST Analyze
Response through one NIM proposal call and one recommendation call. It validates JSON into
`CandidatePlan` / `Recommendation` data only; ScenarioService still validates plans, simulates
them and accepts only a completed candidate. A provider failure fails the run visibly rather
than silently changing providers. A proposal
that returns nothing valid is retried once with the validation errors appended to the conversation,
because re-sending the identical prompt only resamples it. Nemotron also drives the
[MCP tools](#mcp-tools-for-agents) in episodes (`learning/analysts.py`).

`agent/briefing.py` holds what both analysts share: the candidate rows they read results from, the MCP
server's `instructions` workflow prompt, and `PLAN_DESIGN`, the same plan-design and result-reading
guidance without the tool-calling steps. The one-shot REST provider has no tools, so it is given
`PLAN_DESIGN` rather than a workflow it cannot follow.

**Safety.** `RuleBasedSafetyValidator` checks min/max green (including a pedestrian
floor), non-shortened yellow and all-red clearance, cycle bounds, and offset range.
Incompatible movements can't arise, because a `SignalPolicy` changes durations and
offsets only; phase states always come from the base program. `validate_corridor` bounds
a pre-emption request: known signals, a served green of at least the pedestrian minimum
before a green may be cut, a hold ceiling, a 30–400 m detection distance, and a program in
which every green runs into a yellow and then an all-red. At runtime the controller checks
every command with `check_transition`; an audit of 2,778 realised signal changes across all
branches found none unsafe. A production version would load agency timing sheets and the
conflict-monitor matrix.

## MCP tools (for agents)

Served at `/mcp` (streamable HTTP, stateless, JSON responses). The server's `instructions`
describe the workflow and the metrics and can serve as the agent's system prompt. Spec
and client snippet: [docs/specs/scenario-engine-mcp.md](specs/scenario-engine-mcp.md).

| Tool | Pipeline stages | Returns |
|---|---|---|
| `start_analysis(incident_ids?, horizon_s?, agent, memory_mode?)` | trigger + capture | run id, the incident(s) (default: every active one), segments (worst congestion first), every signal's phases, standing responses, and `experience` (playbook + similar past episodes) when memory mode is `use`; `ignore` makes a no-recall control |
| `validate_plan(run_id, plan)` | validate | `{safe, violations}` without simulating |
| `simulate_plans(run_id, plans[])` | validate + simulate, one round | this round's candidates with deltas against the baseline (blocks while the branches run; baseline added on the first round) |
| `get_analysis(run_id)` | none | every candidate so far |
| `submit_recommendation(run_id, candidate_id, summary, rationale[])` | recommend | the completed run |
| `implement_recommendation(run_id)` | implement (gated by `AGENT_MAY_IMPLEMENT`) | the `Implementation`: programs now running, EMS probes dispatched, staleness |
| `recall_experience(run_id, limit?)` | none | the playbook and more similar past episodes |

Tool runs mutate the same `ScenarioRun` the REST API exposes, so the UI streams an agent's
analysis as it happens. An agent run that goes `SCENARIO_IDLE_TIMEOUT_S` (300 s) without a
tool call is failed, releasing the one-run lock. Every tool is read-only or runs inside a
SUMO branch, except `implement_recommendation`, which can apply only the run's own
recommendation, re-validated on the live programs.

## Autonomous episode (`app/learning/`)

An episode is one agent answering one set of active incidents, from detection to a stored
lesson. `EpisodeService` is the only thing that starts an agent, and only while a demo
script is armed. The README's
[autonomous episode section](../README.md#the-autonomous-self-learning-episode) has the
rules (two-crash rule, decisions, thresholds). Nemotron is the team every episode uses; there is
no runtime selection, and no fallback to mock when `NVIDIA_API_KEY` is absent (its calls fail). `operator-collision` is armed at
startup, so a collision pauses the city and the episode waits (`awaiting`) for
`POST /api/demo/analyze`. This table is the code path.

| # | Stage | Code | Output |
|---|---|---|---|
| 1 | Script | `EpisodeService.start_demo` → `CityService.set_scripted_events` → `LiveSimulationRunner` | scheduled crashes fire on the runner thread; an empty script waits for operator injection; episode `armed` |
| 2 | Detect | `MockSmartCityProvider` → `CityService.incident_listeners` → `EpisodeService._on_incident` | episode `detected` over every active incident, or the working one superseded. An operator script instead goes `awaiting`: the city is paused and `EpisodeService.analyze` (`POST /api/demo/analyze`) starts stage 3 |
| 3 | Analyze | `MockAnalyst` (`ScenarioService.run_pipeline`) or `ModelAnalyst` (MCP client driven by Claude or Nemotron, optional fallback to the mock) | a completed `ScenarioRun`; episode `analyzing` |
| 4 | Implement | `Implementor.implement(run_id, by)` in one `run_on_live` command | re-validation on live programs, EMS probes, `apply_plan`; `Implementation` on the run and the episode; the plan joins the standing responses; episode `monitoring`; the city resumes (`_on_implemented`) |
| 5 | Monitor | `LiveMonitor` (a frame observer) | `LiveSample` every 5 simulated seconds; a `LiveRecord` after the window |
| 6 | Score | live `response_notes()` → `build_scorecard` | `Scorecard` (code only): predicted vs realised on absolute simulation time, typed corridor/diversion checks, provisional state, outcome |
| 7 | Review | `MockReviewer` / selected Claude or Nemotron `ModelReviewer` | `Lesson` (verdict = the scorecard's outcome); model errors fail the episode rather than changing providers; episode `reviewing` |
| 8 | Remember | `ExperienceStore.save` | `memory/episodes/EP-NNNN.md` + `playbook.md`; episode `completed`; live sim paused |
| 9 | Recall | `ScenarioService.lessons_source` → `ExperienceStore.recall` | structured/semantic/combined/ranking scores and query-scoped trust in `IncidentContext.lessons`, `ScenarioRun.recalled`, MCP `experience` |

Hooks (`incident_listeners`, `reset_listeners`, implementation listeners, the monitor) run
inside the frame pipeline or a request, so they change state synchronously and spawn tasks
for slow work. The implementor's live apply runs in a shielded task: once started, a plan
that reached the live signals is always recorded as standing, even if its agent is
superseded mid-apply.

Response checks determine trust, not the scorecard verdict. A corridor needs a recorded
pre-emption and a diversion needs a recorded diverted vehicle; failed or unavailable evidence
makes the stored lesson provisional and caps confidence at 0.4. Recall discounts an
unconfirmed provisional lesson. It trusts one for the current query only after two distinct,
verified experiences with the same plan family and verdict also score at least 0.75 by the
structured matcher. The mock may reorder on any recalled lesson, but only trusted structured
matches at or above 0.75 may prune candidates.

Diversion attribution is deliberately conservative. `Implementation.diverted` is the count
returned by the current apply call and can prove success. The monitor-close response note is
a lifetime total across every advisory still active in the shared controller: zero proves
explicit failure, but a positive total cannot be assigned to the newest response and remains
unknown unless its apply-time count was already positive.

Memory mode and recall provenance are persisted on scenario, episode and durable experience.
An `ignore` control remains in the learning report but is never eligible for recall or the
playbook. Optional `NimEmbedder` calls `/embeddings` asynchronously: current situations use
`input_type=query`, experiences use `passage`, and sidecars invalidate on model or input hash.
The blend is `max(structured, min(0.74, 0.65 × structured + 0.35 × semantic))`; semantic scores
can raise recall and reorder candidates but can never satisfy the structured pruning threshold.
`GET /api/learning/report` compares warm runs against same-script controls and exposes the
configured transfer signals in the episode panel without claiming measurements not in the data.
