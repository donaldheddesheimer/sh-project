import type {
  Episode,
  EpisodeStatus,
  NetworkGeometry,
  RunStatus,
  ScenarioRun,
  ScenarioStatus,
  SimulationCandidate,
  TrafficMetrics,
} from '../api/types'
import { duration } from './format'

/** Green-phase labels learned from the live stream: intersection id -> phase index -> "EB/WB". */
export type PhaseLabels = Record<string, Record<number, string>>

// ---- colors -----------------------------------------------------------------------------

// Categorical series slots (dataviz reference palette, dark steps), in their validated order.
// Validated against the panel surface with the dataviz skill's validate_palette.js.
export const SERIES = ['#3987e5', '#d95926', '#199e70', '#c98500', '#d55181', '#00a000', '#9085e9']
export const BASELINE_COLOR = '#8f99a8'
const OVERFLOW_COLOR = '#5f6b7c' // never generate a hue past the palette

/** Baseline is neutral; every other plan takes the next slot in the run's order, so colors never shift. */
export function candidateColors(run: ScenarioRun): Record<string, string> {
  const colors: Record<string, string> = {}
  let slot = 0
  for (const c of run.candidates) {
    colors[c.id] = c.id === 'baseline' ? BASELINE_COLOR : (SERIES[slot++] ?? OVERFLOW_COLOR)
  }
  return colors
}

// ---- run progress -----------------------------------------------------------------------

const TERMINAL: ReadonlySet<ScenarioStatus> = new Set(['completed', 'failed'])

export const isRunning = (run: ScenarioRun | null): run is ScenarioRun => run != null && !TERMINAL.has(run.status)

/** Branches finished (completed or failed) out of branches to simulate; rejected plans never run. */
export function branchProgress(run: ScenarioRun): { done: number; total: number } {
  const simulated = run.candidates.filter((c) => c.status !== 'rejected')
  return {
    done: simulated.filter((c) => c.status === 'completed' || c.status === 'failed').length,
    total: simulated.length,
  }
}

/** Seconds from request to recommendation, once the run is over. */
export function runWallTime(run: ScenarioRun): number | null {
  if (!run.completed_at) return null
  const s = (Date.parse(run.completed_at) - Date.parse(run.created_at)) / 1000
  return Number.isFinite(s) && s >= 0 ? s : null
}

export interface AnalyzeState {
  enabled: boolean
  label: string
  reason: string
  progress: number | null // 0..1 while a run is in flight
}

// Episode statuses in which the autonomous agent owns the response (mirrors WORKING_STATUSES in models/episode.py).
const WORKING: ReadonlySet<EpisodeStatus> = new Set(['detected', 'analyzing', 'monitoring'])

export const episodeWorking = (episode: Episode | null): episode is Episode =>
  episode != null && WORKING.has(episode.status)

// Deliberately narrower than WORKING: by `monitoring` the plan is chosen and applied, so the
// agent is measuring, not deciding. Keep this the only definition of "the agent is deciding".
const DECIDING: ReadonlySet<EpisodeStatus> = new Set(['detected', 'analyzing'])

/** True while a decision is being made: an analysis run is open, or an episode is still choosing. */
export const agentThinking = (run: ScenarioRun | null, episode: Episode | null): boolean =>
  isRunning(run) || (episode != null && DECIDING.has(episode.status))

export function analyzeState(opts: {
  connected: boolean
  runStatus: RunStatus | null
  hasIncident: boolean
  busy: boolean
  scenario: ScenarioRun | null
  episode: Episode | null
  fixture: boolean
}): AnalyzeState {
  const { connected, runStatus, hasIncident, busy, scenario, episode, fixture } = opts
  if (isRunning(scenario)) {
    const { done, total } = branchProgress(scenario)
    const label = {
      queued: 'Snapshotting…',
      proposing: 'Proposing plans…',
      simulating: `Analyzing ${done}/${total}…`,
      recommending: 'Recommending…',
      completed: '',
      failed: '',
    }[scenario.status]
    const steps = { queued: 0.05, proposing: 0.12, simulating: 0.2, recommending: 0.95, completed: 1, failed: 1 }
    const progress = scenario.status === 'simulating' && total ? 0.2 + 0.75 * (done / total) : steps[scenario.status]
    return { enabled: false, label, reason: `Analysis ${scenario.id} in progress`, progress }
  }
  if (episodeWorking(episode) && !fixture) {
    return {
      enabled: false,
      label: 'Agent responding',
      reason: `${episode.id}: the autonomous agent owns this response (${episode.status})`,
      progress: null,
    }
  }
  const base = { label: 'Analyze Response', progress: null }
  if (busy) return { ...base, enabled: false, reason: 'Waiting for the previous command' }
  if (!connected) return { ...base, enabled: false, reason: 'Not connected to the simulation' }
  if (runStatus !== 'running' && runStatus !== 'paused') return { ...base, enabled: false, reason: 'Simulation is not ready' }
  if (!hasIncident) return { ...base, enabled: false, reason: 'Needs an active incident: inject a collision first' }
  if (fixture) return { ...base, enabled: true, reason: 'Replay the recorded fixture run (no backend call)' }
  return {
    ...base,
    enabled: true,
    reason: 'Branch the live network and simulate candidate responses over a 10-minute horizon',
  }
}

// ---- what a plan does -------------------------------------------------------------------

export interface PlanChip {
  kind: 'timing' | 'corridor' | 'divert'
  label: string
  title: string
}

export function planChips(c: SimulationCandidate, phaseLabels: PhaseLabels): PlanChip[] {
  const chips: PlanChip[] = []
  for (const p of c.policies) {
    const phases = Object.entries(p.phase_durations)
      .map(([index, seconds]) => ({ index: Number(index), seconds, label: phaseLabels[p.intersection_id]?.[Number(index)] }))
      .sort((a, b) => a.index - b.index)
const named = phases.length > 0 && phases.every((ph) => ph.label)
const changes: string[] = []
if (phases.length > 0) {
  changes.push(
    named
      ? phases.map((ph) => `${ph.label} ${Math.round(ph.seconds)}s`).join(' / ')
      : `split ${phases.map((ph) => Math.round(ph.seconds)).join('/')}`,
  )
}
if (p.offset_s != null) changes.push(`offset ${Math.round(p.offset_s)}s`)
const label = `${p.intersection_id}${changes.length ? ` · ${changes.join(' · ')}` : ''}`
const detail = [
  ...phases.map((ph) => `phase ${ph.index}${ph.label ? ` (${ph.label})` : ''} ${Math.round(ph.seconds)}s green`),
  ...(p.offset_s != null ? [`offset ${Math.round(p.offset_s)}s`] : []),
].join(', ')
chips.push({ kind: 'timing', label, title: `${p.intersection_id}: ${detail || 'timing update'}. ${p.reason}` })
  }
  if (c.corridor) {
    const ids = c.corridor.intersection_ids
    chips.push({
      kind: 'corridor',
      label: ids.length ? `Green corridor · ${ids.join(', ')}` : 'Green corridor',
      title:
        `Pre-empts ${ids.length ? ids.join(', ') : 'every signal on a responder’s route'} within ` +
        `${Math.round(c.corridor.detection_distance_m)} m; conflicting green serves ≥ ${Math.round(c.corridor.min_served_green_s)}s ` +
        `first, holds ≤ ${Math.round(c.corridor.max_hold_s)}s. ${c.corridor.reason}`,
    })
  }
  for (const r of c.reroutes) {
    chips.push({
      kind: 'divert',
      label: `Divert ${Math.round(r.compliance * 100)}% around ${r.avoid_segment_ids.join(', ')}`,
      title: r.reason,
    })
  }
  return chips
}

// ---- KPIs vs baseline -------------------------------------------------------------------

export interface Kpi {
  key: 'delay' | 'queue' | 'throughput' | 'ems'
  label: string
  unit: string
  lowerIsBetter: boolean
  value: (m: TrafficMetrics) => number | null
  format: (v: number) => string
  /** Unsigned magnitude of a change, in the KPI's own units. */
  formatChange: (d: number) => string
}

const int = (v: number) => Math.round(v).toLocaleString('en-US')

export const KPIS: Kpi[] = [
  {
    key: 'delay',
    label: 'Mean delay',
    unit: 's',
    lowerIsBetter: true,
    value: (m) => m.mean_vehicle_delay,
    format: int,
    formatChange: (d) => `${int(d)} s`,
  },
  {
    key: 'queue',
    label: 'Max queue',
    unit: 'veh',
    lowerIsBetter: true,
    value: (m) => m.max_queue_length,
    format: int,
    formatChange: int,
  },
  {
    key: 'throughput',
    label: 'Throughput',
    unit: 'veh/h',
    lowerIsBetter: false,
    value: (m) => m.throughput,
    format: int,
    formatChange: int,
  },
  {
    key: 'ems',
    label: 'EMS response',
    unit: '',
    lowerIsBetter: true,
    value: (m) => m.emergency_vehicle_eta,
    format: duration,
    formatChange: duration,
  },
]

export interface Delta {
  tone: 'better' | 'worse' | 'same'
  text: string // "▼ −15 s"
  pct: string | null // "−16%"
}

const MINUS = '−'

export function kpiDelta(kpi: Kpi, value: number | null, base: number | null): Delta | null {
  if (value == null || base == null) return null
  const d = value - base
  if (Math.round(Math.abs(d)) === 0) return { tone: 'same', text: '± 0', pct: null } // every KPI shows whole units
  const shown = kpi.formatChange(Math.abs(d))
  const up = d > 0
  const pct = kpi.key === 'delay' && base ? `${up ? '+' : MINUS}${Math.abs(Math.round((d / base) * 100))}%` : null
  return {
    tone: up === !kpi.lowerIsBetter ? 'better' : 'worse',
    text: `${up ? '▲ +' : `▼ ${MINUS}`}${shown}`,
    pct,
  }
}

/** "not on scene within 10:00" when the probe never arrived. */
export const emsMissing = (run: ScenarioRun) =>
  run.ems_probe ? `not on scene within ${duration(run.horizon_s)}` : 'no EMS probe in this run'

// ---- map overlay ------------------------------------------------------------------------

export interface PlanOverlay {
  candidateId: string
  color: string
  retimed: string[]
  avoid: string[]
  /** Signals to badge for a green corridor; `assumed` when the plan covers "every signal on the route". */
  corridor: { ids: string[]; assumed: boolean } | null
}

/**
 * Signals on the direct path from the first EMS station to the incident segment, when both
 * lie on the same street and direction. The UI doesn't know the responder's real route, so
 * callers must present this as an assumption.
 */
export function assumedCorridor(network: NetworkGeometry, incidentSegmentId: string | null): string[] {
  const station = network.stations[0]
  const bySource = new Map<string, NetworkGeometry['segments']>()
  for (const s of network.segments) bySource.set(s.source, [...(bySource.get(s.source) ?? []), s])
  const target = network.segments.find((s) => s.id === incidentSegmentId)
  let segment = network.segments.find((s) => s.id === station?.segment_id)
  if (!target || !segment || segment.name !== target.name || segment.direction !== target.direction) return []
  const signalized = new Set(network.intersections.filter((i) => i.signalized).map((i) => i.id))
  const path: string[] = []
  for (let hops = 0; segment && segment.id !== target.id && hops < 32; hops++) {
    if (signalized.has(segment.destination)) path.push(segment.destination)
    segment = bySource.get(segment.destination)?.find((s) => s.name === target.name && s.direction === target.direction)
  }
  return segment?.id === target.id ? path : []
}

export function planOverlay(
  c: SimulationCandidate,
  color: string,
  network: NetworkGeometry,
  incidentSegmentId: string | null,
): PlanOverlay {
  let corridor: PlanOverlay['corridor'] = null
  if (c.corridor) {
    corridor = c.corridor.intersection_ids.length
      ? { ids: c.corridor.intersection_ids, assumed: false }
      : { ids: assumedCorridor(network, incidentSegmentId), assumed: true }
  }
  return {
    candidateId: c.id,
    color,
    retimed: c.policies.map((p) => p.intersection_id),
    avoid: c.reroutes.flatMap((r) => r.avoid_segment_ids),
    corridor,
  }
}
