# Demo guide

This guide covers the two presentation paths through Traffic Operations Center. Use the
operator path first: it demonstrates the simulation and safety story without using model
credits. The autonomous path adds Mock, Claude, or Nemotron as the analyst and reviewer.

## Before presenting

1. Complete the [local setup](../README.md#quick-start).
2. Open the console at <http://localhost:5173> and confirm the status is **running**.
3. Select **Pittsburgh** for the most recognizable map, or **3×3 Grid** for the most
   predictable timing and comparison numbers.
4. Confirm which analyst will run: open **Agent** and read the analyst name and model ids. It
   is set by the credentials in `.env`, not by a selector — an NVIDIA key means Nemotron. See
   [configuration](configuration.md#analyst-selection) to change it.
5. Keep API keys in the repository-root `.env`; never show that file on screen.

The desktop console has five stable regions:

- The **command bar** selects the map and speed, controls the simulation, arms the autonomous
  agent, and exposes the incident, EMS, and analysis actions.
- The **workspace rail** changes between Live, Analysis, and Agent.
- The **map** always shows the live twin; scenario branches never replace it.
- The **workspace drawer** contains the controls and details for the selected view.
- The **performance dock** switches between live trends, scenario comparison, activity, and model
  calls (each request to the analyst or reviewer model, with its outcome).

## Operator path: Analyze Response

Allow about three minutes.

1. In **Live**, click **Inject collision**. A collision marker appears after the simulated
   detection delay and the incident is added to the activity log.
2. Let the queue build for roughly two simulated minutes. At the default 4× speed this takes
   about 30 seconds; the command bar can temporarily raise the speed.
3. Click **Analyze Response**. The console moves to Analysis while the backend snapshots the
   current city and runs each candidate in a new SUMO process.
4. Narrate the safety boundary. The intentionally unsafe `aggressive-flush` plan is rejected
   because it requests an eight-second green, below the configured twelve-second minimum.
   Rejected plans are not simulated.
5. Use **Scenario comparison** to compare delay, maximum queue, throughput, and EMS response
   time against the baseline. Hover or select a plan to display its affected roads and
   signals on the live map.
6. Review the recommended plan and rationale. Nothing has touched the live city yet; the
   recommendation remains advisory until **Apply to live signals** is clicked.
7. Apply the plan, then optionally click **Dispatch EMS**. The application revalidates the
   recommendation against the current signal programs before installing it.
8. Click **Clear scene** to reopen the lane, or **Reset** to return to a clean city.

## Autonomous path: respond, monitor, learn

This path is one button. **Arm agent** in the command bar arms `AUTONOMOUS_SCRIPT`
(`operator-collision`): it resets the city, selects 16× speed, uses the configured analyst
with memory on, and waits for the presenter to inject the crash. There is nothing else to
choose on screen.

1. Click **Arm agent**. The button lights up, the console opens the **Agent** readout, and the
   episode strip shows `armed`.
2. Click **Inject collision** in the command bar.
3. Follow the episode strip through Detect, Analyze, Monitor, Review, and Learn. The agent
   uses the same guarded scenario engine as the operator, applies only a completed and validated
   recommendation, then watches the live outcome.
4. At completion, show the predicted-versus-realized scorecard, verdict, lesson, and updated
   Learning report.

Click **Arm agent** again to disarm: no autonomous response to the next incident. Arming a
second time resets the city, which is how the warm run in the checklist below is set up.

## Which analyst runs

The analyst and reviewer come from the credentials in `.env`, decided at startup — an NVIDIA
key means Nemotron for every episode. The **Agent** readout prints the analyst name and both
model ids, so what ran is always on screen.

| Analyst | How to get it | Why use it |
|---|---|---|
| Mock | No `NVIDIA_API_KEY` | Proves the UI, simulation, safety, apply, monitoring, and memory paths without an external request |
| Claude | `ANTHROPIC_API_KEY` only | Qualifies the shared model/MCP loop while preserving NVIDIA credits |
| Nemotron | `NVIDIA_API_KEY` | Already qualified locally on 2026-09-20; repeat only after a change to its structured provider path |

Switching means changing `.env` and restarting, or calling `POST /api/demo/analyst` between
episodes. Model-backed failures remain visible and do not silently fall back to Mock. If a run
fails, preserve the episode error and consult the [project status](project-status.md) before
spending another request.

## Other scripts

`POST /api/demo/start` with an explicit `script` arms one of these instead. They are an
API-only path; the console arms `operator-collision`, the clearest staged demo.

- `crash-ahead` injects a scheduled crash and runs without the presenter's collision click.
- `crash-already` begins with the crash already present.
- `double-crash` exercises the rule that a newer incident supersedes a working episode so
  the next analysis can cover all active incidents.

The same endpoint takes `{"memory_mode": "ignore"}` for a persisted no-recall control run,
which the console does not offer.

## Presenter reset checklist

- Use **Reset** between rehearsals; applied signal programs persist until reset.
- Clear memory with **clear** in the Agent readout when a genuinely cold episode is required.
- Remember that switching maps creates a fresh live simulation and clears map-local runs,
  incidents, trends, and episode state. Durable lessons remain.
