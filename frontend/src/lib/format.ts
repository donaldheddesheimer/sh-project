import type { CongestionLevel, SignalColor } from '../api/types'

const MPS_TO_MPH = 2.23694
const SIM_START_HOUR = 7 // the scenario represents the AM peak starting at 07:00

export const mph = (mps: number) => mps * MPS_TO_MPH

export function clock(simSeconds: number): string {
  const total = Math.floor(simSeconds) + SIM_START_HOUR * 3600
  const h = Math.floor(total / 3600) % 24
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return [h, m, s].map((v) => String(v).padStart(2, '0')).join(':')
}

export function duration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds)) return '—'
  const s = Math.max(0, Math.round(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function compact(value: number): string {
  if (Math.abs(value) >= 10_000) return `${(value / 1000).toFixed(1)}K`
  return Math.round(value).toLocaleString('en-US')
}

// Status palette (fixed; see dataviz reference): good / warning / serious / critical.
export const STATUS = {
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
} as const

export const CONGESTION_COLOR: Record<CongestionLevel, string> = {
  free: STATUS.good,
  moderate: STATUS.warning,
  heavy: STATUS.serious,
  severe: STATUS.critical,
}

export const CONGESTION_LABEL: Record<CongestionLevel, string> = {
  free: 'Free flow',
  moderate: 'Moderate',
  heavy: 'Heavy',
  severe: 'Severe',
}

export const SIGNAL_COLOR: Record<SignalColor, string> = {
  green: '#2fd05b',
  yellow: '#fab219',
  red: '#e5484d',
}

export const DIRECTION_LABEL: Record<string, string> = {
  NB: 'Northbound',
  SB: 'Southbound',
  EB: 'Eastbound',
  WB: 'Westbound',
}
