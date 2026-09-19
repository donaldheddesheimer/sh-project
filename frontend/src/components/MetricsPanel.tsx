import type { RoadSegmentState, TrafficMetrics } from '../api/types'
import type { MetricSample } from '../hooks/useCityStream'
import { compact, duration, mph } from '../lib/format'
import { Section } from './Section'
import { Sparkline } from './Sparkline'

interface Props {
  metrics: TrafficMetrics | null
  history: MetricSample[]
  reference: MetricSample | null
  segments: RoadSegmentState[]
}

interface Tile {
  label: string
  value: string
  unit?: string
  trend?: number[]
  delta?: { value: number; text: string; upIsGood: boolean } | null
  note?: string
}

const TREND_POINTS = 36

function Delta({ value, text, upIsGood }: NonNullable<Tile['delta']>) {
  if (Math.abs(value) < 1e-9) return <span className="delta flat">±0 vs pre-incident</span>
  const better = value > 0 === upIsGood
  return (
    <span className={`delta ${better ? 'better' : 'worse'}`}>
      {value > 0 ? '▲' : '▼'} {text} vs pre-incident
    </span>
  )
}

export function MetricsPanel({ metrics, history, reference, segments }: Props) {
  const recent = history.slice(-TREND_POINTS)
  const series = (key: keyof MetricSample, map: (v: number) => number = (v) => v) => recent.map((s) => map(s[key]))
  const worst = segments.find((s) => s.id === metrics?.max_queue_segment)

  const tiles: Tile[] = metrics
    ? [
        {
          label: 'Mean vehicle delay',
          value: Math.round(metrics.mean_vehicle_delay).toString(),
          unit: 's',
          trend: series('delay'),
          delta: reference && {
            value: metrics.mean_vehicle_delay - reference.delay,
            text: `${Math.abs(Math.round(metrics.mean_vehicle_delay - reference.delay))}s`,
            upIsGood: false,
          },
        },
        {
          label: 'Max queue',
          value: metrics.max_queue_length.toString(),
          unit: 'veh',
          trend: series('queue'),
          note: worst ? `${worst.name} ${worst.direction}` : 'no queue',
        },
        {
          label: 'Throughput',
          value: compact(metrics.throughput),
          unit: 'veh/h',
          trend: series('throughput'),
          delta: reference && {
            value: metrics.throughput - reference.throughput,
            text: compact(Math.abs(metrics.throughput - reference.throughput)),
            upIsGood: true,
          },
        },
        {
          label: 'Mean speed',
          value: mph(metrics.mean_speed).toFixed(1),
          unit: 'mph',
          trend: series('speed', mph),
          delta: reference && {
            value: mph(metrics.mean_speed) - mph(reference.speed),
            text: `${Math.abs(mph(metrics.mean_speed) - mph(reference.speed)).toFixed(1)} mph`,
            upIsGood: true,
          },
        },
        {
          label: 'EMS ETA',
          value: duration(metrics.emergency_vehicle_eta),
          unit: metrics.emergency_vehicle_eta == null ? undefined : 'min',
          note: metrics.emergency_vehicle_eta == null ? 'no unit en route' : 'to scene, live estimate',
        },
        {
          label: 'Vehicles in network',
          value: metrics.vehicles_in_network.toString(),
          trend: series('vehicles'),
          note: metrics.vehicles_waiting_to_enter ? `${metrics.vehicles_waiting_to_enter} waiting to enter` : undefined,
        },
      ]
    : []

  return (
    <Section title="Network performance" icon="gauge" meta={metrics ? 'live' : undefined}>
      <div className="tiles">
        {tiles.map((t) => (
          <div className="tile" key={t.label}>
            <div className="tile-label">{t.label}</div>
            <div className="tile-row">
              <div className="tile-value">
                {t.value}
                {t.unit && <span className="tile-unit">{t.unit}</span>}
              </div>
              {t.trend && <Sparkline values={t.trend} width={64} height={22} />}
            </div>
            {t.delta ? <Delta {...t.delta} /> : <span className="tile-note">{t.note ?? ' '}</span>}
          </div>
        ))}
        {!metrics && <div className="empty">Waiting for simulation…</div>}
      </div>
    </Section>
  )
}
