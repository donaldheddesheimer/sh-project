# Architecture and design notes

## Components and boundaries

| Layer | Module | Knows about | Never knows about |
|---|---|---|---|
| UI | `frontend/` | REST + WebSocket payloads | SUMO, providers |
| API | `app/api/routes.py` | `CityService` | TraCI |
| Orchestration | `app/services/city.py` | provider **interfaces**, `TrafficSimulation`, runner | mock vs NVIDIA, SUMO specifics |
| Incidents | `app/smart_city/` | its data source | how incidents are used |
| Decisions | `app/agent/` | `IncidentContext` → `CandidatePlan` / `Recommendation` | signal hardware, TraCI |
| Safety | `app/safety/validator.py` | `SignalPolicy` + base `SignalProgram` | who proposed the policy |
| Simulation | `app/simulation/` | SUMO / TraCI | agents, LLMs, providers |

## Live data flow

```
SUMO ──TraCI──► SumoSimulation.step()          (runner thread, paced to SIM_SPEED)
                   │ subscriptions: vehicles, edges, signals, departures/arrivals
                   ▼
        LiveSimulationRunner._publish()  ≤ BROADCAST_HZ ── LiveFrame(NetworkState)
                   │ loop.call_soon_threadsafe
                   ▼
        CityService._handle_frame()      (asyncio)
           ├─ frame observers (MockSmartCityProvider.observe → incident events)
           ├─ trend sampling, responder tracking, ops events
           └─ CityState ──► ConnectionHub.broadcast() ──► /ws/state clients
```

- TraCI is blocking and not thread-safe, so **one thread owns the live connection**.
  API commands are closures queued to that thread and run between steps
  (`runner.call(lambda sim: sim.inject_collision(...))`), which gives ordered,
  race-free control.
- The WebSocket hub gives each client a small drop-oldest buffer, so a slow browser can
  never stall the simulation or other clients.
- New clients get a `hello` with the current state, the recent ops log and the metric
  trend, so a reload mid-incident still shows the pre-incident baseline.

## Simulation model (`simulation/`)

**Network.** `build_network.py` writes plain node/edge XML with real street names and
runs `netconvert`. The grid is 3×3 with 250 m blocks and 200 m approach roads. Main St
and Central Ave have 2 lanes per direction at 50 km/h; the other streets have 1 lane at
40 km/h. All 9 junctions are signalized with a 90 s fixed-time plan (40 s green, 3 s
yellow, 2 s all-red). Offsets form an **eastbound green wave** (18 s per block),
computed from each program's actual phase order, because the phase index serving Main St
differs by junction.

**Demand.** There are 132 Poisson-like flows between all boundary roads (about 3,100
veh/h). 70% of trips go straight through, and Main St eastbound (750 veh/h) is the peak
direction. The baseline is healthy: about 30 s mean delay, no teleports, and every queue
clears within a cycle.

**Habitual routing.** `device.rerouting.adaptation-interval = 0`. By default SUMO routes
each new trip with live travel times, which amounts to perfect-information diversion
that dissolves incidents instantly. Drivers here take habitual free-flow routes, and
diversion is left as an explicit response to simulate later (`simulate_reroute`).

**Collision model.** `inject_collision()` inserts two stopped `crash` vehicles in the
blocked lane (held by a SUMO stop, so it survives snapshots). Traffic on the open lanes
within −40 m…+8 m of the scene is slowed to a *pass speed* (major: 0.6 m/s, meaning
responders waving traffic past, so the open lane carries about a third of the link's
750 veh/h). The effect is re-derived every step from the disruption registry, so
restored branches reproduce it. With these settings the crash link goes severe in about
2 min and spills back two blocks in about 5 min, and network delay roughly doubles.

**Congestion index.** Per segment, raw = speed deficit × min(1, density / 0.04 veh/m),
**averaged over one 90 s signal cycle**. Normal red-light queues cancel out, while
persistent incident queues stand out. Measured on this grid, the baseline 99th
percentile is 0.39 and crash links reach 0.95+. Thresholds: free < 0.30 ≤ moderate
< 0.55 ≤ heavy < 0.80 ≤ severe.

**Metrics** (`TrafficMetrics`):
- *Live* values describe the current instant: mean accumulated time loss of vehicles in
  the network, worst halting queue, trips completed over the last 5 min as veh/h, and
  mean speed.
- *Horizon* values (from `run_for`) cover a window: time loss accrued in the window per
  vehicle served, **including time spent waiting to enter the network**, so a policy
  can't look good by blocking entries. They also include the realized EMS response time.

**EMS.** `spawn_emergency_vehicle()` routes an `ems` vehicle to a stop 20 m behind the
crash in the blocked lane. The live ETA uses smoothed segment speeds, the responder's own
recent speed on its current segment (so being stuck shows), and 10 s expected wait per
signal ahead.

**Branching and determinism.** `save_snapshot()` writes SUMO state with the RNG state
and 8-digit precision, and records disruptions and dispatches alongside it. Verified
behavior:
- Fresh SUMO processes restored from one snapshot evolve bit-identically (test:
  `test_fresh_branches_from_one_snapshot_are_identical`).
- Re-loading into a process that has already run diverges, because internal SUMO state
  leaks.

**So every candidate must run in a fresh process**, which also makes parallel candidate
evaluation natural: a 600 s horizon takes about 0.5 s headless.

## Provider boundaries

**Smart City.** `SmartCityProvider` is `start(emit)`, `list_incidents`, `get_incident`
and `list_cameras`.
- `MockSmartCityProvider` stands in for video analytics. It watches the disruptions the
  simulation models (ground truth) and reports each as an `Incident` after a detection
  delay. It fills in named location text, sensor ids of the adjacent intersection
  cameras, and object ids. It receives frames through a factory-registered observer, so
  `CityService` doesn't know it exists.
- `NvidiaSmartCityProvider` will consume the **VSS Video Analytics MCP server**, whose
  Smart City profile exposes `get_incidents`, `get_incident`, `get_sensor_ids`,
  `get_places`, `get_average_speeds`, `get_fov_histogram` and `analyze` over
  Elasticsearch `mdx-*` indices. The planned field mapping is documented in
  `app/smart_city/nvidia.py`. The missing piece is **map matching** of VSS lat/lon and
  place names to a network segment and lane, which should be its own component. Once an
  incident is matched, the digital twin can mirror it with `inject_collision()`.

**Agent.** `AgentProvider` is `propose_candidates(IncidentContext)` and
`recommend(context, results)`. The mock proposes the classic responses for a blocked
link: downstream flush, upstream metering, and cross-street relief. The validator already
caught one of its early plans shortening a green below the pedestrian minimum.
`NemotronAgentProvider` will run a tool-calling loop over NIM.

**Safety.** `RuleBasedSafetyValidator` checks min/max green (including a pedestrian
floor), non-shortened yellow and all-red clearance, cycle bounds, and offset range.
Incompatible movements can't arise, because a `SignalPolicy` changes durations and
offsets only; phase states always come from the base program. A production version
would load agency timing sheets and the conflict-monitor matrix.

## Future MCP tools

| Tool | Backed by |
|---|---|
| `get_city_state()` | `CityService.state` |
| `get_incident(id)` | `SmartCityProvider.get_incident` |
| `get_congested_segments()` | `CityState.segments` filtered by `level` |
| `snapshot_simulation()` | `TrafficSimulation.save_snapshot` (live runner) |
| `validate_signal_plan(plan)` | `SafetyValidator.validate` |
| `simulate_signal_plan(plan, horizon)` | fresh `SumoSimulation` → `restore_snapshot` → `apply_signal_policy` → `run_for` |
| `simulate_emergency_corridor(...)` | same, plus a preemption controller |
| `simulate_reroute(...)` | same, plus TraCI rerouting of a share of affected vehicles |
| `compare_scenarios(ids)` | horizon `TrafficMetrics` of completed candidates |

Nemotron gets read and simulate tools only. No tool changes live signals.
