import type { CSSProperties } from 'react'
import type { ScenarioRun, SimulationCandidate } from '../../api/types'
import { emsMissing, KPIS } from '../../lib/plans'

interface Props {
  run: ScenarioRun
  colors: Record<string, string>
  activeId: string | null
  selectedId: string | null
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
}

function position(value: number, values: number[]): number {
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (max === min) return 50
  return 8 + ((value - min) / (max - min)) * 84
}

function unavailable(candidate: SimulationCandidate, run: ScenarioRun): string {
  if (candidate.status === 'rejected') return 'Rejected by safety validator'
  if (run.status === 'failed' && (candidate.status === 'pending' || candidate.status === 'running')) {
    return 'Cancelled: the analysis ended before it finished'
  }
  if (candidate.status === 'failed') return candidate.notes[0] ?? 'Branch failed'
  if (candidate.status === 'running') return 'Simulation running…'
  if (candidate.status === 'pending') return 'Waiting for a worker…'
  return emsMissing(run)
}

/** Four aligned KPI dot plots. Values share a scale only within their own KPI column. */
export function KpiComparison({ run, colors, activeId, selectedId, onHover, onSelect }: Props) {
  const baseline = run.candidates.find((candidate) => candidate.id === 'baseline')

  return (
    <div className="compare-pane">
      {/* these are branch-horizon averages; the live delay in Live trends is measured differently */}
      <div className="subhead">Candidate outcomes · {Math.round(run.horizon_s / 60)}-minute horizon</div>
      <div className="kpi-matrix" role="table" aria-label="Scenario KPI comparison">
        <div role="row" style={{ display: 'contents' }}>
          <div className="km-head" role="columnheader">Plan</div>
          {KPIS.map((kpi) => (
            <div className="km-head" role="columnheader" key={kpi.key}>
              {kpi.label}
              <span className="km-dir">{kpi.lowerIsBetter ? '↓' : '↑'} better</span>
            </div>
          ))}
        </div>

        {run.candidates.map((candidate) => {
          const metrics = candidate.metrics
          const isActive = activeId === candidate.id
          const isRecommended = run.recommendation?.candidate_id === candidate.id
          const rowClass = ['km-row', isActive && 'is-active', isRecommended && 'is-rec'].filter(Boolean).join(' ')
          const style = { '--cand-color': colors[candidate.id] } as CSSProperties
          return (
            <div className={rowClass} role="row" key={candidate.id} style={style}>
              <div role="rowheader" style={{ display: 'contents' }}>
                <button
                  type="button"
                  className="km-name"
                  aria-pressed={selectedId === candidate.id}
                  onMouseEnter={() => onHover(candidate.id)}
                  onMouseLeave={() => onHover(null)}
                  onFocus={() => onHover(candidate.id)}
                  onBlur={() => onHover(null)}
                  onClick={() => onSelect(candidate.id)}
                >
                  <span className={`swatch${candidate.id === 'baseline' ? ' baseline' : ''}`} />
                  <span>{candidate.name}</span>
                </button>
              </div>

              {metrics ? (
                KPIS.map((kpi) => {
                  const value = kpi.value(metrics)
                  const values = run.candidates.flatMap((item) => {
                    if (!item.metrics) return []
                    const itemValue = kpi.value(item.metrics)
                    return itemValue == null ? [] : [itemValue]
                  })
                  const baseValue = baseline?.metrics ? kpi.value(baseline.metrics) : null
                  if (value == null) {
                    return (
                      <div className="km-cell" role="cell" key={kpi.key} title={emsMissing(run)}>
                        <span className="km-note missing">{run.ems_probe ? 'not on scene' : 'no EMS probe'}</span>
                      </div>
                    )
                  }
                  const pos = position(value, values)
                  const ref = baseValue == null ? null : position(baseValue, values)
                  return (
                    <div
                      className="km-cell"
                      role="cell"
                      key={kpi.key}
                      title={`${candidate.name}: ${kpi.format(value)}${kpi.unit ? ` ${kpi.unit}` : ''}`}
                      onMouseEnter={() => onHover(candidate.id)}
                      onMouseLeave={() => onHover(null)}
                      onClick={() => onSelect(candidate.id)}
                    >
                      <span className="km-track" aria-hidden>
                        {ref != null && <span className="km-ref" style={{ left: `${ref}%` }} />}
                        {ref != null && (
                          <span
                            className="km-stem"
                            style={{ left: `${Math.min(ref, pos)}%`, width: `${Math.abs(pos - ref)}%` }}
                          />
                        )}
                        <span
                          className={`km-dot${candidate.id === 'baseline' ? ' baseline' : ''}`}
                          style={{ left: `${pos}%` }}
                        />
                      </span>
                      <span className="km-value">{kpi.format(value)}</span>
                    </div>
                  )
                })
              ) : (
                <div className={`km-cell km-span km-note${candidate.status === 'rejected' ? ' rejected' : ''}`} role="cell">
                  {unavailable(candidate, run)}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
