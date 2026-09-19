# CLAUDE.md

Traffic operations center: a live SUMO digital twin (3×3 downtown grid) behind a FastAPI
backend and a React/MapLibre console. When an incident hits, candidate responses (signal
timing, EMS green corridor, diversion) are **simulated in parallel SUMO branches before
anything is recommended**. Milestone 2 (Analyze Response + MCP tools) is merged; milestone 3
(Nemotron agent, operator-approved apply, NVIDIA input) is next. Read
[README.md](README.md) for the demo and [docs/architecture.md](docs/architecture.md) for
the design and the pipeline stage by stage.

Start every session with `git fetch && git status -sb`. `main` moves quickly and local docs
can predate a merged cleanup.

## Commands

Run from the repo root. SUMO comes from PyPI (`eclipse-sumo`), so nothing else is installed.
The **Makefile and `scripts/dev.sh` are POSIX-only** (`.venv/bin/`); on Windows call the
venv directly (this is what works in PowerShell or Git Bash):

| Task | POSIX (`make`) | Windows |
|---|---|---|
| Set up | `make setup` | `python -m venv backend\.venv`, then `backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt`, then `npm --prefix frontend install` |
| Backend tests (19, real SUMO, 20–40 s) | `make test` | `cd backend; .venv\Scripts\python.exe -m pytest -q` |
| One test | `cd backend && .venv/bin/pytest -q -k <name>` | `.venv\Scripts\python.exe -m pytest -q -k <name>` (in `backend`) |
| Backend on :8000 | `make backend` | `cd backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000` |
| Frontend on :5173 | `make frontend` | `npm --prefix frontend run dev` |
| Frontend check | `make build` | `npm --prefix frontend run lint` and `npm --prefix frontend run build` (both clean today; a >500 kB chunk warning is expected) |
| Regenerate network/demand | `make network` | run `simulation/networks/grid3x3/build_network.py` then `simulation/scenarios/downtown_grid/build_demand.py` with the venv python |

- `ModuleNotFoundError: mcp` means the venv predates a `requirements.txt` change. Re-run
  the pip install line above.
- Health check: `GET /api/health`; API docs at `/docs`; MCP at `POST /mcp` (streamable HTTP).
- Try the pipeline: `POST /api/incidents/inject` with `{}`, wait about 2 simulated minutes
  (30 s at the default 4×), then `POST /api/scenarios/run` with `{}`. The run takes ~25 s.
  Use `curl.exe` in PowerShell (`curl` is an alias for `Invoke-WebRequest`).
- To test the UI against a non-default backend: `BACKEND_URL=http://127.0.0.1:8001` for Vite.
  `?fixture=scenario` replays a recorded run but still needs a running backend and an active incident.
- Config is env vars or `.env` (repo root or `backend/`); every setting is in
  [backend/app/config.py](backend/app/config.py) and documented in `.env.example`.

## Where to change what

| To change | Look at |
|---|---|
| REST endpoints, `/ws/state` | [backend/app/api/routes.py](backend/app/api/routes.py) |
| MCP tools and their `instructions` prompt | [backend/app/api/mcp_tools.py](backend/app/api/mcp_tools.py) |
| Live orchestration, ops log, WebSocket frames | [backend/app/services/city.py](backend/app/services/city.py) |
| Analyze Response pipeline (`open → capture → evaluate → finish/fail`) | [backend/app/services/scenarios.py](backend/app/services/scenarios.py) |
| One candidate branch in a fresh SUMO process | [backend/app/simulation/branching.py](backend/app/simulation/branching.py) |
| TraCI behavior: collisions, EMS, snapshots, rubbernecking | [backend/app/simulation/sumo.py](backend/app/simulation/sumo.py) (contract: `interface.py`) |
| EMS pre-emption / diversion | `simulation/preemption.py`, `simulation/reroute.py` |
| Safety rules (timing limits, corridor bounds) | [backend/app/safety/validator.py](backend/app/safety/validator.py) |
| Which plans the mock proposes, and how it recommends | [backend/app/agent/mock.py](backend/app/agent/mock.py) |
| Data models | `backend/app/models/{domain,api,scenario}.py` |
| Network, demand, incident defaults | `simulation/networks/grid3x3/`, `simulation/scenarios/downtown_grid/` (`scenario.json`) |
| Frontend layout and actions | [frontend/src/App.tsx](frontend/src/App.tsx) |
| WebSocket client, scenario state | [frontend/src/hooks/useCityStream.ts](frontend/src/hooks/useCityStream.ts) |
| Response-plan UI and derived deltas | `frontend/src/components/plans/`, `frontend/src/lib/plans.ts` |
| Theme | one file, [frontend/src/styles.css](frontend/src/styles.css), driven by CSS variables |

## Rules the design depends on

- **Providers are interfaces.** `CityService` sees `SmartCityProvider` and `AgentProvider`
  only; `providers.py` picks mock vs NVIDIA/Nemotron. The NVIDIA/Nemotron classes are stubs
  that raise `NotImplementedError`, and selecting them fails at startup.
- **One thread owns the live TraCI connection.** TraCI is blocking and not thread-safe.
  Touch the live simulation only through `CityService.run_on_live(fn)`.
- **Every candidate runs in a brand-new SUMO process** restored from one snapshot. That is
  the only way SUMO stays bit-reproducible; reloading into a used process diverges. Never
  reuse a branch process.
- **Agents never touch live signals.** Plans are data (`SignalPolicy`, `EmergencyCorridor`,
  `RerouteAction`); the validator checks them, and only branches execute them. Pre-emption
  commands also pass `check_transition` at runtime. Keep new agent-facing tools read-only
  or branch-only.
- **`ScenarioRun` is mutated only on the event loop.** Workers return their own candidate
  object; publish changes with `publish_scenario`.
- One analysis at a time (409 otherwise), and it needs an active, map-matched incident.
  Snapshots capture base signal programs only.

## Keep in sync by hand

- `backend/app/models/*.py` ↔ [frontend/src/api/types.ts](frontend/src/api/types.ts).
  There is no codegen.
- `ScenarioStatus` ↔ the `STAGE` map in `useCityStream.ts`.
- `TREND_SAMPLE_S` in `services/city.py` ↔ `SAMPLE_EVERY_S` in `useCityStream.ts`.
- A new setting goes in `config.py` **and** `.env.example`.
- The mock proposes exactly 8 plans, which equals the default `SCENARIO_MAX_CANDIDATES`. A
  ninth plan silently pushes `divert-advisory` off the end.
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
  still waits in the blocked lane. This is a measured result, not a defect.
- Post-crash branches take 10–16 s each (about half is `_apply_rubbernecking`'s per-vehicle
  TraCI lookups in `sumo.py`, the known optimisation). `simulate_plans` blocks until every
  branch finishes, so MCP clients need a timeout of 120 s or more.
- Snapshot files go to `<OS temp>/traffic-ops-snapshots` and are deleted when a run ends.

## Conventions

- **Python 3.11+.** `from __future__ import annotations`, full type hints, Pydantic v2
  models, comments that explain why. About 120 columns. No formatter or linter is configured.
- **Frontend.** TypeScript with `noUnused*` on, React 19 function components, no
  semicolons, single quotes, 2-space indent. Lint is `oxlint`. Plan colors follow backend
  candidate order, never rank; the baseline stays neutral.
- **Tests.** The team decided not to add new test files (demo over coverage, recorded in
  `docs/milestone-2/MASTER.md`). Keep the suite green and verify by running things. Note the
  gaps: pre-emption, reroute, `ScenarioService` and the MCP tools have no tests. The
  `make_sim` fixture in `tests/conftest.py` starts real SUMO processes.
- **Git.** Work happens on feature branches merged by PR; don't push to `main`.

## Docs map

- [docs/architecture.md](docs/architecture.md): current design, the 8-stage pipeline table, the safety model.
- [docs/specs/scenario-engine-mcp.md](docs/specs/scenario-engine-mcp.md): MCP tool contract and a client snippet for the Nemotron loop.
- [simulation/controllers/README.md](simulation/controllers/README.md): how pre-emption stays safe.
- `docs/milestone-2/`: **historical** planning record. Its file-ownership and "frozen"
  rules no longer apply. `docs/hackathon-reference-projects.md` is unrelated inspiration.
