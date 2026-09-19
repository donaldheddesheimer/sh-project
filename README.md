# Traffic Operations Center: simulation-backed incident response

A city traffic operations center that extends the NVIDIA Smart City blueprint idea:
when a camera-detected incident (collision, stalled vehicle, …) hits the network,
candidate responses are **tested in a SUMO traffic simulation before anything is
recommended**. Later, a Nemotron agent will drive those experiments through MCP tools.

This repository is at **milestone 1**: a live SUMO digital twin of a 3×3 downtown grid,
a FastAPI backend streaming city state over WebSocket, and a React/MapLibre operations
console where you can inject a collision and watch the queue spill back. Everything runs
on a laptop with no GPU. NVIDIA components plug in later behind interfaces that already
exist.

## Quick start

Requirements: Python ≥ 3.11, Node ≥ 20. SUMO is installed from PyPI (`eclipse-sumo`), so
there is no system package, no GPU and no Docker.

```bash
make setup   # backend/.venv (FastAPI + SUMO wheels) and frontend/node_modules
make dev     # backend on :8000 + UI on :5173
```

Open http://localhost:5173. Or run the two halves in separate terminals:

```bash
make backend    # cd backend && .venv/bin/uvicorn app.main:app --port 8000
make frontend   # npm --prefix frontend run dev
```

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
5. **Dispatch EMS**: EMS-1 leaves Fire Station 3 and the live ETA tracks it. It
   typically loses a couple of minutes in the incident queue, which is the motivation
   for simulating an emergency green corridor in the next milestone.
6. **Clear scene** reopens the lane; **Reset** restores a clean network.

Use the speed buttons (1×–16×) to fast-forward. Click an intersection or road to inspect
its phase, queues and speed.

## Architecture

```
┌──────────────── frontend/ (React + TS + MapLibre) ────────────────┐
│ map · incident card · KPIs · trends · ops log · controls          │
└──────────── REST /api/* ─────────────┬──────── WS /ws/state ───────┘
                                       │
┌──────────────────────── backend/ (FastAPI) ─────────────────────────┐
│ api/routes.py ─► services/city.py (CityService: orchestration)      │
│                    │            │                │                  │
│   SmartCityProvider│  AgentProvider  SafetyValidator  websocket/hub │
│   (mock | nvidia)  │  (mock | nemotron)                             │
│                    ▼                                                │
│   simulation/runner.py  one thread owns the live TraCI connection   │
│   simulation/interface.py  TrafficSimulation (MCP-wrappable ops)    │
│   simulation/sumo.py  SumoSimulation (TraCI implementation)         │
└───────────────────────────────┬─────────────────────────────────────┘
                                │ TraCI
                    Eclipse SUMO (simulation/scenarios/downtown_grid)
```

Rules the code keeps:

- **The rest of the app never knows where data came from.** `CityService` sees only the
  `SmartCityProvider` and `AgentProvider` interfaces, and a factory picks the
  implementation from `SMART_CITY_PROVIDER` / `AGENT_PROVIDER`.
- **The simulation has no decision logic.** `TrafficSimulation` executes and measures;
  callers decide what to try. Its operations map one-to-one to future MCP tools.
- **Agents never touch signals.** Agents return `SignalPolicy` data, a deterministic
  `SafetyValidator` checks it, and only simulation branches execute it.

Details, design decisions and the NVIDIA integration plan are in
[docs/architecture.md](docs/architecture.md).

## Repository layout

```
backend/app/
  main.py               FastAPI app + lifespan
  config.py             env settings (SMART_CITY_PROVIDER, AGENT_PROVIDER, SIM_*)
  providers.py          provider factories + service assembly
  api/routes.py         REST + /ws/state
  models/domain.py      IntersectionState, RoadSegmentState, Incident, TrafficMetrics,
                        SignalPolicy, SimulationCandidate, snapshots, geometry
  models/api.py         CityState, requests/responses, ops events
  services/city.py      CityService: frames → CityState, commands, ops log
  simulation/interface.py  TrafficSimulation contract
  simulation/sumo.py    SUMO/TraCI implementation (collisions, EMS, snapshots, metrics)
  simulation/runner.py  paced live loop on its own thread
  simulation/network.py static topology, phase labelling, geo projection
  simulation/metrics.py live + horizon TrafficMetrics
  smart_city/           SmartCityProvider: base, mock (ground truth + detection delay), nvidia (stub)
  agent/                AgentProvider: base, mock (rule-based candidates), nemotron (stub)
  safety/validator.py   SafetyValidator + rule-based MVP limits
  websocket/hub.py      non-blocking WebSocket fan-out
backend/tests/          network, simulation, safety/agent, mock provider, API tests
frontend/src/
  App.tsx               layout + actions
  hooks/useCityStream.ts   WebSocket client (reconnect, trend backfill)
  components/map/       MapLibre map, layer styles, vehicle glyphs
  components/           top bar, incident card, KPI tiles, inspector, trends, ops log
simulation/
  networks/grid3x3/     build_network.py → grid3x3.net.xml (named streets, 9 signals)
  scenarios/downtown_grid/  scenario.sumocfg, demand, vehicle types, scenario.json
  controllers/          reserved for timing plans / preemption (milestone 2)
docs/                   architecture notes, hackathon reference projects
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
| WS | `/ws/state` | `hello` (state, events, trend) then `state` / `status` / `event` messages |

`POST /api/scenarios/run` and `GET /api/scenarios/{id}` arrive with milestone 2 (see below).

## Current limitations

- **Candidate analysis is not wired yet.** The pieces exist and are tested (snapshots
  that branch deterministically into fresh SUMO processes, `apply_signal_policy`,
  horizon metrics, the rule-based `MockAgentProvider`, and the `SafetyValidator`), but
  there is no scenario service, `/api/scenarios/*` endpoints or comparison UI. The
  **Analyze Response** button is disabled.
- NVIDIA adapters (`NvidiaSmartCityProvider`, `NemotronAgentProvider`) are documented
  stubs that raise `NotImplementedError`. Selecting them fails fast at startup.
- Synthetic network and demand. The crash physics (blocked lane plus a 0.6 m/s pass
  speed) and congestion thresholds are calibrated for this grid, not measured data.
- Snapshots capture base signal programs only. A policy applied to the live simulation
  must be re-applied after restore.
- One live simulation per backend process; state is in memory; no auth.
- The live EMS ETA is an estimate (observed speeds plus expected signal waits). The
  realised response time comes from the simulation.

## Next milestone: Analyze Response

1. `ScenarioService`: snapshot the live simulation, then run baseline plus candidates in
   parallel **fresh** SUMO processes (the proven deterministic branching path) for a 5–10
   minute horizon, with an EMS probe dispatched in every branch.
2. Candidate generation from `MockAgentProvider`: upstream metering, cross-street relief,
   downstream flush, plus a **green-corridor preemption** controller in
   `simulation/controllers/`. Every policy passes `SafetyValidator` first; rejected
   plans are shown as rejected.
3. `POST /api/scenarios/run`, `GET /api/scenarios/{id}`, and progress over the WebSocket.
4. UI: candidate cards, a baseline-vs-candidate comparison (delay, max queue, throughput,
   EMS ETA) and a recommendation with its evidence.
5. Then wrap `TrafficSimulation` + `ScenarioService` as MCP tools and start on
   `NemotronAgentProvider`.
