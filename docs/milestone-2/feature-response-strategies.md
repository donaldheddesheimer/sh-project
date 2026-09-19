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

_(Fill in when done: the results table from step 3, the safety-audit count, the
determinism check, the recommendation rule as implemented, deviations from this
handoff, contract change requests, and anything the integrator must know.)_
