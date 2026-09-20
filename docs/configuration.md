# Configuration

Traffic Operations Center keeps credentials in the environment and operational behavior in
reviewed code or runtime controls. The application reads only Anthropic and NVIDIA credentials
from `.env`. The map and simulation speed are console controls; the analyst, the armed script
and memory are reviewed defaults in `config.py`, so the console needs no selector for them.

## API keys

Copy the safe template at the repository root:

```bash
cp .env.demo.example .env
```

Then uncomment and fill only the providers you want to use:

```dotenv
ANTHROPIC_API_KEY=your-anthropic-api-key
# Required only for an organization-level key:
# ANTHROPIC_WORKSPACE_ID=your-anthropic-workspace-id

NVIDIA_API_KEY=your-nvidia-api-key
```

The `.env` file is ignored by Git. Never put either key in `.env.demo.example`, a Docker
build argument, frontend code, or a `VITE_*` variable. A Claude web subscription is separate
from Anthropic API billing.

## Analyst selection

Which analyst and reviewer run is decided at startup from the credentials present, not in the
console. `EPISODE_ANALYST` in [`config.py`](../backend/app/config.py) defaults to `auto`:

| Credentials | `auto` selects | Analyst / reviewer models |
|---|---|---|
| `NVIDIA_API_KEY` | **Nemotron** | Analyst: `nvidia/nemotron-3-ultra-550b-a55b`; reviewer: `nvidia/nemotron-3.5-lightning-30b-a3b` |
| `ANTHROPIC_API_KEY` only | Claude | `claude-haiku-4-5-20251001` |
| None | Mock | Local rule-based analyst and reviewer |

A keyed NVIDIA deployment is therefore always Nemotron: the deterministic Mock team is not
even built while that key is attached, so the backend cannot silently present a deterministic
run as model-backed. The exact analyst and reviewer model ids are printed in the **Agent**
readout, and `GET /api/demo` returns them.

Selecting a team by hand is an API-only path (`POST /api/demo/analyst`), refused while an
episode is armed or working. To run the credit-free Mock team instead, remove `NVIDIA_API_KEY`
and restart.

A loaded credential does not prove that the key is valid, funded, or authorized for an
inference request. Model-backed failures stay visible; the backend never falls back to Mock.

## Local development

### macOS and Linux

```bash
make setup
make dev
```

Or run the services separately:

```bash
make backend
make frontend
```

`make setup` creates `backend/.venv`, installs the Python/SUMO dependencies, and installs the
frontend packages. It is safe to rerun after dependency changes.

### Windows

The Makefile and `scripts/dev.sh` use POSIX virtual-environment paths. In PowerShell, run:

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
npm --prefix frontend install
cd backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Start the frontend from a second repository-root terminal:

```powershell
npm --prefix frontend run dev
```

## Runtime controls

- **Map** switches between the synthetic 3×3 grid and Oakland, Pittsburgh.
- **Speed** changes the live simulation multiplier from 1× to 16×.
- **Arm agent** is the whole autonomous workflow: one command-bar toggle that resets the city
  and hands the next reported incident to the configured analyst with memory on. There is no
  script, memory-mode or analyst choice beside it.
- **clear**, in the Agent readout, forgets every stored lesson for a genuinely cold episode.

Reviewed defaults—including simulation horizon, branch concurrency, model IDs, safety
behavior, provider startup choices, and `AUTONOMOUS_SCRIPT` (`operator-collision`: the script
the **Arm agent** button arms)—live in
[`backend/app/config.py`](../backend/app/config.py). The application intentionally does not
accept those operational choices through `.env`; only the three credential names are read.

## Service endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Process and live-city health |
| `GET /api/state` | Current city state |
| `WS /ws/state` | Live state and workflow events |
| `GET /api/demo` | Available scripts, analysts, model ids, episode, and memory state |
| `POST /api/demo/start` | Arm autonomous response. `{}` arms `AUTONOMOUS_SCRIPT` with memory on (what the **Arm agent** button sends); `{"script": ..., "memory_mode": ...}` selects another script or a no-recall control run |
| `POST /api/demo/stop` | Disarm autonomous response |
| `POST /api/demo/analyst` | Select the analyst/reviewer team for the next episode (API only) |
| `/docs` | Interactive REST API documentation |
| `POST /mcp` | Streamable HTTP MCP endpoint |

See the [MCP specification](specs/scenario-engine-mcp.md) and [curl walkthrough](mcp-curl.md)
for the agent-facing interface.
