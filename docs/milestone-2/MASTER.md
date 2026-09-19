# Milestone 2: Analyze Response (parallel work plan)

**Goal.** When an incident is active, the operator clicks **Analyze Response**. The
backend then:

1. snapshots the live SUMO network;
2. asks the agent (mock for now) for candidate responses;
3. rejects unsafe ones with the deterministic `SafetyValidator`;
4. simulates the baseline and every safe candidate in parallel fresh SUMO branches over
   a 10-minute horizon, with an EMS probe in each branch;
5. recommends one with quantitative evidence.

The UI shows the candidates, how each compares with the baseline, and the
recommendation. Nothing ever touches the live signals.

The work is split into **three independent feature branches**. They share no files, and
none of them waits for another. All three start from the same base commit, which already
contains the frozen contract below.

| Branch | Handoff | In one line |
|---|---|---|
| `feature/scenario-engine` | [feature-scenario-engine.md](feature-scenario-engine.md) | `ScenarioService`: snapshot → propose → validate → parallel SUMO branches → recommend; `/api/scenarios/*`; WebSocket progress |
| `feature/response-strategies` | [feature-response-strategies.md](feature-response-strategies.md) | EMS green-corridor pre-emption and diversion rerouting inside SUMO; mock agent proposes them; validator and recommendation logic |
| `feature/analysis-ui` | [feature-analysis-ui.md](feature-analysis-ui.md) | Response-plans panel, candidate cards, baseline comparison, recommendation, map overlays (built against a fixture) |

Out of scope for all three: applying a plan to the live simulation, MCP tools,
`NemotronAgentProvider`, new NVIDIA integration. These come after integration, in
milestone 3.

## Start here

1. **Base.** `main`, at the commit that adds `docs/milestone-2/`. It contains
   milestone 1 plus the frozen contract code. The three branches already exist on
   `origin` at that commit, so check out yours:
   ```bash
   git fetch origin && git switch feature/<name>
   ```
2. Read this file, then **only** your own handoff. Each handoff is self-contained for
   its branch.
3. Work only in the files your branch owns (table below), and fill in the `## Result`
   section of your handoff when done.

To hand a branch to an agent, a prompt like this is enough:

> You own `feature/scenario-engine`. Read `docs/milestone-2/MASTER.md` and
> `docs/milestone-2/feature-scenario-engine.md`, then implement it.

## Rules for every agent

- **Stay inside your file ownership** (table below). If you believe a frozen or
  foreign file must change, don't edit it. Write a *Contract change request* in the
  Result section of your handoff and work around it.
- **No new test files.** The user wants demo progress, not test coverage. Keep the
  existing suite passing (`make test`, about 20 s) and verify behavior by running
  things: scripts in your scratchpad, curl, the browser.
- **No new dependencies** unless unavoidable. If you add one, say so in your Result.
- Commit on your feature branch with small, meaningful commits. End every commit
  message with `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`. Push only your
  own feature branch. **Never push to `main`**: merging happens in the integration pass.
- When done, fill in the `## Result` section at the bottom of **your own** handoff file
  (the only file under `docs/` you may edit): what you built, how you verified it,
  numbers you measured, known gaps, and anything the integrator must know.

## Environment

Each agent works in its own git worktree on its own branch. For setup, either run
`make setup`, or reuse the milestone-1 environments by symlinking them. They are
git-ignored, and the app code is always imported from your own worktree.

```bash
ln -s /Users/donald.heddesheimer/Documents/GitHub/sh-project/.claude/worktrees/smart-city-incident-simulator-d56573/backend/.venv backend/.venv
ln -s /Users/donald.heddesheimer/Documents/GitHub/sh-project/.claude/worktrees/smart-city-incident-simulator-d56573/frontend/node_modules frontend/node_modules
```

**Ports** (8000 and 5173 belong to the milestone-1 demo; don't use them):

| Branch | Backend | Frontend |
|---|---|---|
| feature/scenario-engine | 8001 | — |
| feature/response-strategies | 8002 (only if needed) | — |
| feature/analysis-ui | 8003 (optional, stock backend) | 5174 (`BACKEND_URL=http://127.0.0.1:8003`) |

Start servers with background Bash (`preview_start` can't read this directory), for
example `cd backend && .venv/bin/uvicorn app.main:app --port 8001`.

## File ownership

Every path has exactly one owner. "Frozen" means the contract: nobody edits it on a
feature branch.

| Path | Owner |
|---|---|
| `backend/app/models/domain.py`, `models/api.py`, `models/scenario.py` | **frozen** |
| `backend/app/agent/base.py`, `backend/app/simulation/interface.py` | **frozen** |
| `frontend/src/api/types.ts` | **frozen** |
| `docs/milestone-2/MASTER.md`, `docs/milestone-2/fixtures/**` | **frozen** |
| `README.md`, `docs/architecture.md`, `Makefile`, `scripts/**`, `backend/tests/**`, `backend/requirements*.txt` | **frozen** (integration pass) |
| `backend/app/services/**` (city.py, events.py, new scenarios.py) | scenario-engine |
| `backend/app/api/**`, `backend/app/main.py`, `backend/app/providers.py`, `backend/app/config.py` | scenario-engine |
| `backend/app/simulation/branching.py` (new), `backend/app/simulation/runner.py` | scenario-engine |
| `backend/app/websocket/**`, `.env.example` | scenario-engine |
| `backend/app/simulation/sumo.py`, `network.py`, `metrics.py`, `scenario.py` | response-strategies |
| `backend/app/simulation/preemption.py`, `reroute.py` (new) | response-strategies |
| `backend/app/agent/mock.py`, `backend/app/safety/**` | response-strategies |
| `simulation/**` (network, scenario, controllers) | response-strategies |
| `frontend/**` except `src/api/types.ts` | analysis-ui |
| `backend/app/smart_city/**`, `backend/app/agent/nemotron.py` | nobody |
| anything not listed above (e.g. `__init__.py` files, `pytest.ini`, `.claude/`, other docs) | nobody |

## The frozen contract (already in the base commit)

**Python models** (`backend/app/models/`):

- `domain.py`
  - `EmergencyCorridor {intersection_ids[] (empty = every signal on a responder's route), detection_distance_m, min_served_green_s, max_hold_s, reason}`
  - `RerouteAction {avoid_segment_ids[], compliance 0..1, reason}`
  - `SimulationCandidate {id, name, description, policies[], corridor?, reroutes[], status, metrics?, timeline[MetricSample], violations[], notes[], wall_time_s?}`.
    The id `"baseline"` is the do-nothing reference.
  - `MetricSample` (moved here and re-exported from api.py) and `Recommendation {candidate_id, summary, rationale[]}` (moved here and re-exported from agent/base.py).
- `scenario.py`
  - `ScenarioStatus`: `queued → proposing → simulating → recommending → completed | failed`
  - `ScenarioRunRequest {incident_id?, horizon_s=600 (120–1800), ems_probe=true}`
  - `ScenarioRun {id "SCN-0001", incident_id, status, agent, created_at, completed_at?, snapshot_sim_time?, horizon_s, ems_probe, candidates[], recommendation?, error?}`

**Agent boundary** (`agent/base.py`):
- `CandidatePlan {id, name, description, policies[], corridor?, reroutes[]}`. A plan
  with none of the three is the baseline.
- `IncidentContext` gained `emergency_vehicles[]` and `ems_origin_segment`.

**Simulation contract** (`simulation/interface.py`). These are non-abstract; the
defaults raise `NotImplementedError` or return `[]`, and response-strategies implements
them in `SumoSimulation`:
- `enable_emergency_corridor(corridor) -> None`: pre-empt signals ahead of every
  en-route EMS vehicle, including ones dispatched later.
- `reroute_vehicles(action) -> int`: persistent diversion advisory; returns the number
  of vehicles diverted at activation.
- `response_notes() -> list[str]`: human-readable effects so far, e.g. `"3 pre-emptions (A2, B2, C2); longest hold 14s"`.
- `run_for(seconds, speed_multiplier=None, on_sample=None, sample_every_s=30.0)`:
  `on_sample(TrafficMetrics)` receives live metrics every `sample_every_s` simulated
  seconds (already implemented in `SumoSimulation`).

**Safety** (`safety/validator.py`, owned by response-strategies after the base commit):
- `SafetyValidator.validate(policy, base_program) -> ValidationResult` (unchanged).
- `SafetyValidator.validate_corridor(corridor, programs) -> list[Violation]`. The base
  commit has a working rule-based version: known signals, a min-served-green floor, a
  max-hold ceiling, and bounds on detection distance.

**HTTP / WebSocket** (implemented by scenario-engine, consumed by analysis-ui):

| Method | Path | Body → Response | Errors |
|---|---|---|---|
| POST | `/api/scenarios/run` | `ScenarioRunRequest` → **202** `ScenarioRun` (status `queued`) | 409 run already in progress / no active incident · 404 unknown incident · 503 simulation not ready |
| GET | `/api/scenarios/{id}` | → `ScenarioRun` | 404 |
| GET | `/api/scenarios` | → `ScenarioRun[]`, newest first (≤ 10) | |
| WS | `/ws/state` | new message `{"type":"scenario","data":ScenarioRun}` on **every** change (status, each candidate update, recommendation); `hello.data.scenario` = latest run or `null` | |

Behavioral guarantees the UI can rely on:
- Candidates keep the agent's order, with `baseline` first.
- Rejected candidates appear with `status: "rejected"` and non-empty `violations`, and
  are never simulated.
- `timeline[].t` is absolute simulation time, starting after `snapshot_sim_time`.
- `metrics.emergency_vehicle_eta` in a completed candidate is the **realised** EMS
  response time in that branch. It is `null` if no responder reached the scene within
  the horizon (or `ems_probe` was false).

**TypeScript mirror:** `frontend/src/api/types.ts` (`SimulationCandidate`,
`ScenarioRun`, `StreamMessage` including `scenario` and `hello.data.scenario`).

**Fixture:** `fixtures/scenario-run.json` is a completed run with 8 candidates (one
rejected by the validator). The **numbers are synthetic**: they are there for UI
development, not simulation output. The candidate ids and names are the ones
response-strategies will produce.

## Integration (after all three branches land)

Merge in any order; the branches touch disjoint files. Then, in one integration pass:

1. Run end to end: collision → Analyze Response → candidates stream in → recommendation.
   Check that the corridor candidate improves EMS response.
2. Reconcile any Contract change requests.
3. Update `README.md` and `docs/architecture.md` (API table, demo script, limitations,
   next milestone).

**Integration result** (done in the milestone-3 prep PR):
1. The end-to-end run works: collision → Analyze Response → candidates stream in →
   recommendation, 26 s wall time for 7 branches. The corridor check **did not hold**
   once a queue had formed. Its EMS response was 334 s against 292 s for the baseline,
   and the diversion advisory won instead. Numbers and levers are in
   [feature-response-strategies.md](feature-response-strategies.md#result).
2. Contract change requests:
   - done: `ems_origin_segment`, the `reroute_vehicles` docstring, the `mcp` dependency;
   - holds as is: the candidate cap;
   - not done: the test relaxation, because the project no longer adds tests.
3. `README.md` and `docs/architecture.md` now describe milestone 2 and the pipeline stage
   by stage.
