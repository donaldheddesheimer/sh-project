# Handoff: `feature/scenario-engine`

Read [MASTER.md](MASTER.md) first. It has the frozen contract, the file ownership, the
ports and the rules. This file covers only your branch.

## Context

This is a traffic operations center: FastAPI plus a live SUMO/TraCI digital twin of a
3×3 downtown grid, with a React/MapLibre console. Milestone 1 is done. Collisions can be
injected, a mock Smart City provider "detects" them (INC-0001), queues spill back, and
EMS can be dispatched. Read `README.md` and `docs/architecture.md` for how it works.

The pieces for candidate analysis already exist:
- `SumoSimulation.save_snapshot()` / `restore_snapshot()`. Fresh processes restored
  from one snapshot evolve bit-identically.
- `apply_signal_policy()`, and horizon `TrafficMetrics` from `run_for()`.
- `MockAgentProvider` (timing plans) and `RuleBasedSafetyValidator`.

Nothing connects them yet. **That is your job:** the orchestration service, the API and
the WebSocket progress.

## Goal

`POST /api/scenarios/run` does the following, while the live simulation keeps running:

1. **Guard.** Return 503 if the live simulation is starting or in error, and 409 if a
   run is already in progress. Resolve the incident as `request.incident_id` or the most
   recent active incident; return 409 if there is none and 404 if the id is unknown.
   Create a `ScenarioRun` (`SCN-0001`, …) with status `queued`, broadcast it, and return
   **202**. Everything after this runs in a background task.
2. **Snapshot.** In a single `runner.call(...)`, so both come from one instant between
   steps, save a snapshot of the live simulation and read the signal program of every
   signalized intersection. Set `snapshot_sim_time`.
3. **Propose** (status `proposing`).
   - Build an `IncidentContext` from the latest `CityState`: the incident, sim time,
     segments, intersections, the programs from step 2, `emergency_vehicles`, and
     `ems_origin_segment` (`scenario.ems_stations[0].edge`).
   - Call `agent.propose_candidates(context)`.
   - If the agent returns no plan with id `baseline`, insert the do-nothing baseline
     first. Cap the list at `SCENARIO_MAX_CANDIDATES` (default 8).
   - If the agent raises (the Nemotron stub raises `NotImplementedError`), the run
     becomes `failed` with `error` set.
4. **Validate.** Nothing unsafe is ever simulated.
   - Check each policy with `validator.validate(policy, programs[policy.intersection_id])`.
     A policy for an intersection with no program counts as a violation.
   - Check the corridor with `validator.validate_corridor(corridor, programs)`.
   - Check that every reroute's `avoid_segment_ids` exist in the network.
   - Any finding makes the candidate `rejected`, with human-readable `violations`, for
     example `"C2 phase 3: green 8s < 12s (vehicle/pedestrian minimum)"`. Rejected
     candidates are never simulated.
5. **Simulate** (status `simulating`). Run every accepted candidate **in parallel** in a
   `ThreadPoolExecutor(SCENARIO_WORKERS)` (default 4). Each branch:
   - Creates a **brand-new** `SumoSimulation` with a unique label, then `start()` and
     `restore_snapshot(snapshot)`. Never reuse a process: re-loading into a process that
     already ran diverges.
   - Dispatches the EMS probe, if `ems_probe` is on and no dispatch in the snapshot is
     still `en_route`. It goes from the station segment to the incident segment at
     `position_m - 20`, in the blocked lane: the same staging as
     `CityService.dispatch_emergency`, so factor out a shared helper. The probe is
     spawned **before** the plan is applied, at the same sim time in every branch, which
     keeps branches comparable.
   - Applies the plan: each policy via `apply_signal_policy`, then
     `enable_emergency_corridor(corridor)` if set, then `reroute_vehicles(action)` for
     each reroute.
   - Calls `metrics = run_for(horizon_s, on_sample=..., sample_every_s=SCENARIO_SAMPLE_S)`.
     `on_sample` appends a `MetricSample` to `candidate.timeline`, with `t` = absolute
     sim time.
   - Appends `sim.response_notes()` to `candidate.notes` and records `wall_time_s`.
   - Calls `close()` in a `finally`.
   - Any exception makes the candidate `failed` with the message in `notes`; the run
     carries on. Before `feature/response-strategies` lands, corridor and reroute raise
     `NotImplementedError`, and that is the expected outcome.
   - Broadcast the run when a candidate starts (`running`) and when it finishes.
6. **Recommend** (status `recommending`). Call `agent.recommend(context, candidates)`.
   If it names a candidate that isn't `completed`, fall back to `baseline` and add a
   note. Then set status `completed` and `completed_at`, broadcast, and delete the
   snapshot file.

Ops log entries (`city.events.add`), each tagged with the incident id:
- when a run starts: "Analyzing INC-0001: 7 candidates, 1 rejected by safety validator";
- when it completes: "Recommendation: <name> (delay 96s → 81s, EMS 4:23 → 2:30)";
- when a run fails, at level `alert`.

## Also build

- `GET /api/scenarios/{id}`, and `GET /api/scenarios` (newest first, the last
  `SCENARIO_HISTORY` = 10 runs, in memory).
- WebSocket: broadcast `{"type":"scenario","data":<ScenarioRun>}` through the existing
  `ConnectionHub`, with the `envelope()` helper in `services/city.py`, on every change.
  Add `"scenario": <latest run or null>` to the `hello` payload so a page reload
  mid-analysis resumes.
- Settings in `config.py`, documented in `.env.example`: `SCENARIO_HORIZON_S` (600,
  used when the request omits it), `SCENARIO_WORKERS` (4), `SCENARIO_SAMPLE_S` (30),
  `SCENARIO_MAX_CANDIDATES` (8), `SCENARIO_HISTORY` (10).
- Wiring in `providers.py` / `main.py`. The service needs a factory for branch
  simulations; build it next to the live factory in `build_city_service`. Keep
  `CityService` independent of concrete providers, as it is today.

## Files you own

Create:
- `backend/app/services/scenarios.py` (`ScenarioService`)
- `backend/app/simulation/branching.py`: a pure "run one candidate in a fresh branch"
  function. It takes a factory, the snapshot, the plan and the probe spec, and returns
  a filled `SimulationCandidate`. No asyncio in here.

Edit:
- `backend/app/services/city.py`
- `backend/app/services/events.py`
- `backend/app/api/routes.py`
- `backend/app/main.py`
- `backend/app/providers.py`
- `backend/app/config.py`
- `backend/app/simulation/runner.py` (only if needed)
- `backend/app/websocket/hub.py` (only if needed)
- `.env.example`
- the `## Result` section of this file

**Do not touch:**
- `sumo.py`, `network.py`, `metrics.py`, `agent/mock.py`, `safety/**`, `simulation/**`
  (all owned by feature/response-strategies);
- anything in `frontend/`;
- the frozen contract files, `README.md`, `docs/architecture.md`, and tests.

## Design notes and gotchas

- **TraCI threading.**
  - The live connection belongs to the runner thread, so talk to it only through
    `await runner.call(lambda sim: ...)`.
  - Branch simulations are separate processes with their own connections. Drive each
    one from exactly one worker thread.
  - `SumoSimulation.start()` already serializes `traci.start` with a module lock, so
    parallel starts are safe.
- **Snapshots.** `restore_snapshot` restores disruptions (the crash, and its
  rubbernecking slowdown) and dispatches, and it re-subscribes everything. Snapshots
  capture **base** signal programs only, which is fine: the live simulation never runs
  a policy in this milestone, so the programs you read in step 2 are the base programs
  to validate against.
- **Horizon metrics** (see `metrics.py`):
  - `mean_vehicle_delay` is time loss accrued per vehicle served, *including*
    vehicle-seconds waiting to enter the network, so blocking entries can't look good.
  - `emergency_vehicle_eta` is the realised response time of the first responder to
    arrive in the window, or `None`.
  - The probe is dispatched at window start, so for the probe it is the full response
    time.
- **Speed.** A 600 s horizon on this grid takes roughly 0.5–1 s of wall time headless.
  Measure it: with 4 workers, a run of 7 candidates should finish in a few seconds.
  Progress updates still matter, because the UI animates them.
- **Isolation.** Keep the live simulation running during analysis; branches must not
  pause or slow it noticeably. Don't hold the asyncio loop: await
  `loop.run_in_executor` for each branch.
- **Reset during a run.** Branches are independent, so let the run finish. The results
  describe the snapshot moment.
- The frozen `IncidentContext` requires `signal_programs` for **every** signalized
  intersection. The agent needs them to build policies.

## Verify (no new test files)

The current mock agent on your branch proposes timing plans only. That is enough.

1. `make test` passes.
2. Start the backend on :8001.
   ```bash
   curl -X POST :8001/api/incidents/inject -H 'content-type: application/json' -d '{}'
   ```
   Wait about 2 simulated minutes (use `/api/simulation/speed` with
   `{"multiplier":16}`), then:
   ```bash
   curl -X POST :8001/api/scenarios/run -H 'content-type: application/json' -d '{}'
   ```
   Poll `GET /api/scenarios/{id}` until it is `completed`, and print a table of each
   candidate's status, delay, queue, throughput, EMS response and wall time.
3. **Determinism.** Two runs from the *same* snapshot must give identical baseline
   metrics. Use a scratch script that calls your branching function twice with one
   snapshot.
4. **WebSocket.** A small scratch client (`websockets` is installed with
   `uvicorn[standard]`) shows `hello.data.scenario` and a stream of `scenario`
   messages covering every status transition.
5. **Guards.** Check each error response:
   - 409 when there is no incident;
   - 409 on a second run while one is in progress;
   - 404 for an unknown incident;
   - a hand-made plan with an 8 s green comes back `rejected` with violations (call the
     service from a script with a stub agent);
   - a plan with a `corridor` comes back `failed`, with a `NotImplementedError` note,
     while the rest of the run completes.
6. The live simulation's `sim_time` keeps advancing during a run. Check it with two
   `GET /api/state` calls.

## Definition of done

Steps 1–6 hold, and the code reads like the existing services: typed, small functions,
comments only where needed. Everything is committed on `feature/scenario-engine`, and
the Result section below is filled in.

## Result

**Built.**
- `services/scenarios.py`: `ScenarioService` runs guard → snapshot → propose → validate → parallel branches → recommend. Each run keeps a history of the last `SCENARIO_HISTORY` runs, and ops-log entries are made on start, recommendation and failure.
- `simulation/branching.py`: the pure `run_branch()`, plus `ProbeSpec` and `probe_for_incident()`. `CityService.dispatch_emergency` now uses the same staging helper.
- `/api/scenarios/run` (202), `/api/scenarios` and `/api/scenarios/{id}`.
- WebSocket `scenario` messages, and `hello.data.scenario`.
- The `SCENARIO_*` settings in `config.py` and `.env.example`.

**How it hooks in.**
- `ScenarioService` depends on `CityService`, not the other way around. `CityService` gained three small public hooks: `run_on_live(fn)` (wraps `runner.call`), `latest_scenario`, and `publish_scenario(run)`.
- `providers.build_city_service` became `build_services()`. It returns `(city, scenarios)` and builds the branch factory (labels `branch-N`) and the `RuleBasedSafetyValidator`.
- `main.py` stores `app.state.scenarios` and shuts the worker pool down on exit.
- The `ScenarioRun` is only mutated on the event loop. Workers return their own candidate object, and "running" is signalled with `call_soon_threadsafe`, so broadcasts never race a worker.

**Verified** (scratch scripts, no new test files):
1. `make test`: 19 passed.
2. End to end on :8001 (inject, 16×, about 120 s, run). `SCN-0001` completed; snapshot at t=430, 600 s horizon, 20 timeline samples per candidate:

   | candidate | status | delay (s) | max queue | throughput (veh/h) | EMS | wall (s) |
   |---|---|---|---|---|---|---|
   | baseline | completed | 100.2 | 60 | 2658 | 4:29 | 13.1 |
   | flush-downstream | completed | 99.7 | 60 | 2610 | 4:26 | 16.7 |
   | meter-upstream | completed | 100.1 | 60 | 2646 | 4:28 | 14.7 |
   | relieve-cross-street | completed | 101.2 | 59 | 2640 | 4:13 | 15.8 |

   The whole run took 16.7 s of wall time with 4 workers. The ops log reads: "Analyzing INC-0001: 4 candidates, 0 rejected by safety validator" and "Recommendation: Extend EB green at C2 (delay 100s → 100s, EMS 4:29 → 4:26)".
3. Determinism: two `run_branch(baseline)` calls from one snapshot gave identical metrics (delay 101.98, queue 59, throughput 2574, EMS 405 s) and identical timelines.
4. WebSocket: `hello.data.scenario` is `null` before the first run. One run produced 13 `scenario` messages, going queued → proposing → simulating → recommending → completed.
5. Guards:
   - no incident: 409;
   - second run while one is in progress: 409;
   - unknown incident: 404;
   - `horizon_s=5`: 422.
   With a stub agent:
   - the 8 s green at C2 was `rejected` with "C2 phase 0: green 8s < 12s (vehicle/pedestrian minimum)";
   - a reroute avoiding an unknown segment was `rejected`;
   - the corridor plan was `failed` with a `NotImplementedError` note while the run completed;
   - the stub recommending the failed corridor fell back to `baseline` with a rationale line;
   - an agent that raises gave a `failed` run with `error` set and an `alert` ops event.
6. Live isolation: during the run the live `sim_time` went 428 → 696 at 16× (16.7 s wall), so the live simulation didn't stall. Snapshot files are deleted after each run.

**Deviations.**
- A missing `baseline` is inserted before the candidate cap is applied.
- The handoff did not specify what happens when `incident_id` names a cleared incident. It returns 409, as does an incident with no matched segment.

**For response-strategies and the integrator: branches are about 10× slower than this handoff estimated.**
- A 600 s branch of the *post-crash* network takes about 8 s alone and 13–17 s with 4 in parallel. The 0.5–1 s estimate holds for the healthy network (300 s of warm-up takes 0.87 s).
- The same slowdown happens without branching: 600 s in the original process takes 6.8 s.
- Profile: about half the time is `SumoSimulation._apply_rubbernecking`, which calls `vehicle.getLanePosition` per vehicle per step on the incident link (about 20k TraCI round trips per branch).
- `start()` also spends about 1 s in a TraCI connect retry per branch.
- Both are in `sumo.py`, which response-strategies owns. Subscribing lane position (`VAR_LANEPOSITION`) and reading it from `self._veh` would probably fix most of it. Until then, an 8-candidate run takes about 30 s. The UI shows progress, but lowering `SCENARIO_HORIZON_S` is the quick lever for the demo.

**Contract change requests.**
- `backend/requirements.txt` gains `mcp>=2.2`, for the MCP server below.

### Addendum: scenario engine as MCP tools
The team's direction changed: an agent (Nemotron) should drive the analysis through MCP tools. Spec: [docs/specs/scenario-engine-mcp.md](../specs/scenario-engine-mcp.md).

**Built.**
- `backend/app/api/mcp_tools.py`: an `MCPServer` served at `/mcp` (streamable HTTP, stateless, JSON), with 5 tools: `start_analysis`, `validate_plan`, `simulate_plans`, `get_analysis`, `submit_recommendation`. Its server `instructions` carry the workflow and how to read the metrics, ready to use as the agent's system prompt.
- `ScenarioService` is split into reusable steps (`open` → `capture` → `evaluate` → `finish`/`fail`). The mock `POST /api/scenarios/run` pipeline now runs on the same steps.
- An agent can call `simulate_plans` several times from the same snapshot, up to `SCENARIO_MAX_CANDIDATES` in total. The baseline is added automatically.
- Tool runs mutate the same `ScenarioRun`, so the UI receives them over the existing `scenario` WebSocket messages without any contract change. `ScenarioRun.agent` holds the agent's name.
- `SCENARIO_IDLE_TIMEOUT_S` (default 300) fails an abandoned agent run and releases the one-run lock. On shutdown, the open run's snapshot file is deleted.

**Verified** with a scripted MCP client session over real streamable HTTP:
- The client lists the 5 tools.
- With no incident, `start_analysis` returns a tool error.
- `start_analysis` on the crash returned the context: incident on Main St EB, 9 signals, 48 segments, worst first.
- A second `start_analysis` gave a tool error because a run was already open.
- `validate_plan` flagged "C2 phase 3: green 8s < 12s". The same plan inside `simulate_plans` came back `rejected`.
- Round 1: baseline, flush and meter completed in 13.0 s. The live simulation advanced 429 → 636 during it.
- A duplicate plan id and going over the candidate cap both gave tool errors.
- Round 2 (1 plan) took 7.9 s.
- Recommending a rejected candidate gave a tool error. The valid recommendation completed the run and wrote it to the ops log.
- Tools called on the closed run gave tool errors.
- The idle timeout (20 s in the test) failed an abandoned run, and a new analysis could then start.
- WebSocket transitions: queued → proposing → simulating → proposing → simulating → proposing → completed.
- The mock REST path still goes queued → proposing → simulating → recommending → completed.
- `make test`: 19 passed.

