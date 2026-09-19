# Traffic Operations Center: simulation-backed incident response

A city traffic operations center that extends the NVIDIA Smart City blueprint idea:
when a camera-detected incident (collision, stalled vehicle, …) hits the network,
candidate responses are **tested in a SUMO traffic simulation before anything is
recommended**. The experiments are already exposed as MCP tools; in milestone 3 a
Nemotron agent drives them.

This repository has completed **milestone 2**. It has a live SUMO digital twin of a 3×3
downtown grid and a FastAPI backend that streams city state over WebSocket. There is a
React/MapLibre operations console: inject a collision, watch the queue spill back, then
click **Analyze Response** to test up to 8 candidate plans in parallel SUMO branches.
Those plans include signal timing, an EMS green corridor and a diversion advisory. The
console compares each plan against the baseline and recommends one. Everything runs on a
laptop with no GPU. NVIDIA components plug in behind interfaces that already exist; the
adapters are still stubs.

## Quick start

Requirements: Python ≥ 3.11, Node ≥ 22.12. SUMO is installed from PyPI (`eclipse-sumo`), so
there is no system package, no GPU and no Docker.

```bash
make setup   # backend/.venv (FastAPI + SUMO wheels) and frontend/node_modules; safe to re-run
make dev     # backend on :8000 + UI on :5173
```

Open http://localhost:5173. Or run the two halves in separate terminals:

```bash
make backend    # cd backend && .venv/bin/uvicorn app.main:app --port 8000
make frontend   # npm --prefix frontend run dev
```

`make setup` keeps an existing venv and only syncs it with `backend/requirements*.txt`.
Re-run it after pulling a branch that adds a dependency (milestone 2 added `mcp`).
It uses `uv` when installed, and pip otherwise.

Without `make`:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
npm --prefix frontend install
(cd backend && .venv/bin/uvicorn app.main:app --port 8000) &
npm --prefix frontend run dev
```

On **Windows** the Makefile and `scripts/dev.sh` don't work (they assume `.venv/bin/`). Run
the pieces directly in PowerShell instead, the backend and frontend in separate terminals:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm --prefix frontend install
cd backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
npm --prefix frontend run dev
cd backend; .venv\Scripts\python.exe -m pytest -q      # tests
```

Other commands:

| | |
|---|---|
| `make test` | backend test suite (spawns real SUMO processes, 20–40 s) |
| `make build` | type-check and production-build the UI |
| http://localhost:5173/?fixture=scenario | Analyze Response replays a recorded run (synthetic numbers) instead of calling `POST /api/scenarios/run`; `?fixture=scenario-failed` replays the failure path. Only the analysis call is replaced: the backend must still be running and a collision active, because the map and the button's prerequisites come from the live stream |
| `make network` | regenerate the SUMO network and demand from their build scripts |
| `SUMO_GUI=true make backend` | watch the live simulation in sumo-gui as well |
| http://127.0.0.1:8000/docs | interactive API docs |

## Demo script

1. The network warms up (5 simulated minutes, about 1 s) and runs at 4× real time. Roads
   are colored by cycle-averaged congestion, signal heads show the live phase, and
   vehicles move.
2. **Inject Collision**: two crashed vehicles block the right lane of Main St
   eastbound between Central Ave and Pine Ave. Traffic squeezes past at walking pace.
3. About 4 simulated seconds later the (mock) Smart City provider reports **INC-0001**
   from cameras CAM-C2/CAM-B2. The incident card, map marker and ops log light up.
4. Within 2–3 simulated minutes the link goes **severe**, the queue spills back past
   Central Ave onto Main St toward Oak Ave, and Central Ave starts to back up. Network
   delay roughly doubles (KPI tiles show the delta against pre-incident values).
5. **Analyze Response** (top bar, once the incident is detected). The backend snapshots
   the live network, then simulates the baseline and each candidate plan for 10 minutes
   in its own fresh SUMO process, 4 at a time. It dispatches an EMS probe from Fire
   Station 3 in every branch. The run takes about 25 s, and the live simulation keeps
   running meanwhile. The panel shows the following:
   - Plan cards appear as each branch finishes. Each shows delay, max queue, throughput
     and EMS response time, with the change against the baseline.
   - `aggressive-flush` comes back **rejected by the safety validator** (an 8 s green is
     below the 12 s pedestrian minimum) and is never simulated.
   - A recommendation with its rationale. In the measured run below it was the
     diversion advisory: mean delay down 15% and EMS response down 17% (4:52 → 4:03).
   - The comparison dock, horizon chart and map overlays show what each plan changes.
6. **Dispatch EMS**: EMS-1 leaves Fire Station 3 and the live ETA tracks it. It
   typically loses a couple of minutes in the incident queue.
7. **Clear scene** reopens the lane; **Reset** restores a clean network.

Nothing in the analysis changes the live signals: recommendations are advisory.

Use the speed buttons (1×–16×) to fast-forward. Click an intersection or road to inspect
its phase, queues and speed.

## Autonomous demo episode (in progress)

The next demo runs itself. The "live city" is a SUMO simulation standing in for real
camera data, and an agent responds to it end to end, then remembers what happened:

```
scripted crash ─► live sim plays it as "real data" ─► crash detected, state sent to the agent
   ─► agent tests alternatives in parallel branches (the live view keeps running)
   ─► agent's chosen plan is IMPLEMENTED on the live sim (really applied)
   ─► live data is cached for a fixed number of simulated seconds
   ─► reviewer agent condenses it into a lesson ─► live collection stops
   ─► lesson stored in the RAG memory ─► episode finished
   ─► next episode: remembered lessons are handed to the agent (the self-learning part)
```

A demo script says when the crash happens: **already happened** (injected during warm-up,
so a queue is forming when the console opens) or **will happen** (at a later simulation
time). Scripts live in `simulation/scenarios/downtown_grid/demos/`: `crash-ahead`,
`crash-already`, `double-crash` and `varied-crash` (a different crash, to test whether a
lesson transfers instead of being memorised).

### When a second crash happens

One agent works at a time, and it always works on **every active incident**:

| Situation when a crash is detected | What happens |
|---|---|
| Nothing is running | Normal workflow: one crash triggers one agent. |
| Another crash arrived while the first agent is still responding (analyzing, or its plan is being monitored) | The first agent and its implementor are **stopped completely**: the open analysis is abandoned and its queued branches dropped, its monitor stops, and no lesson is stored for it (the second crash contaminates it). A **new agent starts with the context of both crashes** and solves them at the same time. |
| The first episode is already reviewing or finished | The review finishes normally. The new crash starts a new episode whose context still includes any incident that has not been cleared. |

What the new agent inherits from the stopped one:

- **The plan that was already applied stays on the live signals.** There is no automatic
  revert yet. The new agent is told about it (`standing_responses` in `start_analysis`),
  every branch it simulates starts with those responses re-applied, and a plan that
  changes the same intersections replaces them.
- **One EMS responder per incident.** Realised EMS response now means the *last* scene
  reached (unchanged when there is a single responder).
- The mock analyst, used offline, proposes combined plans (metering, diversion, corridor
  for all crashes at once) plus each incident's own plans.

Already in the code (groundwork, not yet triggered by anything): demo scripts and the
boot-time crash hook (`simulation/scenario.py`, `simulation/runner.py`); analyses over several
incidents (`ScenarioRun.incident_ids`, `POST /api/scenarios/run` with `incident_ids`, MCP
`start_analysis` defaulting to all active incidents, standing responses re-applied in
branches, `ScenarioService.abandon`); and the episode, scorecard, lesson and memory
records in `backend/app/models/episode.py`. The remaining work is the roadmap below.

## Architecture

```
┌───────────────── frontend/ (React + TS + MapLibre) ─────────────────┐
│ map · incident card · KPIs · trends · ops log · response plans      │
└────────── REST /api/* ─────────┬──────────── WS /ws/state ──────────┘
                                 │                    agents (MCP) ──► /mcp
┌───────────────────────── backend/ (FastAPI) ─────────┼──────────────────┐
│ api/routes.py              api/mcp_tools.py ◄────────┘                  │
│      │                           │                                      │
│      ▼                           ▼                                      │
│ services/city.py ◄──── services/scenarios.py (ScenarioService)          │
│ (CityService)            open → capture → propose → validate →          │
│  │   SmartCityProvider   simulate → recommend                           │
│  │   (mock | nvidia)       │ AgentProvider (mock | nemotron)            │
│  │                         │ SafetyValidator                            │
│  ▼                         ▼                                            │
│ simulation/runner.py     simulation/branching.py                        │
│ one thread owns the      one fresh SUMO process per candidate,          │
│ live TraCI connection    4 in parallel, restored from one snapshot      │
│  └──────────┬──────────────┘                                            │
│   simulation/interface.py  TrafficSimulation                            │
│   simulation/sumo.py       SumoSimulation (TraCI)                       │
│   preemption.py · reroute.py  corridor and diversion responses          │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ TraCI
                   Eclipse SUMO (simulation/scenarios/downtown_grid)
```

Rules the code keeps:

- **The rest of the app never knows where data came from.** `CityService` sees only the
  `SmartCityProvider` and `AgentProvider` interfaces, and a factory picks the
  implementation from `SMART_CITY_PROVIDER` / `AGENT_PROVIDER`.
- **The simulation has no decision logic.** `TrafficSimulation` executes and measures;
  callers decide what to try.
- **Agents never touch signals.** Agents return plans as data (`SignalPolicy`,
  `EmergencyCorridor`, `RerouteAction`). A deterministic `SafetyValidator` checks them,
  and only simulation branches execute them. Inside a branch, every pre-emption command
  also passes a runtime transition check.

Details, design decisions and the NVIDIA integration plan are in
[docs/architecture.md](docs/architecture.md). That doc has a
[stage-by-stage input/output table](docs/architecture.md#analyze-response-pipeline) for
the Analyze Response pipeline.

## Repository layout

```
backend/app/
  main.py               FastAPI app + lifespan
  config.py             env settings (SMART_CITY_PROVIDER, AGENT_PROVIDER, SIM_*)
  providers.py          provider factories + service assembly
  api/routes.py         REST + /ws/state
  api/mcp_tools.py      the scenario engine as MCP tools at /mcp
  models/domain.py      IntersectionState, RoadSegmentState, Incident, TrafficMetrics,
                        SignalPolicy, EmergencyCorridor, RerouteAction, SimulationCandidate,
                        Recommendation, snapshots, geometry
  models/scenario.py    ScenarioRun, ScenarioRunRequest, ScenarioStatus (analysis contract)
  models/episode.py     demo-episode records: Episode, Scorecard, Lesson, Experience (not wired yet)
  models/api.py         CityState, requests/responses, ops events
  services/city.py      CityService: frames → CityState, commands, ops log
  services/scenarios.py ScenarioService: the Analyze Response pipeline (REST and MCP drivers)
  simulation/interface.py  TrafficSimulation contract
  simulation/sumo.py    SUMO/TraCI implementation (collisions, EMS, snapshots, metrics)
  simulation/branching.py  run one candidate in a fresh SUMO process from a snapshot
  simulation/preemption.py EMS green-corridor controller + runtime transition check (no TraCI)
  simulation/reroute.py diversion advisory with a compliance share
  simulation/runner.py  paced live loop on its own thread
  simulation/network.py static topology, phase labelling, geo projection
  simulation/metrics.py live + horizon TrafficMetrics
  smart_city/           SmartCityProvider: base, mock (ground truth + detection delay), nvidia (stub)
  agent/                AgentProvider: base, mock (8 rule-based plans), nemotron (stub)
  safety/validator.py   SafetyValidator (signal policies + corridors) and rule-based MVP limits
  websocket/hub.py      non-blocking WebSocket fan-out
backend/tests/          network, simulation, runner, safety/agent, mock provider, API tests
frontend/src/
  App.tsx               layout + actions
  hooks/useCityStream.ts   WebSocket client (reconnect, trend backfill, scenario runs)
  components/map/       MapLibre map, layer styles, vehicle glyphs, plan overlays
  components/plans/     response plans: candidate cards, KPI comparison, horizon chart, dock
  components/           top bar, incident card, KPI tiles, inspector, trends, ops log
  lib/plans.ts          analysis state, deltas, plan overlays
  dev/                  ?fixture=scenario replay of a recorded run
simulation/
  networks/grid3x3/     build_network.py → grid3x3.net.xml (named streets, 9 signals)
  scenarios/downtown_grid/  scenario.sumocfg, demand, vehicle types, scenario.json,
                        demos/*.json scripted crash scenarios (loaded, not yet triggered)
  controllers/          how pre-emption plugs in (the code lives in backend/app/simulation/)
docs/
  architecture.md       design notes, the pipeline stage by stage, MCP tools
  milestone-2/          the milestone-2 plan, per-feature specs and results
  specs/                scenario-engine-mcp.md (MCP tools spec + client snippet)
```

## API

| Method | Path | |
|---|---|---|
| GET | `/api/state` | full `CityState` (same payload as the WebSocket frames) |
| GET | `/api/network` | static geometry for the map |
| GET | `/api/incidents` | active incidents (`?include_cleared=true` for history) |
| GET | `/api/incidents/{id}` | one incident |
| POST | `/api/incidents/inject` | stage a collision (defaults from `scenario.json`) |
| POST | `/api/incidents/{id}/clear` | clear the scene and reopen lanes |
| POST | `/api/simulation/start` \| `pause` \| `reset` | run control |
| POST | `/api/simulation/speed` | `{"multiplier": 8}` |
| POST | `/api/emergency/dispatch` | send EMS to the latest incident |
| GET | `/api/signals/{intersection}` | active signal program |
| GET | `/api/cameras`, `/api/events` | camera registry, ops log |
| POST | `/api/scenarios/run` | start Analyze Response: `{"incident_id"?, "incident_ids"?, "horizon_s": 600, "ems_probe": true}` → `ScenarioRun` (202; 409 if no active incident or a run is open). `incident_ids` analyzes several crashes together |
| GET | `/api/scenarios` | recent runs (newest first, last 10) |
| GET | `/api/scenarios/{id}` | one run with candidates, metrics, timelines and the recommendation |
| WS | `/ws/state` | `hello` (state, events, trend, latest run) then `state` / `status` / `event` / `scenario` messages |
| MCP | `/mcp` | streamable HTTP: `start_analysis` (`incident_ids?`, default all active incidents), `validate_plan`, `simulate_plans`, `get_analysis`, `submit_recommendation` ([spec](docs/specs/scenario-engine-mcp.md)) |

## Current limitations

- **Recommendations are advisory.** No path applies a plan to the live simulation yet;
  plans only run inside branches.
- **The EMS corridor rarely helps once the queue has formed.** Pre-emption turns the
  signals green, but the responder still waits behind the queue in the blocked lane.
  Measured on main with the analysis started about 75 s after the crash:
  - `ems-corridor`: 334 s EMS response, against 292 s for the baseline;
  - `divert-advisory`: 242 s, because shortening the queue is what helps.

  The mock's EMS tolerance keeps a slower corridor from being recommended. Levers to try:
  a longer detection distance, or a combined corridor + diversion plan.
- **Branches are slow-ish.** Each takes 10–16 s of wall time when 4 run in parallel, so a
  full run takes about 25 s. About half of that is per-step rubbernecking bookkeeping in
  `sumo.py`, which is the obvious optimisation.
- The mock agent is rule-based: it proposes a fixed set of 8 plans and recommends with a
  fixed rule (see [architecture.md](docs/architecture.md#analyze-response-pipeline)).
- NVIDIA adapters (`NvidiaSmartCityProvider`, `NemotronAgentProvider`) are documented
  stubs that raise `NotImplementedError`. Selecting them fails fast at startup.
- Synthetic network and demand. The crash physics (blocked lane plus a 0.6 m/s pass
  speed) and congestion thresholds are calibrated for this grid, not measured data.
- One live simulation per backend process and one analysis run at a time. State is in
  memory, and nothing has auth, including `/mcp`.
- The live EMS ETA is an estimate (observed speeds plus expected signal waits). The
  realised response time comes from the simulation.

## Next: milestone 3

1. **Nemotron drives the analysis.** `NemotronAgentProvider` becomes an MCP client of
   `/mcp`: it lists the tools, hands them to the OpenAI-compatible NIM endpoint, and runs
   the model's tool calls. The loop goes `start_analysis` → `validate_plan` →
   `simulate_plans` (one or more rounds) → `submit_recommendation`. The mock stays the
   default and the fallback. See [nemotron.py](backend/app/agent/nemotron.py) and the
   [MCP spec](docs/specs/scenario-engine-mcp.md).
2. **Implement on the live twin.** *(Changed from "operator-approved only": the autonomous
   episode needs the agent to implement its own choice.)* An `implement_recommendation`
   MCP tool and a matching REST endpoint apply a plan to the live simulation. They accept
   **no plan payload**: only the recommended, completed candidate of a finished run, which
   is re-validated against the live signal programs first and refused if the incident is
   already cleared. The operator path and the agent path share one code path, and a setting
   can turn the agent path off. This replaces the rule "agents never touch live signals",
   so update that rule in this README, in `CLAUDE.md` and in the UI's "Advisory" footer
   when it lands.
3. **NVIDIA Smart City input, prepared but not faked.** The `NvidiaSmartCityProvider`
   mapping onto the VSS Video Analytics MCP tools, plus a map-matching component (lat/lon
   and place names → segment and lane). This milestone does not install or run the full
   Blueprint; until a real VSS endpoint exists, the mock stays the provider.

### Task list for the autonomous, self-learning episode

Ordered so each step is demoable with the **mock** analyst and reviewer (no NIM key needed)
before Nemotron is involved. `[x]` = the groundwork described under
[Completed in this pass](#completed-in-this-pass-for-review).

- [x] Demo scripts (crash already happened / will happen / second crash / different crash)
- [x] Scenario engine solves several incidents together; branches replay standing responses
  (written, not yet exercised)
- [x] Two-crash mechanics in the engine (`abandon`, per-incident EMS probes, combined mock
  plans) (written, not yet exercised)
- [ ] **1. Episode orchestration** (`backend/app/learning/episode.py`). Trigger on incident
  detection through a new `CityService` incident listener; register the crash injector
  (runtime crashes) and the boot events (already-happened crashes) from the armed script;
  `POST /api/demo/start {script}`, `POST /api/demo/stop`, `GET /api/demo`,
  `GET /api/episodes[/{id}]`; a WebSocket `episode` message; `DEMO_SCRIPT` to arm at startup.
  Statuses: `armed → detected → analyzing → monitoring → reviewing → completed`, plus
  `superseded`, `aborted`, `failed`.
- [ ] **2. The two-crash rule** (see above). A new detection while an episode is `detected`,
  `analyzing` or `monitoring` cancels its agent task, calls `ScenarioService.abandon`, stops
  its monitor, marks it `superseded`, and starts an episode over all active incidents.
  A `reset` aborts the episode.
- [ ] **3. Implementor** (`learning/implementor.py`). `implement_recommendation(run_id)` as an
  MCP tool and `POST /api/scenarios/{id}/implement`. Applies the recommended candidate to
  the live sim with `apply_plan` through `CityService.run_on_live`, dispatches the EMS
  probes at that moment, re-validates on the live programs, keeps the registry of standing
  responses (`ScenarioService.standing_source`, cleared on reset), and writes ops events.
- [ ] **4. Live monitor and scorecard** (`learning/monitor.py`, `scorecard.py`). A frame
  observer caches live samples before and after implementation for `EPISODE_MONITOR_S`
  **simulation** seconds. Code (not the LLM) computes realised vs predicted, vs the predicted
  baseline, the delay/queue slope before and after, prediction error, staleness and a
  materiality flag, reusing the mock's recommend rubric.
- [ ] **5. Reviewer and memory** (`learning/reviewer.py`, `store.py`). The reviewer sees only
  the scorecard and the condensed episode, never the solver's reasoning. Episodes are stored
  as markdown with JSON front matter under `memory/episodes/`, plus a generated
  `memory/playbook.md`. Mock reviewer first, Nemotron reviewer second.
- [ ] **6. Finish step.** Stop the monitor, pause the live sim (`EPISODE_PAUSE_ON_FINISH`),
  store the lesson, mark the episode `completed`.
- [ ] **7. Nemotron analyst** (`learning/analysts.py`, `agent/nemotron.py`). An MCP client of
  `/mcp` driving NIM tool calling, with a step cap and timeout, and a fallback to the mock
  analyst. Needs `NEMOTRON_MODEL` and `NVIDIA_API_KEY`.
- [ ] **8. Recall and injection.** `start_analysis` returns an `experience` block (playbook
  plus similar past episodes), a `recall_experience` tool, `IncidentContext.lessons`
  (`ScenarioService.lessons_source`). Lessons seed round one; they never replace
  `validate_plan` and `simulate_plans`.
- [ ] **9. Frontend.** An episode panel (timeline, monitor progress, lessons used and
  recorded, which agent was superseded), `frontend/src/api/types.ts` in sync (`incident_ids`,
  `episode`), and the "advisory only" wording updated once plans are really applied.
- [ ] **10. Config and docs.** New settings in `config.py` **and** `.env.example`
  (`MEMORY_ENABLED`, `MEMORY_DIR`, `DEMO_SCRIPT`, `EPISODE_MONITOR_S`,
  `EPISODE_AGENT_TIMEOUT_S`, `EPISODE_FALLBACK_TO_MOCK`, `EPISODE_PAUSE_ON_FINISH`,
  `EPISODE_ANALYST`, `MCP_URL`); update `docs/architecture.md`; revise the "agents never
  touch live signals" rule everywhere it appears.
- [ ] **Measure the learning.** Run the same script cold (empty memory) and warm, and the
  `varied-crash` script, comparing rounds and candidates used, wall time and recommendation
  quality. Same-script gains are memorisation; only the varied script shows transfer.

Later: embedding-based recall (NVIDIA embedding NIM behind `ExperienceStore.recall`);
automatic revert of an applied plan when the scene clears; per-responder EMS metrics;
verifying the corridor and diversion results before trusting their lessons (the
[limitations](#current-limitations) above show the corridor often loses); the branch
speed-up in `sumo.py`; a slower live speed during analysis to reduce snapshot staleness;
tests for `ScenarioService`, the MCP tools and the learning package (the team convention so
far is no new test files, so agree on this before adding them).

Decisions already made by the team: the implementor really applies the plan to the live
sim; the crash is scripted (already there, or later); the reviewer runs after a fixed number
of simulated seconds; the lesson goes to a RAG memory; the live collection stops at the
end; and the two-crash rule above. Decisions made in the plan that still need a yes:
pausing the live sim at the end of an episode, markdown-backed memory with structured
recall first (embeddings optional), no lesson stored for a superseded episode, a standing
plan staying on the signals after its agent is superseded, and a fallback to the mock
analyst if NIM fails.

## Completed in this pass (for review)

Scope: the multi-crash groundwork and the records the episode will use. **Nothing in this
list is triggered yet**: there is no episode service, implementor, monitor, reviewer or
memory (see the task list). Existing behavior with a single crash is meant to be unchanged.

### What changed

| File | Change |
|---|---|
| `simulation/scenarios/downtown_grid/demos/*.json` (new) | Four scripts: `crash-ahead` (crash at sim 420 s), `crash-already` (240 s, inside warm-up so it has already happened), `double-crash` (400 s on Main St EB, then 460 s on Central Ave NB `B1_B2`, which feeds the same intersection), `varied-crash` (left lane, Main St WB). |
| `backend/app/simulation/scenario.py` | `DemoCrash`, `DemoScript`, `load_demo_scripts`; `Scenario.demos` is filled from `demos/*.json`. |
| `backend/app/simulation/runner.py` | `set_boot_events` and `_warm_up`: events fire at their simulation time inside every warm-up (boot and reset), so a crash can already have happened when the console opens. Nothing calls `set_boot_events` yet. |
| `backend/app/simulation/branching.py` | New `apply_plan(sim, plan)` (policies, corridor, reroutes; the step a live apply will reuse). `run_branch` now takes `probes: list[ProbeSpec]` (was a single `probe`) and an optional `standing` list, replayed before the candidate because snapshots keep only base signal programs. |
| `backend/app/simulation/sumo.py` | `_realised_emergency_eta` is now the response time of the **last** responder to reach its scene, and `None` while any relevant responder has not arrived. Unchanged for one responder. |
| `backend/app/models/scenario.py` | `ScenarioRun.incident_ids` and `ScenarioRunRequest.incident_ids`. `incident_id` stays as the primary (earliest detected). |
| `backend/app/agent/base.py` | `IncidentContext.incidents`, `.standing`, `.lessons`, `.all_incidents`. `CandidatePlan` moved above `IncidentContext`. |
| `backend/app/agent/mock.py` | `recommend` accepts `context=None` (it never used it), so the scorecard can reuse the rubric. |
| `backend/app/services/scenarios.py` | Several incidents per analysis (`open(..., incident_ids=)`, `_resolve_incidents`); one EMS probe per incident without a responder already en route (`_probes`); `standing_source` and `lessons_source` hooks (default: nothing); `_propose` and `_combined_plans` for the mock with several incidents; `abandon()`; `_close` cancels queued branch tasks and defers deleting the snapshot until the running round ends; `fail` is idempotent; the ops-log line names every incident. |
| `backend/app/api/mcp_tools.py` | `start_analysis(incident_ids?)` defaults to **all** active incidents (**renamed from `incident_id`**); the payload gains `incidents` and `standing_responses`; the instructions tell the agent to solve several incidents together. |
| `docs/specs/scenario-engine-mcp.md` | The `start_analysis` row matches. |
| `backend/app/models/episode.py` (new) | `Episode`, `EpisodeStatus` (with `superseded`), `Implementation`, `LiveSample`, `LiveRecord`, `Scorecard`, `Lesson`, `Experience`, `RecalledExperience`. Unused so far. |
| `README.md` | This section, the episode section, the task list, and the API and layout rows. |

### What was and was not checked

- **Checked:** the app and the new models import; the four demo scripts parse and load
  (`crash-ahead [420]`, `crash-already [240]`, `double-crash [400, 460]`,
  `varied-crash [420]`); the existing backend suite passed (19 tests, 37.6 s) against the
  code as of the scenario-service and MCP edits.
- **Not checked, so please review by reading or by trying:** the existing suite does not
  exercise `ScenarioService`, the MCP tools, branching or the runner boot path, so
  a green run says nothing about these changes. No test or scratch script was run against them: an
  analysis over two crashes (REST and MCP), standing responses replayed in a branch,
  `abandon` while branches are queued or running, a crash injected during warm-up, the
  multi-responder EMS metric, and the mock's combined plans. To try the two-crash analysis
  by hand: `POST /api/incidents/inject` twice (`{}` and
  `{"segment_id":"B1_B2"}`), wait for both to be detected, then `POST /api/scenarios/run`
  with `{"incident_ids": ["INC-0001","INC-0002"]}`.
- The frontend was not touched. `frontend/src/api/types.ts` still lacks `incident_ids` (an
  extra field the UI ignores) and needs the sync listed in the task list.

### Where a reviewer should look hardest

1. **`_realised_emergency_eta`** (`sumo.py`). A dispatch that never arrives now makes the
   whole metric `None`. Check that nothing else relies on the old "first arrival wins".
2. **Closing an analysis while branches run** (`scenarios.py`: `_close`, `_run_round`,
   `_drop_snapshot`). Look for a path where the snapshot is deleted too early or never, and
   for a cancelled branch leaving a candidate stuck in `running`.
3. **Probes.** Before, any responder en route suppressed the probe for the whole run. Now
   only a responder already heading to that incident's segment does.
4. **The `start_analysis` change** is breaking for any MCP client that passed `incident_id`
   (none exist in the repo). With one active incident the behavior is the same.
5. **Mock proposals with several incidents.** `_combined_plans` keeps the first policy per
   intersection. The run is capped at 8 candidates (`SCENARIO_MAX_CANDIDATES`), so the
   cap truncates the per-incident plans at the tail (the combined plans come first).
   `aggressive-flush` is dropped in this case.
6. **`run_branch`'s signature changed** (list of probes, optional `standing`). The only
   caller is `ScenarioService._simulate`.
7. **Unrelated uncommitted edits.** Before this pass the working tree already held small
   dead-code removals in `network.py`, `preemption.py`, `reroute.py`, `build_network.py` and
   two docs under `docs/milestone-2/`, and an untracked `CLAUDE.md`. They are not part of this
   work and were left as they were.
