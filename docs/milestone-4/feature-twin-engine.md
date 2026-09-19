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
- **The mock on Oakland** can propose fewer than 8 plans (`_safe_shift`); do not assume the
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

_To be filled in by whoever implements this branch._

**Built.**

**How it hooks in.**

**Verified by reading.**

**Not run / not verified.**

**Checks for the user to run** (commands, and what to look for).

**Measured** (only numbers the user reported).

**Contract change requests.**

**Deviations.**
