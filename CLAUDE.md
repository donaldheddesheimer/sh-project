# CLAUDE.md

Traffic operations center: a live SUMO digital twin (3×3 downtown grid) behind a FastAPI
backend and a React/MapLibre console. When an incident hits, candidate responses (signal
timing, EMS green corridor, diversion) are **simulated in parallel SUMO branches before
anything is recommended**. Milestone 2 (Analyze Response + MCP tools) is merged. Milestone 3,
the autonomous, self-learning episode (agent responds, applies its plan to the live twin,
measures it, stores a lesson), is built and has run end to end once (a cold mock run, in a
smoke test; README "Tests"). Milestone 4's faster twin
and NVIDIA Smart City input are built and merged, neither run nor measured; its transferable
memory remains planned in [docs/milestone-4/MASTER.md](docs/milestone-4/MASTER.md).
Read [README.md](README.md) for the demos and the episode (its "The autonomous, self-learning
episode" section), and [docs/architecture.md](docs/architecture.md) for the design and both
pipelines stage by stage.

Start every session with `git fetch && git status -sb`. `main` moves quickly and local docs
can predate a merged cleanup.

## Hard rules

Set by the user. They override anything else in this file or in the docs.

1. **Never run tests, ever.** Do not run `pytest`, `make test` or any other test runner, and
   do not write or run scratch scripts or other automated checks whose purpose is to test or
   verify behavior. This holds even if a task, a doc or another part of this file suggests
   it. **Write tests only when the user asks, and only the set they name**: never add, edit or
   extend tests on your own initiative. A test you write is correct by construction: written
   against code you have read in full, so that a failure means a bug in the code and never
   in the test. Check work by reading the code, and say plainly in the report what was **not**
   run or verified. If a check by running seems necessary, ask the user; they will run it.
2. **Maintain [README.md](README.md) as a consistent source of information, always.** Every
   change to behavior, API, settings, file layout, terminology, roadmap or decisions updates
   the README in the same change, and before finishing you re-read the parts it touches.
   One term keeps one meaning (see the README's Terms table). No section may contradict
   another, and the same fact is not written in two places. Mark what is built versus
   planned, and what was not verified. If the README and the code disagree, fix one of them
   in that change; never leave both.

## Commands

Run from the repo root. SUMO comes from PyPI (`eclipse-sumo`), so nothing else is installed.
The **Makefile and `scripts/dev.sh` are POSIX-only** (`.venv/bin/`); on Windows call the
venv directly (this is what works in PowerShell or Git Bash):

| Task | POSIX (`make`) | Windows |
|---|---|---|
| Set up | `make setup` | `python -m venv backend\.venv`, then `backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt`, then `npm --prefix frontend install` |
| Backend tests (24, real SUMO; for what has and has not been run see README "Tests"). **Reference only: never run unasked (Hard rules)** | `make test` | `cd backend; .venv\Scripts\python.exe -m pytest -q` |
| One test. **Reference only: never run unasked (Hard rules)** | `cd backend && .venv/bin/pytest -q -k <name>` | `.venv\Scripts\python.exe -m pytest -q -k <name>` (in `backend`) |
| Backend on :8000 | `make backend` | `cd backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000` |
| Frontend on :5173 | `make frontend` | `npm --prefix frontend run dev` |
| Frontend check | `make build` | `npm --prefix frontend run lint` and `npm --prefix frontend run build` (both clean today; a >500 kB chunk warning is expected) |
| Oakland, Pittsburgh instead of the grid | `make backend-oakland` (still :8000) | set `SCENARIO_DIR=simulation/scenarios/pittsburgh_oakland` in `.env`, then run the backend as usual |
| Rebuild Oakland net/demand/timing | `make network-oakland` | run `simulation/networks/pittsburgh_oakland/build_network.py` with the venv python |
| Regenerate network/demand | `make network` | run `simulation/networks/grid3x3/build_network.py` then `simulation/scenarios/downtown_grid/build_demand.py` with the venv python |

- `ModuleNotFoundError: mcp` means the venv predates a `requirements.txt` change. Re-run
  the pip install line above.
- Health check: `GET /api/health`; API docs at `/docs`; MCP at `POST /mcp` (streamable HTTP).
- Try the pipeline: `POST /api/incidents/inject` with `{}`, wait about 2 simulated minutes
  (30 s at the default 4×), then `POST /api/scenarios/run` with `{}`. The run takes ~25 s.
  Use `curl.exe` in PowerShell (`curl` is an alias for `Invoke-WebRequest`).
- Try an episode: `POST /api/demo/start` with `{"script": "crash-ahead"}` (or the console's
  Autonomous agent panel). `DELETE /api/memory` first for a cold run.
- To test the UI against a non-default backend: `BACKEND_URL=http://127.0.0.1:8001` for Vite.
  `?fixture=scenario` replays a recorded run but still needs a running backend and an active incident.
- Config is env vars or `.env` (repo root or `backend/`); every setting is in
  [backend/app/config.py](backend/app/config.py) and documented in `.env.example`.

## Where to change what

| To change | Look at |
|---|---|
| REST endpoints, `/ws/state` | [backend/app/api/routes.py](backend/app/api/routes.py) |
| MCP tools | [backend/app/api/mcp_tools.py](backend/app/api/mcp_tools.py) |
| The analyst prompt and candidate rows both analysts share | [backend/app/agent/briefing.py](backend/app/agent/briefing.py) |
| Live orchestration, ops log, WebSocket frames | [backend/app/services/city.py](backend/app/services/city.py) |
| Analyze Response pipeline (`open → capture → evaluate → finish/fail`) | [backend/app/services/scenarios.py](backend/app/services/scenarios.py) |
| One candidate branch in a fresh SUMO process | [backend/app/simulation/branching.py](backend/app/simulation/branching.py) |
| TraCI behavior: collisions, EMS, snapshots, rubbernecking | [backend/app/simulation/sumo.py](backend/app/simulation/sumo.py) (contract: `interface.py`) |
| EMS pre-emption / diversion | `simulation/preemption.py`, `simulation/reroute.py` |
| Safety rules (timing limits, corridor bounds) | [backend/app/safety/validator.py](backend/app/safety/validator.py) |
| Which plans the mock proposes, how lessons prune them, and how it recommends | [backend/app/agent/mock.py](backend/app/agent/mock.py) |
| Episode state machine, two-crash rule, demo scripts | [backend/app/learning/episode.py](backend/app/learning/episode.py) |
| Applying a recommendation to the live city (the only live apply) | [backend/app/learning/implementor.py](backend/app/learning/implementor.py) |
| Live monitor, scorecard thresholds and outcome rules | `backend/app/learning/monitor.py`, `scorecard.py` |
| Reviewer (lesson text), memory files, recall similarity | `backend/app/learning/reviewer.py`, `store.py` |
| Analysts: mock pipeline, Nemotron MCP loop over NIM | `backend/app/learning/analysts.py`, `backend/app/agent/nemotron.py` (NIM client) |
| Data models | `backend/app/models/{domain,api,scenario,episode}.py` |
| Network, demand, incident defaults | `simulation/networks/grid3x3/`, `simulation/scenarios/downtown_grid/` (`scenario.json`) |
| The Oakland city (OSM map, synthetic demand and timing) | `simulation/networks/pittsburgh_oakland/` (README, build), `simulation/scenarios/pittsburgh_oakland/` |
| Frontend layout and actions | [frontend/src/App.tsx](frontend/src/App.tsx) |
| WebSocket client, scenario state | [frontend/src/hooks/useCityStream.ts](frontend/src/hooks/useCityStream.ts) |
| Response-plan UI and derived deltas | `frontend/src/components/plans/`, `frontend/src/lib/plans.ts` |
| Episode panel (scripts, step strip, lesson) | [frontend/src/components/EpisodePanel.tsx](frontend/src/components/EpisodePanel.tsx) |
| Theme | one file, [frontend/src/styles.css](frontend/src/styles.css), driven by CSS variables |

## Rules the design depends on

- **Providers are interfaces.** `CityService` sees `SmartCityProvider` and `AgentProvider`
  only; `providers.py` picks mock vs NVIDIA/Nemotron. `NvidiaSmartCityProvider` polls VSS
  over MCP (or a development replay file), map-matches reports and has `CityService` mirror
  matched collisions into the twin. `NemotronAgentProvider` powers REST Analyze Response with
  schema-validated plan data; the safety validator and completed-candidate gate still decide
  what can run. Its first failure switches that run to the mock when
  `AGENT_FALLBACK_TO_MOCK=true`, or fails it when false. Nemotron also runs as an episode
  analyst (`EPISODE_ANALYST`).
- **One thread owns the live TraCI connection.** TraCI is blocking and not thread-safe.
  Touch the live simulation only through `CityService.run_on_live(fn)`; scripted crashes are
  fired by the runner thread itself (`set_scripted_events`).
- **Every candidate runs in a brand-new SUMO process** restored from one snapshot. That is
  the only way SUMO stays bit-reproducible; reloading into a used process diverges. Never
  reuse a branch process.
- **Agents never set signal states; one gated implementor applies recommendations.** Plans
  are data (`SignalPolicy`, `EmergencyCorridor`, `RerouteAction`); the validator checks them
  and branches simulate them. Only `learning/implementor.py` changes the live signals, and
  only with a completed run's recommended candidate, re-validated against the live programs
  and installed with the same `apply_plan` a branch runs. `AGENT_MAY_IMPLEMENT` gates the agent
  path; the operator path is `POST /api/scenarios/{id}/implement`. Pre-emption commands also
  pass `check_transition` at runtime. Keep every other agent-facing tool read-only or
  branch-only.
- **An approach is its incoming segment.** `IntersectionInfo.approaches_by_segment`,
  `SignalPhase.served_segments`, pre-emption targets and the live `approach_signals` /
  `queue_lengths` are keyed by segment id. NB/SB/EB/WB is a display label and repeats at
  off-grid junctions (Oakland's Fifth & Neville has two SB legs), so never key logic by it.
  `IntersectionInfo.approaches` is a lossy label view kept only for `test_network.py`.
- **`ScenarioRun` is mutated only on the event loop.** Workers return their own candidate
  object; publish changes with `publish_scenario`.
- One analysis at a time (409 otherwise), and it needs an active, map-matched incident.
  Snapshots carry programs installed at runtime (`custom_programs`, re-created before
  `loadState`); corridors, diversions and pending offsets are Python-side, so branches replay
  the standing responses.
- **Only the episode service starts an agent**, and only while a demo script is armed. Its
  hooks run inside the frame pipeline: change state synchronously, spawn tasks for slow work.

## Keep in sync by hand

- `backend/app/models/*.py` (including `episode.py`) ↔
  [frontend/src/api/types.ts](frontend/src/api/types.ts). There is no codegen.
- `ScenarioStatus` ↔ the `STAGE` map in `useCityStream.ts`.
- `EpisodeStatus` ↔ `FLOW` / `STATUS_TAG` in `EpisodePanel.tsx`; `WORKING_STATUSES` ↔
  `WORKING` in `lib/plans.ts`.
- `TREND_SAMPLE_S` in `services/city.py` ↔ `SAMPLE_EVERY_S` in `useCityStream.ts` ↔
  `SAMPLE_S` in `learning/monitor.py`.
- A new setting goes in `config.py` **and** `.env.example`.
- The mock proposes exactly 9 plans, which equals the default `SCENARIO_MAX_CANDIDATES`. A
  tenth plan silently pushes `divert-advisory` off the end (it is appended last). (A warm run
  with a close lesson proposes 4 on purpose.)
- The recorded run lives in `frontend/src/dev/scenario-run.json`. The copy under
  `docs/milestone-2/fixtures/` is a frozen duplicate; edit the frontend one.

## Things that look like bugs but aren't

- `aggressive-flush` is **deliberately unsafe** (an 8 s green) so the demo always shows the
  validator rejecting a plan. Don't fix it, and don't loosen the validator to make it pass.
- Corridor plans and `aggressive-flush` are proposed only when an EMS origin or a responder
  exists, so tests with no EMS context don't see them.
- Drivers use habitual routes (`adaptation-interval = 0`). Without it SUMO reroutes
  everyone with live travel times and incidents dissolve instantly.
- The EMS corridor often loses to `divert-advisory` once a queue has formed: the responder
  still waits in the blocked lane. This was a measured result, not a defect. Two levers are
  now built and unmeasured: the mock asks for `EMS_DETECTION_M` (350 m, against the 150 m
  model default) and proposes `corridor-plus-divert`. SUMO's blue-light device is a third
  lever, deliberately not built (a modelling decision for the user; MASTER D4).
- Post-crash branches took 10–16 s each. The per-vehicle TraCI lookups in
  `_apply_rubbernecking` and the responder walk are gone (lane id and lane position ride on
  `VEHICLE_VARS`), and `start()` no longer pays traci's fixed 1 s wait between connect
  attempts — **neither speed-up has been timed**. `simulate_plans` still blocks until every
  branch finishes, so MCP clients need a timeout of 120 s or more.
- Snapshot files go to `<OS temp>/traffic-ops-snapshots` and are deleted when a run ends.
- A warm mock run simulates fewer plans than a cold one: `_apply_lessons` in `agent/mock.py`
  prunes the set when a remembered episode is a close match.
- Many lessons come out `inconclusive`: a gain below the materiality thresholds in
  `learning/scorecard.py` (5% delay, 5 vehicles, 30 s EMS) counts as noise, and timing plans
  move delay by about 1%.
- A reset or **Clear scene** during an episode aborts it, and a Reset also fails any open
  analysis ("the simulation was reset"): its snapshot describes a city that is gone.
- Applied plans stay on the live signals until a reset; nothing reverts them.
  `SumoSimulation.revert_response()` exists and is idempotent, but has no caller yet (the
  service that will use it is milestone 4 part 2).
- An unsafe pre-emption stops a branch but not the live twin: `fail_safe_preemption=True`
  is set only in `providers.py`'s `live_simulation()`, where the corridor is dropped and a
  note recorded instead of the runner going to `error`.
- On Oakland the mock can propose fewer than 9 plans. A timing shift never takes a green below
  12 s (`_safe_shift` in `agent/mock.py`); when the donor phase has less than 4 s to spare, that
  plan is skipped rather than proposed and rejected. `aggressive-flush` is still proposed on purpose.
- The backend assumes a signal's id is its junction id. netconvert names OSM-guessed signals
  `GS_<junction>`, so the Oakland build drops the prefix. Non-signalized junctions have `tls_id=None`.

## Conventions

- **Python 3.11+.** `from __future__ import annotations`, full type hints, Pydantic v2
  models, comments that explain why. About 120 columns. No formatter or linter is configured.
- **Frontend.** TypeScript with `noUnused*` on, React 19 function components, no
  semicolons, single quotes, 2-space indent. Lint is `oxlint`. Plan colors follow backend
  candidate order, never rank; the baseline stays neutral.
- **Tests.** Never run them, and write them only when asked (Hard rules). The team chose demo
  over coverage (recorded in `docs/milestone-2/MASTER.md`); the five demo-readiness tests
  (`test_demo_setup.py`, `test_demo_smoke.py`, one in `test_simulation.py`; README "Tests")
  passed on one run the user asked for, which predates the async `ExperienceStore`:
  `test_memory_store_round_trip` was updated for it and has not been re-run.
  Verify by reading the code and report what was not run. Known gaps:
  pre-emption, reroute, the MCP tools, Nemotron, warm recall and the two-crash rule have no
  tests; `ScenarioService` and the episode are covered only by the two smoke tests. The suite
  is in `backend/tests/`; the `make_sim` fixture in `tests/conftest.py` starts real SUMO
  processes, and the smoke tests boot the whole app with `Settings(_env_file=None, ...)`.
- **README.** It is the source of truth for the episode and the roadmap; keep it consistent
  (Hard rules).
- **Git.** Work happens on feature branches merged by PR; don't push to `main`.

## Docs map

- [docs/architecture.md](docs/architecture.md): current design, the Analyze Response and episode stage tables, the safety model.
- [docs/specs/scenario-engine-mcp.md](docs/specs/scenario-engine-mcp.md): MCP tool contract and a client snippet.
- [simulation/controllers/README.md](simulation/controllers/README.md): how pre-emption stays safe.
- [docs/milestone-4/MASTER.md](docs/milestone-4/MASTER.md): the **current** plan for milestone 4
  (three parts, file ownership, the step-0 contract, decisions to confirm). The README's "Next:
  milestone 4" table is where each part's status lives. The hard rules above override it.
- `docs/milestone-2/`: **historical** planning record. Its file-ownership and "frozen"
  rules no longer apply. `docs/hackathon-reference-projects.md` is unrelated inspiration.
