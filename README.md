# Traffic Operations Center: simulation-backed incident response

A city traffic operations center that extends the NVIDIA Smart City blueprint idea:
when a camera-detected incident (collision, stalled vehicle, …) hits the network,
candidate responses are **tested in a SUMO traffic simulation before anything is
recommended**. The experiments are already exposed as MCP tools; in milestone 3 a
Nemotron agent drives them and learns from each incident (see
[Next stage](#next-stage-the-autonomous-self-learning-episode)).

This repository has completed **milestone 2**. It has a live SUMO digital twin of a 3×3
downtown grid and a FastAPI backend that streams city state over WebSocket. There is a
React/MapLibre operations console: inject a collision, watch the queue spill back, then
click **Analyze Response** to test up to 8 candidate plans in parallel SUMO branches.
Those plans include signal timing, an EMS green corridor and a diversion advisory. The
console compares each plan against the baseline and recommends one. Everything runs on a
laptop with no GPU. NVIDIA components plug in behind interfaces that already exist; the
adapters are still stubs. The groundwork for milestone 3 (scripted crashes, analyses over
several incidents) is in the code but not yet exercised; see [Next stage](#next-stage-the-autonomous-self-learning-episode).

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
| `make backend-oakland` | the backend simulating [Oakland, Pittsburgh](#second-city-oakland-pittsburgh) instead of the grid (same port, so `make frontend` works as is) |
| `make network-oakland` | rebuild the Oakland network, demand and signal timing from the OSM extract |
| `SUMO_GUI=true make backend` | watch the live simulation in sumo-gui as well |
| http://127.0.0.1:8000/docs | interactive API docs |

## Demo walkthrough (today)

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

Today nothing in the analysis changes the live signals: recommendations are advisory.
The next stage adds a gated way to apply one.

Use the speed buttons (1×–16×) to fast-forward. Click an intersection or road to inspect
its phase, queues and speed.

## Second city: Oakland, Pittsburgh

A second scenario, `pittsburgh_oakland`, runs the same console and pipeline on a real street
layout: central Oakland around Fifth and Forbes Avenues, with 332 road links and 33 signals.
The downtown grid stays the default, and the tests always use it.

```bash
make backend-oakland   # or SCENARIO_DIR=simulation/scenarios/pittsburgh_oakland in .env
make frontend
```

- **What is real.** Only the map: streets, lane counts, speed limits and signal locations come
  from OpenStreetMap (© OpenStreetMap contributors, ODbL; the credit is shown on the map).
- **What is synthetic.** The demand is fringe-to-fringe flows weighted by road class, about
  2,500 veh/h in total. The signal timing is Webster timing sized to that demand, with 12 s
  minimum greens and coordinated offsets on a 54 s cycle. The build is described in
  [simulation/networks/pittsburgh_oakland/](simulation/networks/pittsburgh_oakland/README.md).
- **The default crash** blocks the two right lanes of Forbes Avenue eastbound, between
  S Bouquet St and Bigelow Blvd. The EMS probe leaves from Fire Station 14.
- **Measured.** With no crash, traffic is stable for two simulated hours, with no gridlock and
  no teleports. After the crash, Analyze Response recommends the diversion advisory, which
  cuts mean delay by 10–18% across runs. The timing plans move delay by only 1–3%.
  `aggressive-flush` is still rejected by the validator. A run over two crashes (Forbes EB
  plus Fifth Avenue WB, `incident_ids`) also completes.
- **Slower than the grid.** Each branch takes 20–45 s of wall time with
  `SCENARIO_WORKERS=6`, so a full run takes 40–60 s.
- **The EMS figures are noisy here.** The probe often does not reach the scene within the
  10-minute horizon, and then the column shows no value.
- **Map-model limits.** Compass labels (NB/SB/EB/WB) are assigned after a 45° rotation,
  because Oakland's grid runs diagonally (`heading_offset_deg` in `scenario.json`). A label
  is display only: an approach is identified by its incoming segment, so the five-leg
  junction at Fifth Ave and Neville St keeps all its approaches even though two of them read
  SB (the inspector adds the street name there). There is no basemap under the road network.

## Next stage: the autonomous, self-learning episode

This is the plan for the next stage and the single place that describes it. Milestones 1
and 2 (everything above) work today. This section says what is built, what is not, and
what each remaining piece must do.

**Status at a glance**

| Piece | State | Where |
|---|---|---|
| Live twin, incidents, Analyze Response, MCP tools | Working | milestones 1–2 |
| Scripted crash scenarios | Files load; nothing triggers them yet | `simulation/scenarios/downtown_grid/demos/` |
| One analysis over several incidents; branches that replay responses already applied; abandoning an analysis | Written, **not yet exercised** | `services/scenarios.py`, `simulation/branching.py` |
| Records for an episode, scorecard, lesson and memory entry | Defined, unused | `backend/app/models/episode.py` |
| Episode service, implementor, monitor, scorecard, reviewer, memory, Nemotron loop, UI | **Not started** | [task list](#task-list) |

### The idea

The "live city" is a SUMO simulation standing in for real camera data. An agent responds
to it end to end, then remembers what happened, so the next incident starts with
experience instead of cold.

```
scripted crash ─► live sim plays it as "real data" ─► crash detected, state sent to the agent
   ─► agent tests alternatives in parallel branches (the live view keeps running)
   ─► agent's chosen plan is IMPLEMENTED on the live sim (really applied)
   ─► live data is cached for a fixed number of simulated seconds
   ─► reviewer condenses it into a lesson ─► live collection stops
   ─► lesson stored in the RAG memory ─► episode finished
   ─► next episode: remembered lessons are handed to the agent (the self-learning part)
```

| Role | What it is | Uses a model? |
|---|---|---|
| **Analyst agent** | The loop that reads the incident state, tests plans in parallel branches, picks one, then calls the implementor. Nemotron over MCP; the rule-based mock when offline. | Nemotron / no (mock) |
| **Implementor** | The gated step that applies the agent's *recommended, already-simulated* plan to the live sim. Not a model. | No |
| **Monitor** | Caches live data before and after the plan goes live, for a fixed number of **simulated** seconds. | No |
| **Scorecard** | Deterministic numbers: what was predicted, what really happened, how far apart. | No |
| **Reviewer** | A separate call that sees only the scorecard and the condensed episode (never the analyst's reasoning) and writes the lesson. Mock reviewer offline. | Nemotron / no (mock) |
| **Memory** | Stores lessons and recalls the relevant ones for the next incident. | No (embeddings optional, later) |
| **Episode service** | The state machine that ties these together. It is the only thing that starts an agent. | No |

Expect an episode to take about 4–6 minutes at 4× speed (an estimate from today's timings:
analysis rounds of 10–16 s wall each, plus a 600 s window, about 150 s wall). Use 8–16×
on stage.

### Terms

Used the same way everywhere in this README, the code and the ops log.

| Term | Meaning |
|---|---|
| **Crash** | The physical event scripted into the simulation (a `Disruption`). |
| **Incident** | The Smart City provider's report of a crash (`INC-0001`). Detection comes a few simulated seconds after the crash. |
| **Analysis / run** | One `ScenarioRun` (`SCN-0001`): one snapshot, a baseline plus candidate plans, one recommendation. |
| **Plan / candidate** | A `CandidatePlan` (signal timing changes, an EMS corridor, reroutes) before simulating; a `SimulationCandidate` once it has metrics. `baseline` is the do-nothing plan. |
| **Implement** | Apply the recommended plan to the *live* simulation, as opposed to simulating it in a branch. |
| **Standing response** | A plan already applied to the live sim. Every later branch starts with it re-applied, because snapshots keep only the base signal programs. |
| **Episode** | One agent working one set of active incidents, from detection to a stored lesson (`EP-0001`). |
| **Monitor window** | The fixed number of simulated seconds the applied plan is watched. Default: the script's `monitor_s`, else the run's horizon (600 s in every shipped script), so predicted and realised windows match. |
| **Staleness** | Live time that passed between the branch snapshot and the moment the plan went live. Recorded with every episode. |
| **Lesson / experience** | The reviewer's verdict plus the numbers and the situation, stored as one memory entry. |
| **Playbook** | A short, generated digest of all lessons, always given to the agent. |
| **Superseded** | An episode stopped because another crash arrived while its agent was still working. |
| **Demo script** | A JSON file in `demos/` that says when each crash happens (`DemoScript`). Not the manual [demo walkthrough](#demo-walkthrough-today) above. |

### Scripted crash scenarios

A demo script says when each crash happens. A crash before the end of warm-up (300 s) has
**already happened** when the console opens: it is injected during boot and a queue is
forming. A later crash **will happen** while the demo runs. Scripts live in
`simulation/scenarios/downtown_grid/demos/*.json` and are loaded by
`simulation/scenario.py`.

| Script | Crashes | What it shows |
|---|---|---|
| `crash-ahead` | Main St eastbound at sim 420 s | The normal workflow, live. |
| `crash-already` | Main St eastbound at sim 240 s (inside warm-up) | Opening on a crash that has already happened. |
| `double-crash` | Main St eastbound at 400 s, then Central Ave northbound (`B1_B2`, which feeds the same intersection) at 460 s | The two-crash rule below. |
| `varied-crash` | Left lane, Main St westbound, at 420 s | Whether a lesson transfers to a different crash instead of being memorised. |

### The two-crash rule

One agent works at a time, and it always works on **every active incident**.

| When a crash is detected… | What happens |
|---|---|
| Nothing is running | Normal workflow: one crash triggers one agent. |
| The first agent is still responding (analyzing, or its plan is being monitored) | The first agent and its implementor are **stopped completely**: its analysis is abandoned and its queued branches dropped, its monitor stops, and no lesson is stored for it (the second crash contaminates the data). A **new agent starts with the context of both crashes** and solves them at the same time. |
| The first episode is already reviewing or finished | The review finishes normally. The new crash starts a new episode whose context still includes any incident that has not been cleared. |

What the new agent inherits from the stopped one:

- **The plan already applied stays on the live signals.** There is no automatic revert
  yet. The new agent is told about it (`standing_responses` in `start_analysis`), every
  branch it simulates starts with it re-applied, and a plan that changes the same
  intersections replaces it. A second EMS corridor replaces the first (the latest wins).
- **One EMS responder per incident.** Realised EMS response means the *last* scene
  reached, and is unknown while any responder has not arrived. With one responder it is
  unchanged.
- The **mock analyst** proposes combined plans (metering, diversion, corridor for all
  crashes at once) plus each incident's own plans.

### How the pieces work

**Implementor.** `implement_recommendation(run_id)` takes **no plan payload**. It applies
only the recommended, completed candidate of a finished run, using `apply_plan` in
`simulation/branching.py` (the same steps a branch runs, so what was tested is what goes
live) through `CityService.run_on_live`. First it re-validates the plan against the *live*
signal programs (they may have changed), and it refuses if the incident is already
cleared. It dispatches an EMS probe at that moment, mirroring the probe every branch had
(one per incident without a responder already en route). A `baseline` recommendation
applies nothing but is still monitored and reviewed: doing nothing can be the right answer.
Because the live sim kept running during analysis, the plan goes live at a later time
than the branches started; that gap is the staleness.

**Monitor.** A frame observer keeps a ring of live samples the whole time, so the
unmanaged period between detection and implementation is already on record. After
implementation it caches a sample every 5 simulated seconds (network delay, max queue,
throughput, speed, vehicles, halted vehicles and speed on the incident segments) plus
the responders' realised response time. It stops after the monitor window, or aborts if
the simulation is reset (time goes backwards) or the scene is cleared. The window is in
**simulated** seconds, so pausing the sim pauses it.

**Predicted vs realised.** A branch's `timeline` and the live samples use the same
definitions (`MetricSample`), so they can be compared directly. The branch *horizon*
metrics are computed differently and are not compared with live numbers. There is only
one live timeline, hence no realised do-nothing counterfactual: "improvement" means
realised against the *predicted* baseline and against the trend before implementation,
and the gap between realised and predicted for the chosen plan (prediction error) is
itself a lesson about how far to trust the twin.

**Scorecard** (`Scorecard` in `models/episode.py`; code, never the model). Realised,
predicted, predicted-baseline and pre-implementation aggregates; delay and queue slope
before and after; predicted gain; prediction error; staleness; how many plans were
tried and rejected; whether the recommendation matched the mock's rubric among the
simulated plans (`agent/mock.py`, reused as the "what should have won" reference); a
`material` flag; and an outcome of `effective`, `ineffective` or `inconclusive`.
Timing plans currently move delay by about 1%, so the materiality thresholds matter:
without them the reviewer would learn noise. The thresholds are still to be chosen.

**Reviewer.** Writes a `Lesson`: verdict, summary, what worked, what did not, what to
try next time, and a confidence. Given only the scorecard and the condensed episode. The
raw cache is condensed into the scorecard's numbers and discarded.

**Memory.** Each episode becomes a markdown file, `memory/episodes/<episode id>.md`, with
a JSON front matter block (so nothing needs a YAML dependency) and a human-readable body,
so people can read and diff it. Illustrative shape, placeholders instead of numbers:

```
---
{"id": "EP-0004", "script_id": "crash-ahead", "analyst": "nemotron",
 "incidents": [{"type": "collision", "severity": "major", "street": "Main St",
                "direction": "EB", "blocked_lanes": [0], "total_lanes": 2, ...}],
 "chosen": {"id": "<plan id>", "kinds": ["<timing | corridor | diversion>"]},
 "tried": [...], "scorecard": {...},
 "lesson": {"verdict": "<effective | ineffective | inconclusive>", "next_time": [...]}}
---
# EP-0004: <one-line summary>
What was tried, the numbers, and the lesson, in prose.
```

`memory/playbook.md` is a generated digest of all lessons. Recall ranks past episodes by
how similar the situation is (incident type, segment, street and direction, severity,
share of lanes blocked, up- and downstream intersections, number of incidents), then by
recency and by how well the lesson held up. Embedding-based recall (an NVIDIA embedding
NIM) can be added behind the same `recall` call when the corpus is big enough to need it.

**Injection.** `start_analysis` returns an `experience` block (the playbook plus the top
similar episodes), and a `recall_experience` tool lets the agent ask for more. The
prompt says lessons only seed the first round: they never replace `validate_plan` and
`simulate_plans`, so memory advises while the validator and the simulator stay the gate.

**Finish.** The monitor stops, the live sim is paused (configurable), the lesson is
written, and the episode is `completed`. Episode statuses: `armed → detected → analyzing →
monitoring → reviewing → completed`, plus `superseded`, `aborted` (reset, or the scene was
cleared before the window closed) and `failed` (agent, implementor or reviewer error).

### Planned interfaces

Nothing in this section exists yet.

| Kind | Interface |
|---|---|
| REST | `POST /api/demo/start {"script": …}` (reset and arm), `POST /api/demo/stop`, `GET /api/demo`, `GET /api/episodes`, `GET /api/episodes/{id}`, `POST /api/scenarios/{id}/implement` (operator path, same code as the agent's), `GET /api/memory`, and a way to clear memory for a cold run |
| WebSocket | an `episode` message on every change; `hello.data.episode` = the latest episode or `null` |
| MCP | `implement_recommendation(run_id)`, `recall_experience(...)`; `start_analysis` also returns `experience` |

| Setting (proposed) | Meaning |
|---|---|
| `DEMO_SCRIPT` | Arm a script at startup |
| `EPISODE_ANALYST` | `auto` (Nemotron if a key and model are set, else mock), `mock` or `nemotron` |
| `EPISODE_MONITOR_S` | Monitor window; default the script's `monitor_s`, else the run horizon |
| `EPISODE_AGENT_TIMEOUT_S` | Wall-clock limit for the agent |
| `EPISODE_FALLBACK_TO_MOCK` | Fall back to the mock analyst if NIM fails |
| `EPISODE_PAUSE_ON_FINISH` | Pause the live sim when the episode completes |
| `AGENT_MAY_IMPLEMENT` | Allow the MCP implement tool (the operator path is separate) |
| `MEMORY_ENABLED`, `MEMORY_DIR` | Memory on/off and where it lives (`memory/`, with `memory/episodes/` git-ignored) |
| `MCP_URL` | Where the Nemotron loop reaches this app's `/mcp` |
| `NEMOTRON_MODEL`, `NVIDIA_API_KEY`, `NEMOTRON_BASE_URL` | Already in `config.py`; the model id has to be supplied |

Every new setting goes in `backend/app/config.py` **and** `.env.example`.

### Task list

Ordered so each step can be demoed with the **mock** analyst and reviewer (no NIM key)
before Nemotron is involved. `[x]` = groundwork written but not yet exercised (see
[Review notes](#review-notes-the-groundwork-pass)).

- [x] Demo scripts (crash already happened / will happen / second crash / different crash)
- [x] Scenario engine solves several incidents together; branches replay standing responses
- [x] Two-crash mechanics in the engine (`abandon`, per-incident EMS probes, combined mock plans)
- [ ] **1. Episode service** (`backend/app/learning/episode.py`). Trigger on incident
  detection through a new `CityService` incident listener; inject runtime crashes (a frame
  observer) and register boot events (already-happened crashes) from the armed script; the
  REST and WebSocket interfaces above. *Done when:* `POST /api/demo/start` on `crash-ahead`
  reaches `analyzing` with no click, with the mock analyst.
- [ ] **2. The two-crash rule.** A new detection while an episode is `detected`,
  `analyzing` or `monitoring` cancels its agent task, calls `ScenarioService.abandon`,
  stops its monitor, marks it `superseded`, and starts an episode over all active
  incidents; a reset aborts the episode. *Done when:* `double-crash` ends with one
  `superseded` episode and one `completed` episode that lists both incidents.
- [ ] **3. Implementor** (`learning/implementor.py`). The MCP tool and the REST endpoint;
  the registry of standing responses (`ScenarioService.standing_source`, cleared on
  reset); ops-log events. *Done when:* after implementing, the live signal program ids
  change and the run's EMS probe is dispatched on the live sim.
- [ ] **4. Monitor and scorecard** (`learning/monitor.py`, `scorecard.py`). *Done when:* a
  finished window yields a `Scorecard` with realised, predicted and prediction-error
  numbers, and reset or a cleared scene aborts the window.
- [ ] **5. Reviewer and memory** (`learning/reviewer.py`, `store.py`). Mock reviewer
  first, Nemotron second; markdown episodes and the playbook. *Done when:* a completed
  episode leaves a readable `memory/episodes/EP-….md`.
- [ ] **6. Finish step.** Stop the monitor, pause the live sim, store the lesson.
- [ ] **7. Nemotron analyst** (`learning/analysts.py`, `agent/nemotron.py`). An MCP client
  of `/mcp` driving NIM tool calling with a step cap and a timeout, and a fallback to the
  mock. *Done when:* the same script runs with `EPISODE_ANALYST=nemotron`.
- [ ] **8. Recall and injection.** `experience` in `start_analysis`, `recall_experience`,
  `IncidentContext.lessons` (`ScenarioService.lessons_source`), the playbook. *Done when:*
  a second episode's context contains the first episode's lesson.
- [ ] **9. Frontend.** An episode panel (timeline, monitor progress, lessons used and
  recorded, which episode was superseded), `frontend/src/api/types.ts` in sync
  (`incident_ids`, `episode`), and the "advisory" wording updated once plans really go live.
- [ ] **10. Docs and the safety rule.** Update `docs/architecture.md` and the "agents
  never touch live signals" rule wherever it appears (this README, `CLAUDE.md`, the UI
  footer) to describe the one gated exception.
- [ ] **Measure the learning.** Same script cold (empty memory) and warm, plus
  `varied-crash`: rounds and candidates used, wall time, recommendation quality. Same-script
  gains are memorisation; only the varied script shows transfer.

Later: embedding-based recall; automatic revert of an applied plan when the scene
clears; per-responder EMS metrics; verifying the corridor and diversion results before
trusting their lessons; the branch speed-up in `sumo.py`; a slower live speed during
analysis to reduce staleness; tests for `ScenarioService`, the MCP tools and the learning
package (the convention so far is no new test files, so agree on this first).

### Decisions and open questions

**Decided by the team:** the implementor really applies the plan to the live sim; the
crash is scripted (already there, or later); the reviewer runs after a fixed number of
simulated seconds; the lesson goes to a RAG memory; the live collection stops at the end;
and the two-crash rule.

**Proposed in the plan, still needs a yes:**

- Pause the live sim at the end of an episode (vs leaving it running).
- Markdown-backed memory with structured recall first, embeddings optional.
- No lesson is stored for a superseded episode.
- A standing plan stays on the signals after its agent is superseded.
- Fall back to the mock analyst if NIM fails.

**Open:** which NIM model id to use; whether to slow the live sim during analysis to
reduce staleness; whether a scene cleared mid-window should abort the episode (proposed)
or store an inconclusive lesson.

### Risks and known gaps

- **The learnable signal is thin.** Timing plans move delay by about 1%, and the EMS
  corridor often loses once a queue has formed (see [limitations](#current-limitations)).
  The high-impact plans (corridor, diversion) have the least verification, so check them
  before trusting their lessons.
- **The plan stays on the live signals** after the episode. Only a reset removes it.
- **Snapshots go stale** while the agent works, so the branches predicted a slightly
  earlier city than the one the plan is applied to.
- **Live-apply paths have never run on the live sim.** The corridor's `setPhase` jump in
  particular was reported as never exercised.
- **The groundwork in this pass has not been run** beyond imports (see the review notes).

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
  also passes a runtime transition check. This holds today; the next stage adds one gated
  exception, the implementor (see [How the pieces work](#how-the-pieces-work)).

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
  learning/             (planned, next stage) episode service, implementor, monitor,
                        scorecard, reviewer, memory store, analysts
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
  networks/pittsburgh_oakland/  OSM extract (ODbL) + build_network.py → oakland.net.xml (33 signals)
  scenarios/downtown_grid/  scenario.sumocfg, demand, vehicle types, scenario.json,
                        demos/*.json scripted crash scenarios (loaded, not yet triggered)
  scenarios/pittsburgh_oakland/  the same files for Oakland; demand from build_demand.py (synthetic)
  controllers/          how pre-emption plugs in (the code lives in backend/app/simulation/)
memory/                 (planned) lessons and the playbook; memory/episodes/ is git-ignored
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

Endpoints planned for the next stage are listed under [Planned interfaces](#planned-interfaces).

## Current limitations

- **Recommendations are advisory.** No path applies a plan to the live simulation yet;
  plans only run inside branches. The next stage adds one (the implementor).
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
  stubs that raise `NotImplementedError`. `SMART_CITY_PROVIDER=nvidia` fails at startup;
  `AGENT_PROVIDER=nemotron` starts, but the REST Analyze Response then fails when it
  calls the stub.
- Synthetic network (the grid) and synthetic demand (both cities). The crash physics (blocked lane plus a 0.6 m/s pass
  speed) and congestion thresholds are calibrated for this grid, not measured data.
- One live simulation per backend process and one analysis run at a time. State is in
  memory, and nothing has auth, including `/mcp`.
- The live EMS ETA is an estimate (observed speeds plus expected signal waits). The
  realised response time comes from the simulation.

## Next: milestone 3

Milestone 3 has two parts.

1. **The autonomous, self-learning episode.** The plan, the status of each piece and the
   task list are in [Next stage](#next-stage-the-autonomous-self-learning-episode). It
   includes the Nemotron loop (an MCP client of `/mcp` that hands the tools to the
   OpenAI-compatible NIM endpoint and runs the model's tool calls; the mock stays the
   default and the fallback) and the gated implementor that applies a plan to the live
   twin. See [nemotron.py](backend/app/agent/nemotron.py) and the
   [MCP spec](docs/specs/scenario-engine-mcp.md).
2. **NVIDIA Smart City input, prepared but not faked.** The `NvidiaSmartCityProvider`
   mapping onto the VSS Video Analytics MCP tools, plus a map-matching component (lat/lon
   and place names → segment and lane). This milestone does not install or run the full
   Blueprint; until a real VSS endpoint exists, the mock stays the provider.

## Review notes: the groundwork pass

Scope: the multi-crash groundwork and the records the episode will use. **Nothing in this
list is triggered yet**: there is no episode service, implementor, monitor, reviewer or
memory (see the [task list](#task-list)). Existing behavior with a single crash is meant to be unchanged.

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
| `README.md` | The Next stage section (status, roles, terms, scripts, two-crash rule, design, planned interfaces, task list, decisions, risks), these review notes, and the intro, demo, architecture, layout, API and limitations wording. |
| `CLAUDE.md` (untracked) | A Hard rules section (never write or run tests; keep the README consistent), the intro and test-related lines aligned with it, and a corrected note on the stub providers. |

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
   work and were left as they were, except that `CLAUDE.md` gained the Hard rules and the
   wording fixes listed above.
