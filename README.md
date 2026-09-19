# Traffic Operations Center: simulation-backed incident response

A city traffic operations center that extends the NVIDIA Smart City blueprint idea:
when a camera-detected incident (collision, stalled vehicle, …) hits the network,
candidate responses are **tested in a SUMO traffic simulation before anything is
recommended**. The experiments are already exposed as MCP tools; in milestone 3 a
Nemotron agent drives them.

This repository has completed **milestone 2**. It has a live SUMO digital twin of a 3×3
downtown grid and a FastAPI backend that streams city state over WebSocket. There is a
React/MapLibre operations console: inject a collision, watch the queue spill back, then
click **Analyze Response** to test up to 8 candidate plans in parallel SUMO branches.
Those plans include signal timing, an EMS green corridor and a diversion advisory. The
console compares each plan against the baseline and recommends one. Everything runs on a
laptop with no GPU. NVIDIA components plug in behind interfaces that already exist; the
adapters are still stubs.

## Quick start

Requirements: Python ≥ 3.11, Node ≥ 22.12. SUMO is installed from PyPI (`eclipse-sumo`), so
there is no system package, no GPU and no Docker.

```bash
make setup   # backend/.venv (FastAPI + SUMO wheels) and frontend/node_modules; safe to re-run
make dev     # backend on :8000 + UI on :5173
```

Open http://localhost:5173. Or run the two halves in separate terminals:

```bash
make backend    # cd backend && .venv/bin/uvicorn app.main:app --port 8000
make frontend   # npm --prefix frontend run dev
```

`make setup` keeps an existing venv and only syncs it with `backend/requirements*.txt`.
Re-run it after pulling a branch that adds a dependency (milestone 2 added `mcp`).
It uses `uv` when installed, and pip otherwise.

Without `make`:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements-dev.txt
npm --prefix frontend install
(cd backend && .venv/bin/uvicorn app.main:app --port 8000) &
npm --prefix frontend run dev
```

Other commands:

| | |
|---|---|
| `make test` | backend test suite (spawns real SUMO processes, ~20 s) |
| `make build` | type-check and production-build the UI |
| http://localhost:5173/?fixture=scenario | Analyze Response replays a recorded run (synthetic numbers) instead of calling `POST /api/scenarios/run`; `?fixture=scenario-failed` replays the failure path. Only the analysis call is replaced: the backend must still be running and a collision active, because the map and the button's prerequisites come from the live stream |
| `make network` | regenerate the SUMO network and demand from their build scripts |
| `SUMO_GUI=true make backend` | watch the live simulation in sumo-gui as well |
| http://127.0.0.1:8000/docs | interactive API docs |

## Demo script

1. The network warms up (5 simulated minutes, about 1 s) and runs at 4× real time. Roads
   are colored by cycle-averaged congestion, signal heads show the live phase, and
   vehicles move.
2. **Inject Collision**: two crashed vehicles block the right lane of Main St
   eastbound between Central Ave and Pine Ave. Traffic squeezes past at walking pace.
3. About 4 simulated seconds later the (mock) Smart City provider reports **INC-0001**
   from cameras CAM-C2/CAM-B2. The incident card, map marker and ops log light up.
4. Within 2–3 simulated minutes the link goes **severe**, the queue spills back past
   Central Ave onto Main St toward Oak Ave, and Central Ave starts to back up. Network
   delay roughly doubles (KPI tiles show the delta against pre-incident values).
5. **Analyze Response** (top bar, once the incident is detected). The backend snapshots
   the live network, then simulates the baseline and each candidate plan for 10 minutes
   in its own fresh SUMO process, 4 at a time. It dispatches an EMS probe from Fire
   Station 3 in every branch. The run takes about 25 s, and the live simulation keeps
   running meanwhile. The panel shows the following:
   - Plan cards appear as each branch finishes. Each shows delay, max queue, throughput
     and EMS response time, with the change against the baseline.
   - `aggressive-flush` comes back **rejected by the safety validator** (an 8 s green is
     below the 12 s pedestrian minimum) and is never simulated.
   - A recommendation with its rationale. In the measured run below it was the
     diversion advisory: mean delay down 15% and EMS response down 17% (4:52 → 4:03).
   - The comparison dock, horizon chart and map overlays show what each plan changes.
6. **Dispatch EMS**: EMS-1 leaves Fire Station 3 and the live ETA tracks it. It
   typically loses a couple of minutes in the incident queue.
7. **Clear scene** reopens the lane; **Reset** restores a clean network.

Nothing in the analysis changes the live signals: recommendations are advisory.

Use the speed buttons (1×–16×) to fast-forward. Click an intersection or road to inspect
its phase, queues and speed.

## Architecture

```
┌───────────────── frontend/ (React + TS + MapLibre) ─────────────────┐
│ map · incident card · KPIs · trends · ops log · response plans      │
└────────── REST /api/* ─────────┬──────────── WS /ws/state ──────────┘
                                 │                    agents (MCP) ──► /mcp
┌───────────────────────── backend/ (FastAPI) ─────────┼──────────────────┐
│ api/routes.py              api/mcp_tools.py ◄────────┘                  │
│      │                           │                                      │
│      ▼                           ▼                                      │
│ services/city.py ◄──── services/scenarios.py (ScenarioService)          │
│ (CityService)            open → capture → propose → validate →          │
│  │   SmartCityProvider   simulate → recommend                           │
│  │   (mock | nvidia)       │ AgentProvider (mock | nemotron)            │
│  │                         │ SafetyValidator                            │
│  ▼                         ▼                                            │
│ simulation/runner.py     simulation/branching.py                        │
│ one thread owns the      one fresh SUMO process per candidate,          │
│ live TraCI connection    4 in parallel, restored from one snapshot      │
│  └──────────┬──────────────┘                                            │
│   simulation/interface.py  TrafficSimulation                            │
│   simulation/sumo.py       SumoSimulation (TraCI)                       │
│   preemption.py · reroute.py  corridor and diversion responses          │
└──────────────────────────────┬──────────────────────────────────────────┘
                               │ TraCI
                   Eclipse SUMO (simulation/scenarios/downtown_grid)
```

Rules the code keeps:

- **The rest of the app never knows where data came from.** `CityService` sees only the
  `SmartCityProvider` and `AgentProvider` interfaces, and a factory picks the
  implementation from `SMART_CITY_PROVIDER` / `AGENT_PROVIDER`.
- **The simulation has no decision logic.** `TrafficSimulation` executes and measures;
  callers decide what to try.
- **Agents never touch signals.** Agents return plans as data (`SignalPolicy`,
  `EmergencyCorridor`, `RerouteAction`). A deterministic `SafetyValidator` checks them,
  and only simulation branches execute them. Inside a branch, every pre-emption command
  also passes a runtime transition check.

Details, design decisions and the NVIDIA integration plan are in
[docs/architecture.md](docs/architecture.md). That doc has a
[stage-by-stage input/output table](docs/architecture.md#analyze-response-pipeline) for
the Analyze Response pipeline.

## Repository layout

```
backend/app/
  main.py               FastAPI app + lifespan
  config.py             env settings (SMART_CITY_PROVIDER, AGENT_PROVIDER, SIM_*)
  providers.py          provider factories + service assembly
  api/routes.py         REST + /ws/state
  api/mcp_tools.py      the scenario engine as MCP tools at /mcp
  models/domain.py      IntersectionState, RoadSegmentState, Incident, TrafficMetrics,
                        SignalPolicy, EmergencyCorridor, RerouteAction, SimulationCandidate,
                        snapshots, geometry
  models/scenario.py    ScenarioRun, ScenarioRunRequest, Recommendation (analysis contract)
  models/api.py         CityState, requests/responses, ops events
  services/city.py      CityService: frames → CityState, commands, ops log
  services/scenarios.py ScenarioService: the Analyze Response pipeline (REST and MCP drivers)
  simulation/interface.py  TrafficSimulation contract
  simulation/sumo.py    SUMO/TraCI implementation (collisions, EMS, snapshots, metrics)
  simulation/branching.py  run one candidate in a fresh SUMO process from a snapshot
  simulation/preemption.py EMS green-corridor controller + runtime transition check (no TraCI)
  simulation/reroute.py diversion advisory with a compliance share
  simulation/runner.py  paced live loop on its own thread
  simulation/network.py static topology, phase labelling, geo projection
  simulation/metrics.py live + horizon TrafficMetrics
  smart_city/           SmartCityProvider: base, mock (ground truth + detection delay), nvidia (stub)
  agent/                AgentProvider: base, mock (8 rule-based plans), nemotron (stub)
  safety/validator.py   SafetyValidator (signal policies + corridors) and rule-based MVP limits
  websocket/hub.py      non-blocking WebSocket fan-out
backend/tests/          network, simulation, safety/agent, mock provider, API tests
frontend/src/
  App.tsx               layout + actions
  hooks/useCityStream.ts   WebSocket client (reconnect, trend backfill, scenario runs)
  components/map/       MapLibre map, layer styles, vehicle glyphs, plan overlays
  components/plans/     response plans: candidate cards, KPI comparison, horizon chart, dock
  components/           top bar, incident card, KPI tiles, inspector, trends, ops log
  lib/plans.ts          analysis state, deltas, plan overlays
  dev/                  ?fixture=scenario replay of a recorded run
simulation/
  networks/grid3x3/     build_network.py → grid3x3.net.xml (named streets, 9 signals)
  scenarios/downtown_grid/  scenario.sumocfg, demand, vehicle types, scenario.json
  controllers/          how pre-emption plugs in (the code lives in backend/app/simulation/)
docs/
  architecture.md       design notes, the pipeline stage by stage, MCP tools
  milestone-2/          the milestone-2 plan, per-feature specs and results
  specs/                scenario-engine-mcp.md (MCP tools spec + client snippet)
```

## API

| Method | Path | |
|---|---|---|
| GET | `/api/state` | full `CityState` (same payload as the WebSocket frames) |
| GET | `/api/network` | static geometry for the map |
| GET | `/api/incidents` | active incidents (`?include_cleared=true` for history) |
| GET | `/api/incidents/{id}` | one incident |
| POST | `/api/incidents/inject` | stage a collision (defaults from `scenario.json`) |
| POST | `/api/incidents/{id}/clear` | clear the scene and reopen lanes |
| POST | `/api/simulation/start` \| `pause` \| `reset` | run control |
| POST | `/api/simulation/speed` | `{"multiplier": 8}` |
| POST | `/api/emergency/dispatch` | send EMS to the latest incident |
| GET | `/api/signals/{intersection}` | active signal program |
| GET | `/api/cameras`, `/api/events` | camera registry, ops log |
| POST | `/api/scenarios/run` | start Analyze Response: `{"incident_id"?, "horizon_s": 600, "ems_probe": true}` → `ScenarioRun` (202; 409 if no active incident or a run is open) |
| GET | `/api/scenarios` | recent runs (newest first, last 10) |
| GET | `/api/scenarios/{id}` | one run with candidates, metrics, timelines and the recommendation |
| WS | `/ws/state` | `hello` (state, events, trend, latest run) then `state` / `status` / `event` / `scenario` messages |
| MCP | `/mcp` | streamable HTTP: `start_analysis`, `validate_plan`, `simulate_plans`, `get_analysis`, `submit_recommendation` ([spec](docs/specs/scenario-engine-mcp.md)) |

## Current limitations

- **Recommendations are advisory.** No path applies a plan to the live simulation yet;
  plans only run inside branches.
- **The EMS corridor rarely helps once the queue has formed.** Pre-emption turns the
  signals green, but the responder still waits behind the queue in the blocked lane.
  Measured on main with the analysis started about 75 s after the crash:
  - `ems-corridor`: 334 s EMS response, against 292 s for the baseline;
  - `divert-advisory`: 242 s, because shortening the queue is what helps.

  The mock's EMS tolerance keeps a slower corridor from being recommended. Levers to try:
  a longer detection distance, or a combined corridor + diversion plan.
- **Branches are slow-ish.** Each takes 10–16 s of wall time when 4 run in parallel, so a
  full run takes about 25 s. About half of that is per-step rubbernecking bookkeeping in
  `sumo.py`, which is the obvious optimisation.
- The mock agent is rule-based: it proposes a fixed set of 8 plans and recommends with a
  fixed rule (see [architecture.md](docs/architecture.md#analyze-response-pipeline)).
- NVIDIA adapters (`NvidiaSmartCityProvider`, `NemotronAgentProvider`) are documented
  stubs that raise `NotImplementedError`. Selecting them fails fast at startup.
- Synthetic network and demand. The crash physics (blocked lane plus a 0.6 m/s pass
  speed) and congestion thresholds are calibrated for this grid, not measured data.
- One live simulation per backend process and one analysis run at a time. State is in
  memory, and nothing has auth, including `/mcp`.
- The live EMS ETA is an estimate (observed speeds plus expected signal waits). The
  realised response time comes from the simulation.

## Next: milestone 3

1. **Nemotron drives the analysis.** `NemotronAgentProvider` becomes an MCP client of
   `/mcp`: it lists the tools, hands them to the OpenAI-compatible NIM endpoint, and runs
   the model's tool calls. The loop goes `start_analysis` → `validate_plan` →
   `simulate_plans` (one or more rounds) → `submit_recommendation`. The mock stays the
   default and the fallback. See [nemotron.py](backend/app/agent/nemotron.py) and the
   [MCP spec](docs/specs/scenario-engine-mcp.md).
2. **Operator-approved apply.** A recommended plan reaches the live twin only after the
   operator approves it. It is re-validated against the live signal programs first and
   goes through the same runtime transition check. The agent never gets a tool for this.
3. **NVIDIA Smart City input, prepared but not faked.** The `NvidiaSmartCityProvider`
   mapping onto the VSS Video Analytics MCP tools, plus a map-matching component (lat/lon
   and place names → segment and lane). This milestone does not install or run the full
   Blueprint; until a real VSS endpoint exists, the mock stays the provider.
