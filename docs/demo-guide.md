# Demo guide

This guide covers the two presentation paths through Traffic Operations Center. Use the
operator path first: it demonstrates the simulation and safety story without using model
credits. The autonomous path adds Mock, Claude, or Nemotron as the analyst and reviewer.

## Before presenting

1. Complete the [local setup](../README.md#quick-start).
2. Open the console at <http://localhost:5173> and confirm the status is **running**.
3. Select **Pittsburgh** for the most recognizable map, or **3×3 Grid** for the most
   predictable timing and comparison numbers.
4. Start with **Mock** in the Agent workspace. Select Claude or Nemotron only after the Mock
   path succeeds.
5. Keep API keys in the repository-root `.env`; never show that file on screen.

The desktop console has five stable regions:

- The **command bar** selects the map and speed, controls the simulation, and exposes the
  incident, EMS, and analysis actions.
- The **workspace rail** changes between Live, Analysis, and Agent.
- The **map** always shows the live twin; scenario branches never replace it.
- The **workspace drawer** contains the controls and details for the selected view.
- The **performance dock** switches between live trends, scenario comparison, and activity.

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

The stage script is `operator-collision`: it resets the city, arms the autonomous workflow,
and waits for the presenter to inject the crash.

1. Open **Agent** and select **Mock** as the analyst.
2. Select **Operator collision**, choose **Use memory** or **Ignore memory**, and click
   **Arm**.
3. When the panel says the agent is armed, click **Inject collision** in the command bar.
4. Follow the episode strip through Detect, Analyze, Monitor, Review, and Learn. The agent
   calls the same scenario tools used by the operator, applies only a completed and validated
   recommendation, then watches the live outcome.
5. At completion, show the predicted-versus-realized scorecard, verdict, lesson, and updated
   Learning report.

The analyst selector is locked while an episode is armed or working. This prevents an episode
from changing providers halfway through. To switch, finish or stop the current episode, then
select the next provider.

## Credit-conscious provider order

| Order | Analyst | Why use it |
|---|---|---|
| 1 | Mock | Proves the UI, simulation, safety, apply, monitoring, and memory paths without an external request |
| 2 | Claude | Qualifies the shared model/MCP loop while preserving NVIDIA credits |
| 3 | Nemotron | Final parity check after the same flow works with Mock and Claude |

Model-backed failures remain visible and do not silently fall back to Mock. If a run fails,
preserve the episode error, switch back to Mock, and consult the [project status](project-status.md)
before spending another request.

## Other scripts

- `crash-ahead` injects a scheduled crash and runs without the presenter's collision click.
- `crash-already` begins with the crash already present.
- `double-crash` exercises the rule that a newer incident supersedes a working episode so
  the next analysis can cover all active incidents.

These are useful qualification paths, but `operator-collision` is the clearest staged demo.

## Presenter reset checklist

- Stop an armed episode before changing analyst.
- Use **Reset** between rehearsals; applied signal programs persist until reset.
- Clear memory from the Agent panel when a genuinely cold episode is required.
- Remember that switching maps creates a fresh live simulation and clears map-local runs,
  incidents, trends, and episode state. Durable lessons remain.
