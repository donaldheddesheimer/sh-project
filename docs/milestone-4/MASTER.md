# Milestone 4: real input, a faster twin, a memory that transfers (parallel work plan)

> **Status: in progress.** Goal 3's agent-memory transfer scope is built but not run; its
> auto-revert and per-responder EMS items remain planned, as do twin-engine and VSS. This is a
> live plan, not a historical record. The [README](../../README.md) is the source of truth for status (its
> "Next: milestone 4" table); each handoff's `## Result` records what was built. The format
> follows [milestone 2](../milestone-2/MASTER.md), but that milestone's rules (file ownership,
> "frozen" files, "no new test files, run things to verify") were for that effort and do not
> carry over. The rules below do.

**Goal.** Milestone 3 built the loop: detect → analyze → implement → monitor → review →
remember. It runs on a mock city that reports its own crashes, on a twin that is slower than it
should be, with a memory that has never been measured. Milestone 4 closes those three gaps:

1. **Real input.** Incidents can come from the NVIDIA VSS Video Analytics MCP server. Each is
   matched to a road segment and lane, mirrored into the twin as a crash, and analyzed like any
   other. Oakland gets demo scripts.
2. **A faster, more truthful twin.** A branch costs less wall time. The EMS corridor's behaviour
   is explained, and the levers the README names are tried. A response can be taken back off
   the live signals safely.
3. **A memory that transfers.** Lessons about corridors and diversions are verified before they
   are trusted, recall can use embeddings, the learning is measured cold, warm and on a varied
   crash, and the REST pipeline can use Nemotron.

The work is split into **three feature branches** with disjoint file ownership. They share one
tiny contract (below), and everything else is independent.

| Branch | Handoff | In one line | Size |
|---|---|---|---|
| `feature/twin-engine` | [feature-twin-engine.md](feature-twin-engine.md) | `sumo.py` speed-up, corridor explained and improved, per-responder EMS numbers (twin side), `revert_response()`, live-safe pre-emption failure, slower live speed during analysis | 13 |
| `feature/agent-memory` | [feature-agent-memory.md](feature-agent-memory.md) | built, not run: lesson trust, embedding recall, learning report and REST `NemotronAgentProvider`; planned: auto-revert and per-responder scorecard | 13 |
| `feature/vss-input` | [feature-vss-input.md](feature-vss-input.md) | `NvidiaSmartCityProvider` on the VSS MCP tools, map matching, mirroring incidents into the twin, cameras and incident UI, Oakland demo scripts | 13 |

**Size** is a rough workload estimate (S = 1, M = 2, L = 3 points per task, listed in each
handoff), only meant to show the three parts are about equal. It is not a schedule.

## How this scope was chosen

Every item is one the README already defers: the second half of milestone 3 (NVIDIA Smart
City input), the "Later" list under the episode task list, the unticked **Measure the
learning** task, and the entries under **Current limitations**. The scope was not otherwise
agreed with the team. The [Decisions](#decisions-to-confirm) table lists what the handoffs
assume; change it before starting if you disagree.

Grouping rule: work that edits the same files, or that answers the same question, sits in the
same part.

- **twin-engine** is everything inside the SUMO twin and its plan generator.
- **agent-memory** is everything in `learning/` and the agent providers.
- **vss-input** is everything between the outside world and an `Incident`.

## Out of scope for all three

- **New tests.** Milestone-4 agents write none and run none. Five demo-readiness tests already
  exist (written at the user's request and never run; see the README's
  [Tests](../../README.md#tests)). The README's "Later" mentions more tests for
  `ScenarioService`, the MCP tools and the learning package; that item stays deferred until
  the user asks for them.
- Running or installing the full VSS Blueprint. Part 3 builds and checks the adapter against
  recorded VSS-shaped documents; a live endpoint is a bonus, not a requirement.
- Autonomous episodes on real incidents without a demo script (decision D6).
- Auth on `/mcp` or any endpoint; a second live simulation per process; real traffic demand;
  a basemap for Oakland.
- Anything that lets an agent change live signals outside the gated implementor. The
  **revert** in part 2 is triggered by the system or the operator, never by an agent tool.

## Start here

1. **Base.** `main` after PR #8 (the autonomous episode) and PR #7 (Oakland) are merged, plus
   the step-0 contract (below). Create your branch from it:
   ```bash
   git fetch origin && git switch -c feature/<name> origin/main
   ```
   The branches do not exist on `origin` yet; whoever starts a part creates it.
2. Read this file, then **only** your own handoff. Each is self-contained for its branch.
3. Work only in the files your branch owns (table below). When done, fill in the `## Result`
   section at the bottom of **your own** handoff.

To hand a branch to an agent, a prompt like this is enough:

> You own `feature/twin-engine`. Read `docs/milestone-4/MASTER.md` and
> `docs/milestone-4/feature-twin-engine.md`, then implement it. Follow the hard rules in
> `CLAUDE.md`: no new tests, nothing run, report what was not verified.

## Rules for every agent

- **The repo's hard rules apply, and they override anything in a handoff.** From
  [CLAUDE.md](../../CLAUDE.md):
  - **Never run tests, and write none unless the user asks.** No new test files, no `pytest`,
    no scratch scripts or other automated checks whose purpose is to verify behaviour. Verify by **reading** the
    code, and say plainly in your Result what was **not run**. If a check by running seems
    necessary, do not run it: put it in your handoff's *Checks for the user to run*.
  - **Static checks that are not behaviour tests are fine:** `npm --prefix frontend run lint`
    and `npm --prefix frontend run build`. Nothing that starts SUMO or the backend. If in
    doubt, ask the user.
  - **Maintain the README as the single source of information.** Every change to behaviour,
    API, settings, file layout, terminology, roadmap or decisions updates the README **in the
    same change**, and before finishing you re-read the parts you touched. One term keeps one
    meaning (its Terms table). No section may contradict another, and the same fact is not
    written in two places. Mark what is built versus planned, and what was not verified.
- **Stay inside your file ownership** (table below). If a file you do not own must change,
  do not edit it: write a *Contract change request* in your Result and work around it.
- **Providers are interfaces; agents never touch live signals.** Keep new agent-facing tools
  read-only or branch-only. The only exception is the gated implementor.
- **No new dependencies** unless unavoidable. If you add one, say so in your Result. None of
  the three parts should need one (`mcp` and `httpx2` are already installed).
- **Every setting goes in `backend/app/config.py` and `.env.example`**, in your own labelled
  section. Python 3.11+, `from __future__ import annotations`, full type hints, Pydantic v2,
  comments that explain why, about 120 columns. Frontend: TypeScript, no semicolons, single
  quotes, 2-space indent, `noUnused*` on.
- **Keep the by-hand pairs in sync** (from CLAUDE.md): `backend/app/models/*.py` ↔
  `frontend/src/api/types.ts`; `ScenarioStatus` ↔ the `STAGE` map in `useCityStream.ts`;
  `TREND_SAMPLE_S` ↔ `SAMPLE_EVERY_S`.
- **Git.** Commit on your feature branch in small, meaningful commits, ending each message with
  the attribution line your session gives you. Push only your own feature branch. **Never push
  to `main`**: merging happens by PR.
- **Do not fake results.** No made-up measurements, and no recorded run that a code path did
  not produce. Numbers in the README come from the user's runs.

### How the README is edited by three branches

The README is shared, so each branch edits only these places, and resolves a merge conflict
by keeping both sides' rows:

- **Its row** in the status table under **Next: milestone 4** (planned → built, not run).
- **Rows it adds** to the Terms, Settings and API tables and to the Repository layout.
- **Bullets it resolves or adds** under Current limitations and Risks.
- **Text elsewhere that its change makes wrong** (for example "up to 8 candidate plans").
  Whoever changes the behaviour fixes every mention, and greps for it.

The status table is already in the README (added with this plan). Do not add a second
milestone-4 section.

## Environment

**Agents do not run anything** (hard rule). This section is for the user, who runs the checks
in each handoff.

- Windows setup and commands are in [CLAUDE.md](../../CLAUDE.md#commands). In PowerShell use
  `curl.exe`, not `curl`.
- To try three branches side by side, use one git worktree per branch, and run each on its own
  ports. `.venv` and `node_modules` are git-ignored, so each worktree needs `make setup` (or
  the Windows equivalent), and its own copy of `.env`. Give each its own `MEMORY_DIR` so the
  lessons of one branch do not leak into another.

| Branch | Backend | Frontend |
|---|---|---|
| feature/twin-engine | 8001 | 5174 (`BACKEND_URL=http://127.0.0.1:8001`) |
| feature/agent-memory | 8002 | 5175 (`BACKEND_URL=http://127.0.0.1:8002`) |
| feature/vss-input | 8003 | 5176 (`BACKEND_URL=http://127.0.0.1:8003`) |

```powershell
cd backend; .venv\Scripts\python.exe -m uvicorn app.main:app --port 8001
npm --prefix frontend run dev -- --port 5174
```

## File ownership

Every path has one owner. "Frozen" means the step-0 contract: nobody edits it on a feature
branch. A path listed as **shared** is split by function or section, named in the row.

| Path | Owner |
|---|---|
| `backend/app/models/domain.py` (`EmergencyResponse`, `TrafficMetrics.emergency_responses`), `backend/app/simulation/interface.py` (`revert_response`) | **frozen** (step-0 contract) |
| `docs/milestone-4/MASTER.md` | **frozen** (the integration pass edits it) |
| `backend/app/simulation/sumo.py`, `metrics.py`, `preemption.py`, `reroute.py`, `branching.py`, `runner.py` | twin-engine |
| `backend/app/safety/**`, `simulation/controllers/README.md` | twin-engine |
| `backend/app/agent/mock.py` | twin-engine, **except** `_by_plan` and `_apply_lessons` (agent-memory) |
| `backend/app/services/scenarios.py` | **shared:** twin-engine owns `open`, `finish`, `fail`, `abandon`, `_close` (the speed hook only); agent-memory owns `lessons_source` and where `_context` reads it; vss-input owns the guard in `_resolve_incidents` |
| `backend/app/services/city.py` | **shared:** twin-engine owns the speed helpers; vss-input owns `_on_smart_city_event`, the mirroring, and the re-mirror at the end of `reset` |
| `backend/app/learning/**`, `backend/app/models/episode.py`, `backend/app/models/scenario.py` | agent-memory |
| `backend/app/agent/nemotron.py`, `backend/app/api/mcp_tools.py` | agent-memory |
| `backend/app/api/routes.py` | **shared:** agent-memory owns the revert and learning-report routes; vss-input owns `/api/cameras`, the new `/api/smart-city/status` and the one guard on `POST /api/demo/start` |
| `backend/app/providers.py` | **shared:** agent-memory owns `build_agent_provider` and `build_episode_agents`; vss-input owns `build_smart_city_provider` (including its refusal of `DEMO_SCRIPT` with a non-mock provider); twin-engine owns the one line that marks the live simulation fail-safe |
| `backend/app/smart_city/**`, `backend/app/simulation/network.py`, `backend/app/simulation/scenario.py` | vss-input |
| `backend/app/models/domain.py` beyond the contract | vss-input may add **optional** fields (with defaults) to `Incident` / `IncidentLocation`, mirrored in `types.ts` |
| `simulation/scenarios/pittsburgh_oakland/demos/**`, `simulation/scenarios/*/vss/**` (new) | vss-input |
| `backend/app/config.py`, `.env.example` | **shared:** each branch adds its own labelled section; vss-input extends the existing NVIDIA block |
| `frontend/src/components/EpisodePanel.tsx`, `components/plans/**`, `lib/plans.ts`, `hooks/useCityStream.ts`, `dev/**` | agent-memory |
| `frontend/src/components/IncidentPanel.tsx`, `components/map/**`, `MapLegend.tsx` | vss-input |
| `frontend/src/api/types.ts`, `api/client.ts`, `styles.css`, `App.tsx` | **shared:** each branch adds its own block under its own comment header |
| `README.md`, `CLAUDE.md`, `docs/architecture.md`, `docs/specs/**` | **shared:** see [how the README is edited](#how-the-readme-is-edited-by-three-branches); `CLAUDE.md` and the docs are edited only where your change makes them wrong |
| `Makefile`, `scripts/**`, `backend/requirements*.txt`, `backend/tests/**` | nobody |

## The step-0 contract

The three parts touch each other in exactly two places. Both are **additive with defaults**,
so a branch that has not landed its half does not break the others. They go in as one small
PR to `main` (models and one interface method, no behaviour) **before the branches start**.
It can be prepared on request.

**1. Per-responder EMS response** (`models/domain.py`). Twin-engine produces it for branches;
agent-memory consumes it.

```python
class EmergencyResponse(BaseModel):
    vehicle_id: str
    destination_segment: str  # the scene it was sent to: identifies the incident (ids differ between branch and live)
    dispatched_at: float
    arrived_at: float | None = None
    response_s: float | None = None  # arrived_at - max(window start, dispatched_at); None until it arrives

# TrafficMetrics gains, default empty. `emergency_vehicle_eta` is unchanged: the last responder to arrive,
# None while any has not.
emergency_responses: list[EmergencyResponse] = Field(default_factory=list)
```

**2. Reverting a response** (`simulation/interface.py`). Twin-engine implements it in
`SumoSimulation`; agent-memory calls it from the implementor. Non-abstract: the default raises
`NotImplementedError`, as the corridor and reroute methods did in milestone 2.

```python
def revert_response(self) -> list[str]:
    """Take every response back off this simulation and return human-readable notes.

    - Each intersection whose timing apply_signal_policy changed returns to its base program. The running
      phase keeps its state and its remaining time, so no signal state changes and no clearance is skipped.
    - The EMS corridor is disabled; the signals carry on with their own program clock.
    - The diversion advisory stops for future departures and its per-vehicle overrides are cleared. Vehicles
      already rerouted keep their route: drivers do not un-divert.
    - Idempotent. A simulation with no response returns []. Disruptions and responders are untouched.
    - Every signal change passes check_transition.
    """
```

**How each side degrades until the other lands.** If `emergency_responses` is empty,
agent-memory's scorecard falls back to the aggregate `ems_response_s`. If `revert_response`
raises `NotImplementedError`, agent-memory reports the revert as failed in the ops log and the
plan stays in force, which is today's behaviour.

## Terms

New terms this milestone introduces. Each branch adds its rows to the README's Terms table
when its work is built; until then this is where they are defined.

| Term | Meaning |
|---|---|
| **Revert** | Take a standing response back off the live signals (`Implementor.revert`, `TrafficSimulation.revert_response`). Recorded on the `Implementation` (`reverted_at`, `revert_reason`). Not a reset: the city, its vehicles and its incidents are untouched. |
| **Mirror** | Reflect a reported incident in the twin by injecting the matching crash. The link from incident to crash is kept so a cleared incident reopens the lanes. |
| **Map match** | Turn a lat/lon or a place name into a segment, lane and position, with the method used and a confidence (`smart_city/matching.py`). |
| **Replay client** | A VSS client that reads VSS-shaped documents from a file. Development only. Its incidents say `source: vss-replay`, so they cannot be mistaken for live data. |
| **Response check** | Code-computed evidence that a corridor or diversion did something in the live window (a pre-emption happened, vehicles were diverted). |
| **Provisional lesson** | A corridor or diversion lesson whose response checks failed or could not be computed, and that no similar episode has confirmed. Recall discounts it and the mock does not prune on it. |
| **Learning report** | The read-only aggregate of remembered episodes per script, cold against warm (`GET /api/learning/report`). |

## Decisions to confirm

The handoffs assume the **default** column. Change a default here, before starting, if you
disagree.

| # | Question | Default assumed |
|---|---|---|
| D1 | Is this the right scope for milestone 4? | Yes: the README's deferred items, with new tests excluded by the hard rule. |
| D2 | What does reverting a diversion do to vehicles already diverted? | They keep their new route; only future departures stop diverting. |
| D3 | When does auto-revert fire? | When **every** incident an applied plan covers is cleared. Plans covering a still-active incident stay, and are re-applied after the revert. |
| D4 | May the twin make emergency vehicles a "blue light" (SUMO's `bluelight` device, so surrounding traffic yields)? | **No** unless you say so. It changes the baseline physics, every recorded number and the demo fixture. The handoff documents it as the last lever. |
| D5 | The mock gets a ninth plan (`corridor-plus-divert`). | Raise the `SCENARIO_MAX_CANDIDATES` default from 8 to 9 and fix every "8" (CLAUDE.md lists them). |
| D6 | Should real (VSS) incidents start an episode with no demo script? | **No.** An operator runs Analyze Response on a mirrored incident. `POST /api/demo/start` is refused with the VSS provider. |
| D7 | Slow the live simulation while an analysis runs, to cut staleness? | **Automatic**: hold at 1× during analysis; the operator's runtime speed change wins. |
| D8 | Which NIM model ids? | The analyst uses `nvidia/nemotron-3-ultra-550b-a55b`; the scorecard reviewer uses `nvidia/nemotron-3.5-lightning-30b-a3b`. Embedding recall remains off and structured recall stays primary. |

## Integration (after all three branches land)

Merge order does not matter for correctness, because the contract defaults hold. For a
working end to end, land **twin-engine before agent-memory** (agent-memory's revert and
per-responder scorecard need its methods). **vss-input** is independent of both.

In one integration pass:

1. **Reconcile the README.** Three branches edited it; re-read every section they touched,
   and check the Terms, Settings, API and layout tables for duplicates.
2. **Reconcile `CLAUDE.md` and `docs/architecture.md`:** the "Keep in sync" list (the plan
   count), "Things that look like bugs" (the corridor, if its result changed), the provider
   and pipeline sections, and the docs map. Update `docs/specs/scenario-engine-mcp.md` if the
   MCP output changed.
3. **Resolve every Contract change request** in the three Results.
4. **The user runs the checks** in each handoff, then the learning protocol in
   [feature-agent-memory.md](feature-agent-memory.md#measure-the-learning-protocol) (the
   user's runs are the only source of the numbers the README will record).
5. **Update the README status table** (built, run or not run) and the Risks that changed.
   Move anything left over to a "Later" list. Do not tick **Measure the learning** until
   the user has reported the numbers.
