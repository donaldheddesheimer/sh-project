# Scenario engine: what's built

> **Snapshot from before `feature/response-strategies` merged.** The "Known limitations"
> and "For the integration pass" sections below are out of date: the EMS corridor and
> diversion now exist and the integration pass is done. The sample numbers are from that
> earlier build. For the current pipeline, measurements and limitations see
> [../architecture.md](../architecture.md#analyze-response-pipeline) and the README.

Branch `feature/scenario-engine`. Details: [handoff](feature-scenario-engine.md) · [MCP spec](../specs/scenario-engine-mcp.md)

## In one paragraph

When a collision is active, the scenario engine freezes the live SUMO city, tries candidate responses (signal timing changes, and later an EMS green corridor and diversion) in **parallel copies of the simulation**, and compares each against doing nothing. Plans that break signal safety rules are rejected before they run. The engine can be driven two ways:
- the **Analyze Response** REST endpoint, which uses the mock agent;
- an **AI agent over MCP**: five tools the upcoming Nemotron loop will call.

Nothing ever touches the live traffic signals. Every run streams to the UI over the existing WebSocket.

## How it works

```
               REST: POST /api/scenarios/run              MCP: /mcp (5 tools)
               (mock agent proposes + recommends)         (Nemotron / any MCP client)
                              \                               /
                               ▼                             ▼
                     ScenarioService  (backend/app/services/scenarios.py)
   open ──► capture ──────────────► evaluate (one or more rounds) ──────────► finish / fail
   guard,   snapshot the live SUMO   validate → reject unsafe plans             recommendation,
   incident + read all 9 signal      simulate the rest in parallel,             ops log, delete
            programs, one instant    each in a fresh SUMO process               the snapshot
                                     (backend/app/simulation/branching.py)
                               │
                               └──► every change: WebSocket {"type":"scenario"} ──► UI
```

- **Fair comparison.** Every plan in a run starts from the same snapshot, and a test ambulance is dispatched in every branch at the same moment. A branch is always a brand-new SUMO process, because SUMO is only bit-reproducible that way. Two branches from one snapshot gave identical results.
- **The live city keeps running.** Branches run on a thread pool (4 workers). The live simulation advanced normally at 16× during every analysis.
- **One analysis at a time.** An agent that walks away is timed out after `SCENARIO_IDLE_TIMEOUT_S` (300 s) so it can't block the system.

## Using it

### REST (mock agent)

| Method | Path | |
|---|---|---|
| POST | `/api/scenarios/run` | `{incident_id?, horizon_s?, ems_probe?}` → **202** run (queued). 409: no incident, or a run is in progress · 404: unknown incident · 503: simulation not ready |
| GET | `/api/scenarios` | the last 10 runs, newest first |
| GET | `/api/scenarios/{id}` | one run |
| WS | `/ws/state` | `scenario` message on every change; `hello.data.scenario` holds the latest run |

### MCP (for agents): `http://127.0.0.1:8000/mcp`, streamable HTTP

| Tool | Purpose |
|---|---|
| `start_analysis` | Freeze the city. Returns the incident, 48 segments (worst congestion first) and every signal's phases |
| `validate_plan` | Safety check without simulating |
| `simulate_plans` | Run a batch of plans in parallel. Baseline is added automatically. Can be repeated from the same snapshot, up to 8 candidates in total |
| `get_analysis` | All results so far, with deltas against the baseline |
| `submit_recommendation` | Close the run with the chosen *completed* plan and its rationale |

The server's `instructions` explain the workflow and the metrics, so they can be used directly as the agent's system prompt. The MCP spec includes a client snippet for the Nemotron loop.

### Try it

```bash
make backend
```
```bash
curl -X POST localhost:8000/api/incidents/inject -H 'content-type: application/json' -d '{}'
```
Wait about 2 simulated minutes, then:
```bash
curl -X POST localhost:8000/api/scenarios/run -H 'content-type: application/json' -d '{}'
```

## Verified

There are no new test files, per MASTER.md. Everything was verified with scratch scripts against a running backend.

| Check | Result |
|---|---|
| `make test` | 19 passed |
| Mock REST run | Completed with 4 candidates in about 16 s. Status order: queued → proposing → simulating → recommending → completed |
| MCP agent session | 5 tools listed. `validate_plan` flagged an 8 s green. Round 1 (3 plans) took 13 s, round 2 (1 plan) took 8 s. The recommendation was written to the ops log |
| Errors | Each returns a clean 409/404/503 or MCP tool error: no incident, concurrent run, unknown incident, duplicate plan id, over the cap, recommending a rejected plan, using a closed run |
| Rejected plan | "C2 phase 3: green 8s < 12s (vehicle/pedestrian minimum)", never simulated |
| Failing branch | A corridor plan shows `failed` (NotImplementedError); the rest of the run completes |
| Determinism | Two branches from one snapshot gave identical metrics and timelines |
| Idle timeout | An abandoned run failed after the timeout and a new analysis could start |
| Cleanup | Snapshot files are deleted on completion, on failure and on shutdown |

Sample result (post-crash, 600 s horizon):

| Candidate | Delay (s) | Max queue | Throughput (veh/h) | EMS | Wall (s) |
|---|---|---|---|---|---|
| baseline | 100.2 | 60 | 2658 | 4:29 | 13.1 |
| flush-downstream | 99.7 | 60 | 2610 | 4:26 | 16.7 |
| meter-upstream | 100.1 | 60 | 2646 | 4:28 | 14.7 |
| relieve-cross-street | 101.2 | 59 | 2640 | 4:13 | 15.8 |

## Known limitations

- **Timing plans barely move the numbers.** The high-impact responses, the EMS green corridor and diversion, come from `feature/response-strategies`. Until that merges they come back `failed`.
- **Branches are slow.** A branch takes about 8 s alone and 13–17 s in parallel, against the expected ~1 s. The cause is in `sumo.py` (response-strategies): `_apply_rubbernecking` makes a TraCI call per vehicle per step, plus a 1 s connect retry on startup. Until that's fixed, a full 8-candidate run takes about 30 s; lowering `SCENARIO_HORIZON_S` speeds up the demo.
- **`simulate_plans` blocks until done.** MCP clients need a request timeout of 120 s or more.
- **The Nemotron tool-calling loop (`agent/nemotron.py`) isn't written yet.** It's the next step.

## For the integration pass

- **Contract change request:** `backend/requirements.txt` gains `mcp>=2.2`, which the MCP server needs. It's SDK v2, where `FastMCP` is renamed `MCPServer`.
- **New settings** (documented in `.env.example`): `SCENARIO_HORIZON_S`, `SCENARIO_WORKERS`, `SCENARIO_SAMPLE_S`, `SCENARIO_MAX_CANDIDATES`, `SCENARIO_HISTORY`, `SCENARIO_IDLE_TIMEOUT_S`.
- **Agent runs repeat a status.** They go back to `proposing` between `simulate_plans` rounds, so the UI should allow that sequence to repeat.
- **Shared staging rule.** `CityService.dispatch_emergency` and the branch probe now use one staging rule (`probe_for_incident`): 20 m behind the crash, in the blocked lane.
