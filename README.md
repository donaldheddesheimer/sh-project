# Traffic Operations Center: simulation-backed incident response

A city traffic operations center that extends the NVIDIA Smart City blueprint idea:
when a camera-detected incident (collision, stalled vehicle, …) hits the network,
candidate responses are **tested in a SUMO traffic simulation before anything is
recommended**. The experiments are exposed as MCP tools. An agent (Nemotron over NIM, or a
rule-based mock offline) drives them, applies its choice to the live twin and learns from
the result (see [the autonomous episode](#the-autonomous-self-learning-episode)).

This repository has completed **milestone 2**, built **milestone 3** (the autonomous
episode; it has not been run yet), and built the VSS-input part of milestone 4 without running it
(see [Next: milestone 4](#next-milestone-4)). It
has a live SUMO digital twin of a 3×3 downtown grid and a FastAPI backend that streams city
state over WebSocket. There is a React/MapLibre operations console: inject a collision,
watch the queue spill back, then click **Analyze Response** to test up to 8 candidate plans
in parallel SUMO branches. Those plans include signal timing, an EMS green corridor and a
diversion advisory. The console compares each plan against the baseline and recommends one.
The **autonomous episode** runs that loop without clicks: a scripted crash, an agent that
tests plans, applies the best one to the live twin, watches it and stores a lesson for the
next incident. It is built but has not been run end to end yet (see
[Review notes](#review-notes-the-episode-pass)). Everything runs on a laptop with no GPU. The
NVIDIA Smart City input is implemented against the published VSS 3.2 MCP contract, with a
simulation-time replay client for development; neither client has been run here.

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
| `DEMO_SCRIPT=crash-ahead make backend` | arm an autonomous-episode script at startup |
| http://127.0.0.1:8000/docs | interactive API docs |

## Demo walkthrough: Analyze Response

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

Nothing in the analysis changes the live signals: a recommendation is advisory until it is
applied. **Apply to live signals**, under the recommendation, is the operator's way to apply
it. It uses the same gated implementor as an autonomous agent (see
[How the pieces work](#how-the-pieces-work)).

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

## Demo walkthrough: autonomous episode

Built but not yet run end to end, so the timings below are estimates. Use the
**Autonomous agent** panel at the top of the side column, or the API.

1. For a cold run, clear the memory: the panel's **clear**, or `DELETE /api/memory`.
2. Pick **Crash ahead** and click **Run** (`POST /api/demo/start {"script": "crash-ahead"}`).
   The city resets, resumes if the previous episode paused it, and the episode is `armed`.
3. The crash happens at sim 420 s (about 30 s later at 4×). About 4 simulated seconds
   after that, INC-0001 is detected and the episode goes `detected → analyzing`. The
   analysis streams into Response plans as in the walkthrough above.
4. The agent applies its recommendation. The ops log lists the new signal programs and the
   EMS probe it dispatched, and the recommendation footer reads "Applied to live signals".
   The episode goes `monitoring`, with an accessible progress bar over 600 simulated seconds.
5. `reviewing → completed`: the panel shows the lesson (verdict, summary, next time) and
   the scorecard's delay numbers, `memory/episodes/EP-0001.md` is written, and the live sim
   pauses.
6. Run **Crash ahead** again. The analysis lists `EP-0001` under lessons used and, if that
   lesson was `effective`, simulates 4 plans instead of 8.
7. **Different crash** (`varied-crash`) resembles the first crash but is not the same, so the
   lesson only reorders the plans. **Second crash mid-response** (`double-crash`) shows the
   two-crash rule: one episode `superseded`, one `completed` over both incidents.

An episode takes about 4–6 minutes of wall time at 4× (an estimate from today's timings:
analysis rounds of 10–16 s each plus the 600 s window, about 150 s), so use 8–16× on stage.

## The autonomous, self-learning episode

This is milestone 3 and the single place that describes it. It is **built
but has not been run end to end**: no test and no run has exercised it yet. Only build checks
were run (see [Review notes](#review-notes-the-episode-pass)).

**Status at a glance**

| Piece | State | Where |
|---|---|---|
| Live twin, incidents, Analyze Response, MCP tools | Working | milestones 1–2 |
| Scripted crash scenarios, fired by the live runner | Built, not run | `simulation/scenarios/downtown_grid/demos/`, `simulation/runner.py` |
| One analysis over several incidents; branches that replay standing responses; abandoning an analysis | Written, not run | `services/scenarios.py`, `simulation/branching.py` |
| Episode service, implementor, monitor, scorecard, reviewer, memory, recall, the mock acting on lessons | Built, not run | `backend/app/learning/`, `agent/mock.py` |
| Nemotron analyst and reviewer (MCP client over NIM) | Built, never called: needs `NVIDIA_API_KEY` and `NEMOTRON_MODEL` | `learning/analysts.py`, `agent/nemotron.py` |
| Autonomous agent panel, **Apply to live signals** | Built; type-checked and linted, not run | `frontend/src/components/EpisodePanel.tsx`, `plans/ResponsePlans.tsx` |

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
| **Analyst agent** | The loop that reads the incident state, tests plans in parallel branches, picks one, then calls the implementor. Nemotron over MCP (`EPISODE_ANALYST`); the rule-based mock when offline or as the fallback. | Nemotron / no (mock) |
| **Implementor** | The gated step that applies the agent's *recommended, already-simulated* plan to the live sim. Not a model. | No |
| **Monitor** | Caches live data before and after the plan goes live, for a fixed number of **simulated** seconds. | No |
| **Scorecard** | Deterministic numbers: what was predicted, what really happened, how far apart. | No |
| **Reviewer** | A separate call that sees only the scorecard and the condensed episode (never the analyst's reasoning) and writes the lesson. The mock reviewer uses templates. | Nemotron / no (mock) |
| **Memory** | Stores lessons and recalls the relevant ones for the next incident. | No (embeddings optional, later) |
| **Episode service** | The state machine that ties these together. It is the only thing that starts an agent, and only while a demo script is armed. | No |

### Terms

Used the same way everywhere in this README, the code and the ops log.

| Term | Meaning |
|---|---|
| **Crash** | The physical event scripted into the simulation (a `Disruption`). |
| **Incident** | A Smart City provider's report (`INC-0001`): a mock-detected crash or an external VSS event. |
| **Analysis / run** | One `ScenarioRun` (`SCN-0001`): one snapshot, a baseline plus candidate plans, one recommendation. |
| **Plan / candidate** | A `CandidatePlan` (signal timing changes, an EMS corridor, reroutes) before simulating; a `SimulationCandidate` once it has metrics. `baseline` is the do-nothing plan. |
| **Implement** | Apply the recommended plan to the *live* simulation, as opposed to simulating it in a branch. Recorded with who did it: `agent`, `operator`, or `coordinator` (the episode service, when Nemotron recommended but did not apply). |
| **Standing response** | A plan already applied to the live sim. Every later branch starts with it re-applied, because corridors and diversions are not part of a SUMO snapshot. |
| **Episode** | One agent working one set of active incidents, from detection to a stored lesson (`EP-0001`). |
| **Monitor window** | The fixed number of simulated seconds the applied plan is watched (how it is chosen: see Monitor under [How the pieces work](#how-the-pieces-work)). |
| **Staleness** | Live time that passed between the branch snapshot and the moment the plan went live. Recorded with every episode. |
| **Lesson / experience** | The reviewer's verdict plus the numbers and the situation, stored as one memory entry. |
| **Playbook** | A short, generated digest of recent lessons (`memory/playbook.md`), handed to an MCP agent with every analysis. |
| **Superseded** | An episode stopped because another crash arrived while its agent was still working. |
| **Demo script** | A JSON file in `demos/` that says when each crash happens (`DemoScript`). Not the [demo walkthroughs](#demo-walkthrough-analyze-response) above. |
| **Armed** | A demo script is loaded. While one is armed, every detected crash starts an episode (a manual Inject too); `POST /api/demo/stop` disarms. |
| **Mirror** | Reflect an externally reported collision in the twin as one linked disruption, removed when the report clears. |
| **Map match** | Turn a report's lat/lon, place or sensor into a road segment, position and assumed lane, with method and confidence. |
| **Replay client** | Development-only VSS client that releases VSS-shaped fixture documents on simulation time and labels them `vss-replay`. |

### Scripted crash scenarios

A demo script says when each crash happens. The live runner fires each crash at its
simulation time, on every boot. A crash before the end of warm-up (300 s) has **already
happened** when the console opens, so a queue is forming. A later crash **will happen** while
the demo runs. Scripts live in each scenario's `demos/*.json` and are
loaded by `simulation/scenario.py`. `POST /api/demo/start` resets the city and arms one;
`DEMO_SCRIPT` arms one at startup. They are available for both the grid and Oakland with the
same ids; Oakland uses Forbes Avenue eastbound and Fifth Avenue westbound instead of the grid roads.

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

**Implementor** (`learning/implementor.py`). The MCP tool `implement_recommendation(run_id)`
and `POST /api/scenarios/{id}/implement` take **no plan payload**. They apply only the
recommended, completed candidate of a finished run, once. One command on the live thread
re-validates it against the *live* signal programs (they may have changed), dispatches the
EMS probes every branch had (one per incident without a responder already en route), and
installs it with `apply_plan`, the same steps a branch runs. It is refused (409) if an
incident of the run is no longer active, and rejected (400) if the validator objects on the
live programs. A `baseline` recommendation applies nothing but still dispatches the probes
and is monitored and reviewed: doing nothing can be the right answer.

- **Who applies it.** The mock analyst applies its own recommendation. If Nemotron submits
  without applying, the episode service does (`coordinator`). With `AGENT_MAY_IMPLEMENT=false`
  neither does: the episode waits in `analyzing` until an operator clicks **Apply to live
  signals**.
- **After it is applied.** The plan joins the standing responses until a reset. Apply and
  reset are serialized: a reset clears the standing registry and makes every earlier run
  ineligible for a later apply. Because the live sim kept running during analysis, the plan
  goes live later than the branches started; that gap is the staleness.

**Monitor** (`learning/monitor.py`). A frame observer samples the live city every 5
simulated seconds, all the time, and keeps 30 minutes, so the unmanaged period between
detection and implementation is already on record. Each sample holds network delay, max
queue, throughput, speed, vehicles, and halted vehicles and speed on the crash segments.
After implementation the window records the realised response time for both probes dispatched
with the plan and responders that were already en route at the snapshot. It uses the same
timing origin as the branches and reports the last arrival, unknown while any timed responder
has not arrived.

The window lasts `EPISODE_MONITOR_S`, else the script's `monitor_s`, else the run's horizon
(600 s in every shipped script, so predicted and realised windows match). It counts
**simulated** seconds, so pausing the sim pauses it. A reset or a cleared scene aborts it.

**Predicted vs realised.** A branch's `timeline` and the live samples use the same
definitions (`MetricSample`); the branch *horizon* metrics are computed differently and are
not compared with live numbers. The comparison is on **absolute simulation time**, over
`[applied, min(applied + monitor window, snapshot + horizon)]`. Over that stretch the
predicted baseline is what doing nothing would have given, and staleness shows up as
prediction error. There is only one live timeline, hence no realised do-nothing
counterfactual: "improvement" means realised against the *predicted* baseline. The gap
between realised and predicted for the chosen plan (prediction error) is itself a lesson
about how far to trust the twin.

**Scorecard** (`learning/scorecard.py`; code, never the model). It holds:

- realised, predicted, predicted-baseline and pre-implementation aggregates;
- delay and queue slopes before and after the plan went live;
- predicted gain, prediction error, realised vs the predicted baseline, and staleness;
- how many plans were tried and rejected;
- whether the recommendation matched the mock's rubric among the simulated plans
  (`agent/mock.py`, reused as the "what should have won" reference);
- a `material` flag and an outcome.

Materiality thresholds, chosen in this pass (constants in `scorecard.py`): **5% mean delay,
5 vehicles of peak queue, 30 s of EMS response**. Timing plans move delay by about 1%, so
below these a difference is noise. The outcome:

- `effective`: realised beats the predicted baseline by a threshold and no metric is
  materially worse.
- `ineffective`: a material gain was predicted but not realised, or a metric got
  materially worse.
- `inconclusive`: anything else. This includes a `baseline` recommendation (no
  counterfactual) and an incomplete window.

**Reviewer** (`learning/reviewer.py`). It writes a `Lesson`: verdict, summary, what worked,
what did not, what to try next time, and a confidence. It is given only the scorecard and
the condensed episode (the situation, one line per plan tried, the plan applied). The mock
reviewer uses templates. The Nemotron reviewer asks NIM for the prose as JSON and falls back
to the mock. Either way the verdict is the scorecard's outcome: the model explains it and
cannot overrule it. The raw samples are condensed into the scorecard and discarded.

**Memory** (`learning/store.py`). Each completed episode becomes
`memory/episodes/EP-NNNN.md`: a JSON front-matter block holding the whole `Experience` (so
nothing needs a YAML dependency) and a readable body, so people can read and diff it.
Readable incident descriptions derive the leftmost lane from the road's total lane count;
interior lanes are kept numeric. Episode ids continue from the highest one in memory, so
files survive restarts. Illustrative shape, placeholders instead of numbers:

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

`memory/playbook.md` is a generated digest of the 20 most recent lessons. Both it and
`memory/episodes/` are git-ignored; `DELETE /api/memory` forgets everything for a cold run.

Recall ranks past episodes by how similar the situation is, then by recency, then by the
lesson's confidence, and hands the top 3 to the agent. Past episodes below 0.3 similarity are
not recalled. Each current incident is matched to its most alike past incident with these
weights (sum 1.0):

| Feature | Weight |
|---|---|
| same incident type | 0.15 |
| same segment, else same street and direction, else same street | 0.35 / 0.2 / 0.1 |
| same severity | 0.1 |
| same share of lanes blocked | 0.1 |
| shares the up- or downstream intersection | 0.1 |
| the same lane(s) blocked | 0.1 |
| as many incidents at once | 0.1 |

So another crash on the same segment scores 1.0 and `varied-crash` against a `crash-ahead`
lesson about 0.65. Embedding-based recall (an NVIDIA embedding NIM) can later replace this
behind the same `recall` call.

**Injection.** Recalled lessons go into the agent's context (`IncidentContext.lessons`) and
are listed on the run (`ScenarioRun.recalled`). Over MCP, `start_analysis` returns an
`experience` block (the playbook plus the top similar episodes), and `recall_experience`
returns more. The instructions say lessons only seed the first round: they never replace
`validate_plan` and `simulate_plans`, so memory advises while the validator and the simulator
stay the gate.

**The mock acts on lessons** (`_apply_lessons` in `agent/mock.py`), for a single incident only:

- A close match (similarity 0.75 or more) that found a plan `effective` makes the mock
  simulate that plan first with just two others: 4 candidates, one wave of branches.
- A close match that found a plan `ineffective` drops that plan.
- A looser match (0.5 to 0.75) only reorders: effective plans first, ineffective last.

A warm `crash-ahead` run therefore uses fewer candidates and less wall time. That is
memorisation, not transfer; `varied-crash` (about 0.65) shows only a reordering.

**Finish.** The monitor stops, the live sim is paused (`EPISODE_PAUSE_ON_FINISH`, unless
another episode is working), the lesson is written, and the episode is `completed`.
Episode statuses: `armed → detected → analyzing → monitoring → reviewing → completed`, plus
`superseded`, `aborted` (reset, **Clear scene** or `POST /api/demo/stop` before the window
closed) and `failed` (analyst, implementor or reviewer error). A reset also fails any open
analysis, because its snapshot describes a city that no longer exists.

### Settings

Endpoints are in the [API](#api) table. Every setting is in `backend/app/config.py` **and**
`.env.example`.

| Setting | Default | Meaning |
|---|---|---|
| `DEMO_SCRIPT` | unset | Arm a script at startup |
| `EPISODE_ANALYST` | `auto` | `auto` (Nemotron if `NVIDIA_API_KEY` and `NEMOTRON_MODEL` are set, else mock), `mock` or `nemotron` |
| `EPISODE_MONITOR_S` | unset | Overrides the monitor window (see Monitor above) |
| `EPISODE_AGENT_TIMEOUT_S` | 300 | Wall-clock limit for one Nemotron analysis (plus a cap of 16 model turns) |
| `EPISODE_FALLBACK_TO_MOCK` | true | Run the mock analyst when the Nemotron loop fails |
| `EPISODE_PAUSE_ON_FINISH` | true | Pause the live sim when an episode completes |
| `AGENT_MAY_IMPLEMENT` | true | Let the agent apply its recommendation (the operator path works either way) |
| `MEMORY_ENABLED`, `MEMORY_DIR` | true, `<repo>/memory` | Memory on/off and where it lives |
| `MCP_URL` | unset | Where the Nemotron analyst reaches the MCP tools; unset = this app's server, in-process |
| `NEMOTRON_MODEL`, `NVIDIA_API_KEY`, `NEMOTRON_BASE_URL` | unset, unset, NIM | The model id has to be supplied (see [open questions](#decisions-and-open-questions)) |
| `NVIDIA_VA_MCP_URL` | unset | Streamable-HTTP VSS Video Analytics MCP endpoint; required for live VSS unless replay is set |
| `VSS_REPLAY_FILE` | unset | Development-only VSS-shaped timeline; with `SMART_CITY_PROVIDER=nvidia`, takes precedence over the live URL |
| `VSS_POLL_S` | 5 | Base wall-clock polling interval; failures back off to 60 s |
| `VSS_MATCH_MAX_DIST_M` | 40 | Maximum geometry match distance |
| `VSS_REQUIRE_VLM_CONFIRMATION` | true | Filter collisions unless VSS reports the VLM verdict `confirmed` |
| `VSS_DEFAULT_SEVERITY` | `major` | Twin modelling fallback because VSS does not define the twin's severity |

### Task list

`[x]` = written. **None of it has been run**, so each *Done when* is still to be seen (see
[Review notes](#review-notes-the-episode-pass)). Every step works with the **mock** analyst and
reviewer, so no NIM key is needed except for step 7.

- [x] Demo scripts (crash already happened / will happen / second crash / different crash)
- [x] Scenario engine solves several incidents together; branches replay standing responses
- [x] Two-crash mechanics in the engine (`abandon`, per-incident EMS probes, combined mock plans)
- [x] **1. Episode service** (`learning/episode.py`). Detection through a `CityService`
  incident listener; the live runner fires the armed script's crashes; the demo, episode and
  WebSocket interfaces. *Done when:* `POST /api/demo/start` on `crash-ahead` reaches
  `analyzing` with no click, with the mock analyst.
- [x] **2. The two-crash rule.** *Done when:* `double-crash` ends with one `superseded`
  episode and one `completed` episode that lists both incidents.
- [x] **3. Implementor** (`learning/implementor.py`), the MCP tool and the REST endpoint.
  *Done when:* after implementing, the live signal program ids change and the run's EMS
  probe is dispatched on the live sim.
- [x] **4. Monitor and scorecard** (`learning/monitor.py`, `scorecard.py`). *Done when:* a
  finished window yields a `Scorecard` with realised, predicted and prediction-error
  numbers, and reset or a cleared scene aborts the window.
- [x] **5. Reviewer and memory** (`learning/reviewer.py`, `store.py`). *Done when:* a
  completed episode leaves a readable `memory/episodes/EP-….md`.
- [x] **6. Finish step.** Stop the monitor, pause the live sim, store the lesson.
- [x] **7. Nemotron analyst** (`learning/analysts.py`, `agent/nemotron.py`). *Done when:*
  the same script runs with `EPISODE_ANALYST=nemotron`.
- [x] **8. Recall and injection**, plus the mock acting on lessons. *Done when:* a second
  episode's run lists the first episode under `recalled`.
- [x] **9. Frontend.** The Autonomous agent panel, **Apply to live signals**, accessible
  monitor progress, startup retry for the script list, types in sync, and advisory wording.
- [x] **10. Docs and the safety rule** (this README, `CLAUDE.md`, `docs/architecture.md`, the
  MCP spec, the UI).
- [ ] **Measure the learning.** Same script cold (empty memory) and warm, plus
  `varied-crash`: rounds and candidates used, wall time, recommendation quality
  (`GET /api/episodes` has `rounds`, `candidates`, `analysis_wall_s`, `recalled` and the
  scorecard). Same-script gains are memorisation; only the varied script shows transfer.
  Planned as part 2 of [milestone 4](#next-milestone-4), with a run protocol that adds a cold
  `varied-crash` control. Nothing has been measured yet.

The items this list used to defer (embedding-based recall, reverting an applied plan when the
scene clears, per-responder EMS metrics, verifying corridor and diversion lessons, the branch
speed-up, a slower live speed during analysis, the REST `NemotronAgentProvider`) are planned
in [milestone 4](#next-milestone-4). Still later: tests for `ScenarioService`, the MCP tools and
the learning package (the convention so far is no new test files, so agree on this first).

### Decisions and open questions

**Decided by the team:**

- The implementor really applies the plan to the live sim.
- The crash is scripted (already there, or later).
- The reviewer runs after a fixed number of simulated seconds.
- The lesson goes to a RAG memory, which is markdown-backed with structured recall first;
  embeddings are optional.
- The live collection stops at the end, and the live sim is paused when an episode finishes.
- The two-crash rule.
- No lesson is stored for a superseded episode.
- A standing plan stays on the signals after its agent is superseded.
- The mock analyst takes over if NIM fails.
- A scene cleared mid-window aborts the episode.
- The mock analyst acts on lessons.

**Chosen in the build pass, easy to change:** the materiality thresholds, the similarity
weights and the mock's pruning rules above; comparing predicted and realised on absolute
simulation time.

**Open:** which NIM model id to use (`nvidia/nemotron-3-super-120b-a12b` is built for agentic
tool calling; `nvidia/nemotron-3-nano-30b-a3b` is faster); whether to slow the live sim
during analysis to reduce staleness (milestone 4 plans it as an opt-in setting, off by
default).

### Risks and known gaps

- **The learnable signal is thin.** Timing plans move delay by about 1%, and the EMS
  corridor often loses once a queue has formed (see [limitations](#current-limitations)),
  so many lessons will be `inconclusive`. The high-impact plans (corridor, diversion) have
  the least verification, so check them before trusting their lessons.
- **Live-apply paths have never run on the live sim.** The corridor's `setPhase` jump in
  particular was never exercised. A pre-emption command that fails the runtime transition
  check raises on the live runner, which puts the live sim in `error`; **Reset** recovers.
- **Snapshots go stale** while the agent works, so the branches predicted a slightly
  earlier city than the one the plan is applied to. The comparison on absolute simulation
  time makes this visible as prediction error rather than hiding it.
- **Snapshots after a live timing change** rely on a fix (`custom_programs`) that was
  checked against the SUMO source, not by a run. Without it every branch of a later analysis
  would fail with `Unknown program`.
- **Two-crash EMS numbers are one figure.** Branch and live both time every responder the plan
  involves (its own dispatches, and any already on the way at the snapshot) from the same
  origin, and report the last one to arrive. Per-responder times are not shown (planned in
  [milestone 4](#next-milestone-4), parts 1 and 2). Read from the code, not run.
- **Nemotron is untested.** Its latency, rate limits and tool-calling reliability on NIM are
  unknown, and the fallback to the mock can hide a failure, so read the episode's steps.
- **The in-process MCP connection** uses the SDK's in-memory transport, which the SDK
  describes as a testing transport. `MCP_URL` switches the analyst to HTTP.

## Architecture

```
┌───────────────── frontend/ (React + TS + MapLibre) ─────────────────┐
│ map · episode panel · incident · KPIs · trends · ops log · plans    │
└────────── REST /api/* ─────────┬──────────── WS /ws/state ──────────┘
                                 │                    agents (MCP) ──► /mcp
┌───────────────────────── backend/ (FastAPI) ─────────┼──────────────────┐
│ api/routes.py              api/mcp_tools.py ◄────────┘                  │
│      │                           │     ▲ Nemotron analyst (in-process)  │
│      │   learning/ EpisodeService: detect → analyst → Implementor →     │
│      │   LiveMonitor → scorecard → reviewer → ExperienceStore (memory/) │
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
- **Agents never set signal states; one gated implementor applies recommendations.** Agents
  return plans as data (`SignalPolicy`, `EmergencyCorridor`, `RerouteAction`). A
  deterministic `SafetyValidator` checks them and simulation branches run them. The only
  live change is the implementor's: a completed run's recommended candidate, re-validated on
  the live signal programs and installed with the same steps a branch runs (see
  [How the pieces work](#how-the-pieces-work)). Every pre-emption command also passes a
  runtime transition check.

Details, design decisions and the NVIDIA integration plan are in
[docs/architecture.md](docs/architecture.md). That doc has stage-by-stage tables for
[Analyze Response](docs/architecture.md#analyze-response-pipeline) and for
[the autonomous episode](docs/architecture.md#autonomous-episode-applearning).

## Repository layout

```
backend/app/
  main.py               FastAPI app + lifespan
  config.py             env settings (SMART_CITY_PROVIDER, AGENT_PROVIDER, SIM_*, EPISODE_*, MEMORY_*)
  providers.py          provider and analyst factories + service assembly
  api/routes.py         REST + /ws/state
  api/mcp_tools.py      the scenario engine as MCP tools at /mcp
  models/domain.py      IntersectionState, RoadSegmentState, Incident, TrafficMetrics,
                        SignalPolicy, EmergencyCorridor, RerouteAction, SimulationCandidate,
                        Recommendation, snapshots, geometry
  models/scenario.py    ScenarioRun, ScenarioRunRequest, ScenarioStatus (analysis contract)
  models/episode.py     episode records: Episode, Implementation, Scorecard, Lesson, Experience
  models/api.py         CityState, requests/responses, ops events
  services/city.py      CityService: frames → CityState, commands, ops log
  services/scenarios.py ScenarioService: the Analyze Response pipeline (REST and MCP drivers)
  simulation/interface.py  TrafficSimulation contract
  simulation/sumo.py    SUMO/TraCI implementation (collisions, EMS, snapshots, metrics)
  simulation/branching.py  run one candidate in a fresh SUMO process from a snapshot
  simulation/preemption.py EMS green-corridor controller + runtime transition check (no TraCI)
  simulation/reroute.py diversion advisory with a compliance share
  simulation/runner.py  paced live loop on its own thread; fires scripted crashes
  simulation/network.py static topology, phase labelling, bidirectional geo projection, nearest roads
  simulation/metrics.py live + horizon TrafficMetrics
  smart_city/           provider boundary, mock, VSS MCP/replay clients, mapping and map matching
  agent/                AgentProvider: base, mock (8 rule-based plans, pruned by lessons);
                        nemotron.py: the NIM chat client (its REST AgentProvider is a stub)
  safety/validator.py   SafetyValidator (signal policies + corridors) and rule-based MVP limits
  websocket/hub.py      non-blocking WebSocket fan-out
  learning/episode.py   EpisodeService: demo scripts, the episode state machine, two-crash rule
  learning/analysts.py  MockAnalyst (the mock pipeline) and NemotronAnalyst (MCP client over NIM)
  learning/implementor.py  applies a recommendation to the live sim; standing responses
  learning/monitor.py   live sample ring and monitor windows
  learning/scorecard.py predicted vs realised, materiality thresholds, outcome
  learning/reviewer.py  mock and Nemotron reviewers (scorecard → lesson)
  learning/store.py     markdown memory, playbook, similarity recall
backend/tests/          network, simulation, runner, safety/agent, mock provider, API tests
frontend/src/
  App.tsx               layout + actions
  hooks/useCityStream.ts   WebSocket client (reconnect, trend backfill, scenario runs, episodes)
  components/map/       MapLibre map, layer styles, vehicle glyphs, plan overlays
  components/plans/     response plans: candidate cards, KPI comparison, horizon chart, dock,
                        Apply to live signals
  components/EpisodePanel.tsx  autonomous agent: scripts, step strip, lesson, memory
  components/           top bar, incident card, KPI tiles, inspector, trends, ops log
  lib/plans.ts          analysis state, deltas, plan overlays
  dev/                  ?fixture=scenario replay of a recorded run
simulation/
  networks/grid3x3/     build_network.py → grid3x3.net.xml (named streets, 9 signals)
  networks/pittsburgh_oakland/  OSM extract (ODbL) + build_network.py → oakland.net.xml (33 signals)
  scenarios/downtown_grid/  scenario.sumocfg, demand, vehicle types, scenario.json,
                        demos/*.json scripted crash scenarios for episodes
  scenarios/pittsburgh_oakland/  the same files and demo scripts for Oakland; synthetic demand
  scenarios/*/vss/      development-only VSS-shaped replay timelines (coordinates not run/verified)
  controllers/          how pre-emption plugs in (the code lives in backend/app/simulation/)
memory/                 written at runtime: episodes/EP-NNNN.md lessons and playbook.md (git-ignored)
docs/
  architecture.md       design notes, both pipelines stage by stage, MCP tools
  milestone-2/          the milestone-2 plan, per-feature specs and results (historical)
  milestone-4/          the milestone-4 plan: MASTER.md, one handoff per part, the step-0 contract
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
| GET | `/api/smart-city/status` | provider health, last success/error, filtered and malformed document counts |
| POST | `/api/scenarios/run` | start Analyze Response: `{"incident_id"?, "incident_ids"?, "horizon_s": 600, "ems_probe": true}` → `ScenarioRun` (202; 409 if no active incident or a run is open). `incident_ids` analyzes several crashes together |
| GET | `/api/scenarios` | recent runs (newest first, last 10) |
| GET | `/api/scenarios/{id}` | one run with candidates, metrics, timelines, the recommendation, and `implementation` once applied |
| POST | `/api/scenarios/{id}/implement` | operator path: apply the run's recommendation to the live sim (same code as the agent's) → `Implementation`. 404 unknown run; 409 not completed, already applied, predating a reset or an incident cleared; 400 rejected by the validator on the live programs |
| GET | `/api/demo` | demo scripts, the armed script, the analyst, the latest episode, memory stats |
| POST | `/api/demo/start` | `{"script": "crash-ahead"}`: reset and resume the city, then arm the script → the `armed` `Episode` (202); 409 with an external VSS provider |
| POST | `/api/demo/stop` | disarm: no more scripted crashes or autonomous response; aborts the working episode |
| GET | `/api/episodes`, `/api/episodes/{id}` | recent episodes (newest first, last 20), one episode |
| GET, DELETE | `/api/memory` | remembered episodes and the playbook; DELETE forgets them (a cold run) |
| WS | `/ws/state` | `hello` (state, events, trend, latest run, latest episode) then `state` / `status` / `event` / `scenario` / `episode` messages |
| MCP | `/mcp` | streamable HTTP: `start_analysis` (`incident_ids?`, default all active incidents), `validate_plan`, `simulate_plans`, `get_analysis`, `submit_recommendation`, `implement_recommendation`, `recall_experience` ([spec](docs/specs/scenario-engine-mcp.md)) |

## Current limitations

- **An applied plan stays on the live signals until a reset.** Nothing reverts it when
  the scene clears.
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
- The mock agent is rule-based: it proposes a fixed set of 8 plans (fewer when a close
  lesson prunes them) and recommends with a fixed rule (see
  [architecture.md](docs/architecture.md#analyze-response-pipeline)).
- `NvidiaSmartCityProvider` is built against NVIDIA's published VSS 3.2 MCP tool contract,
  but neither the live client nor replay fixtures have been run. A real server may expose
  field variants that need an update in the intentionally isolated `vss_mapping.py`.
  The REST pipeline's `NemotronAgentProvider` remains a stub; Nemotron runs only as an
  episode's analyst and reviewer.
- Mirrored crash size, spacing, blocked-lane effects and pass speed are twin assumptions,
  not quantities observed by VSS. Lane 0 is assumed because VSS has no lane-grade position.
- Synthetic network (the grid) and synthetic demand (both cities). The crash physics
  (blocked lane plus a 0.6 m/s pass speed) and congestion thresholds are calibrated for
  this grid, not measured data.
- One live simulation per backend process, one analysis run and one working episode at a
  time. State is in memory, except the lessons under `memory/`. Nothing has auth, including
  `/mcp` and its `implement_recommendation`.
- The live EMS ETA is an estimate (observed speeds plus expected signal waits). The
  realised response time comes from the simulation.

## Next: milestone 4

**VSS input is built but not run; the other two parts remain planned.**

**First, milestone 3 has to be run.** It is built, not yet run end to end. What remains is to
run it (the [review notes](#review-notes-the-episode-pass) list what to try, in order), to run
it with Nemotron once a model id is chosen, and to measure the learning (see the task list).

Milestone 4 takes what milestones 2 and 3 deferred: the NVIDIA Smart City input that was the
second half of milestone 3, the episode's "Later" items, and the measurement of the learning.
It is split into three parts with disjoint files, planned in
[docs/milestone-4/](docs/milestone-4/MASTER.md). Each part's row below is where its status is
kept.

| Part | Branch and plan | What it delivers | Status |
|---|---|---|---|
| 1. Twin engine | `feature/twin-engine`, [plan](docs/milestone-4/feature-twin-engine.md) | The branch speed-up in `sumo.py`; the EMS corridor explained and its levers tried (a longer detection distance, a combined corridor and diversion plan); per-responder EMS response from the twin; a `revert_response()` primitive; a pre-emption failure on the live twin that degrades instead of stopping it; an opt-in slower live speed during analysis | Planned |
| 2. Agent and memory | `feature/agent-memory`, [plan](docs/milestone-4/feature-agent-memory.md) | Reverting an applied plan when the scene clears (automatic, and by the operator); per-responder EMS in the scorecard; response checks, so corridor and diversion lessons are verified before they are trusted; embedding-based recall; a learning report and the cold, warm and varied run protocol; the REST `NemotronAgentProvider` | Planned |
| 3. VSS input | `feature/vss-input`, [plan](docs/milestone-4/feature-vss-input.md) | `NvidiaSmartCityProvider` on the VSS Video Analytics MCP tools, with a replay client for development; a map matcher (lat/lon and place names → segment and lane); mirroring a reported incident into the twin so it can be analyzed; cameras and match details in the UI; Oakland demo scripts | Built, not run |

**Prepared but not faked** still holds for part 3. The full Blueprint is not installed or run;
until a real VSS endpoint exists, the mock stays the default provider. The replay client reads
VSS-shaped documents and labels their incidents `vss-replay`. The mapping was checked against
NVIDIA's published VSS 3.2 Video Analytics MCP reference, but not against a running server.

Not in milestone 4: tests (see the task list above), autonomous episodes on real incidents
without a demo script, and auth. The assumptions the plans make (for example what reverting a
diversion does, and that a blue-light device is off unless approved) are in the
[decisions table](docs/milestone-4/MASTER.md#decisions-to-confirm); confirm them before
starting.

## Review notes: the episode pass

Scope: README tasks 1–10 of the autonomous episode, on top of the multi-crash groundwork from
the previous pass, plus five fixes found while reading the code. The single-crash
**Analyze Response** walkthrough should behave as before, apart from those fixes.

### What changed

| Area | Files | Change |
|---|---|---|
| Fixes | `simulation/sumo.py`, `models/domain.py` | Programs installed at runtime are recorded, carried in the snapshot (`custom_programs`) and re-created before `loadState`. Without this, every branch after a live timing change fails with `Unknown program` (SUMO's `MSStateHandler`). |
| | `api/mcp_tools.py` | `start_analysis` read `a.probe`, which does not exist (`Analysis.probes`). Every call raised after the snapshot and left the run locked until the idle timeout. |
| | `services/scenarios.py` | `finish` and `evaluate` refuse an analysis that is already closed, so a pipeline still running after `abandon` can no longer complete the failed run. `run_pipeline` is public (the mock analyst awaits it); new `ScenarioRun.rounds` and `.recalled`. |
| | `smart_city/mock.py` | A frame clears vanished incidents before detecting new ones. Otherwise, after a reset, a crash detected in the first frame would see the previous run's incidents as still active. |
| | `frontend/src/App.tsx` | Response plans also show an analysis whose `incident_ids` include the latest incident. Before, a two-crash analysis was hidden. |
| Episode | `learning/episode.py`, `analysts.py`, `implementor.py`, `monitor.py`, `scorecard.py`, `reviewer.py`, `store.py` (all new) | Everything in [The autonomous, self-learning episode](#the-autonomous-self-learning-episode). |
| | `simulation/runner.py` | `set_boot_events` became `set_scripted_events`: the runner fires scripted crashes inside the warm-up **and** while running. They fire on the runner's own thread, so a reset cannot race them. |
| | `services/city.py` | `incident_listeners`, `reset_listeners`, `add_frame_observer`, `set_scripted_events`, `crash_command` (fills defaults the same way as Inject), `publish_episode`, and `episode` in `hello`. |
| | `simulation/branching.py` | `apply_plan` also returns the vehicles diverted. |
| | `agent/mock.py` | `_apply_lessons`: close lessons prune the plan set, looser ones reorder it. |
| | `agent/nemotron.py` | `NimClient`: OpenAI-compatible chat completions over `httpx2`. |
| | `api/mcp_tools.py`, `api/routes.py`, `models/*`, `providers.py`, `main.py`, `config.py` | The tools, endpoints, records, settings and wiring described above. |
| UI | `components/EpisodePanel.tsx` (new), `plans/ResponsePlans.tsx`, `hooks/useCityStream.ts`, `lib/plans.ts`, `api/*`, `dev/replay.ts`, `styles.css` | The Autonomous agent panel, **Apply to live signals**, the applied/advisory footer, Analyze Response disabled while an episode is working, and the types in sync. |
| Config and docs | `.env.example`, `requirements.txt` (`httpx2`, already a dependency of `mcp`), `.gitignore` (`memory/`), `CLAUDE.md`, `docs/architecture.md`, `docs/specs/scenario-engine-mcp.md`, this README | The safety rule now names its one gated exception everywhere. |

### What was and was not checked

- **Run: build checks only.** `python -m compileall app` and an import of `app.main` both
  passed. The import builds the app and registers every MCP tool, so the tool schemas load;
  it starts no SUMO and no server. `make build` (tsc + vite) and
  `npm --prefix frontend run lint` are clean.
- **Not run:** every behavior in this pass. CLAUDE.md hard rule 1 allows no tests, no
  scratch scripts and no running the app. Nothing has started an episode, applied a plan to a
  live sim, loaded a snapshot with `custom_programs`, called NIM, or rendered the new panel.
  The existing test suite was not run either.
- **To try, in order:**
  1. A cold `crash-ahead` with the mock (steps 1–5 of the
     [walkthrough](#demo-walkthrough-autonomous-episode)). Check the ops log for the applied
     programs and the EMS probe, and that `memory/episodes/EP-0001.md` is readable.
  2. A warm `crash-ahead`. `recalled` lists the first episode, and it uses fewer candidates
     if that lesson was `effective`.
  3. `varied-crash`, `crash-already` and `double-crash`.
  4. The snapshot fix. From any MCP client (for example `npx @modelcontextprotocol/inspector`
     on `http://127.0.0.1:8000/mcp`), call `start_analysis`, `simulate_plans` with a timing
     plan, `submit_recommendation` for it and `implement_recommendation`. Then run another
     analysis: its branches must complete, not fail with `Unknown program`.
  5. Reset and **Clear scene** during `monitoring`: both should give `aborted`. With
     `AGENT_MAY_IMPLEMENT=false`, the episode should wait for **Apply to live signals**.
  6. Nemotron: set `NVIDIA_API_KEY`, `NEMOTRON_MODEL` and `EPISODE_ANALYST=nemotron`. A wrong
     key should fall back to the mock, visible in the episode's steps.

### Where a reviewer should look hardest

1. **The live apply** (`implementor.py`). One `run_on_live` command validates, dispatches and
   installs. Check that `run.implementation`, the standing registry and the episode stay
   consistent when the caller is cancelled mid-apply (the apply runs in a shielded task).
2. **Episode transitions** (`episode.py`): supersede, abort and fail from every status, and
   the reset hook re-arming. When review starts, `_working` is released, so a crash during
   review starts a new episode and the finish step does not pause the sim under it.
3. **Scripted events** (`runner.py`). Once the thread runs, `set_scripted_events` goes through
   the command queue, so a new script cannot fire into the old simulation before the reset
   reboots it.
4. **The scorecard** (`scorecard.py`): the absolute-time window, the empty-window cases, and
   whether the thresholds give sensible verdicts on real runs.
5. **The Nemotron loop** (`analysts.py`): message shapes against NIM's OpenAI-compatible API
   (tool calls, `tool_call_id`, arguments as a JSON string), the forced `incident_ids`, and
   the nudge when the model stops early.
6. **Still unexercised from the groundwork pass:**
   - `_realised_emergency_eta` returns `None` while any responder has not arrived.
   - Closing an analysis while branches run (`_close`, `_run_round`, `_drop_snapshot`).
   - Per-incident EMS probes.
   - The mock's combined plans: `_combined_plans` keeps the first policy per intersection,
     and the 8-candidate cap drops per-incident plans and `aggressive-flush` at the tail.
