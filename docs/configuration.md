# Configuration

Traffic Operations Center keeps credentials in the environment and operational behavior in
reviewed code or runtime controls. The application reads only Anthropic and NVIDIA credentials
from `.env`; maps, simulation speed, demo scripts, memory mode, and analyst selection are
controlled in the console or API.

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

## Runtime analyst selection

With no NVIDIA key, the backend starts with the credit-free Mock team. An NVIDIA key makes
Nemotron the startup team; an Anthropic key adds Claude. Open **Agent** and change the
**Analyst** selector between episodes when more than one configured team is available:

| Selection | Credential | Analyst / reviewer models |
|---|---|---|
| Mock | None | Local rule-based analyst and reviewer |
| Claude | `ANTHROPIC_API_KEY` | `claude-haiku-4-5-20251001` |
| Nemotron | `NVIDIA_API_KEY` | Analyst: `nvidia/nemotron-3-ultra-550b-a55b`; reviewer: `nvidia/nemotron-3.5-lightning-30b-a3b` |

The large analyst model is slower and busier than the reviewer: one completion may take minutes,
and the hosted endpoint answers 503 "Service temporarily overloaded" under load. `nemotron_timeout_s`
(300 s) is the limit for a single completion, and a retryable status is retried three times with a
growing pause; both are reviewed defaults in `config.py`, not environment settings.

Only configured providers appear. A keyed NVIDIA deployment deliberately withholds Mock, so
it cannot silently present a deterministic run as model-backed. The exact analyst and reviewer
models are shown beside the selector. Changing the selector does not require editing `.env` or
restarting the backend, but it is disabled while an episode is armed or working.

The selector confirms that a credential was loaded; it does not prove that the key is valid,
funded, or authorized for an inference request. Run Mock first, then one controlled Claude
episode, and use Nemotron only after the shared model path is known to work.

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
- **Memory mode** chooses whether an episode may use stored lessons.
- **Analyst** chooses Mock, Claude, or Nemotron for the next episode.
- **Demo script** chooses the incident sequence; the stage path is `operator-collision`.

Reviewed defaults—including simulation horizon, branch concurrency, model IDs, safety
behavior, and provider startup choices—live in
[`backend/app/config.py`](../backend/app/config.py). The application intentionally does not
accept those operational choices through `.env`.

## Service endpoints

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Process and live-city health |
| `GET /api/state` | Current city state |
| `WS /ws/state` | Live state and workflow events |
| `GET /api/demo` | Available scripts, analysts, model label, episode, and memory state |
| `POST /api/demo/analyst` | Select the analyst/reviewer team for the next episode |
| `/docs` | Interactive REST API documentation |
| `POST /mcp` | Streamable HTTP MCP endpoint |

See the [MCP specification](specs/scenario-engine-mcp.md) and [curl walkthrough](mcp-curl.md)
for the agent-facing interface.
