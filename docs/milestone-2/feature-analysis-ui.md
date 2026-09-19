# Handoff: `feature/analysis-ui`

Read [MASTER.md](MASTER.md) first. It has the frozen contract, the file ownership, the
ports and the rules. This file covers only your branch.

## Context

This is a traffic operations center: FastAPI plus a live SUMO digital twin of a 3×3
downtown grid, with a React + TypeScript + Vite + MapLibre v6 console. Milestone 1 is
done: map, KPI tiles, incident card, trend charts and ops log, all live over
`/ws/state`. Read `README.md` for the demo script. The UI is a dark ops theme; the
tokens are at the top of `frontend/src/styles.css`, and the status colors
(`--good/--warning/--serious/--critical`) are reserved for state.

The **Analyze Response** button in `TopBar.tsx` is disabled, and `App.tsx` shows a
"Response plans" placeholder. You build the analysis experience. The backend endpoints
come from a parallel branch (`feature/scenario-engine`), so **build against the frozen
contract and the fixture**. Don't wait for the backend, and don't modify it.

- Types: `frontend/src/api/types.ts` (`ScenarioRun`, `SimulationCandidate`,
  `Recommendation`, and the `scenario` / `hello.data.scenario` stream messages). This
  file is frozen; put any UI-only types in your own modules.
- Endpoints and guarantees: the table in MASTER.md.
- Fixture: `docs/milestone-2/fixtures/scenario-run.json`, a completed run with 8
  candidates, one of them `rejected`. **Synthetic numbers**, for layout only.

## Goal

An operator with an active incident clicks **Analyze Response** and, within seconds,
watches candidates stream in and sees a clear, evidence-backed recommendation, plus how
every option compares with doing nothing.

### 1. Data plumbing
- `api/client.ts`: `runScenario(req?: ScenarioRunRequest)` (POST
  `/api/scenarios/run`), `getScenario(id)`, `listScenarios()`.
- `hooks/useCityStream.ts`: expose `scenario: ScenarioRun | null`.
  - Seed it from `hello.data.scenario`.
  - Replace it on every `scenario` message with the same id or a newer run.
  - Keep it across `state` messages. Clear it on a live-sim reset only if its incident
    is gone (use your judgment).
- **Fixture mode**, for development without the scenario backend:
  - Copy the fixture to `frontend/src/dev/scenario-run.json` and load it with a dynamic
    `import()`, only when the URL has `?fixture=scenario`, so it never ships in the
    main chunk path.
  - In fixture mode, **Analyze Response** replays the fixture progressively:
    `queued` → `proposing` → `simulating` (candidates flip `pending` → `running` →
    `completed`, a few hundred ms apart; the rejected one shows as rejected from the
    start) → `recommending` → `completed`.
  - Also add a way to preview a `failed` run and a `failed` candidate (e.g.
    `?fixture=scenario-failed`, mutating a copy).
  - Everything else (map, KPIs) still comes from whatever backend is running, if any.
  - Fixture mode must be impossible to hit by accident. Show a visible "FIXTURE DATA"
    badge while it is on.

### 2. Analyze Response button (`TopBar.tsx`)
- Enabled when connected, the sim is `running` or `paused`, an incident is active, and
  no run is in progress (status not `completed` or `failed`).
- While a run is in progress, the button shows its progress, e.g. `Analyzing 4/7…`.
- Surface errors (409, 503) through the existing toast.

### 3. Response plans panel
This replaces the placeholder section in `App.tsx`; delete `.plans-placeholder` from
the CSS.

**Header**
- Run status, the incident id, "branched at 07:07:00" (`clock(snapshot_sim_time)`),
  the horizon ("10 min horizon"), and the agent name.
- Total wall time once completed. "8 plans simulated in 3.2 s" is a strong demo line.

**Recommendation block** (once present)
- The recommended candidate's name, the `summary`, and the `rationale` bullets.
- Make it visually primary, but not with status colors.

**Candidate cards**, in the order the backend gives them (baseline first). Each card
shows:
- Name, description, and a status pill with an icon *and* a label (pending, running,
  completed, rejected, failed).
- What the plan does, as compact chips: `B2 split 55/25`, `Green corridor`,
  `Divert 30% around B2_C2`. Derive them from `policies`, `corridor` and `reroutes`,
  and turn phase indices into labels using the live
  `state.intersections[].phase_label` where you can.
- For completed candidates other than baseline, KPI deltas against baseline:
  - mean delay (s and %);
  - max queue (veh);
  - throughput (veh/h);
  - **EMS response** (m:ss). If it is `null` and `ems_probe` is on, show "not on scene
    within 10:00".
  - Deltas carry a sign and ▲/▼ as well as color, so meaning never relies on color
    alone. Lower is better for delay, queue and EMS; higher is better for throughput.
- Rejected cards list the validator `violations` and say "Not simulated: failed safety
  validation". This is a key demo moment, so make it legible, not an afterthought.
- Failed cards show `notes`.
- `notes` such as "3 pre-emptions (A2, B2, C2)" appear as secondary text.
- Hovering a card, or clicking it to select, drives the map overlay and the comparison
  highlight.

### 4. Comparison view (bottom area)
When a run exists, give the bottom section a toggle: **Live trends | Scenario
comparison**. The comparison contains:
- **KPI small multiples.** One small chart per KPI (delay, max queue, throughput, EMS
  response), one mark per completed candidate, with a baseline reference line and
  direct labels.
  - A horizontal dot or bar per candidate works well.
  - One axis per chart; never a dual axis.
- **Horizon timeline.** Mean delay over the horizon (`timeline[]`, `t` = sim time; show
  it as "+0 … +10 min" after `snapshot_sim_time`). Draw baseline (neutral gray), the
  recommended candidate, and the hovered/selected candidate. Reuse the look of
  `TrendChart.tsx` (crosshair and tooltip).
- Colors:
  - Give candidates categorical colors assigned **by candidate id in a fixed order**
    (the order in the run), never by rank. Baseline is always neutral.
  - Don't use the reserved status colors as series colors.
  - Use the `dataviz` skill for this: it has the procedure, the mark specs and a
    palette validator script. Run the validator against `--panel` (#0e1319) as the dark
    surface, and include its output in your Result.

### 5. Map overlay (`components/map/`)
For the hovered or selected candidate:
- a ring or halo on each intersection it retimes (`policies[].intersection_id`);
- a dashed highlight on the `reroutes[].avoid_segment_ids`;
- a "corridor" badge on the signals when `corridor` is set. The UI doesn't know the
  responder's route: if `intersection_ids` is empty, badge the station-to-incident
  Main St signals, or show a legend note. Keep it honest.

Clear the overlay when nothing is hovered or selected. Don't disturb the existing live
layers.

## Files you own

Everything under `frontend/` **except** `src/api/types.ts` (frozen), plus the
`## Result` section of this file.

**Do not touch:** anything in `backend/` or `simulation/`, the frozen contract files,
`README.md`, `docs/architecture.md`, or `Makefile`.

## Notes

- MapLibre v6 is ESM-only. The worker is wired in `CityMap.tsx`
  (`setWorkerUrl` + `?worker&url`). Street labels are HTML markers, because there is no
  glyph server. Follow the existing feature-state patterns in `style.ts` / `CityMap.tsx`
  for highlights.
- `phase_durations` keys arrive as strings (`"0"`, `"3"`), because they are JSON object
  keys.
- Formatting helpers are in `lib/format.ts` (`clock`, `duration`, `compact`, `mph`).
  Speeds are m/s on the wire; show mph as the rest of the UI does.
- The layout has a `@media (max-width: 1280px)` block. The panel must stay usable at
  1280 px wide, and the side column must scroll instead of overflowing.
- `preview_start` can't read this directory. Run Vite with background Bash:
  ```bash
  BACKEND_URL=http://127.0.0.1:8003 npx vite --port 5174 --strictPort
  ```
  Then use the browser pane's `navigate`. For live map data, run the stock backend on
  :8003 from your worktree; it has no scenario endpoints, which is what fixture mode is
  for.

## Verify (no new test files)

1. `npm run build` passes: type-check and production build, with no new lint warnings.
2. In the browser, on `http://localhost:5174/?fixture=scenario` with the backend on
   :8003:
   - inject a collision and wait for INC-0001;
   - click Analyze Response and watch the progressive replay;
   - check the recommendation, the rejected card, the deltas, the comparison toggle, the
   hover overlays on the map, and the failed variant.
   Take screenshots at 1440 and 1280 px wide.
3. Without `?fixture`, the Analyze button calls the real endpoint and shows the 404
   error from the stock backend gracefully (no crash).
4. With no run, the panel shows a useful empty state: what analysis will do, and that
   it needs an active incident.

## Definition of done

The operator flow above works end to end in fixture mode, and the code follows the
existing component style: small typed components, CSS in `styles.css` using the
tokens. Everything is committed on `feature/analysis-ui`. The Result section below is
filled in, with screenshots saved under your scratchpad and their paths listed.

## Result

Implemented the complete Analyze Response operator flow on `feature/analysis-ui`.

### Components and behavior

- Reworked the console into a dense Blueprint/Palantir-style dark operations theme with
  shared `Icon` and collapsible `Section` components.
- Added the live Analyze Response control and progress states, scenario HTTP methods,
  WebSocket scenario state, learned phase labels, and reset-safe scenario retention.
- Added `ResponsePlans`, recommendation and run summaries, candidate cards, compact plan
  chips, baseline-relative KPI deltas, safety violations, failure details, and run notes.
- Added the Live trends / Scenario comparison dock. The comparison view has four aligned
  KPI dot plots with baseline reference lines and a crosshair/tooltip horizon chart for
  baseline, the recommendation, and the focused plan.
- Added plan hover/selection previews to MapLibre: retimed-intersection halos, dashed
  avoided segments, corridor badges, and an honest assumed-route label when the backend
  supplies an empty corridor intersection list.
- Candidate colors are assigned once by backend order and never by rank; baseline stays
  neutral. Status colors remain separate from candidate identity.

### Fixture mode

- `?fixture=scenario` dynamically imports `src/dev/replay.ts` and the copied JSON fixture,
  then replays queued → proposing → simulating (four workers; pending/running/terminal
  candidates) → recommending → completed. The replay module is emitted as its own Vite
  chunk and is not in the main module path.
- `?fixture=scenario-failed` changes one branch to failed and ends the run in failed state.
- Both variants retain the live map and network stream and display a persistent
  `FIXTURE DATA` badge. Analyze still requires a connected running/paused simulation and
  an active incident.

### Palette validation

PASS against the requested dark surface `#0e1319`; all seven categorical marks exceed
the 3:1 graphical-object contrast threshold:

```
#3987e5 5.13:1
#d95926 4.80:1
#199e70 5.48:1
#c98500 6.07:1
#d55181 4.73:1
#008300 3.77:1
#9085e9 5.97:1
```

### Verification

- `npm run lint` — clean, no warnings.
- `npm run build` — clean TypeScript and production Vite build. The existing MapLibre/main
  bundle size advisory remains; no dependency was added.
- Browser-tested with the stock backend on `:8003` and Vite on `:5174`: collision →
  `INC-0001` → progressive analysis → recommendation → rejected card → KPI comparison →
  corridor/retiming and diversion overlays.
- Verified the failed-run and failed-candidate fixture. Without a fixture query, the stock
  backend's missing scenario endpoint produces the existing `Not Found` toast and no
  console error or crash.
- Screenshots: `/tmp/feature-analysis-ui-1440.png`,
  `/tmp/feature-analysis-ui-1280.png`, and
  `/tmp/feature-analysis-ui-failed-1280.png`.

### Integration notes

- No frozen contract, backend, simulation, dependency, or package-lock changes.
- No contract change requests and no functional deviations from the handoff.
