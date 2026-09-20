# Handoff: `feature/vss-input`

Read [MASTER.md](MASTER.md) first. It has the step-0 contract, the file ownership, the
decisions and the rules, and the hard rules in it (no tests, nothing run, README kept
consistent) apply to every line below. This file covers only your branch.

## Context

Everything downstream of an `Incident` (Analyze Response, the MCP tools, the episode, the
memory) already works. What is missing is a **real source** of incidents. This is the second
half of milestone 3 that was deferred: "NVIDIA Smart City input, prepared but not faked".

How it works today, so you can see what has to change:

- `SmartCityProvider` (`smart_city/base.py`) is `start(emit)`, `stop`, `list_incidents`,
  `get_incident`, `list_cameras`. Nothing else in the app knows which provider it has.
- `MockSmartCityProvider` runs **backwards from what a real feed does**. The simulation holds
  the truth (a `Disruption`); the provider watches frames and reports it as an `Incident` a few
  simulated seconds later, already matched to a segment, lane and position.
- `NvidiaSmartCityProvider` (`smart_city/nvidia.py`) is a stub. Its docstring holds the
  planned mapping from a VSS incident document to an `Incident`, and says the missing piece is
  **map matching**. `SMART_CITY_PROVIDER=nvidia` fails at startup today.
- With a real feed the order flips: a report arrives, and only then does the twin have to
  *show* it. Nothing does that yet: with no crash in SUMO, a branch has nothing to clear, and a
  snapshot has no disruption to restore. **The mirroring in task 4 is the piece that makes a
  real incident analyzable.** Without it the rest is a demo of parsing.
- `ScenarioService` refuses an incident with no matched segment (409), which is the right
  behaviour for an unmatched real incident.

**The VSS schema is not verified.** The tool names (`get_incidents`, `get_incident`,
`get_sensor_ids`, `get_places`, `get_average_speeds`, `get_fov_histogram`) and the document
fields (`sensorId`, `category`, `start`, `end`, `place.name`, `place.location`, `objectIds`,
`analyticsModule.info.confidence`, the `mdx-vlm-incidents` verdict) come from the docstring in
`smart_city/nvidia.py`, which cites the VSS Blueprint's `smartcities` profile. Nothing in this
repo has talked to a VSS server. If the Blueprint's `va_mcp_server_config.yml` or docs are
available to you, **read them first and correct the docstring**. If not, treat the docstring as
the schema and keep every assumption in one mapping module (task 2), so a wrong guess is a
one-file fix. Say in your Result which one you had.

The default provider stays `mock`. Nothing here may change what the mock does.

## Goal

Six tasks. Sizes are S = 1, M = 2, L = 3 points, 13 in total.

### 1. A VSS client and the provider (L)

- **`smart_city/vss_client.py`.** A `VssClient` protocol with the calls the provider needs
  (incidents since a time, one incident, sensors, places), and two implementations:
  - `McpVssClient(url, api_key)`: an MCP **client** over streamable HTTP to `NVIDIA_VA_MCP_URL`
    (bearer header when `NVIDIA_API_KEY` is set), using the `mcp` SDK that is already a dependency.
    `learning/analysts.py` already connects to an MCP server as a client (in-process, or over HTTP
    with `MCP_URL`); follow its pattern rather than inventing one.
  - `ReplayVssClient(path)`: reads a JSON file of **VSS-shaped documents** and releases them on a
    timeline. It is a **development client, and it must be labelled as one**: its incidents say
    `source: vss-replay`, so a replayed incident can never be mistaken for live data. Each
    document may carry a `replay` block (`release_at_sim_s`, `clear_at_sim_s`) that the mapping
    ignores. Use **simulation time** as the clock so a run is reproducible at any speed (the
    provider gets it from frames, see below).
- **The provider.** `NvidiaSmartCityProvider(client, network, matcher, poll_s)`:
  - `start(emit)` runs a polling task every `VSS_POLL_S` seconds (default 5) and diffs by
    external id: a new document is `INCIDENT_DETECTED`, a changed one `INCIDENT_UPDATED`, one with
    an end time `INCIDENT_CLEARED`. `stop()` cancels it cleanly.
  - `list_incidents(include_cleared)` and `get_incident(id)` answer from the provider's own view
    (last poll), never with a fresh network call per request.
  - **Ids.** Internal ids stay `INC-0001…` because run ids, memory files and the ops log all use
    them. Keep the VSS id for traceability in a new optional `Incident.external_id` (you may add
    optional fields to `Incident`; mirror them in `types.ts`).
  - **Frames.** Unlike the mock's, this provider needs the simulation clock: to stamp
    `Incident.sim_time` and to drive the replay. Register an `observe(state)` through the
    existing observers list in `build_smart_city_provider`, as the mock does.
  - **Failure is not a crash.** A dead endpoint or a timeout degrades to "no new incidents", with
    backoff. Log once when it goes down and once when it recovers. A malformed *document* is
    narrower: skip that one and keep the rest of the batch, because the bad document stays in the
    upstream feed and failing the poll over it would stop incident input for good. The frame
    pipeline and the live twin never stop because VSS is down. Expose `status()` (ok, last success,
    last error, and how many documents the latest poll filtered or could not read) and serve it from
    a new `GET /api/smart-city/status`.
  - **Two attributes on the base class:** `simulation_is_source: bool = True` (the mock
    keeps it; this provider sets `False`), used by task 4.

### 2. Document → `Incident` mapping (M)

One module, `smart_city/vss_mapping.py`, and no assumption about a VSS field outside it.

- **Type.** Map the VSS category onto `IncidentType` (`collision`, `stalled_vehicle`,
  `wrong_way`, `congestion`). An unknown category is kept, typed as the nearest match, and
  flagged; do not drop a real report because the mapping is incomplete.
- **VLM verdict.** `VSS_REQUIRE_VLM_CONFIRMATION` (default `true`): only collisions the
  vision-language model confirmed surface (the `mdx-vlm-incidents` verdict in the docstring).
  An unconfirmed one is dropped and counted in `status()`, not silently lost.
- **Severity.** VSS incident documents do not carry the twin's severity. Use a documented rule
  with `VSS_DEFAULT_SEVERITY` (default `major`) as the fallback, and say in the module docstring
  that it is a modelling assumption.
- **The rest.** `sensorId` → `sensor_ids`, `objectIds` → `object_ids`,
  `analyticsModule.info.confidence` → `confidence`, `start`/`end` → `timestamp`/`cleared_at`,
  `place.name` → part of `location.description`, `source` from the client's name.
- **Location** is the matcher's job (task 3); the mapping passes it the raw point, place name
  and sensor ids.

### 3. The map matcher (L)

VSS reports lat/lon and place names; the twin needs a segment, lanes and a position. This is
the piece the stub's docstring calls "a dedicated matcher, not the UI".

- **`RoadNetwork` support** (`simulation/network.py`, yours). `GeoProjector` only converts
  x/y to lon/lat. Add the inverse (`to_xy`): a linear inverse for the synthetic grid, and
  `net.convertLonLat2XY` when the network has a real projection (Oakland). Add a nearest-segments
  query over the `SegmentInfo.centerline` polylines: pure Python and deterministic, so results
  do not depend on a spatial index's ordering. Skip internal (junction) edges, as `segments` already does.
- **`smart_city/matching.py`.** `match(point | place | sensors) -> MatchResult` with
  `segment_id | None`, `position_m`, `lanes`, `intersection_id`, `distance_m`, `method`
  (`geometry` | `place` | `sensor`), `confidence` and `notes`. It **never raises**; an unmatched
  report comes back with `segment_id=None` and a `reason`.
  - **Geometry:** nearest segment within `VSS_MATCH_MAX_DIST_M` (default 40 m); the position is
    the projection along the centerline, clamped like `inject_collision` does (20 m from either end).
  - **Two directions of one street** have nearly identical centerlines, and camera or GPS error is
    larger than the gap between them. Use a heading when the document has one, comparing it with the
    segment's **true** bearing. Do not apply `heading_offset_deg`: that offset exists only to rotate a
    displacement into an NB/SB/EB/WB display label (`heading_direction` in `network.py`), so applying
    it here would measure a reported true bearing against a label-frame one and be 45° out on Oakland.
    Without a heading, pick the nearer, **lower the confidence and say so in
    `notes`**; do not pretend to know.
  - **Lanes.** VSS does not say which lane. Default to lane 0 (the rightmost), or `[0]` on a
    one-lane road, and mark it `assumed`. Infer from the lateral offset only if you can justify
    the accuracy; say so in the Result either way.
  - **Place names.** `IntersectionInfo.name` reads `Central Ave & Main St` (north-south names
    first). A VSS place may say `Main Street and Central Avenue`. Normalise case, `&`/`and`/`@`,
    and the usual suffixes (St/Street, Ave/Avenue, Blvd), compare the street *sets*, then pick the
    approach.
  - **An approach is its incoming segment**, never a compass label (CLAUDE.md). NB/SB/EB/WB repeat
    at Oakland's off-grid junctions (Fifth & Neville has two SB legs), so never key a match by one.
  - **Sensors.** A camera resolves to an intersection (`Camera.intersection_id`), which bounds the
    candidate segments when no better evidence exists.
- **Confidence** is a documented function of distance, ambiguity and method; state it in the
  module docstring.

### 4. Mirror the incident into the twin (M)

`CityService._on_smart_city_event` (yours) already logs an event and calls the listeners. For
a provider whose `simulation_is_source` is `False`, add the reflection:

- **Reconcile, do not react.** Keep one function that makes the live twin agree with the
  provider's active, matched **collisions**: exactly one disruption per such incident, none for a
  cleared one. Track `incident_id → disruption_id`, and verify against `sim.list_disruptions()`
  rather than trusting the map alone. Run it on detection, on update, on clear and **after a
  reset's reboot**. Being idempotent is what stops a duplicate crash when two events race.
- **Injection** is `sim.inject_collision(segment_id, lanes, position_m, severity)` through
  `run_on_live`, the same call the operator's Inject uses (`inject_incident`). Hold
  `live_change_lock` while you do it: a reset holds it for the whole reboot, so a mirror waits and
  then lands in the new simulation. Clearing is `clear_disruption`.
- **After a reset the mirror is lost** (the reboot drops every disruption) while the provider
  still lists the incident as active. `reset_listeners` run *before* the reboot, so re-mirror at
  the end of `CityService.reset`, after `runner.reset()`. The warm-up has already run without the
  crash, so the queue builds from that moment; say so in the ops line.
- **Other types are not mirrored** (`inject_incident` only stages collisions). Log
  `INC-0003 not mirrored: stalled vehicle reported; only collisions can be staged`, in that one
  shape for every reason (an unmatched report says why the same way) and **once** per incident,
  since VSS re-reports the same one on every poll. Such an
  incident must **not be analyzable**, because a branch would run with no crash in it: add an
  optional `IncidentLocation.match` block (method, distance, confidence, notes, `mirrored`) and
  make `ScenarioService._resolve_incidents` (you own only this guard) return the existing 409 for
  an incident that is not mirrored when its provider is not the simulation.
- **Ops log:** `INC-0001 mirrored in the twin as C-ab12cd on Forbes Ave EB (matched by geometry,
  12 m; lane assumed)`.
- **Demo scripts and the real provider do not mix** (decision D6). A script injects crashes the
  provider never reports. `POST /api/demo/start` returns 409 with a clear message when
  `simulation_is_source` is false, and `build_smart_city_provider` refuses `DEMO_SCRIPT` with the
  VSS provider. You own only those two guards.

### 5. Cameras and the incident UI (S)

- **`list_cameras`** from `get_sensor_ids` and `get_places`: `Camera.location` from lat/lon and
  `intersection_id` from the matcher. `GET /api/cameras` already serves it.
- **`IncidentPanel.tsx`** hard-codes "Camera analytics monitoring 9 intersections." That is
  wrong on Oakland (33 signals) and wrong for a real feed. Derive the count from what the
  provider reports. Then show, for an incident: its **source** (`mock`, `nvidia-vss`,
  `vss-replay`), confidence, VLM-confirmed, and how it was matched (method, distance, notes,
  whether the lane was assumed) or "not matched to the road network".
- **Map** (`components/map/`): camera markers when `/api/cameras` is non-empty, with a legend
  entry. Keep it small; the mock's cameras are already served, so check they do not clutter the
  grid map.
- Types in `api/types.ts` (`Camera`, the `Incident` additions, the `match` block), in your own block.

### 6. Oakland demo scripts and VSS fixtures (M)

- **Oakland demo scripts.** `simulation/scenarios/pittsburgh_oakland/demos/` does not exist, so
  on Oakland the episode panel has nothing to arm. The loader (`simulation/scenario.py`) reads
  `<scenario_dir>/demos/*.json`, so adding files is enough. Write the four the grid has, with the
  same intent (`crash-ahead`, `crash-already` inside the 300 s warm-up, `double-crash`,
  `varied-crash`), using **Oakland's** segments. The defaults are in Oakland's `scenario.json`
  (`714429624#0`, lanes `[0, 1]`, major). The README's Oakland section names a second crash on
  Fifth Avenue westbound. Find the segment ids in
  `simulation/networks/pittsburgh_oakland/oakland.net.xml`, and justify each choice (street,
  direction, lane count) in the script's `description`. A lane index must exist on the road
  (`inject_collision` rejects one that does not).
- **VSS-shaped fixtures**, `simulation/scenarios/<city>/vss/incidents.json`, for the replay
  client, per city, each with a `replay` block: a matchable VLM-confirmed collision; a second
  collision on another street (two crashes); one the VLM did **not** confirm (must be
  filtered); one **off the network** (must come back unmatched); and one that later clears.
- **Coordinates.** You cannot run anything to compute them. For Oakland take the lat/lon of
  nodes on the chosen segment straight from `oakland.osm`. For the grid compute them from
  `scenario.json`'s `geo_origin` and the `GeoProjector` formula (metres per degree, from the
  segment's x/y in the net file), and show the arithmetic in a `_note` field. Both are
  **unverified until the user runs the replay** (Checks, below); say so.

## Also do

- **Settings** in `config.py` and `.env.example`, extending the existing NVIDIA block:
  `VSS_REPLAY_FILE` (when set, `SMART_CITY_PROVIDER=nvidia` uses the replay client and the
  provider reports as `vss-replay`), `VSS_POLL_S`, `VSS_MATCH_MAX_DIST_M`,
  `VSS_REQUIRE_VLM_CONFIRMATION`, `VSS_DEFAULT_SEVERITY`. `SMART_CITY_PROVIDER=nvidia` must start
  with either `NVIDIA_VA_MCP_URL` or `VSS_REPLAY_FILE`, and fail with a clear message otherwise.
- **README rows** (MASTER): terms Mirror, Map match and Replay client; the settings; the new route;
  the new files; the Oakland demo scripts in the scripts table; and the Current limitations bullet
  about the NVIDIA stub, which stops being true. **Do not** claim it works against a real VSS
  server: say which client was exercised, and that nothing was run.
- **`docs/architecture.md`, "Provider boundaries"**: it describes `NvidiaSmartCityProvider` in
  the future tense and says "the digital twin can mirror it with `inject_collision()`". Make it
  describe what is built.
- **Update `smart_city/nvidia.py`'s docstring** if the Blueprint says something different from it.

## Files you own

Create: `backend/app/smart_city/vss_client.py`, `vss_mapping.py`, `matching.py`;
`simulation/scenarios/pittsburgh_oakland/demos/*.json`; `simulation/scenarios/*/vss/incidents.json`.

Edit: `backend/app/smart_city/**`, `backend/app/simulation/network.py`, `simulation/scenario.py`
(only if the loader needs it), `services/city.py` (`_on_smart_city_event`, the mirroring, the
end of `reset`), `services/scenarios.py` (only the `_resolve_incidents` guard),
`api/routes.py` (`/api/cameras`, `/api/smart-city/status`, the `POST /api/demo/start` guard),
`providers.py` (`build_smart_city_provider`), optional fields on `Incident` /
`IncidentLocation` in `models/domain.py`, `config.py` and `.env.example` (your section), the
frontend files listed for you in MASTER, your rows of the README and docs, and the `## Result`
section below.

**Do not touch:** `learning/**`, `agent/**`, `simulation/sumo.py` and the rest of the twin,
`safety/**`, the frozen contract files, the episode and plan UI, and `backend/tests/`.

## Design notes and gotchas

- **One thread owns the live TraCI connection.** Mirroring goes through `run_on_live`, never a
  direct call.
- **The mirrored crash is the twin's model of it**, not the reported one: the collision's
  vehicle count, gap and pass speed (`CRASH_*`, `PASS_SPEED`) are the twin's assumptions. Say
  so in the docs; do not present the twin's numbers as observed.
- **`Incident.timestamp` is wall-clock (VSS's `start`); `sim_time` is the twin's.** Keep both,
  and never compare them.
- **A cleared incident and a reset.** A cleared incident whose disruption is already gone
  (a reset) must not raise in `clear_disruption`; reconcile handles it.
- **Concurrency.** The provider polls on the event loop; matching and mapping are CPU-light but
  the client is network I/O, so never block the loop, and never call TraCI from the poller.
- **Oakland's unsignalized junctions** have `tls_id=None`; `IntersectionInfo.name` can be a raw id
  where a junction has no named streets. The place matcher must tolerate both.
- **The mock is untouched.** `simulation_is_source` defaults to `True`; a mock run must behave
  exactly as it does today, including its ops-log lines.
- **Secrets.** The API key is a `SecretStr`; never log it or put it in a status payload.

## Verify

**By reading (you do this, and report it):**

1. Trace an incident through the whole path with each of the four fixtures: mapping, matching,
   the event, the mirror, `list_incidents`, and what `ScenarioService` does with it (analyzable, or
   409 and why). Include the reset case and the cleared case.
2. Confirm `simulation_is_source` gates *every* new behaviour, so a mock run reaches none of it.
3. Check every edit to `_on_smart_city_event`, `reset`, `_resolve_incidents` and the routes
   against the owners in MASTER: you may change only what the table gives you.
4. Read `network.py` around `GeoProjector` and `SegmentInfo.centerline` and confirm the inverse
   projection round-trips with `to_lonlat` for both projection kinds.
5. `npm --prefix frontend run lint` and `build`.

**Checks for the user to run** (write the exact commands and what to look for into your Result;
the user runs them):

1. **Replay on the grid.** `SMART_CITY_PROVIDER=nvidia`, `VSS_REPLAY_FILE=simulation/scenarios/downtown_grid/vss/incidents.json`.
   At the release time an incident appears with source `vss-replay`, its match line, and a crash
   on the map; a queue forms; **Analyze Response** completes.
2. **The filters.** The unconfirmed one never appears (and `status()` counts it); the off-network
   one is listed as not matched, and Analyze Response returns 409 for it.
3. **Clear and reset.** The clear time reopens the lanes. Reset mid-incident: the crash comes back
   in the new simulation, exactly once.
4. **Oakland.** The same on `SCENARIO_DIR=simulation/scenarios/pittsburgh_oakland`, and
   `POST /api/demo/start` for each new Oakland script with the **mock** provider (and 409 with the
   VSS one).
5. **Failure.** Point `NVIDIA_VA_MCP_URL` at a dead port: the app stays up, one warning logs, and
   `GET /api/smart-city/status` says why.
6. **A real endpoint**, only if one exists: what the server actually returned against the
   docstring's schema.

## Definition of done

Tasks 1 to 6 are written and re-read; every setting, term, route and file is in the README and
the architecture doc describes what is built; the Result says which client was exercised, whether
the schema was checked against the Blueprint, and which checks were **not run**. Everything is
committed on `feature/vss-input`.

## Result

**Built.** `McpVssClient` (streamable HTTP, optional bearer token) and the development-only
simulation-clock `ReplayVssClient`; the polling/cache/status `NvidiaSmartCityProvider`; isolated
VSS mapping and VLM filtering; geometry/place/sensor map matching; inverse geo projection and
deterministic nearest-road queries; external-incident reconciliation into the live twin; reset,
analysis and demo guards; camera and match UI; provider status API; both replay timelines; four
Oakland demo scripts; settings and documentation.

**How it hooks in.** `build_smart_city_provider` chooses MCP or replay when
`SMART_CITY_PROVIDER=nvidia`, registers the provider's frame observer for simulation time, and
leaves the mock branch unchanged. A poll maps an external id to stable `INC-NNNN`, emits a Smart
City event, and `CityService` reconciles the provider's active matched collisions on the live
runner thread while holding `live_change_lock`. The provider cache backs incident/camera routes;
`GET /api/smart-city/status` is read-only. Reconciliation clears ended reports and restores active
ones once after reset warm-up. The twin's crash vehicle count, spacing and pass speed remain model
assumptions, never observations attributed to VSS.

**Schema source.** Checked against NVIDIA's published VSS 3.2 Video Analytics MCP reference:
[`vss-query-analytics`](https://github.com/nvidia/skills/blob/main/skills/vss-query-analytics/SKILL.md)
and the [VSS alert-verification documentation](https://docs.nvidia.com/vss/3.1.0/alert-verification-service.html).
They establish the `video_analytics__*` tool names, `timestamp`/`end`, `category`, `sensorId`,
`place.name`, `objectIds`, and `info.verdict`. The compatibility mapper also accepts the older
docstring's `start` and nested analytics-module fields. No running Blueprint server was available.

**Verified by reading.** Traced the confirmed fixtures through release → mapping → geometry match
→ event → one mirrored disruption → cached incident → analyzable scenario. The second confirmed
collision becomes a separate internal incident/disruption. An unconfirmed collision stops in the
mapper and increments the status count. An off-network report remains listed with the reason, is
not mirrored, and reaches the existing 409 path. At `clear_at_sim_s`, replay adds `end`, the provider
emits clear, and reconciliation removes only its linked disruption. After reset, stale disruption
ids are checked against `list_disruptions()` and each still-active report is injected once after
warm-up. Non-collisions remain visible but unmirrored and fail the same analysis guard. Confirmed
all reflection/analysis/demo changes are gated by `simulation_is_source=False`; the mock provider's
construction, detection and cache paths are unchanged. Read both `GeoProjector` branches: the
synthetic inverse is algebraically paired with `to_lonlat`, and projected networks use SUMO's paired
`convertLonLat2XY`/`convertXY2LonLat` calls. Read every owned shared-file edit against MASTER.

**Not run / not verified.** No tests, backend, SUMO, replay, demo, MCP endpoint or real VSS endpoint
was run, as required by the repository hard rule. Neither VSS client was exercised. Fixture
coordinates and lane choices were read from XML/OSM and remain runtime-unverified. Frontend lint
and build were attempted but did not run: this checkout has no `node_modules`, so `oxlint` and
`tsc` were not found. No dependencies were added.

**Checks for the user to run** (commands, and what to look for).

1. Grid replay: `SMART_CITY_PROVIDER=nvidia VSS_REPLAY_FILE=simulation/scenarios/downtown_grid/vss/incidents.json make backend`, then `npm --prefix frontend run dev -- --port 5176`. At sim 320,
   confirm source `vss-replay`, a geometry match, one crash marker/queue, then run Analyze Response.
2. Filters: after sim 560, run `curl -s http://127.0.0.1:8000/api/smart-city/status` and
   `curl -s http://127.0.0.1:8000/api/incidents`. Confirm one filtered unconfirmed report and an
   unmatched off-network incident. POST it to `/api/scenarios/run`; expect 409.
3. Clear/reset: let sim 740 pass and confirm the clearing fixture reopens its lane. Reset between
   release and clear with `curl -X POST http://127.0.0.1:8000/api/simulation/reset`; confirm every
   still-active mirrored crash returns exactly once after warm-up.
4. Oakland replay: `SCENARIO_DIR=simulation/scenarios/pittsburgh_oakland SMART_CITY_PROVIDER=nvidia VSS_REPLAY_FILE=simulation/scenarios/pittsburgh_oakland/vss/incidents.json make backend`; repeat the
   match/mirror/analyze checks. For scripts, restart with `SMART_CITY_PROVIDER=mock` and POST each of
   `crash-ahead`, `crash-already`, `double-crash`, `varied-crash` to `/api/demo/start`. With VSS,
   the same POST must return 409.
5. Failure: `SMART_CITY_PROVIDER=nvidia NVIDIA_VA_MCP_URL=http://127.0.0.1:9/mcp make backend`.
   Confirm the app stays up, one warning is logged until recovery, and `/api/smart-city/status`
   reports the connection error while simulation frames continue.
6. Real endpoint, if one exists: set `NVIDIA_VA_MCP_URL` (and `NVIDIA_API_KEY` if required), leave
   `VSS_REPLAY_FILE` unset, compare returned documents with `vss_mapping.py`, and record any field
   correction. After `npm install`, run `npm --prefix frontend run lint` and
   `npm --prefix frontend run build`.

**Measured.** None; no user-reported runs or numbers were available.

**Contract change requests.** None.

**Deviations.** No lateral lane inference was attempted: every VSS match explicitly assumes lane
0 because the published contract supplies no lane-grade accuracy. The published VSS 3.2 reference,
not an available `va_mcp_server_config.yml` or live response, was the schema authority.
