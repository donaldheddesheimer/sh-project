# Handoff: `feature/twin-engine`

Read [MASTER.md](MASTER.md) first. It has the step-0 contract, the file ownership, the
decisions and the rules, and the hard rules in it (no tests, nothing run, README kept
consistent) apply to every line below. This file covers only your branch.

## Context

The live twin is `SumoSimulation` (`backend/app/simulation/sumo.py`): one SUMO process behind
one TraCI connection. Every candidate of an analysis runs in a **brand-new** process restored
from one snapshot (`simulation/branching.py`), which is the only way runs stay bit-reproducible.
Plans are data (`SignalPolicy`, `EmergencyCorridor`, `RerouteAction`); `apply_plan` installs
the same three things in a branch and on the live twin.

What is known, and where it is written (read these, do not restate them elsewhere):

- **Branches are slow.** README "Current limitations": 10 to 16 s of wall time each on the
  grid with 4 in parallel; the README's Oakland section gives 20 to 45 s there. About half of a post-crash branch is
  `_apply_rubbernecking`, the per-step TraCI lookups. The scenario-engine Result in
  [milestone 2](../milestone-2/feature-scenario-engine.md#result) profiled it and also found
  about 1 s per branch in a TraCI connect retry inside `start()`.
- **The EMS corridor rarely helps once a queue has formed.** README "Current limitations":
  334 s against 292 s for the baseline, and `divert-advisory` wins. That is a **measured
  result, not a defect** (CLAUDE.md). The README names two levers: a longer detection
  distance, and a combined corridor and diversion plan.
- **Snapshots go stale** while an agent works (README "Risks"), and whether to slow the live
  simulation during analysis is an open question.
- **Live-apply paths have never run on the live sim.** A pre-emption command that fails the
  runtime transition check raises on the live runner, and that puts the live simulation in
  `error` until **Reset** (README "Risks").

## Goal

Seven tasks. Sizes are S = 1, M = 2, L = 3 points, 13 in total.

### 1. Cut the per-step TraCI round trips (L)

In `sumo.py`, `_apply_rubbernecking` runs every step whenever a crash leaves lanes open. For
each open lane it calls `lane.getLastStepVehicleIDs`, and for each vehicle in it
`vehicle.getLanePosition`. That is roughly 20,000 TraCI round trips per branch.

- Move the data onto the vehicle subscription that `_subscribe_all` and `_after_step` already
  maintain: add the lane position and the lane or road id to `VEHICLE_VARS` (look up the
  constant names in `traci.constants`, for example `VAR_LANEPOSITION` and `VAR_LANE_ID`),
  and read them from `self._veh`. Then no TraCI call is needed to find the vehicles on the
  crash link and their positions.
- Look for the same pattern elsewhere and fix it in the same pass: `_responder_approaches`
  (`getLanePosition` per responder) and `_estimate_eta`. These run for a handful of vehicles,
  so they matter less; do them only if the change is the same shape.
- **Profile by reading, not by running:** count the TraCI calls per step in each function and
  reason about which dominate. Say in your Result what you counted.

**Equivalence is the constraint.** The change must not alter which vehicles are slowed or
when, so a branch from one snapshot gives the **same metrics as before**, to the digit. That is
also what keeps branches comparable with the recorded numbers in the README. Points to think
through:

- A subscription result is the state after the last step, and `getLanePosition` read after
  the step returns the same value. A vehicle that departed this step is subscribed in
  `_after_step` and `self._veh` is refreshed; check that a just-departed vehicle is covered
  in the same step it was before.
- `_apply_rubbernecking` also runs once in `restore_snapshot` (per-vehicle speed overrides are
  not part of SUMO's saved state), after `_subscribe_all`. Check the data exists at that
  moment.
- Adding variables makes the subscription payload larger for every vehicle. The net effect is
  expected to be a gain because the round trips disappear; say in the Result why you believe
  so, and what you could not measure.

### 2. The connect retry in `start()` (S)

`SumoSimulation.start()` calls `traci.start(...)` under a module lock. Each branch pays about
1 s there. The likely cause is that the first connection attempt happens before SUMO is
listening and the connect loop then waits a fixed interval before retrying. **This is not
verified against the installed `eclipse-sumo`/`traci`**: open the installed `traci/main.py`
and read `start` and `connect` before deciding.

If that is the cause, the options are a shorter retry interval, or starting SUMO ourselves on
the chosen port and connecting with `traci.connect(...)`. Keep the module lock (it exists
because `traci.start` mutates module-level connection state) and keep the label handling. If
the wait turns out to be SUMO's own start-up, say so and leave the code alone.

### 3. The corridor: explain it, then try the levers (L)

The goal is an **explained** result, not a corridor that wins. Do not loosen the validator,
and do not touch `aggressive-flush` (deliberately unsafe so the demo always shows a rejection).

**3a. Diagnostics.** Make `response_notes()` say *why* a responder was slow, so a candidate's
notes (and, through them, the lessons) are honest. Per responder, from data already
subscribed (speed, position, and the road id from task 1): seconds spent below about 1 m/s
before reaching the scene, and on which segment; and, with a corridor active, how many
pre-emptions were served on its route while it waited. Example note:

> EMS-1 waited 74 s below 1 m/s on B2_C2 (31 vehicles ahead); 3 pre-emptions (A2, B2, C2), longest hold 14 s

Diagnostics only read state. They must not change what the simulation does. **Add notes; do
not reword the existing ones.** Agent-memory's response checks read them: `no pre-emptions`,
`N pre-emption(s) (…); longest hold Ns` (from `PreemptionController.notes`) and `N vehicle(s)
diverted over the horizon` (from `DiversionAdvisory.notes`). Treat those strings as a contract.

**3b. Levers**, in this order. For each one, work out from the code what it should do, then
list it in *Checks for the user to run* so the user measures it:

1. **Detection distance.** The corridor defaults to 150 m (`EmergencyCorridor.detection_distance_m`);
   the validator allows 30 to 400 m. Have the mock propose `ems-corridor` with a longer
   distance, or both distances as two plans if the budget allows.
2. **A combined plan** `corridor-plus-divert` (pre-emption plus the diversion advisory that
   shortens the queue). The mock already has `corridor-plus-meter`; follow it.
3. **Last, and only with the user's OK (decision D4): the blue-light device.** The `ems`
   vType in `simulation/scenarios/*/vtypes.add.xml` has `vClass="emergency"` and no
   `bluelight` device. SUMO's blue-light device makes surrounding vehicles yield, which
   attacks the real cause (the responder waits in the blocked lane). It also changes the
   baseline physics, every recorded number and the demo fixture, so **do not enable it
   without confirmation**. If the user says yes, it goes in the vType files of **both** cities.

**3c. The plan count.** The mock proposes exactly 8 plans, which equals the default
`SCENARIO_MAX_CANDIDATES`; a ninth silently pushes `divert-advisory` off the end (CLAUDE.md,
"Keep in sync"). Adding `corridor-plus-divert` means raising the default to 9 in
`config.py` **and** `.env.example`, and changing every mention of the old count. Grep for
`8 plans`, `up to 8`, `exactly 8` and `fewer than 8` across the README, CLAUDE.md and the
docs. If you also add the second-distance plan, count again: the budget is the setting, and
the mock must not exceed it. `_combined_plans` in `services/scenarios.py` builds combined
multi-incident plans; check the new plan behaves there (a corridor for several incidents at
once).

### 4. Per-responder EMS response, twin side (S)

Implement the contract's `TrafficMetrics.emergency_responses`. `SumoSimulation._realised_emergency_eta`
already picks the responders that matter to a window (those that had not arrived before it
began) and computes each one's time from `max(window_start, dispatched_at)`. Return that list,
one `EmergencyResponse` per responder, alongside the unchanged aggregate `emergency_vehicle_eta`.

- The list must use the **same origin and the same set** as the aggregate, so that the
  aggregate equals the maximum of the list when every responder arrived.
- Thread it through `MetricsCollector.end_window` / `live` in `metrics.py`.
- The live frames already carry per-responder times (`EmergencyVehicleState`); there is no live
  change here. Agent-memory consumes this.

### 5. `revert_response()` (M)

Implement the contract method in `SumoSimulation` (see MASTER, step 0, for the semantics).

- **Signals.** `apply_signal_policy` installs a new program per changed intersection
  (`policy-N`, recorded in `_custom_programs`). Remember each intersection's **base** program id
  the first time it is changed, and on revert switch back with `setProgram`, keeping the
  running phase index and its remaining time (mirror how `apply_signal_policy` keeps the
  phase's progress). Base and custom programs share phase states and differ only in
  durations, so no state changes and no clearance is skipped. Route the change through
  `check_transition` anyway, as the pre-emption commands do.
- **Corridor.** Setting `self._preemption = None` abandons a service in progress. Check the
  case where a green is being held: the signal must carry on with its own clock and never be
  cut by a command that skipped a yellow.
- **Diversion.** `DiversionAdvisory` (`reroute.py`) sets a huge per-vehicle travel time on the
  avoided edges. Reverting must stop it for future departures and clear those overrides. The
  TraCI call to look up is `vehicle.setAdaptedTraveltime`, which I recall removes a
  previously-set value when called without a time; **verify that in the installed traci** before
  relying on it. Vehicles already rerouted keep their route (decision D2).
- **Snapshots.** After a revert the base programs are active; check `save_snapshot` and
  `restore_snapshot` (`custom_programs`) still agree, and that a snapshot taken after a revert
  restores in a fresh process without `Unknown program`.
- Nothing in this branch calls it: agent-memory does (the implementor). You cannot exercise
  it until then, so read the code paths twice.

### 6. A live-safe pre-emption failure (M)

Today `_preempt_signals` lets `UnsafeTransition` propagate "on purpose", so a branch fails
loudly. On the **live** twin the same exception is caught by the runner, which stops stepping
and sets the status to `error` until Reset. That is a heavy penalty for a command that was
correctly refused.

Make the live twin degrade instead: skip the refused command, disable the corridor (as
`revert_response` does), record a note ("pre-emption disabled: <reason>") that `response_notes()`
reports, and log at `ERROR`. **Branches keep raising**, so a bad plan still fails its candidate.

- Add an explicit constructor flag (for example `fail_safe_preemption: bool = False`) rather
  than guessing from the label. The one line that sets it is in
  `providers.build_services.live_simulation`; you own that line only.
- The refused command is by definition an unsafe one, so it is never emitted. Say in your
  Result why abandoning the service is also safe (the held green runs to its natural end, the
  same as a timing policy installed mid-service).
- Agent-memory captures `response_notes()` at the end of the monitor window, so the failure
  reaches the episode and the lesson without a new channel.

### 7. Slow the live simulation while an analysis runs (S)

Staleness is the live time that passes between the snapshot and the moment a plan goes live.
Task 1 shrinks it; this shortens it further when asked.

- New setting `ANALYSIS_LIVE_SPEED` (`float | None`, default `None` = no change; decision D7).
  When set, an analysis that opens (`ScenarioService.open`) asks `CityService` to run the live
  simulation at that multiplier, and it restores the operator's previous multiplier when the
  analysis ends (`finish`, `fail`, `abandon`, `_close`).
- **The operator wins.** If the operator changed the speed during the analysis, restore
  nothing. A reset clears the remembered speed.
- `finish` and `fail` are synchronous methods on the event loop; scheduling the restore needs a
  task (`_spawn` exists). Do not block the loop.
- Add the two small helpers to `CityService` next to `set_speed`; do not touch the rest.

## Also do

- `simulation/controllers/README.md`: add what `revert_response` and the fail-safe do to the
  safety story ("how pre-emption stays safe"). This is the file that explains it.
- Settings in `config.py` and `.env.example` under a `# --- twin engine` heading:
  `ANALYSIS_LIVE_SPEED`, and the new `SCENARIO_MAX_CANDIDATES` default.
- Update the README (its rules are in MASTER): your status-table row; the Current limitations
  bullets you change (branches are slow, the corridor); the open question about slowing the
  live sim; the plan count everywhere; Settings rows. If the corridor's result changes, do
  **not** write a new number until the user reports it.

## Files you own

Edit: `backend/app/simulation/sumo.py`, `metrics.py`, `preemption.py`, `reroute.py`,
`branching.py` (only if needed), `runner.py` (only if needed), `backend/app/safety/**` (only
if a new corridor field needs a bound), `backend/app/agent/mock.py` (all except `_by_plan` and
`_apply_lessons`), `simulation/controllers/README.md`, `simulation/scenarios/*/vtypes.add.xml`
(only with decision D4 approved), the speed hook in `services/scenarios.py` and
`services/city.py`, one line in `providers.py`, your sections of `config.py`, `.env.example`
and the README, and the `## Result` section below.

**Do not touch:** `learning/**`, `smart_city/**`, `simulation/network.py`, `api/**`, the
frontend, the frozen contract files, and anything under `backend/tests/`.

## Design notes and gotchas

- **One thread owns the live TraCI connection.** Touch it only through
  `CityService.run_on_live`. `revert_response` runs there.
- **Determinism.** Everything a branch does must stay reproducible: sorted iteration, no
  wall-clock, no `hash()` (`reroute.py` uses `crc32` for that reason).
- **An approach is its incoming segment**, never a compass label (CLAUDE.md). This applies to
  the pre-emption and to your diagnostics.
- **Oakland** has unsignalized junctions (`tls_id` is `None`) and a signal id equal to the
  junction id. Anything you loop over intersections for must skip the unsignalized ones, as
  `ScenarioService._capture` does.
- **The mock on Oakland** can propose fewer than 9 plans (`_safe_shift`); do not assume the
  plan set is full.
- **Rubbernecking is a modelling assumption**, not a defect: traffic passing a crash on the
  open lanes is capped at `pass_speed`. Speed it up; do not change what it does.

## Verify

**By reading (you do this, and report it):**

1. For task 1, list each TraCI call the old code made per step and each the new code makes.
2. For tasks 5 and 6, trace every path that can leave a signal in a state that skips a
   clearance, and show none exists. Read `check_transition` and its callers.
3. Re-read `restore_snapshot` for tasks 1 and 5: anything reset or re-created there must
   agree with the new state (`_rubbernecking`, `_custom_programs`, the diversion).
4. Grep for the plan count and every setting you added; confirm `config.py`, `.env.example`
   and the README agree.
5. `npm --prefix frontend run lint` and `build` if you touched anything the frontend
   imports (you should not have).

**Checks for the user to run** (write the exact command and what to look for into your
Result; the user runs them):

1. **Equivalence.** On one snapshot, the baseline candidate's metrics before and after task 1
   are identical. (Run an analysis on the old branch and on yours from the same scenario
   with the same seed; compare `baseline` delay, max queue, throughput and EMS response.)
2. **Speed.** Wall time per branch and per run on the grid and on Oakland, before and after
   tasks 1 and 2. The README's numbers are the "before".
3. **Corridor.** For the README's measurement (analysis about 75 s after the crash): EMS
   response for `ems-corridor`, the new distance, `corridor-plus-divert`, and the baseline;
   plus the new wait note. State whether the loss is explained.
4. **Live safety** (task 6): only checkable by making the runtime check fail on purpose;
   describe how, and let the user decide whether to try it.
5. **Revert** (task 5) end to end once agent-memory's `POST /api/scenarios/{id}/revert`
   exists: apply a timing plan, revert, and confirm the program ids return to base.
6. **Speed setting** (task 7): with `ANALYSIS_LIVE_SPEED=1`, start an analysis and watch the
   speed drop and return; change it by hand mid-analysis and confirm the operator's value stays.

## Definition of done

Tasks 1 to 7 are written and re-read; every mention of a changed behaviour is updated in the
README, CLAUDE.md and the docs; the Result below is filled in, and says plainly which of the
checks above were **not run**. Everything is committed on `feature/twin-engine`.

## Result

**Built.** All seven tasks, on `feature/twin-engine`, in a series of small commits.

- **Step 0 first.** The MASTER's step-0 contract was listed as frozen but had never landed:
  `EmergencyResponse`, `TrafficMetrics.emergency_responses`, `TrafficSimulation.revert_response`
  and the `types.ts` mirror did not exist. Commit 1 lands the contract on its own, unchanged
  from MASTER, so the rest of the branch builds on it. (`eb72b5c` on this branch claims in its
  message to have implemented the twin-engine features; its diff is docs only.)
- **Task 1 (per-step round trips).** `VEHICLE_VARS` gains `VAR_LANE_ID` and `VAR_LANEPOSITION`.
  `_apply_rubbernecking` now builds an "open lane -> disruption" map and scans the subscription;
  `_responder_approaches` and `_estimate_eta` read the same two variables instead of calling
  `getRoadID` and `getLanePosition`.
- **Task 2 (the 1 s connect wait).** `start()` Popens SUMO itself and calls the new `_connect`,
  which retries `traci.connect(..., numRetries=0)` plus `getVersion()` every 20 ms for up to 60 s,
  under the existing module lock and with the same label. It gives up early if the process exits.
  `traci.start` hardcodes `waitBetweenRetries=1` in `init()`, and its first attempt always
  precedes SUMO listening, so every process paid ~1 s.
- **Task 3a (why a responder was slow).** Per responder, per step, while en route and below
  `RESPONDER_STALL_SPEED_MS` (1.0 m/s): seconds stalled, seconds per segment, and the most
  vehicles ahead of it on its lane. `PreemptionController.responder_record` adds which signals
  were pre-empted for that unit and its longest hold. `response_notes()` appends one line per
  responder that was pre-empted for, or held up for at least `RESPONDER_NOTE_MIN_STALL_S` (3 s;
  see the review pass below).
- **Task 3b (levers).** The mock asks for `EMS_DETECTION_M = 350.0` instead of the model's
  150 m default, and proposes a ninth plan, `corridor-plus-divert`. The blue-light device was
  **not** built (MASTER D4 is "no unless the user says so", and the user was not asked).
- **Task 3c (plan count).** `SCENARIO_MAX_CANDIDATES` 8 -> 9 in `config.py` and `.env.example`;
  every live "8" fixed in the README, CLAUDE.md, `docs/architecture.md` and the controllers README.
- **Task 4 (per-responder EMS).** `_emergency_responses(window_start)` builds the list and the
  module-level `_aggregate_response` reduces it to `emergency_vehicle_eta`, so the two cannot
  disagree. `_realised_emergency_eta` is gone; nothing else called it.
- **Task 5 (`revert_response`).** Implemented in `SumoSimulation`, idempotent, returning notes.
- **Task 6 (live-safe pre-emption).** `SumoSimulation(fail_safe_preemption=...)`, set only in
  `providers.py`'s `live_simulation()`. On the live twin an `UnsafeTransition` drops the corridor,
  logs at `ERROR` and adds a `response_notes()` line starting `pre-emption disabled:`; branches
  still raise.
- **Task 7 (`ANALYSIS_LIVE_SPEED`).** Default `None`, bounded to (0, 64]. `CityService.hold_speed` /
  `release_speed` plus `LiveSimulationRunner.restore_speed`; the hold is owned by its analysis (see
  the review pass below).

**How it hooks in.**

- `_apply_rubbernecking`, `_update_dispatches` (which now also calls `_record_stall`) and
  `_preempt_signals` all run from `_after_step`, in that order, unchanged.
- `revert_response()` has **no caller**. Agent-memory's revert endpoint is the caller; until it
  exists the method is dead code that has never executed.
- `fail_safe_preemption` is off by default, so every branch behaves exactly as before. Only
  `live_simulation()` passes `True`.
- `ANALYSIS_LIVE_SPEED` is read in `ScenarioService.open` (hold, at the end, after the run is
  published) and released from `_close`, which `finish`, `fail` and `abandon` all already go
  through. The release is `self._spawn(...)`, never awaited.
- `corridor-plus-divert` is appended inside the `ems_present` block, before `corridor-plus-meter`;
  `divert-advisory` stays last, so it is still the first plan a tenth would push off the end.
  `_apply_lessons` and `_by_plan` are untouched and treat the new id like any other (it falls
  into `middle` until a lesson names it).

**Verified by reading.**

1. **TraCI calls per step (task 1).** Rubbernecking, old: one `lane.getLastStepVehicleIDs`
   per open lane per step, plus one `vehicle.getLanePosition` per vehicle on those lanes per
   step. New: zero — the lane id and lane position come from the subscription. The `setSpeed`
   calls on entering and leaving the zone are unchanged in number and in argument; only the
   order of independent `setSpeed` calls within a step changes, which cannot affect SUMO.
   Responder walk, old: `isStopped`, `getRoute`, `getRouteIndex`, `getRoadID`, `getLanePosition`
   = 5 per en-route responder per step. New: `isStopped`, `getRoute`, `getRouteIndex` = 3.
   `_estimate_eta`, old: `getRoute`, `getRouteIndex`, `getRoadID`, `getLanePosition` = 4 per
   responder per `get_network_state`. New: 2.
2. **Equivalence (task 1).** `lane.getLastStepVehicleIDs` returns the vehicles whose front is
   on the lane — the same predicate as matching `VAR_LANE_ID`. Both the subscription and a
   getter called between steps return the state after the last step. Just-departed vehicles
   are subscribed and `self._veh` is refreshed in `_after_step` *before* `_apply_rubbernecking`.
   `restore_snapshot` calls `_subscribe_all` and `_read_state` before `_apply_rubbernecking`,
   so the new variables are present there too. Crash-type vehicles are still not excluded (only
   `EMS_TYPE`), exactly as before. Where two crashes on one segment leave the same lane open,
   `open_lanes` is built in `self._disruptions` order, so the later one still wins. That is not
   quite the old behaviour in that rare case: the old code tested a vehicle against each crash's
   window in turn, the new code only against the later crash's window. It was **not** changed;
   see the review pass below.
3. **Clearance (tasks 5 and 6).** Every path was traced against `check_transition`.
   `_revert_signals` issues `setProgram`, `setPhase`, `setPhaseDuration` with no step in
   between, so only the final state is ever shown; that state is `base.phases[phase_index].state`
   and it is put through `check_transition` against the live state **before** any command is
   sent. A policy program is built from the base program's phases with only durations changed,
   so the states are identical and the check is an assertion rather than a guess — it raises
   if that ever stops holding. The copied remaining time means the phase still ends when it
   would have, into the base program's own successor. Dropping the corridor issues no command
   (an extended green simply runs out into the program's yellow); `deactivate` touches no
   signal; un-fired offsets are dropped rather than applied. For task 6,
   `PreemptionController.step` appends to a local list and returns it only at the end, so a
   raise means `_preempt_signals` never sends anything — and not sending a command is always
   safe, because the program then runs its own course.
4. **`restore_snapshot`.** Re-read for tasks 1 and 5. `_custom_programs` is deliberately
   re-created and **not** cleared by a revert: SUMO's saved state names every program variant
   and `loadState` fails with `Unknown program` for an id it does not know. `_base_program_ids`,
   `_preemption_notes`, `_preemption_failures` and `_responder_waits` are all reset there, so a
   branch measures the delay it causes rather than the live city's history.
5. **Settings and plan count.** Grepped: `config.py`, `.env.example`, README, CLAUDE.md,
   `docs/architecture.md` and the controllers README all agree on 9 and on `ANALYSIS_LIVE_SPEED`.
   No live "8 plans" text remains; `docs/milestone-2/` is left alone as a historical record.
6. **Note contracts.** `no pre-emptions`, `N pre-emption(s) (...); longest hold Ns` and
   `N vehicle(s) diverted over the horizon` are byte-identical. The new lines are appended.

**Not run / not verified.**

- **No tests were written or run, and nothing was executed.** The repository's hard rule
  forbids it. Everything above is from reading the code.
- `npm --prefix frontend run lint` / `build` were **not run**: `frontend/node_modules` is not
  installed in this worktree and installing it is a side effect outside this task. The frontend
  change is two additive interfaces and two added fields in `types.ts`; no import changed.
- Not one number in this branch has been measured. The README's EMS figures (334 / 292 / 242 s)
  and wall-clock figures (10–16 s per branch, ~25 s per run) are the pre-branch values and are
  now labelled as such in the README, CLAUDE.md and `docs/architecture.md`.
- The degraded pre-emption path (task 6) has never executed; nothing in the scenario provokes an
  `UnsafeTransition`. Minor consequence of the design, stated for the record: the corridor's
  aggregate note is captured from the controller *after* the raise, so it can count an
  activation whose command was never sent. The failure has its own line, starting
  `pre-emption disabled:`, so it is stated rather than left to be inferred from that count.
- `revert_response()` has never executed.
- The 350 m detection distance and `corridor-plus-divert` are **unmeasured guesses at a fix**.
  They may not improve the EMS number at all.

**Checks for the user to run** (exact commands, and what to look for).

1. **Equivalence (task 1).** From the same scenario and seed, on `main` and on this branch:
   ```
   curl.exe -s -X POST http://127.0.0.1:8000/api/incidents/inject -H "content-type: application/json" -d "{}"
   # wait ~30 s wall clock (about 2 simulated minutes at 4x), then
   curl.exe -s -X POST http://127.0.0.1:8000/api/scenarios/run -H "content-type: application/json" -d "{}"
   ```
   Compare the **baseline** candidate's `mean_vehicle_delay`, `max_queue_length`, `throughput`
   and `emergency_vehicle_eta`. They must match to the digit. If they do not, task 1 changed
   behaviour and the rest of the numbers cannot be trusted. (One known exception: with two
   crashes on one segment, rubbernecking is applied against the later crash's window only; see
   the review pass.)
2. **Speed (tasks 1 and 2).** From the same runs, each candidate's `wall_time_s` and the run's
   total, on the grid and with `SCENARIO_DIR=simulation/scenarios/pittsburgh_oakland`. The
   README's 10–16 s per branch and ~25 s per run are the "before".
3. **Corridor (task 3).** With the analysis started about 75 s after the crash, compare
   `emergency_vehicle_eta` for `baseline`, `ems-corridor` (now 350 m), `corridor-plus-divert`
   and `divert-advisory`, and read each candidate's `notes` for the new
   `EMS-N: stopped for Xs on <segment>, up to N vehicles ahead; ...` line. The question to
   answer is whether the corridor's loss is now explained — and whether either lever closes it.
4. **Live safety (task 6).** Only checkable by making the runtime check fail on purpose. The
   cheapest way is to raise unconditionally at the top of `check_transition` in
   `backend/app/simulation/preemption.py`, run an episode with a corridor, and confirm the live
   city keeps stepping, an `ERROR` is logged, and the live twin's response notes carry a
   `pre-emption disabled:` line — where today it would sit in `error` until Reset. **The user should decide whether this is
   worth doing**; it requires a deliberate local edit that must be reverted.
5. **Revert (task 5).** Not checkable in this branch: it has no caller. Once agent-memory's
   `POST /api/scenarios/{id}/revert` exists, apply a timing plan, revert, and confirm
   `GET /api/intersections/{id}` reports the base program id again and that no signal skipped
   a clearance.
6. **Speed setting (task 7).** Start the backend with `ANALYSIS_LIVE_SPEED=1`, open an analysis,
   and watch the console's speed drop to 1x and return to 4x when the run completes. Then change
   the speed by hand mid-analysis and confirm **your** value survives the run completing.

**Measured.** Nothing. No number in this branch has been measured by anyone.

**Contract change requests.** None. The step-0 contract was landed exactly as MASTER states it.

**Deviations.**

1. **Per-responder pre-emption wording.** The handoff's example note reads
   `3 pre-emptions (A2, B2, C2)`. That collides with the corridor's aggregate note, which
   agent-memory treats as a contract. The per-responder lines therefore read
   `N signal(s) pre-empted on its route (...), longest hold Ns` or
   `no signals pre-empted on its route`, leaving the aggregate strings untouched.
2. **The blue-light device was not built.** MASTER D4 is "no unless the user says so" and the
   user was not asked. It is recorded in the README and CLAUDE.md as a deliberate gap.
3. **`revert_response` also drops un-fired offset shifts**, which the contract does not mention.
   An offset waiting for a green has changed nothing yet, so dropping it is the whole revert;
   one already applied lives in the signal's clock and cannot be undone without cutting a phase.
4. **Step 0 landed here**, not before the branch, because it had never been done. It is a
   separate first commit so it can be reviewed as the contract rather than as branch work.

**Review pass.** Changed after the branch was reviewed:

- **Stall diagnostics.** The step in which a responder stops at its scene no longer counts as
  stalled, and a responder gets an `EMS-N: stopped for ...` line only at 3 s or more
  (`RESPONDER_NOTE_MIN_STALL_S`). Braking for the scene or a red light no longer produces one.
- **Failure note prefix.** The live-twin failure note now starts with `pre-emption disabled:`,
  the exact prefix agent-memory's response check looks for.
- **Dropped corridor.** The controller of a dropped corridor is kept, never stepped again, so
  the notes can still report what it did in total and per responder after a refusal or a revert.
- **Hold credit.** A pre-empted responder is credited the hold for every signal service run for
  it, including after it crossed and for the last stretch after the final top-up.
- **Starting SUMO.** Free ports are claimed in a module-level set, so two branches started
  together cannot be given the same port. A failed start kills and reaps the SUMO process and
  unregisters its traci label.
- **`ANALYSIS_LIVE_SPEED`.** Bounded to (0, 64], like the console's speed control.
- **Speed hold.** The hold is owned by its analysis (its run id) and hold and release are
  serialized, so a late release from an earlier analysis cannot release a later one's hold. An
  operator speed change, or a demo start, ends the hold, even to the same value; nothing is
  restored afterwards.
- **Comments and fixture.** The recorded-run fixture and the `emergency_vehicle_eta` comments
  were corrected: in a measured window it is the last responder to arrive, on live metrics the
  soonest estimated ETA among en-route responders.

**Known deviation, not changed.** With two crashes on one segment, the old rubbernecking code
tested a vehicle against each crash's window in turn, and the new code tests it only against the
later crash's window. This is a small difference in a rare case, and it means the "to the digit"
equivalence claim in check 1 above does not strictly hold there.

**Still not run.** As before, none of this review pass was run or measured, and no tests were
written or run.
