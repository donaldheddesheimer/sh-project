# Handoff: `feature/response-strategies`

Read [MASTER.md](MASTER.md) first. It has the frozen contract, the file ownership, the
ports and the rules. This file covers only your branch.

## Context

This is a traffic operations center: FastAPI plus a live SUMO/TraCI digital twin of a
3×3 downtown grid, with a React/MapLibre console. Milestone 1 is done. A collision on
Main St EB (segment `B2_C2`, between Central Ave `B2` and Pine Ave `C2`) spills back
toward `A2`, and an EMS unit from Fire Station 3 (segment `W2_A2`) typically loses a
couple of minutes in the incident queue. Read `README.md` and `docs/architecture.md`
(simulation model, habitual routing, collision model, determinism).

Today the only response the system can simulate is a **signal timing change**
(`SignalPolicy` → `apply_signal_policy`). You add the two responses that make the demo
compelling, and teach the mock agent to propose and judge them:

1. an **EMS green corridor**, which pre-empts signals ahead of responders;
2. a **diversion advisory**, which reroutes a share of drivers around the blocked link.

A parallel branch (`feature/scenario-engine`) builds the service that runs your
strategies in branches, and another (`feature/analysis-ui`) renders the results. You
don't need either: you verify everything with your own scratch scripts against
`SumoSimulation` directly.

## Goal

### 1. Green-corridor pre-emption: `SumoSimulation.enable_emergency_corridor(corridor)`

Put the pure, simulator-agnostic logic in a new `backend/app/simulation/preemption.py`,
and wire it into `sumo.py`'s per-step `_after_step`.

- **Scope.** Once enabled, the corridor stays active for the rest of the run, for
  **every** EMS vehicle (`vType ems`) that is en route, including ones spawned after
  enabling. The scenario engine dispatches its probe *before* applying the plan, but
  don't rely on that order.
- **Trigger.** A responder is on an approach to a signalized intersection that is on
  its remaining route. The intersection must be listed in `corridor.intersection_ids`,
  or that list must be empty (which means all). The responder is within
  `detection_distance_m` of the stop line. Use `RoadNetwork.intersections[id]`: each has
  `tls_id`, and `approaches[direction]` has `link_indices` / `through_link_indices`
  into the TLS state string.
- **Target.** The target is the green phase of the base program that serves the
  responder's approach (`served_approaches` contains its direction; see
  `RoadNetwork.describe_phase`). Phase order differs by junction: at B2 NB/SB is phase
  0, and at C2 EB/WB is phase 0. Never hard-code indices.
- **Safe transition.** This is non-negotiable, because the whole project's premise is
  that nothing unsafe reaches signals.
  - If the target green is already running: hold it (extend it) until the responder has
    crossed the junction, for at most `max_hold_s`.
  - If a conflicting green is running: let it run until it has served at least
    `min_served_green_s`, then end it early. The program then runs **its own full
    yellow and all-red**. Only from an all-red phase (no `G`/`g`/`y` in the state) do
    you jump to the target green.
  - Never skip or shorten a yellow or an all-red phase.
  - Write a small guard in `preemption.py`, `check_transition(prev_state, next_state)`
    or similar: any link going from green to red must pass through yellow, and a jump
    into a green must start from an all-red. Call it at runtime before every commanded
    change. If it fails, raise, so the candidate fails loudly rather than simulating
    unsafe signals.
- **Release.** When the responder has passed, the program continues normally from the
  target green (let it finish at least its minimum green). Coordination offsets will be
  disturbed. That is the real cost of pre-emption, and the simulation should show it
  (cross-street delay), not hide it.
- **Stats.** Track them for `response_notes()`, e.g.
  `"3 pre-emptions (A2, B2, C2); longest hold 14s"`.

### 2. Diversion advisory: `SumoSimulation.reroute_vehicles(action) -> int`

Optionally put the logic in a new `backend/app/simulation/reroute.py`.

- **Affected vehicles.** Background vehicles (not `ems` / `crash` types) whose remaining
  route contains any of `action.avoid_segment_ids`, and which are not already on one of
  those segments.
- **Selection.** Divert a `compliance` share, chosen **deterministically**: for example
  `zlib.crc32(vehicle_id) % 1000 < compliance * 1000`. Never use Python's `random` or
  the builtin `hash()`, so branches stay reproducible.
- **Mechanism.**
  - Inflate the avoided edges' travel time for that vehicle
    (`vehicle.setAdaptedTraveltime(vid, edge, 1e5)`), then call
    `vehicle.rerouteTraveltime(vid, currentTravelTimes=False)`.
  - Count only vehicles whose route actually changed.
  - Remember that drivers are habitual (`device.rerouting.adaptation-interval=0`), which
    is why a diversion is an explicit response here.
- **Persistence.** The advisory is persistent, like a DMS sign or navigation alert:
  apply it at activation, and also to newly departed vehicles every step, using the
  departed ids already read in `_after_step`. The return value is the number diverted
  at activation. `response_notes()` reports the running total, e.g.
  `"212 vehicles diverted over the horizon"`.

### 3. Mock agent: `backend/app/agent/mock.py`

Keep the existing plans and ids (`baseline`, `flush-downstream`, `meter-upstream`,
`relieve-cross-street`), and add the following. The UI fixture already uses these ids
and names, so keep them stable.

- **`ems-corridor`**, "EMS green corridor": `EmergencyCorridor(intersection_ids=[])`
  with default parameters. Propose it whenever `context.ems_origin_segment` is set or a
  responder is en route.
- **`corridor-plus-meter`**, "EMS corridor + meter at B2": the corridor plus the
  `meter-upstream` policy. Build the name from the actual intersection id.
- **`divert-advisory`**, "Divert via Harbor St / Park St":
  `RerouteAction(avoid_segment_ids=[incident segment], compliance=0.3)`. Name the
  parallel streets that actually exist around the incident segment; don't hard-code
  them.
- **`aggressive-flush`**, "Maximum EB green at C2": deliberately leaves the cross
  street 8 s of green. The safety validator **must reject** it; it shows in the demo
  that the validator really gates agent output. Say so in the plan description. Don't
  weaken the validator to let it pass.

Then rewrite `recommend()` with a clear, documented, deterministic rule that uses EMS
response. Suggested rule; you may improve it, but document whatever you choose:
- Consider only `completed` candidates.
- Drop any candidate that makes EMS response more than 10% worse than baseline.
- Among the rest, take the lowest mean delay. But if another candidate improves EMS
  response by 60 s or more and its delay is within 5% of the best, prefer it.
- The rationale lists the concrete before/after figures (delay, max queue, throughput,
  and EMS response as m:ss), plus one line explaining why the runner-up lost.
- The fixture's recommendation (`docs/milestone-2/fixtures/scenario-run.json`) shows
  the intended tone.

### 4. Safety: `backend/app/safety/validator.py`

`validate_corridor` already exists (the base commit), with checks for known signals, a
min-served-green floor, a max-hold ceiling and detection-distance bounds. Tighten it if
your controller has more parameters that need bounds. Keep `validate()` behavior for
`SignalPolicy` unchanged; the existing tests cover it.

## Files you own

Create:
- `backend/app/simulation/preemption.py`
- `backend/app/simulation/reroute.py` (optional)

Edit:
- `backend/app/simulation/sumo.py`
- `backend/app/simulation/network.py` (route/approach helpers)
- `backend/app/simulation/metrics.py` and `scenario.py` (only if needed)
- `backend/app/agent/mock.py`
- `backend/app/safety/validator.py`
- `simulation/**`: update `simulation/controllers/README.md` to describe where
  pre-emption lives and how it stays safe
- the `## Result` section of this file

**Do not touch:**
- `services/**`, `api/**`, `providers.py`, `main.py`, `config.py`, `runner.py`,
  `branching.py` (all owned by feature/scenario-engine);
- `frontend/**`;
- the frozen contract files (`models/**`, `agent/base.py`, `simulation/interface.py`),
  `README.md`, `docs/architecture.md`, and tests.

## Gotchas from milestone 1

- **Snapshots and processes.** Branch comparisons are only valid when each branch is a
  **fresh** `SumoSimulation` process restored from the same snapshot (`start()`, then
  `restore_snapshot()`). Re-loading into a used process diverges.
- **State that isn't saved.** `loadState` drops all TraCI subscriptions (already
  handled), and per-vehicle overrides such as `setSpeed` are not saved. Anything your
  controllers keep must live in Python state and be re-derived every step, as
  `_apply_rubbernecking` does.
- **Live simulation stays as it is.** The live runner must behave exactly as today.
  Corridor and reroute are only active when enabled.
- **Stopped vehicles.** SUMO excludes stopped vehicles from edge mean speed. The crash
  vehicles are held by `setStop`, and the responder parks with a stop at the scene. Its
  `status` goes `on_scene` when `isStopped`, which is the release condition for its
  pre-emption at the last junction.
- **Programs.** `get_signal_program()` returns the *active* program. A branch may have
  a timing policy installed (program id `policy-N`) before the corridor runs, so read
  phases from the active program, not the base one.
- **Pace.** Keep per-step work light. A 600 s branch currently takes about 0.5–1 s.
  Don't make per-vehicle TraCI calls for every vehicle every step unless necessary; use
  the subscription results in `self._veh`.

## Verify (no new test files)

Write a scratch script (in your scratchpad, not the repo) that runs the following. The
scenario engine will do the same thing; you're proving the strategies work.

1. Warm up a `SumoSimulation` for 300 s. Inject the default collision (`B2_C2`, lane 0,
   55%, major). Run 120 s, then snapshot.
2. For each plan from `MockAgentProvider.propose_candidates(context)`:
   - validate it (the rejected one must be rejected);
   - in a fresh sim: `start()`, `restore_snapshot`, spawn the EMS probe (`W2_A2` →
     `B2_C2` at crash position − 20 m, lane 0), apply the plan, then `run_for(600)`.
3. Print a table: delay, max queue, throughput, EMS response and wall time for each
   plan, plus `response_notes()`. Expected shape (the numbers are yours to find):
   - `ems-corridor` gets EMS on scene clearly faster than baseline (tens of seconds or
     more);
   - `divert-advisory` reduces the queue on the incident approach;
   - `aggressive-flush` is rejected;
   - `recommend()` returns a sensible choice with a readable rationale.
4. **Safety audit.** Record every TLS state at each pre-empted intersection during a
   corridor run, and assert your `check_transition` rule over the whole sequence.
   Report the number of transitions checked.
5. **Determinism.** Run the corridor branch twice from one snapshot; the metrics must be
   identical.
6. Run `make test`; the existing suite passes. Then start the backend (:8002) and check
   that the live demo (inject, spill-back, dispatch) looks the same as before.

## Definition of done

Steps 1–6 hold. The pre-emption code is readable and its safety rule is stated in one
place, with a docstring. Everything is committed on `feature/response-strategies`. The
Result section below includes the numbers table.

## Result

**Status: merged (PR #4) and run.** The code was first reviewed statically. After the PR
review it was run twice: once as a scratch script against the branch (Verify steps 1–5),
and once end to end on `main` through `POST /api/scenarios/run` (step 6). The numbers
are below. The one expected shape that did not hold is `ems-corridor` beating the baseline
on EMS response once the queue has formed; see the second table.

### What was built

| File | Change |
|---|---|
| `backend/app/simulation/preemption.py` (new) | `check_transition` (the one statement of the safety rule), `UnsafeTransition`, the pure `PreemptionController` state machine, stats and `notes()` |
| `backend/app/simulation/reroute.py` (new) | `DiversionAdvisory`: crc32 selection, per-vehicle adapted travel times plus `rerouteTraveltime`, counts only changed routes, persistent for later departures |
| `backend/app/simulation/network.py` | `RoadNetwork.signalized_approaches_ahead` and `RouteApproach` (pure route walk) |
| `backend/app/simulation/sumo.py` | `enable_emergency_corridor`, `reroute_vehicles`, `response_notes`; hooks in `_after_step`; `restore_snapshot` clears the new state; `resolve_sumo_binary` also finds `sumo.exe` on Windows |
| `backend/app/agent/mock.py` | Four new plans and a new `recommend()` |
| `backend/app/safety/validator.py` | `validate_corridor` tightened; `validate()` unchanged |
| `simulation/controllers/README.md` | Where pre-emption lives and how it stays safe |

`metrics.py` and `scenario.py` needed no change. With no corridor or diversion enabled,
`_after_step` adds two `is not None` checks and no TraCI calls, so the live simulation is
unchanged.

### Results table (Verify step 3)

Scratch run of the PR branch. The snapshot was taken at t = 430 s, 10 s after the
default collision (not 120 s as step 1 says). Horizon 600 s, branches run one after
another.

| Plan | Delay (s) | Max queue | Throughput (veh/h) | EMS response | Wall (s) | Notes |
|---|---|---|---|---|---|---|
| baseline | 85.8 | 60 | 2676 | 62 s | 5–7 | |
| flush-downstream | 90.4 | 59 | 2664 | 60 s | 5–7 | |
| meter-upstream | 86.1 | 61 | 2664 | 72 s | 5–7 | |
| relieve-cross-street | 88.6 | 59 | 2652 | 80 s | 5–7 | |
| aggressive-flush | rejected by the validator (`min_green`, C2 phase 3: 8 s < 12 s) | | | | | |
| ems-corridor | 86.2 | 59 | 2628 | **49 s** | 5–7 | "2 pre-emptions (A2, B2); longest hold 0s" |
| corridor-plus-meter | 85.9 | 61 | 2664 | **49 s** | 5–7 | |
| divert-advisory | **72.9** | **28** | **2808** | 65 s | 5–7 | 4 rerouted at activation; "43 vehicles diverted" |

`recommend()` picked `divert-advisory`: it had the lowest delay, and neither corridor
plan's 13 s EMS gain reached the 60 s preference.

**End to end on `main` (step 6).** `POST /api/scenarios/run` against a live backend,
with the snapshot at t = 497 s (about 75 s after the crash, so a queue had formed). Polling
saw the run go from queued through simulating to completed in 25.9 s, with 7 branches
on 4 workers. The baseline branch took 11.6 s.

| Plan | Delay (s) | EMS response |
|---|---|---|
| baseline | 103.1 | 292 s |
| flush-downstream | 102.0 | 300 s |
| meter-upstream | 102.1 | 309 s |
| relieve-cross-street | 102.3 | 294 s |
| aggressive-flush | rejected | |
| ems-corridor | 102.6 | **334 s** (slower) |
| corridor-plus-meter | 105.1 | 288 s |
| divert-advisory | **87.2** | **242 s** |

The recommendation was `divert-advisory`:
- EMS response 4:52 → 4:03 (−17%);
- delay −15%;
- max queue 60 → 54;
- throughput 2304 → 2568 veh/h.

Once the spill-back queue exists, pre-emption can't help the responder: it removes the
signal waits, but the responder still waits in the queue. The tuning notes below give
the levers to try.

**Safety audit (step 4):** 2,778 realised signal-state changes at pre-empted
intersections were checked with `check_transition`, and 0 were unsafe.
**Determinism (step 5):** two runs from one snapshot gave identical output for every plan.
**Step 6:** `make test` passed (19 tests) on `main`. The live demo (inject, spill-back,
dispatch) was unchanged, and the Analyze Response run above completed.

Resolved assumptions from the list further down:
- The diversion activates: it rerouted 4 vehicles at activation and 43 in total.
- Pre-emption fired at A2 and B2, which exercises `setPhaseDuration(tls, 0)` and the
  route-index walk. The runtime check caught no unsafe transitions.
- Whether the `setPhase` jump from an all-red's last step ran was not logged.

### Recommendation rule (`MockAgentProvider.recommend`, also in its docstring)

1. Only `completed` candidates with metrics count. None: keep the baseline.
2. EMS filter: drop a candidate whose realised EMS response is more than 10% slower than
   the baseline's. A baseline responder that never arrived means no filtering; a candidate
   responder that never arrived is dropped. The baseline is always eligible.
3. Take the lowest mean delay. But if an eligible candidate improves EMS response by 60 s
   or more and its delay is within 5% of the best, the biggest EMS improvement wins (ties:
   lower delay, then candidate order). If the baseline responder never arrived, its
   response counts as the whole horizon, so a candidate that gets it there scores a gain.
4. The rationale gives before/after lines (EMS response as m:ss with %, delay, max queue,
   throughput) plus one line on why the runner-up lost. Traced by hand on the fixture
   numbers, it picks `corridor-plus-meter` with the fixture's exact rationale text.

### Deviations from this handoff

- **Last junction not pre-empted.** The junction at the end of a responder's last route
  edge is never crossed (it stops on that edge), so it is never pre-empted. On the demo
  route the notes read "2 pre-emptions (A2, B2)", not the example's "(A2, B2, C2)".
- **`aggressive-flush` and the corridor plans appear only when an EMS origin or responder
  is present** (`ems_origin_segment` set, or a responder en route). The frozen test
  `test_mock_agent_candidates_pass_safety_validation` asserts every proposed policy
  passes the validator, in a context with no EMS. See the contract change requests.
- **Hold clock.** `max_hold_s` bounds the time a green is extended past its natural end,
  counted from the first top-up. A green that needed no top-up reports a 0 s hold.
- **One activation per responder per junction.** After the hold cap, or after a timing
  policy replaces the program mid-service, that responder is not pre-empted again at that
  junction.
- **Reroute.** Vehicles whose destination edge is an avoided segment are skipped (they
  cannot avoid it). `total_diverted` counts distinct vehicles; `reroute_vehicles` returns
  the route changes made at that activation.
- **Second `enable_emergency_corridor` replaces the first** (the latest wins).
- **Validator additions:** codes `duplicate_signal`, `min_green_range`, `min_hold`
  (`TimingLimits.min_corridor_hold_s = 5`), and `no_clearance`. `no_clearance` flags a
  targeted program in which some green is not followed by at least one yellow and then
  an all-red; the rule was tightened in the PR #4 review. The default
  `EmergencyCorridor()` still validates.
- **Mock wording.** The `aggressive-flush` description adds a sentence saying it is
  deliberately unsafe (the UI must not string-match descriptions). When the baseline wins,
  the recommendation summary is "Keep current signal timing; no candidate beat it."

### Contract change requests

Outcome at integration:
- **1** was not done, because the project stopped adding and changing tests.
- **2** is done: `ScenarioService` sets `ems_origin_segment` whenever `ems_probe` is true.
- **3** holds: 8 plans against a cap of 8.
- **4** is done in the `interface.py` docstring.

1. **Test.** Relax `test_mock_agent_candidates_pass_safety_validation` to also cover a
   context with `ems_origin_segment` set. There `aggressive-flush` must be the only plan
   whose policies fail the validator (`min_green` on C2 phase 3). Do not weaken the
   validator to make it pass.
2. **Scenario engine** must set `ems_origin_segment` in `IncidentContext` whenever
   `ems_probe` is true, or the corridor plans and `aggressive-flush` will not be proposed.
3. **Candidate cap.** The default proposal set is exactly 8 plans, equal to the engine's
   default `SCENARIO_MAX_CANDIDATES`; any added plan would push `divert-advisory` off.
4. `interface.py` says `reroute_vehicles` returns vehicles diverted "so far"; it returns
   the count at activation (the running total is in `response_notes`).

### For the integrator, and to verify first when testing resumes

Unverified assumptions (the code was written without running SUMO):
- `rerouteTraveltime(vid, currentTravelTimes=False)` honours per-vehicle adapted travel
  times with `adaptation-interval=0`. If not, activation returns 0 while affected vehicles
  exist. Assert `reroute_vehicles(...) > 0` in the first run.
- `setPhaseDuration(tls, 0)` ends the phase on the next step, and `getRouteIndex` on an
  internal edge names the edge being left.
- The `setPhase` jump from an all-red's last step. It is rare on this grid (it fires only
  when a responder is first detected while its own green is clearing) and it is the one
  command path that has never run.

Tuning left open:
- The default `detection_distance_m` (150 m) gives about 8 s of warning at EMS speed, while
  clearing a conflicting green can take up to about 16.5 s, so a responder can still meet
  a red. Raising the mock corridor to about 300 m (the validator allows up to 400) is the
  obvious lever, but the spec asked for default parameters and the fixture shows 150 m.
- The corridor mainly removes signal waits. The responder also loses time in the
  spill-back queue, so its gain may be smaller than the fixture's synthetic figures, and
  the 60 s preference in `recommend()` may need tuning to the measured numbers.

Environment (Windows): `python -m venv backend/.venv`, then
`backend/.venv/Scripts/python.exe -m pip install -r backend/requirements-dev.txt`. The
Makefile targets use `.venv/bin/`, so on Windows run pytest as
`backend/.venv/Scripts/python.exe -m pytest` from `backend/`.
