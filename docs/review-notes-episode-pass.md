# Review notes: the milestone-3 episode pass

Historical record of the build pass that added the autonomous episode. The current state of
the episode, and what has and has not run since, live in the README's
[autonomous episode section](../README.md#the-autonomous-self-learning-episode); this file is
the per-file change record and the reviewer's reading list for that one pass.

Scope: README tasks 1–10 of the autonomous episode, on top of the multi-crash groundwork from
the previous pass, plus five fixes found while reading the code. The single-crash
**Analyze Response** walkthrough should behave as before, apart from those fixes.

## What changed

| Area | Files | Change |
|---|---|---|
| Demo qualification | `agent/claude.py`, `agent/chat.py`, `learning/{analysts,reviewer,episode}.py`, `providers.py`, `api/routes.py`, `components/EpisodePanel.tsx` | Claude Messages API support, runtime Mock / Claude / Nemotron selection between episodes, exact model visibility and a credit-free Mock startup. |
| | `simulation/scenarios/*/demos/operator-collision.json`, `simulation/scenario.py` | An operator-controlled script that arms autonomous response and waits for the presenter to click **Inject collision**. |
| | `Dockerfile`, `.dockerignore`, `.env.demo.example`, `main.py`, `scripts/deploy-cloudrun.sh` | One Cloud Run image serves the console, API, MCP and WebSocket; the safe demo template keeps secrets local and starts on Mock. |
| Fixes | `simulation/sumo.py`, `models/domain.py` | Programs installed at runtime are recorded, carried in the snapshot (`custom_programs`) and re-created before `loadState`. Without this, every branch after a live timing change fails with `Unknown program` (SUMO's `MSStateHandler`). |
| | `api/mcp_tools.py` | `start_analysis` read `a.probe`, which does not exist (`Analysis.probes`). Every call raised after the snapshot and left the run locked until the idle timeout. |
| | `services/scenarios.py` | `finish` and `evaluate` refuse an analysis that is already closed, so a pipeline still running after `abandon` can no longer complete the failed run. `run_pipeline` is public (the mock analyst awaits it); new `ScenarioRun.rounds` and `.recalled`. |
| | `smart_city/mock.py` | A frame clears vanished incidents before detecting new ones. Otherwise, after a reset, a crash detected in the first frame would see the previous run's incidents as still active. |
| | `frontend/src/App.tsx` | Response plans also show an analysis whose `incident_ids` include the latest incident. Before, a two-crash analysis was hidden. |
| Episode | `learning/episode.py`, `analysts.py`, `implementor.py`, `monitor.py`, `scorecard.py`, `reviewer.py`, `store.py` (all new) | Everything in [The autonomous, self-learning episode](../README.md#the-autonomous-self-learning-episode). |
| | `simulation/runner.py` | `set_boot_events` became `set_scripted_events`: the runner fires scripted crashes inside the warm-up **and** while running. They fire on the runner's own thread, so a reset cannot race them. |
| | `services/city.py` | `incident_listeners`, `reset_listeners`, `add_frame_observer`, `set_scripted_events`, `crash_command` (fills defaults the same way as Inject), `publish_episode`, and `episode` in `hello`. |
| | `simulation/branching.py` | `apply_plan` also returns the vehicles diverted. |
| | `agent/mock.py` | `_apply_lessons`: close lessons prune the plan set, looser ones reorder it. |
| | `agent/nemotron.py`, `learning/embeddings.py` | OpenAI-compatible NIM chat and optional `/embeddings` clients over the existing `httpx2`. A failed proposal is retried once with the validation errors fed back. |
| | `agent/briefing.py` (new) | The analyst prompt and candidate rows both analysts share, so the agent layer no longer imports them from `api/mcp_tools.py`. The REST provider gets `PLAN_DESIGN`, the tool-free half. |
| | `api/mcp_tools.py`, `api/routes.py`, `models/*`, `providers.py`, `main.py`, `config.py` | The tools, endpoints, records, settings and wiring described above, including transfer controls and the learning report. |
| UI | `components/EpisodePanel.tsx` (new), `plans/ResponsePlans.tsx`, `hooks/useCityStream.ts`, `lib/plans.ts`, `api/*`, `dev/replay.ts`, `styles.css` | The Autonomous agent panel, **Apply to live signals**, the applied/advisory footer, collapsed Learning report, Analyze Response disabled while an episode is working, and the types in sync. |
| Config and docs | `.env.example`, `requirements.txt` (`httpx2`, already a dependency of `mcp`), `.gitignore` (`memory/`), `CLAUDE.md`, `docs/architecture.md`, `docs/specs/scenario-engine-mcp.md`, the README | The safety rule now names its one gated exception everywhere. |

## What was and was not checked

- **Static checks recorded on main:** `python -m compileall app` and an import of `app.main`
  both passed. The import builds the app and registers every MCP tool, so the tool schemas
  load; it starts no SUMO and no server. `make build` (tsc + vite) and
  `npm --prefix frontend run lint` are clean.
- **Credential and catalog checks:** on 2026-09-19, authenticated read-only model-list calls
  returned HTTP 200 for the Claude and Nemotron credentials in the local `.env`, and each
  configured model id appeared in its provider's catalog. No secret value was printed or added
  to Git. These checks made no inference call.
- **Run since this pass:** on 2026-09-20 the operator-controlled flow was run once on macOS
  against the downtown grid with the mock analyst — one `operator-collision` episode reached
  `completed` with an applied plan, passing corridor and diversion response checks, a scorecard
  and a stored lesson, and the Autonomous agent panel rendered it. The README records the
  numbers. Runtime provider selection, Claude/Nemotron inference, Oakland and the
  single-container deployment still have not been run.
- **Next:** follow the README's [demo-readiness gate](../README.md#demo-readiness-gate). After
  the stage path works, use an MCP client to exercise a timing-plan implementation followed by
  a second analysis; every branch must load the changed signal program instead of failing with
  `Unknown program`. Then exercise `crash-already` and `double-crash` before relying on those
  non-stage paths.

## Where a reviewer should look hardest

1. **The live apply** (`implementor.py`). One `run_on_live` command validates, dispatches and
   installs. Check that `run.implementation`, the standing registry and the episode stay
   consistent when the caller is cancelled mid-apply (the apply runs in a shielded task).
2. **Episode transitions** (`episode.py`): supersede, abort and fail from every status, and the
   reset hook re-arming. When review starts, `_working` is released, so a crash during review
   starts a new episode and the finish step does not pause the sim under it.
3. **Scripted events** (`runner.py`). Once the thread runs, `set_scripted_events` goes through
   the command queue, so a new script cannot fire into the old simulation before the reset
   reboots it.
4. **The scorecard** (`scorecard.py`): the absolute-time window, the empty-window cases, and
   whether the thresholds give sensible verdicts on real runs.
5. **The model loop** (`analysts.py`, `agent/claude.py`, `agent/nemotron.py`): message shapes
   for Claude content blocks and NIM's OpenAI-compatible API, forced `incident_ids`, tool
   result errors, and the nudge when a model stops early.
6. **Still unexercised from the groundwork pass:**
   - `_realised_emergency_eta` returns `None` while any responder has not arrived.
   - Closing an analysis while branches run (`_close`, `_run_round`, `_drop_snapshot`).
   - Per-incident EMS probes.
   - The mock's combined plans: `_combined_plans` keeps the first policy per intersection, and
     the 8-candidate cap drops per-incident plans and `aggressive-flush` at the tail.
