# Spec: scenario engine as MCP tools

Status: **implemented** · Branch: `feature/scenario-engine`

## Goal and demo impact
A Nemotron agent, not the fixed mock pipeline, drives incident analysis. It reads the incident, proposes response plans, tests them in parallel SUMO branches, looks at the numbers, iterates if it wants to, and submits a recommendation. On stage, the UI shows each experiment the agent runs as it happens, which is the "the AI tests before it recommends" story.

This spec covers only the **MCP server side**, i.e. the tools. The Nemotron tool-calling loop (`agent/nemotron.py`) is the next step and consumes these tools.

## Research
- The official `mcp` Python SDK (v2 renamed `FastMCP` to `mcp.server.mcpserver.MCPServer`) mounts inside the existing FastAPI app over streamable HTTP: `app.mount("/mcp", mcp.streamable_http_app())`. Its `session_manager.run()` has to run inside FastAPI's lifespan.
- NIM does not connect to MCP servers itself. The client app lists the MCP tools, converts them to OpenAI `tools`, executes the tool calls the model makes against the MCP server, and returns the results. So the future Nemotron loop is an MCP client of this server.

## Scope
**In scope**
- `backend/app/api/mcp_tools.py` (new; not `mcp.py`, to avoid confusion with the `mcp` package): an `MCPServer` with the 5 tools below, mounted at `/mcp` in `main.py`.
- A refactor of `ScenarioService` that splits the pipeline into reusable steps:
  - `open()`: guard, resolve the incident, create the run;
  - `capture()`: take the snapshot and read the signal programs;
  - `evaluate(analysis, plans)`: validate plans, then simulate them in parallel branches;
  - `finish(analysis, recommendation)` / `fail(analysis, error)`.

  The mock "Analyze Response" path (`POST /api/scenarios/run`) keeps working unchanged, built on the same steps.

**Out of scope**
- The Nemotron loop itself.
- Frontend changes.
- `sumo.py` performance.
- Corridor and reroute implementations (response-strategies).
- Frozen contract files.

## Tools

Every tool is read-only or simulation-only, except `implement_recommendation` (added with the autonomous episode), which can apply only the run's own recommendation to the live city.

| Tool | Input | Returns | Notes |
|---|---|---|---|
| `start_analysis` | `incident_ids?` (default: **all** active incidents; was `incident_id?`), `horizon_s?` (120–1800), `memory_mode?` (`use`, default, or `ignore`) | `run_id`, and a compact incident context: `incident` (primary, earliest detected), `incidents` (every one to solve together), `standing_responses` (plans already applied to the live city; branches start with them), congested segments, signal programs (phases, durations, and per phase the approaches it serves: `serves` as compass labels, which can repeat off-grid, and `serves_segments` as incoming segment ids), EMS origin, and `experience` (the playbook and the most similar past episodes) in `use` mode | Snapshots the live network once. `ignore` is a no-recall control that persists for the learning report but is never eligible for future recall. Every later simulation in this run branches from that instant, so results stay comparable. Run status goes to `proposing`. 409 cases from the REST API map to tool errors. |
| `validate_plan` | `run_id`, `plan: CandidatePlan` | `violations[]` (empty = safe) | Dry run with no simulation. Lets the agent repair a plan before spending simulation time. |
| `simulate_plans` | `run_id`, `plans: CandidatePlan[]` | For each plan: status, delay, max queue, throughput, EMS response, violations or notes, and the delta against baseline | The baseline is simulated automatically on the first call. Unsafe plans come back `rejected` and are not simulated. Branches run in parallel. The call can be made several times per run, up to `SCENARIO_MAX_CANDIDATES` in total, and plan ids must be unique within the run. The run is `simulating` during the call and returns to `proposing` afterwards. |
| `get_analysis` | `run_id` | Full compact comparison table for the run so far | Lets the agent re-read results. |
| `submit_recommendation` | `run_id`, `candidate_id`, `summary`, `rationale[]` | The final run | The candidate must be `completed`, otherwise it's a tool error so the agent can retry. Sets the run to `completed`, logs it to the ops log, and deletes the snapshot. Applies nothing. |
| `implement_recommendation` | `run_id` | The `Implementation` (programs now running, EMS probes dispatched, staleness) | Added with the autonomous episode. No plan payload: only the completed run's recommendation, once, re-validated against the live signal programs; refused if an incident of the run was cleared. A tool error when `AGENT_MAY_IMPLEMENT=false`. |
| `recall_experience` | `run_id`, `limit?` (1–10) | The playbook and the most similar past episodes, with structured/optional semantic/ranking scores and trust state | Added with the autonomous episode. It returns an empty result for an `ignore` control. Lessons advise what to simulate first; they never replace simulating. Query embeddings are cached for an open analysis; semantic similarity never grants pruning authority. |

**UI visibility.** Tools mutate the same `ScenarioRun` and use the same `publish_scenario()`, so the existing `scenario` WebSocket messages already stream agent-driven runs to the UI with no contract change. `ScenarioRun.agent` records who drove the run.

**Lifecycle rules**
- One open run at a time, as today.
- A run left open for longer than `SCENARIO_IDLE_TIMEOUT_S` (default 300 s wall) is marked `failed` ("agent abandoned the run") and its snapshot is deleted, so an agent that crashes can't lock analysis.
- Tool results are compact JSON with rounded numbers, keeping LLM context small.

## Interface changes
- New dependency: **`mcp`** (the official SDK). It has to go into `backend/requirements.txt`, which MASTER.md marks frozen until the integration pass (see Decisions).
- New setting: `SCENARIO_IDLE_TIMEOUT_S`.
- `/mcp` is the new endpoint (streamable HTTP).
- No changes to frozen models. Tools take and return the existing `CandidatePlan`, `ScenarioRun` and `SimulationCandidate`, plus compact JSON results built in `api/mcp_tools.py`.

## Acceptance criteria
- [x] `make test` passes, and the mock `POST /api/scenarios/run` still completes end to end.
- [x] A scratch MCP client (the SDK's `ClientSession` over streamable HTTP) lists the 5 tools with JSON schemas.
- [x] A scripted agent session works through the tools: `start_analysis` → `validate_plan` (an 8 s green gives violations) → `simulate_plans` (2 timing plans; baseline included automatically) → a second `simulate_plans` call from the same snapshot → `submit_recommendation`. The run shows up over the WebSocket with its status transitions.
- [x] Errors come back as MCP tool errors, not crashes: no incident, a second concurrent run, recommending a non-completed candidate, a duplicate plan id, going over the candidate cap.
- [x] An abandoned run times out and releases the lock.
- [x] Live `sim_time` keeps advancing during tool calls.

## Decisions
1. `mcp>=2.2` was added to `backend/requirements.txt`. This is a contract change request for the integration pass, because the server can't run without it.
2. `simulate_plans` blocks until every branch finishes: 8–13 s per round today. Clients need a request timeout of 120 s or more.
3. The endpoint is served stateless with JSON responses (`stateless_http=True, json_response=True`), so there's no MCP session affinity. The route is added straight to the FastAPI router, so it's at exactly `/mcp`.
4. Agent runs go back to `proposing` after each `simulate_plans` round. The mock REST path keeps the frozen order: queued → proposing → simulating → recommending → completed.

## How to connect (for the Nemotron loop)
```python
import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

async with httpx2.AsyncClient(timeout=180) as http, \
        streamable_http_client("http://127.0.0.1:8000/mcp", http_client=http) as streams, \
        ClientSession(*streams) as session:
    init = await session.initialize()          # init.instructions = workflow guidance for the system prompt
    tools = (await session.list_tools()).tools # name, description, input_schema -> OpenAI "tools"
    result = await session.call_tool("start_analysis", {"agent": "nemotron"})
    payload = result.content[0].text           # JSON; result.is_error marks tool errors
```
