import { useState, type CSSProperties } from 'react'
import type { MetricSample, ScenarioRun, SimulationCandidate } from '../../api/types'
import { useSize } from '../../hooks/useSize'
import { duration } from '../../lib/format'

interface Props {
  run: ScenarioRun
  colors: Record<string, string>
  activeId: string | null
}

interface Series {
  candidate: SimulationCandidate
  points: MetricSample[]
  color: string
  baseline: boolean
}

const M = { top: 8, right: 14, bottom: 20, left: 38 }

function path(points: MetricSample[], x: (t: number) => number, y: (v: number) => number): string {
  return points.map((point, index) => `${index ? 'L' : 'M'}${x(point.t).toFixed(1)},${y(point.delay).toFixed(1)}`).join('')
}

function atTime(points: MetricSample[], t: number): MetricSample | null {
  if (!points.length) return null
  let best = points[0]
  for (const point of points) if (Math.abs(point.t - t) < Math.abs(best.t - t)) best = point
  return best
}

/** Mean delay over the simulation horizon for baseline, recommendation, and focused plan. */
export function HorizonChart({ run, colors, activeId }: Props) {
  const [plot, { width, height }] = useSize<HTMLDivElement>()
  const [hoverT, setHoverT] = useState<number | null>(null)
  const ids = new Set(['baseline', run.recommendation?.candidate_id, activeId].filter((id): id is string => !!id))
  const series: Series[] = run.candidates
    .filter((candidate) => ids.has(candidate.id) && candidate.status === 'completed' && candidate.timeline.length > 0)
    .map((candidate) => ({
      candidate,
      points: candidate.timeline,
      color: colors[candidate.id],
      baseline: candidate.id === 'baseline',
    }))

  const values = series.flatMap((item) => item.points.map((point) => point.delay))
  const t0 = run.snapshot_sim_time ?? Math.min(...series.flatMap((item) => item.points.map((point) => point.t)), 0)
  const t1 = t0 + run.horizon_s
  const minValue = values.length ? Math.min(...values) : 0
  const maxValue = values.length ? Math.max(...values) : 1
  const padding = Math.max(4, (maxValue - minValue) * 0.12)
  const y0 = Math.max(0, minValue - padding)
  const y1 = maxValue + padding
  const iw = Math.max(1, width - M.left - M.right)
  const ih = Math.max(1, height - M.top - M.bottom)
  const x = (t: number) => M.left + ((t - t0) / (t1 - t0 || 1)) * iw
  const y = (value: number) => M.top + ih - ((value - y0) / (y1 - y0 || 1)) * ih
  const ready = series.length > 0 && width > 100 && height > 50
  const ticks = [y0, (y0 + y1) / 2, y1]
  const hovered = hoverT == null ? [] : series.map((item) => ({ item, point: atTime(item.points, hoverT) })).filter((x) => x.point)

  const onMove = (event: React.MouseEvent<SVGSVGElement>) => {
    const box = event.currentTarget.getBoundingClientRect()
    const ratio = Math.max(0, Math.min(1, (event.clientX - box.left - M.left) / iw))
    setHoverT(t0 + ratio * (t1 - t0))
  }

  return (
    <div className="compare-pane">
      <div className="subhead">Mean delay · simulation horizon</div>
      <div className="horizon-legend">
        {series.map((item) => (
          <span className="item" key={item.candidate.id}>
            <span
              className={`legend-stroke${item.baseline ? ' dashed' : ''}`}
              style={{ '--swatch': item.color } as CSSProperties}
            />
            <span>{item.candidate.name}</span>
          </span>
        ))}
      </div>
      <div className="horizon-plot" ref={plot}>
        {!ready ? (
          <div className="compare-empty">Timeline data appears as branches complete.</div>
        ) : (
          <svg
            width={width}
            height={height}
            role="img"
            aria-label="Mean vehicle delay across the scenario horizon"
            onMouseMove={onMove}
            onMouseLeave={() => setHoverT(null)}
          >
            {ticks.map((tick) => (
              <g key={tick}>
                <line x1={M.left} x2={width - M.right} y1={y(tick)} y2={y(tick)} className="grid" />
                <text x={M.left - 6} y={y(tick) + 3} className="axis" textAnchor="end">
                  {Math.round(tick)}
                </text>
              </g>
            ))}
            <text x={M.left} y={height - 4} className="axis">+0</text>
            <text x={width - M.right} y={height - 4} className="axis" textAnchor="end">
              +{Math.round(run.horizon_s / 60)} min
            </text>
            {series.map((item) => (
              <path
                key={item.candidate.id}
                d={path(item.points, x, y)}
                className={`horizon-line${item.baseline ? ' baseline' : ''}`}
                style={{ stroke: item.color }}
              />
            ))}
            {hoverT != null && (
              <>
                <line x1={x(hoverT)} x2={x(hoverT)} y1={M.top} y2={M.top + ih} className="crosshair" />
                {hovered.map(({ item, point }) => (
                  <circle
                    key={item.candidate.id}
                    cx={x(point!.t)}
                    cy={y(point!.delay)}
                    r={4}
                    fill={item.color}
                    stroke="var(--panel)"
                    strokeWidth={2}
                  />
                ))}
              </>
            )}
          </svg>
        )}
        {ready && hoverT != null && hovered.length > 0 && (
          <div className="tooltip" style={{ left: Math.min(width - 190, Math.max(4, x(hoverT) + 8)), top: 4 }}>
            <div className="mono">+{duration(Math.max(0, hoverT - t0))}</div>
            {hovered.map(({ item, point }) => (
              <div className="tooltip-row" key={item.candidate.id}>
                <span className="swatch" style={{ '--swatch': item.color } as CSSProperties} />
                <span>{item.candidate.name}</span>
                <strong>{Math.round(point!.delay)} s</strong>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
