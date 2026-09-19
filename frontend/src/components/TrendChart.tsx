import { useState } from 'react'
import { useSize } from '../hooks/useSize'
import { clock } from '../lib/format'

interface Props {
  title: string
  unit: string
  points: { t: number; v: number }[]
  markers: { t: number; label: string }[]
  format?: (v: number) => string
}

const M = { top: 8, right: 10, bottom: 18, left: 34 }

function niceMax(v: number): number {
  if (v <= 0) return 1
  const exp = 10 ** Math.floor(Math.log10(v))
  const f = v / exp
  return (f <= 1 ? 1 : f <= 2 ? 2 : f <= 5 ? 5 : 10) * exp
}

/** Single-series line with a crosshair tooltip; incident times are annotated. */
export function TrendChart({ title, unit, points, markers, format = (v) => Math.round(v).toLocaleString('en-US') }: Props) {
  const [plot, { width, height }] = useSize<HTMLDivElement>()
  const [hover, setHover] = useState<number | null>(null)

  const latest = points.at(-1)
  const ready = points.length >= 2 && width > 60 && height > 40

  const t0 = points[0]?.t ?? 0
  const t1 = latest?.t ?? 1
  const yMax = niceMax(Math.max(0, ...points.map((p) => p.v)) * 1.1)
  const iw = width - M.left - M.right
  const ih = height - M.top - M.bottom
  const x = (t: number) => M.left + ((t - t0) / (t1 - t0 || 1)) * iw
  const y = (v: number) => M.top + ih - (v / yMax) * ih
  const line = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('')
  const area = `${line}L${x(t1).toFixed(1)},${y(0)}L${x(t0).toFixed(1)},${y(0)}Z`
  const ticks = [0, yMax / 2, yMax]
  const hovered = hover != null ? points[hover] : null

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const box = e.currentTarget.getBoundingClientRect()
    const t = t0 + ((e.clientX - box.left - M.left) / iw) * (t1 - t0)
    let best = 0
    for (let i = 1; i < points.length; i++) if (Math.abs(points[i].t - t) < Math.abs(points[best].t - t)) best = i
    setHover(best)
  }

  return (
    <div className="trend">
      <div className="trend-head">
        <span className="trend-title">{title}</span>
        <span className="trend-value">
          {latest ? format(latest.v) : '—'} <span className="muted">{unit}</span>
        </span>
      </div>
      <div className="trend-plot" ref={plot}>
        {!ready ? (
          <div className="trend-empty muted">Collecting samples…</div>
        ) : (
          <svg width={width} height={height} onMouseMove={onMove} onMouseLeave={() => setHover(null)} role="img" aria-label={`${title} over time`}>
            {ticks.map((v) => (
              <g key={v}>
                <line x1={M.left} x2={width - M.right} y1={y(v)} y2={y(v)} className="grid" />
                <text x={M.left - 6} y={y(v) + 3} className="axis" textAnchor="end">
                  {format(v)}
                </text>
              </g>
            ))}
            <text x={M.left} y={height - 4} className="axis">
              {clock(t0)}
            </text>
            <text x={width - M.right} y={height - 4} className="axis" textAnchor="end">
              {clock(t1)}
            </text>
            {markers
              .filter((m) => m.t >= t0 && m.t <= t1)
              .map((m) => (
                <g key={m.label}>
                  <line x1={x(m.t)} x2={x(m.t)} y1={M.top} y2={M.top + ih} className="incident-line" />
                  <text x={x(m.t) + 4} y={M.top + 9} className="incident-label">
                    {m.label}
                  </text>
                </g>
              ))}
            <path d={area} className="series-area" />
            <path d={line} className="series-line" />
            {hovered && (
              <g>
                <line x1={x(hovered.t)} x2={x(hovered.t)} y1={M.top} y2={M.top + ih} className="crosshair" />
                <circle cx={x(hovered.t)} cy={y(hovered.v)} r={4} className="series-dot" />
              </g>
            )}
          </svg>
        )}
        {ready && hovered && (
          <div className="tooltip" style={{ left: Math.min(width - 120, x(hovered.t) + 8), top: 4 }}>
            <div className="mono">{clock(hovered.t)}</div>
            <div>
              <strong>{format(hovered.v)}</strong> {unit}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
