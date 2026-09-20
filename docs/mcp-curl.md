# Calling the MCP tools with curl

How to call the scenario-engine MCP tools by hand, with no MCP client library. Useful for
poking at the tools, reproducing an agent's bug, or scripting a check. The tool contract
(inputs, outputs, lifecycle rules) is in [specs/scenario-engine-mcp.md](specs/scenario-engine-mcp.md);
this page covers only how to reach them.

## What you are talking to

The tools are a JSON-RPC 2.0 endpoint: **`POST /mcp`** on the backend (default
`http://127.0.0.1:8000/mcp`). The server is stateless and answers with plain JSON, so:

- there is no handshake, no session id and nothing to keep between calls: every request
  stands alone (`initialize` is optional);
- there is no event stream to parse, and the response is one JSON object;
- an agent's tool call is exactly this request, made by its client program on the model's behalf.

## Before the first call

1. Start the backend (`CLAUDE.md` "Commands"). `GET /api/health` should say `"status":"running"`.
2. Stage an incident and wait for it to be reported. `start_analysis` needs an active,
   map-matched one, or it answers `no active incident to analyze`.

   ```bash
   curl -s -X POST http://127.0.0.1:8000/api/incidents/inject -H 'Content-Type: application/json' -d '{}'
   # about 30 s later (2 simulated minutes at 4x), an INC-... id is listed:
   curl -s http://127.0.0.1:8000/api/incidents
   ```

## One call

```bash
curl -s --max-time 180 -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"get_analysis","arguments":{"run_id":"SCN-0001"}}}'
```

| Part | Notes |
|---|---|
| `method` | `tools/list` lists the tools and their JSON schemas; `tools/call` calls one. `initialize` returns the workflow guidance (`instructions`) an agent is given. |
| `params.name` / `params.arguments` | The tool name and its arguments object. Send `{}` for a tool with no required arguments. |
| `Accept` | Send both types, as the MCP spec says. curl's own default (`*/*`) also works. A client that sends **no** `Accept` header, such as PowerShell's `Invoke-RestMethod`, gets `406 Not Acceptable`. |
| `--max-time` | `simulate_plans` blocks until every branch finishes (about 16-19 s for three branches here; more plans take longer). Use 120 s or more. |
| `id` | Any JSON value. The response echoes it. |

Do not `GET /mcp`. It opens an event stream that stays open, so it is not a health check
(use `GET /api/health`).

## Reading the response

A successful call returns HTTP 200:

```json
{"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"{\n  \"run_id\": \"SCN-0003\", ... }"}],"isError":false}}
```

The tool's own result is **a JSON string inside `result.content[0].text`**, so it needs a
second parse. There are two error shapes:

| What went wrong | HTTP | Where to look |
|---|---|---|
| The tool refused: an unknown id, `SCN-0004 is still running`, a bad argument type or range, an unknown tool name. (An unsafe *plan* is not an error: `simulate_plans` returns it as a `rejected` candidate with its `violations`.) | 200 | `result.isError` is `true`; `content[0].text` is `Error executing tool <name>: <reason>` (or `Unknown tool: <name>`). Argument errors carry the pydantic message. |
| The request itself is not valid MCP | 200 | `error.code` and `error.message` replace `result` (`-32601` for an unknown method). |
| Malformed JSON | 400 | `error.code` `-32700`. |
| No `Accept: application/json` | 406 | `error.code` `-32600`. |

So check `isError` before trusting `text`.

## Helpers for Git Bash, WSL, macOS and Linux

Paste once into your shell. `unwrap` needs `python` on the PATH; on Windows without one,
use `backend/.venv/Scripts/python.exe`.

```bash
MCP_URL=${MCP_URL:-http://127.0.0.1:8000/mcp}

mcp() {  # mcp <tool> [arguments-json]  ->  the raw JSON-RPC response
  local args="${2:-}"; [ -n "$args" ] || args='{}'
  curl -s --max-time 180 -X POST "$MCP_URL" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"$1\",\"arguments\":$args}}"
}

unwrap() {  # pretty-print the tool's JSON result; a tool error or a JSON-RPC error goes to stderr
  python -c '
import json, sys
r = json.load(sys.stdin)
if "error" in r:
    sys.exit("JSON-RPC error: " + json.dumps(r["error"]))
res = r["result"]
text = res["content"][0]["text"]
if res.get("isError"):
    sys.exit("TOOL ERROR: " + text)
try:
    print(json.dumps(json.loads(text), indent=2))
except ValueError:
    print(text)
'
}
```

With `jq` installed, `mcp <tool> '<args>' | jq -r '.result.content[0].text | fromjson'` does
the unwrapping (see "What was checked": this one line was not run).

## Helper for PowerShell

Windows PowerShell 5.1 strips the inner double quotes from a JSON string passed to
`curl.exe -d '...'`, and the server then answers `-32700 Parse error`. Use one of these:

- **Recommended: `Invoke-RestMethod`.** It builds the JSON from a hashtable, so nothing needs
  quoting. It sends no `Accept` header by default, so the helper sets one.

  ```powershell
  $McpUrl = 'http://127.0.0.1:8000/mcp'
  function Invoke-Mcp([string]$Tool, $Arguments = @{}) {
    $body = @{ jsonrpc = '2.0'; id = 1; method = 'tools/call'; params = @{ name = $Tool; arguments = $Arguments } } |
      ConvertTo-Json -Depth 20 -Compress
    $r = Invoke-RestMethod -Uri $McpUrl -Method Post -ContentType 'application/json' `
      -Headers @{ Accept = 'application/json, text/event-stream' } -Body $body -TimeoutSec 180
    if ($r.error) { throw "JSON-RPC error: $($r.error | ConvertTo-Json -Compress)" }
    $text = $r.result.content[0].text
    if ($r.result.isError) { throw "TOOL ERROR: $text" }
    try { $text | ConvertFrom-Json } catch { $text }
  }

  $a = Invoke-Mcp get_analysis @{ run_id = 'SCN-0003' }
  $plan = @{ id = 'flush-c2'; name = 'Flush C2'; description = 'More green for Main St EB'
             policies = @(@{ intersection_id = 'C2'; phase_durations = @{ '0' = 48; '3' = 32 }; reason = 'flush' }) }
  Invoke-Mcp validate_plan @{ run_id = 'SCN-0003'; plan = $plan }
  ```

  Wrap a one-element list in `@(...)` (as `policies` is above) so it stays an array. A tool
  error is thrown, and a malformed request (HTTP 400) throws from `Invoke-RestMethod` itself.
- **Plain curl.** Write the body to a file and send `curl.exe --data "@body.json"`. Call it
  as `curl.exe`: bare `curl` is an alias for `Invoke-WebRequest` in PowerShell.

## Walkthrough: all seven tools

Assumes the helpers above and a reported incident. The numbers are from a fresh 3x3 grid on
the mock providers (yours will differ a little); C2's greens are 40 s each in a 90 s cycle,
so shifting 8 s between them keeps the cycle at 90 s.

```bash
# 1. Freeze the city. Returns run_id, the incident, segments, every signal's phases.
mcp start_analysis '{"agent":"curl-demo"}' | unwrap > ctx.json
RUN=$(python -c 'import json;print(json.load(open("ctx.json"))["run_id"])')   # e.g. SCN-0003

# 2. Dry-run a plan against the safety limits (nothing is simulated).
mcp validate_plan "{\"run_id\":\"$RUN\",\"plan\":{\"id\":\"unsafe\",\"name\":\"Unsafe\",\"description\":\"8 s green\",\"policies\":[{\"intersection_id\":\"C2\",\"phase_durations\":{\"0\":8},\"reason\":\"probe\"}]}}" | unwrap
#   -> "safe": false, "violations": ["C2 phase 0: green 8s < 12s (vehicle/pedestrian minimum)"]

# 3. Simulate plans in parallel branches (blocks). The baseline is added on the first call.
FLUSH='{"id":"flush-c2","name":"Flush C2","description":"More green for Main St EB at C2","policies":[{"intersection_id":"C2","phase_durations":{"0":48,"3":32},"reason":"flush the queue behind the blocked lane"}]}'
DIVERT='{"id":"divert","name":"Divert advisory","description":"Avoid the blocked segment","reroutes":[{"avoid_segment_ids":["B2_C2"],"compliance":0.4,"reason":"route around the crash"}]}'
mcp simulate_plans "{\"run_id\":\"$RUN\",\"plans\":[$FLUSH,$DIVERT]}" | unwrap
#   -> baseline delay 101.0 s, flush-c2 99.3 s (-1.7), divert 82.7 s (-18.3); status "proposing"

# 4. Re-read the comparison, and ask for lessons from earlier episodes.
mcp get_analysis "{\"run_id\":\"$RUN\"}" | unwrap
mcp recall_experience "{\"run_id\":\"$RUN\",\"limit\":3}" | unwrap      # {"playbook":"","similar":[]} on a cold store

# 5. Close the analysis with a recommendation. Applies nothing.
mcp submit_recommendation "{\"run_id\":\"$RUN\",\"candidate_id\":\"divert\",\"summary\":\"Divert around B2_C2\",\"rationale\":[\"Lowest mean delay of the three\"]}" | unwrap

# 6. Apply it to the LIVE city (once). See "Cautions".
mcp implement_recommendation "{\"run_id\":\"$RUN\"}" | unwrap
#   -> "implemented_by":"agent", "diverted":4, "ems_dispatch_ids":["EMS-1"], "staleness_s":115.5
```

Plans are the `CandidatePlan` shape: `id`, `name`, `description`, and any of `policies`
(`intersection_id`, `phase_durations` as phase index to seconds, optional `offset_s`,
`reason`), `corridor` and `reroutes` (`avoid_segment_ids`, `compliance` 0-1, `reason`).
`tools/list` returns the full schema. Take phase indexes and durations from
`start_analysis`, not from this page.

## Refusals you will see

All of these are HTTP 200 with `isError: true`. Each was produced against the running backend
except the two rows marked *(code)*, whose wording is read from `services/scenarios.py`:

| Message | Cause and way out |
|---|---|
| `unknown id: nope` | No such run or incident. |
| `SCN-0004 is still running` | An analysis is open; only one at a time. Finish it with `submit_recommendation`, or wait for the idle timeout (`SCENARIO_IDLE_TIMEOUT_S`, 300 s wall by default), or reset the twin. |
| `SCN-0003 is completed; start a new analysis` | The run is over; `validate_plan`, `simulate_plans` and `submit_recommendation` only work on the open one. `get_analysis` still reads it. |
| `SCN-0004 is proposing; only a completed analysis can be implemented` | Submit a recommendation first. |
| `SCN-0003 was already implemented` | A run's plan goes live once. |
| `cannot recommend 'baseline' (unknown); pick a completed candidate` | Call `simulate_plans` first (it adds the baseline), and pick a candidate that completed, not a rejected one. |
| `SCN-0004 is still simulating` / `is already simulating` | A round is in flight. See the timeout note below. |
| `plan ids already used in SCN-0004: ...` / `has room for N more candidates (limit 9)` *(code)* | Plan ids are unique per run, and rejected plans count toward the limit of 9. |
| `no active incident to analyze` *(code)* | Inject one and wait for it to be reported. |
| `... validation error for <tool>Arguments ...` | A wrong type or an out-of-range value (`horizon_s` is 120-1800, `limit` is 1-10). |

## Cautions

- **A curl timeout does not cancel the simulation.** If curl gives up during `simulate_plans`
  (exit 28), the round keeps running on the server and the run stays `simulating` for the rest
  of its ~20 s. Meanwhile `submit_recommendation` and a second `simulate_plans` are refused.
  Wait and poll `get_analysis` until the status is `proposing`; the results are all there.
- **An abandoned analysis holds the lock.** `start_analysis` opens a run that only
  `submit_recommendation`, the idle timeout, or a reset closes. A script that dies after it
  blocks the next analysis for up to 300 s.
- **`implement_recommendation` changes the live city** (signal timing, EMS dispatch,
  diversion) and it stays until a reset. `AGENT_MAY_IMPLEMENT=false` makes the tool refuse.
  Nothing on `/mcp` authenticates callers ([README](../README.md) "Current limitations").
- **A reset invalidates open and old runs.** The reboot fails an open analysis and marks
  earlier runs as belonging to a city that is gone, so they cannot be implemented (read from
  `CLAUDE.md` and `learning/implementor.py`; not tried here).

## Other clients

An agent framework does this same call for you: the model emits a tool name and arguments,
and the client posts them to `/mcp`. The Python SDK snippet in
[specs/scenario-engine-mcp.md](specs/scenario-engine-mcp.md) ("How to connect") is the
programmatic equivalent, and the Nemotron analyst uses it (in-process by default, over HTTP
when `MCP_URL` is set). Registering the endpoint with Claude Code as an HTTP MCP server was
not tried.

## What was checked

On 2026-09-20, Windows 11, curl 8.7.1, Git Bash and Windows PowerShell 5.1, against the local
backend with the mock providers on the 3x3 grid, one incident at a time:

- **Ran and worked:** `tools/list` and `initialize` with no handshake; all seven tools through
  the Git Bash helpers (the walkthrough above, with the numbers shown); the PowerShell
  `Invoke-Mcp` helper (`get_analysis`, `validate_plan`, `simulate_plans`, `submit_recommendation`);
  every refusal in the table except the two rows marked *(code)*; the curl-timeout behavior; the
  header, quoting and status-code findings above.
  The backend log showed no traceback across the run.
- **Not run:** the `jq` line (not installed here), macOS and Linux shells, Claude Code as the
  client, Nemotron, more than one incident, the Oakland scenario, a deployed (Cloud Run) backend,
  `AGENT_MAY_IMPLEMENT=false`, and the idle timeout.
