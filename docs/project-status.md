# Project status

This page holds qualification evidence, limitations, and the remaining demo gates. It keeps
the root README focused on the product while making implementation status explicit.

## Built

- Live SUMO twins for a synthetic 3×3 city and synthetic demand over an Oakland, Pittsburgh
  street network
- Map-first React/MapLibre console with REST and WebSocket state
- Snapshot-based scenario engine that runs each candidate in a fresh SUMO process
- Deterministic validation, baseline comparison, recommendation, and gated live apply
- Streamable HTTP MCP tools for the analysis workflow
- Autonomous episode: detect, analyze, implement, monitor, review, and store a lesson
- Runtime Mock, Claude, and Nemotron analyst/reviewer selection
- NVIDIA VSS 3.2 MCP provider boundary and development replay input
- Single-container packaging for Google Cloud Run

## Validation record

- A cold Mock autonomous episode completed end to end once in the smoke suite: it applied a
  valid recommendation and stored a lesson.
- On 2026-09-20, the local console switched Oakland → 3×3 Grid → Oakland and returned to a
  running city with live vehicles and metrics.
- A local Mock stage episode completed on 2026-09-20 with eight candidates, applied
  `corridor-plus-divert`, received an `effective` verdict, and wrote a readable lesson.
- A local Nemotron stage episode completed end to end on 2026-09-20 with fallback disabled.
  It proposed a valid plan after one correction retry, completed the baseline and candidate
  branches in 16.7 seconds, applied the recommendation, monitored 600 simulated seconds, and
  stored an `effective` Nemotron-reviewed lesson. All four NIM inference calls returned 200.
  That qualification used Nemotron 3 Super. The role split to Ultra for analysis and Lightning
  for review is catalog-verified but intentionally awaits one controlled paid qualification run.
- On 2026-09-19, authenticated catalog requests returned HTTP 200 for the configured
  Anthropic and NVIDIA credentials and included the then-configured Claude and Nemotron 3
  Super IDs. On 2026-09-20, another read-only catalog check confirmed the new Ultra analyst
  and Lightning reviewer IDs. Those catalog requests did not invoke a model.
- The Oakland desktop view in the root README was visually inspected with a live collision
  and dispatched EMS responder.

## Not yet qualified

- Claude inference through a full autonomous episode
- The Cloud Run image and public demo URL
- Live NVIDIA VSS input or the VSS replay fixtures
- A warm second episode proving useful recall in a controlled comparison
- The `crash-already` and `double-crash` paths

## Current limitations

- Applied responses remain active until simulation reset; scene clearance does not revert
  them automatically.
- One backend process owns one live city, one open analysis, and one working episode.
- State is in memory except lesson files under `memory/`; Cloud Run storage is ephemeral.
- The service has no authentication, including its MCP implementation tool.
- Both networks use synthetic demand. Collision effects and congestion thresholds are model
  assumptions, not field-calibrated measurements.
- The live EMS ETA is estimated; only the simulation produces realized response time.
- A pre-emption corridor may not help once a responder is already trapped in the incident
  queue. Combined corridor-and-diversion plans address that case more directly.
- Model errors stay visible and do not silently switch to Mock.

## Demo-readiness gates

Complete these in order to minimize paid model usage:

1. Rehearse the operator flow locally with Mock.
2. Complete a cold Mock `operator-collision` episode.
3. Complete one Claude episode and retain the exact episode/error record.
4. Keep the completed Nemotron episode as the qualification record; do not rerun it unless
   a code change affects the structured provider path.
5. Deploy the Mock configuration to Cloud Run and verify UI, API, WebSocket, and analysis.
6. Attach secrets to Cloud Run and repeat one controlled model-backed episode.
7. Rehearse the final presenter reset, including clearing memory and restoring Mock.

The longer milestone history, detailed test record, implementation inventory, and prior
review notes remain available in the [archived project record](project-history.md). The active
architecture lives in [architecture.md](architecture.md).
