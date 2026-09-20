import type { ScenarioRun } from '../api/types'
import type { FixtureMode } from './fixture'
import recorded from './scenario-run.json'

// Pacing of the replay, loosely matching the real engine: 4 parallel branch workers,
// each busy for its candidate's recorded wall time (scaled so the demo stays snappy).
const WORKERS = 4
const MS_PER_WALL_SECOND = 900
const QUEUED_MS = 450
const PROPOSING_MS = 750
const RECOMMENDING_MS = 600

let replays = 0

/** The run as it ends: the recorded fixture, or a copy with a failed branch and a failed run. */
function finalRun(mode: FixtureMode): ScenarioRun {
  // the recording predates the multi-incident and episode fields
  const run = {
    incident_ids: [recorded.incident_id],
    rounds: 1,
    memory_mode: 'use',
    recalled: [],
    recall_provenance: [],
    routes: {},
    implementation: null,
    ...structuredClone(recorded),
  } as ScenarioRun
  if (mode === 'scenario-failed') {
    const divert = run.candidates.find((c) => c.id === 'divert-advisory')
    if (divert) {
      Object.assign(divert, {
        status: 'failed',
        metrics: null,
        timeline: [],
        wall_time_s: 0.12,
        notes: ['NotImplementedError: SumoSimulation.reroute_vehicles() is not implemented'],
      })
    }
    run.status = 'failed'
    run.recommendation = null
    run.error = 'agent.recommend() raised TimeoutError: no response within 30 s (fixture-simulated failure)'
  }
  return run
}

/**
 * Streams a fixture run through every status the scenario engine broadcasts:
 * queued → proposing → simulating (branches pending → running → done) → recommending → completed | failed.
 * Returns a function that cancels the replay.
 */
export function replayFixture(mode: FixtureMode, onUpdate: (run: ScenarioRun) => void): () => void {
  const final = finalRun(mode)
  const timers: ReturnType<typeof setTimeout>[] = []
  const at = (ms: number, step: () => void) => timers.push(setTimeout(step, ms))

  replays += 1
  const run: ScenarioRun = {
    ...final,
    id: `SCN-${String(replays).padStart(4, '0')}`,
    status: 'queued',
    created_at: new Date().toISOString(),
    completed_at: null,
    snapshot_sim_time: null,
    candidates: [],
    recommendation: null,
    error: null,
  }
  const emit = () => onUpdate(structuredClone(run))
  emit()

  let t = QUEUED_MS
  at(t, () => {
    run.status = 'proposing'
    run.snapshot_sim_time = final.snapshot_sim_time
    emit()
  })

  t += PROPOSING_MS
  at(t, () => {
    run.status = 'simulating'
    run.candidates = final.candidates.map((c) =>
      c.status === 'rejected'
        ? structuredClone(c)
        : { ...structuredClone(c), status: 'pending', metrics: null, timeline: [], notes: [], wall_time_s: null },
    )
    emit()
  })

  const free: number[] = Array(WORKERS).fill(t + 200)
  final.candidates.forEach((c, index) => {
    if (c.status === 'rejected') return
    const worker = free.indexOf(Math.min(...free))
    const start = free[worker]
    const end = start + (c.wall_time_s ?? 1) * MS_PER_WALL_SECOND
    free[worker] = end + 80
    at(start, () => {
      run.candidates[index].status = 'running'
      emit()
    })
    at(end, () => {
      run.candidates[index] = structuredClone(c)
      emit()
    })
  })

  t = Math.max(...free) + 200
  at(t, () => {
    run.status = 'recommending'
    emit()
  })

  at(t + RECOMMENDING_MS, () => {
    run.status = final.status
    run.recommendation = final.recommendation
    run.error = final.error
    run.completed_at = new Date().toISOString()
    emit()
  })

  return () => timers.forEach(clearTimeout)
}
