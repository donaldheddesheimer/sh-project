# Handoff: `feature/agent-memory`

Read [MASTER.md](MASTER.md) first. It has the step-0 contract, the file ownership, the
decisions and the rules, and the hard rules in it (no tests, nothing run, README kept
consistent) apply to every line below. This file covers only your branch.

## Context

Milestone 3 built the episode in `backend/app/learning/`: `EpisodeService` (the state
machine), `Implementor` (applies a recommendation to the live twin), `LiveMonitor` and
`scorecard.py` (predicted against realised), the reviewer, and `ExperienceStore` (markdown
memory, similarity recall). Read the README section **The autonomous, self-learning episode**
first; it is the single description of how these fit together, and it says what is built and
what has **not** been run.

The gaps this part closes, each written in the README:

- **An applied plan stays on the live signals until a reset** (Current limitations).
- **The learnable signal is thin, and the high-impact plans have the least verification**
  (Risks): lessons about the corridor and diversion should be checked before they are trusted.
- **Recall is structured only**; embeddings were left for later ("Later", and the store's
  docstring: "an NVIDIA embedding NIM can later replace `similarity` behind the same `recall`").
- **The learning has never been measured** (the unticked **Measure the learning** task).
- **The REST Analyze Response cannot use Nemotron**: `NemotronAgentProvider` is a stub, so
  `AGENT_PROVIDER=nemotron` starts and then fails when a run calls it.
- **The EMS figures are one number** even with two responders.

Twin-engine (a parallel branch) provides `TrafficSimulation.revert_response()` and
`TrafficMetrics.emergency_responses`, both in the step-0 contract. Until it lands, both
degrade as described in [MASTER](MASTER.md#the-step-0-contract).

## Goal

Six tasks. Sizes are S = 1, M = 2, L = 3 points, 13 in total.

### 1. Revert an applied plan when the scene clears (M)

`Implementor` keeps `_standing: list[CandidatePlan]` with no link back to the incidents the plan
was for. Give it one.

- **Standing responses with their incidents.** Replace the bare list with a small record
  (`plan`, `run_id`, `incident_ids`). `standing_plans()` keeps returning plain plans, because
  `ScenarioService.standing_source` and every branch use it.
- **Trigger.** Register an incident listener (`city.incident_listeners`). On
  `INCIDENT_CLEARED`, find the standing responses whose incidents are **all** no longer active
  (decision D3). If there are none, do nothing. Gate it on a new `AUTO_REVERT` setting
  (default `true`).
- **One command on the live thread**, under `city.live_change_lock` (the lock a reset and an
  apply already share): call `sim.revert_response()`, then **re-apply the responses that stay**
  (those covering a still-active incident), in order, with `apply_plan`. Validate each of
  them again against the base programs inside the same command, exactly as `_apply_locked`
  does (skip unsignalized junctions: `info.tls_id`), and drop one that no longer validates,
  with an ops-log warning. Update `_standing`.
- **Do not await the lock inside the incident listener.** It runs on the frame pipeline, and a
  reset holds the lock for the whole reboot. Schedule the revert as a task (`_spawn` in
  `EpisodeService` is the pattern) and let the listener return.
- **Record it.** `Implementation` gains `reverted_at: float | None` and `revert_reason: str |
  None` (`"scene cleared"` or `"operator"`). Publish the run (`publish_scenario`) and add an
  ops event. A reverted run **cannot be applied again**; the existing "already implemented"
  guard covers it. A fresh analysis is needed.
- **Operator path.** `POST /api/scenarios/{id}/revert` → `Implementation`. 404 unknown run;
  409 if it was never applied, was already reverted, or predates a reset. It reverts that
  run's response and re-applies the others, in the same command. There is **no MCP tool**:
  an agent never reverts (MASTER, out of scope).
- **If `revert_response` raises `NotImplementedError`** (twin-engine has not landed), log it,
  add an ops warning ("revert failed: not available") and leave the plan and the standing
  list exactly as they were.
- **The episode is not changed by this.** `EpisodeService._on_incident` already aborts an
  episode whose scene clears mid-window; that stays. The revert is independent of it, and
  happens whether or not an episode is working.
- **UI** (`plans/ResponsePlans.tsx`, and `api/*`, `types.ts`): the applied footer shows
  "Reverted at m:ss (scene cleared)", and an applied, not-yet-reverted run gets a **Revert to
  base timing** button next to the existing apply controls.

### 2. Per-responder EMS in the scorecard (S)

The monitor already times the responders the branches timed (`ems_timed_ids`: the plan's
dispatches plus those already on the way) from the same origin. Both sides still reduce them
to **one** number, the last responder to arrive.

- Keep per-responder times in `LiveRecord` (a list of `EmergencyResponse`, taken from
  `window.arrivals` and each responder's `destination_segment` in the frames). Add the same
  list to `WindowStats`.
- In the scorecard, pair predicted (`candidate.metrics.emergency_responses`) and realised
  **by destination segment**, not by vehicle id (branch and live ids differ). Report each
  scene's predicted, realised and difference next to the aggregate, which stays the headline
  figure and what the materiality threshold uses.
- If the predicted list is empty (twin-engine not landed), fall back to today's aggregate.
- Surface a compact per-responder view in the MCP `_metrics` output **only when there is
  more than one responder**, so single-crash output stays as it is.

### 3. Verify the corridor and diversion before trusting their lessons (M)

The high-impact plans have the least verification, so a lesson about them can be wrong for a
reason the numbers do not show: the corridor never pre-empted anything, or the diversion
diverted no one.

- **Capture the live notes.** In `EpisodeService._review`, before `build_scorecard`, read
  `sim.response_notes()` on the live twin (`run_on_live`) and store them on
  `ep.implementation.notes` (the field exists and is unused). If the call fails, leave the
  notes empty and carry on.
- **Response checks**, computed by code in `scorecard.py`, never by the model. A
  `ResponseCheck {kind, ok: bool | None, detail}` list on the `Scorecard`:
  - *corridor* (when the applied plan has a corridor): `ok` when the notes report at least one
    pre-emption. `False` for `no pre-emptions`, or when a `pre-emption disabled` note is
    present (twin-engine's live-safe failure). `None` if no notes could be read.
  - *diversion* (when the plan has a reroute): `ok` when vehicles were diverted, from the
    `N vehicles diverted` note or `Implementation.diverted`. `False` when zero.
  - These read the **exact strings** `PreemptionController.notes` and `DiversionAdvisory.notes`
    produce today. Twin-engine has been told not to reword them, but if you find a note you need
    is missing, write a Contract change request rather than parse something looser.
- **Provisional.** `Scorecard.provisional` is true when any check is not `ok`. A provisional
  lesson keeps its verdict (the verdict is still the scorecard's outcome, and the model still
  cannot overrule it) but its **confidence is capped** (constant at the top of `scorecard.py`,
  for example 0.4), it is marked in the playbook and the episode file, and:
  - recall multiplies its similarity by a trust factor below 1;
  - **the mock never prunes on it.** `_apply_lessons` (yours) may reorder on a provisional
    lesson but never drops a plan, because a wrong prune removes a plan the simulator should
    have tested. `_by_plan` needs the flag, so `RecalledExperience` carries `provisional`.
- **Confirmed by replication**, at recall time and without changing stored files: a
  provisional lesson counts as confirmed when at least two *other* remembered episodes with
  similarity at or above `CLOSE_MATCH` chose the same plan family, reached the same verdict,
  and passed their checks. State the rule in a constant and a docstring.

### 4. Embedding-based recall (L)

Recall today ranks by hand-set feature weights, so a lesson about a different street can only
score up to about 0.65 and never carries over what it *taught*. Embeddings can add recall
for a situation that is similar in meaning and not in its fields. That is the transfer the
learning report will look for.

- **A client**, `learning/embeddings.py`: `NimEmbedder(base_url, model, api_key)`, an async
  OpenAI-compatible `POST {base}/embeddings` over `httpx2`, like `NimClient`. Some NVIDIA
  retrieval embedding models require an `input_type` (`query` or `passage`); **check the model
  card** for the model the user picks (decision D8). Settings: `EMBEDDING_MODEL` (unset = off),
  and `EMBEDDING_BASE_URL` (default: the value of `NEMOTRON_BASE_URL`). Reuse
  `NVIDIA_API_KEY`.
- **What is embedded.** For a stored episode: its situation text (`describe` of the incidents),
  the plan family, the verdict and the `next_time` lines. For a query: the situation text of
  the current incidents and their standing responses.
- **Where vectors live.** A sidecar next to the episode, `EP-NNNN.vec.json`, holding the model
  id and the vector; recompute lazily when the model id differs. **`ExperienceStore.clear()`
  must delete the sidecars**, or `DELETE /api/memory` (a cold run) would leave vectors behind.
  The sidecar name does not match `_EPISODE_FILE` (`EP-(\d+)\.md$`), which is right.
- **The blend has three constraints**; the exact formula is yours:
  1. **Off, or failing, gives today's result exactly.** No key, no model, a network error or
     a bad response falls back to structured recall, with one ops-log line, not one per call.
  2. **Embeddings can add recall, never trigger pruning.** The semantic contribution is capped
     below `CLOSE_MATCH` (0.75), so only the structured score can make the mock prune plans.
     It may lift a lesson above `MIN_SIMILARITY` (0.3) or into the reorder band (`LOOSE_MATCH`).
     Memory advises; the validator and the simulator stay the gate.
  3. **Deterministic given the same vectors.**
- **`recall` becomes async.** `ExperienceStore.recall` is synchronous and is called from
  `EpisodeService._lessons`, which `ScenarioService.lessons_source` calls inside `_context`, and
  from the MCP tool `recall_experience`. A network call cannot block the event loop. Make
  `lessons_source` an `Awaitable`, `await` it where `_context` reads it (you own that spot),
  and adapt the MCP tool. Cache query embeddings for the life of an analysis.
- **Cost.** One embedding call per analysis and one per saved episode. Say so in the README.

### 5. The learning report (M)

`GET /api/learning/report`: a read-only aggregate that answers "is it learning?" from what is
already stored. No model, no side effects.

- **Source:** `ExperienceStore.load()` (the persisted episodes), not the in-memory episode
  list, which keeps only the last 20 and forgets on restart.
- **Shape (yours to refine):** per `script_id` (`null` shown as "manual"), the episodes in
  order, each with: `id`, `analyst`, whether it was **warm** (`recalled` is non-empty), whether it
  **transferred** (a recalled episode came from a *different* script), `rounds`,
  `candidates_tried`, `analysis_wall_s`, the plan chosen, the verdict, `delay_vs_baseline_pct`,
  the delay prediction error, `staleness_s` and `provisional`. Add first-against-latest deltas
  per script, and mark clearly which comparisons are same-script (memorisation) and which are
  across scripts (transfer).
- **UI:** a collapsed **Learning** section in `EpisodePanel.tsx` with a compact table of those
  rows. Types in `api/types.ts`.
- It reports; it does not conclude. Write no interpretation the numbers do not support.

### 6. The REST `NemotronAgentProvider` (L)

`AGENT_PROVIDER=nemotron` should work for **Analyze Response**, not only inside an episode.

- **`propose_candidates(context)`**: one chat completion through the existing `NimClient`. Give
  it a compact rendering of `IncidentContext` (the incidents, the worst segments, every
  signal's phases and what each serves, the EMS origin, the standing responses and any
  lessons as advice), the `CandidatePlan` JSON schema (`CandidatePlan.model_json_schema()`), the
  candidate budget (`SCENARIO_MAX_CANDIDATES` minus the baseline), and the workflow guidance
  that already exists as the MCP server's `instructions` in `api/mcp_tools.py`. Ask for a JSON
  array. Extract JSON from a fenced or wrapped reply, validate each plan with Pydantic, drop
  invalid ones with a note, and retry once if none survive. Nothing here reaches the live
  signals: plans are data and the validator still runs (CLAUDE.md, "Agents never touch live
  signals").
- **`recommend(context, results)`**: give it the compact result table with the deltas against
  the baseline (reuse the shapes `_candidate` and `_baseline_metrics` build for the MCP
  output), ask for `{candidate_id, summary, rationale[]}`. `ScenarioService._checked` already
  falls back to `baseline` when the id is not a completed candidate; keep that as the backstop.
- **Fallback is explicit, never silent.** New `AGENT_FALLBACK_TO_MOCK` (default `true`, like
  `EPISODE_FALLBACK_TO_MOCK`): when the model fails, the mock answers, `ScenarioRun.agent`
  says so (for example `mock (Nemotron failed: <reason>)`), and an ops warning is added.
  With it off, the run fails as today.
- **Wiring.** `build_agent_provider` requires `NEMOTRON_MODEL` for `AGENT_PROVIDER=nemotron`
  and fails at startup with a clear message when it is unset, as `build_episode_agents` does.
- Update the README's "documented stubs" limitation, the architecture doc's Agent paragraph
  and `agent/nemotron.py`'s docstring, which says the provider "stays a stub".

## Also do

- **Terms, Settings, API, layout and status rows** in the README, per MASTER: the terms
  Revert, Response check, Provisional lesson and Learning report; settings `AUTO_REVERT`,
  `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL`, `AGENT_FALLBACK_TO_MOCK`; the two routes; the new
  files. Settings also go in `config.py` and `.env.example` under a `# --- memory and agents`
  heading.
- **The MCP spec** (`docs/specs/scenario-engine-mcp.md`) if `_metrics` or `recall_experience`
  output changed, and **`docs/architecture.md`** (the autonomous episode and provider sections).
- **The README "Later" list and the task list.** Keep them consistent with what you built. Do
  **not** tick **Measure the learning**: that needs the user's runs.

## Measure the learning protocol

This is for the **user** to run. You do not run it, and you do not write results into the
README that the user has not reported. Put the exact steps and what to record in your Result.

The mock analyst acts on lessons (`_apply_lessons`); Nemotron reads them. Run the protocol
with the mock first (deterministic), then with Nemotron if a key and model id exist.

| Step | Do | Record |
|---|---|---|
| 1 | `DELETE /api/memory`, then `POST /api/demo/start` `{"script": "crash-ahead"}` | EP-1 (**cold**): rounds, candidates, wall time, plan, verdict |
| 2 | Run `crash-ahead` again | EP-2 (**warm**, same script): the same fields, and `recalled` |
| 3 | Run `varied-crash` | EP-3 (**warm, transfer**): the same fields, and which lesson it recalled |
| 4 | `DELETE /api/memory`, then run `varied-crash` | EP-4 (**cold, control**) |

EP-2 against EP-1 is **memorisation** (same script). **Transfer** is EP-3 against EP-4: the same
script, with and without a lesson from a different crash. Only that comparison shows the memory
helping on something new. With embeddings on, repeat step 3 and compare. Report the numbers to
the assistant, and they go into the README's status as measured, with the analyst named.

## Files you own

Create: `backend/app/learning/embeddings.py`.

Edit: `backend/app/learning/**`, `backend/app/models/episode.py`, `models/scenario.py` (only if
needed), `backend/app/agent/nemotron.py`, `agent/mock.py` (**only** `_by_plan` and
`_apply_lessons`), `backend/app/api/mcp_tools.py`, `api/routes.py` (the revert and report
routes), `providers.py` (`build_agent_provider`, `build_episode_agents`), `services/scenarios.py`
(`lessons_source` and where `_context` reads it), `config.py` and `.env.example` (your
section), the frontend files listed for you in MASTER, `frontend/src/dev/replay.ts` if the
fixture needs the new fields, your rows of the README and the docs, and the `## Result`
section below.

**Do not touch:** `simulation/**`, `smart_city/**`, `safety/**`, the rest of `agent/mock.py`,
the frozen contract files, `IncidentPanel.tsx`, the map components, and `backend/tests/`.

## Design notes and gotchas

- **The verdict is the scorecard's outcome.** The reviewer explains it and cannot overrule it.
  Keep that: response checks change confidence and trust, not the verdict.
- **A superseded episode stores no lesson**, and a scene cleared mid-window aborts the
  episode. Neither changes.
- **`ScenarioRun` is mutated only on the event loop.** `Implementation` is set on the run in
  the implementor; publish through `publish_scenario`.
- **One thread owns the live TraCI connection.** Use `run_on_live`.
- **Memory is git-ignored** (`memory/`), and `MEMORY_DIR` can move it. Old episode files have
  no `provisional` or `checks`: every new model field needs a default so `Experience.model_validate_json`
  still reads them, and `store.load` skips (with a warning) anything it cannot read.
- **`similarity` ties.** Recall sorts by similarity, then recency, then confidence; keep that
  order when the semantic term is added, and keep it deterministic.
- **Paused simulation.** `EPISODE_PAUSE_ON_FINISH` pauses the live sim when an episode
  completes. Whether the mock provider notices a cleared scene while paused depends on whether
  the runner publishes frames when paused; read `simulation/runner.py` (`_run`, `_publish`) and
  say in your Result what you found, because the auto-revert depends on it.
- **Nemotron output is not trusted.** Plans it proposes are validated; a recommendation of a
  failed candidate is replaced by `baseline`. Do not add a path around either.

## Verify

**By reading (you do this, and report it):**

1. Trace an auto-revert through every state: one plan for one incident; one plan for two
   incidents with one cleared; two plans, one still active; a reset during the revert; the
   revert while an apply is in flight (the lock); `revert_response` raising.
2. Trace an old memory file (no new fields) through `load`, `recall`, the playbook and the
   learning report.
3. Read every caller of `lessons_source` and `recall` after making them async; none may call
   them without awaiting, and none may block the loop.
4. Check the README, `.env.example` and `config.py` agree on every setting, and that
   `types.ts` mirrors every model field you added.
5. `npm --prefix frontend run lint` and `build`.

**Checks for the user to run** (write the exact commands and what to look for into your
Result; the user runs them):

1. **Revert.** Inject a crash, Analyze Response, Apply, then **Clear scene**: the signal
   program ids return to base, the footer shows the revert, the ops log says so. Repeat with
   `AUTO_REVERT=false` and use **Revert to base timing**. Repeat with the sim paused when the
   scene is cleared.
2. **Two crashes.** Apply a plan covering both, clear one: nothing reverts. Clear the second:
   it does.
3. **Trust.** A run whose corridor never pre-empts produces a `provisional` lesson with
   capped confidence; the next analysis on a similar crash does not prune on it.
4. **Embeddings.** With `EMBEDDING_MODEL` unset, recall is unchanged. With a model and key, a
   different-street crash recalls the earlier lesson. With a wrong key, recall falls back and
   says so once.
5. **The learning report** after the protocol above.
6. **REST Nemotron.** `AGENT_PROVIDER=nemotron` with a key and model: Analyze Response
   completes, `agent` says `nemotron`; with a wrong key it falls back to the mock and says so.

## Definition of done

Tasks 1 to 6 are written and re-read; every setting, term, route and file is in the README;
the Result below is filled in, and says plainly which checks were **not run**; **Measure the
learning** is left unticked. Everything is committed on `feature/agent-memory`.

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
