import type { CSSProperties } from 'react'
import type { CandidateStatus, ScenarioRun, SimulationCandidate } from '../../api/types'
import { emsMissing, KPIS, kpiDelta, planChips, type PhaseLabels, type PlanChip } from '../../lib/plans'
import { Icon, type IconName } from '../Icon'

const STATUS: Record<CandidateStatus, { icon: IconName; label: string }> = {
  pending: { icon: 'pending', label: 'Pending' },
  running: { icon: 'spinner', label: 'Running' },
  completed: { icon: 'check', label: 'Completed' },
  rejected: { icon: 'ban', label: 'Rejected' },
  failed: { icon: 'error', label: 'Failed' },
}

const CHIP_ICON: Record<PlanChip['kind'], IconName> = { timing: 'signal', corridor: 'bolt', divert: 'divert' }

export function StatusPill({ status }: { status: CandidateStatus }) {
  const { icon, label } = STATUS[status]
  return (
    <span className={`status-pill pill-${status}`}>
      <Icon name={icon} size={11} className={status === 'running' ? 'spin' : undefined} />
      {label}
    </span>
  )
}

interface Props {
  run: ScenarioRun
  candidate: SimulationCandidate
  baseline: SimulationCandidate | undefined
  color: string
  recommended: boolean
  active: boolean
  selected: boolean
  phaseLabels: PhaseLabels
  names: Record<string, string>
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
}

function Kpis({ run, candidate, baseline }: Pick<Props, 'run' | 'candidate' | 'baseline'>) {
  const m = candidate.metrics
  if (!m) return null
  const isBaseline = candidate.id === 'baseline'
  return (
    <dl className="kpis">
      {KPIS.map((k) => {
        const value = k.value(m)
        const base = baseline?.metrics ? k.value(baseline.metrics) : null
        const delta = isBaseline ? null : kpiDelta(k, value, base)
        return (
          <div className="kpi-row" key={k.key}>
            <dt className="kpi-label">{k.label}</dt>
            <dd className="kpi-value">
              {value == null ? '—' : k.format(value)}
              {value != null && k.unit && <span className="kpi-unit">{k.unit}</span>}
            </dd>
            {value == null && k.key === 'ems' ? (
              <dd className="kpi-delta missing">{emsMissing(run)}</dd>
            ) : delta ? (
              <dd className={`kpi-delta ${delta.tone}`}>
                {delta.text}
                {delta.pct && <span className="kpi-pct"> ({delta.pct})</span>}
              </dd>
            ) : (
              <dd className="kpi-delta">{isBaseline ? 'reference' : ''}</dd>
            )}
          </div>
        )
      })}
    </dl>
  )
}

export function CandidateCard(props: Props) {
  const { run, candidate: c, baseline, color, recommended, active, selected, phaseLabels, names, onHover, onSelect } =
    props
  const chips = planChips(c, phaseLabels, names)
  // a failed or aborted run cancels its unfinished branches, so a card still "running" would spin forever
  const cancelled = run.status === 'failed' && (c.status === 'pending' || c.status === 'running')
  const status: CandidateStatus = cancelled ? 'failed' : c.status
  const notes = cancelled && c.notes.length === 0 ? ['Cancelled before it finished: the analysis ended first'] : c.notes
  const classes = ['cand', `status-${status}`, active && 'is-active', selected && 'is-selected'].filter(Boolean).join(' ')
  const minutes = Math.round(run.horizon_s / 60)

  return (
    <div
      className={classes}
      style={{ '--cand-color': color } as CSSProperties}
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onMouseEnter={() => onHover(c.id)}
      onMouseLeave={() => onHover(null)}
      onClick={() => onSelect(c.id)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onSelect(c.id)
        }
      }}
    >
      <div className="cand-head">
        <span className={`swatch${c.id === 'baseline' ? ' baseline' : ''}`} />
        <div className="cand-heading">
          <div className="cand-name">
            {c.name}
            {recommended && (
              <span className="tag tag-primary">
                <Icon name="star" size={10} /> Recommended
              </span>
            )}
          </div>
          <div className="cand-desc">{c.description}</div>
        </div>
        <StatusPill status={status} />
      </div>

      {chips.length > 0 && (
        <div className="chips">
          {chips.map((chip) => (
            <span className="chip" key={chip.label} title={chip.title}>
              <Icon name={CHIP_ICON[chip.kind]} size={11} />
              {chip.label}
            </span>
          ))}
        </div>
      )}

      {status === 'pending' && (
        <div className="cand-wait">
          <Icon name="pending" size={12} /> Waiting for a simulation worker
        </div>
      )}
      {status === 'running' && (
        <div className="cand-wait">
          Simulating {minutes} min horizon <div className="bar-indet" />
        </div>
      )}
      {status === 'completed' && <Kpis run={run} candidate={c} baseline={baseline} />}

      {status === 'rejected' && (
        <div className="violations">
          <div className="violations-head">
            <Icon name="shield" size={13} /> Not simulated: failed safety validation
          </div>
          <ul>
            {(c.violations.length ? c.violations : ['No details reported']).map((v) => (
              <li key={v}>
                <Icon name="ban" size={11} />
                {v}
              </li>
            ))}
          </ul>
          <div className="violations-foot">The deterministic validator blocks unsafe timings before any branch runs.</div>
        </div>
      )}

      {status === 'failed' && (
        <div className="callout callout-danger failure">
          <Icon name="error" size={14} />
          <div className="callout-body">
            <strong>{cancelled ? 'Branch cancelled' : 'Branch failed'}</strong>
            <ul>
              {(notes.length ? notes : ['No details reported']).map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      {c.status === 'completed' && (c.notes.length > 0 || c.wall_time_s != null) && (
        <div className="cand-foot">
          {c.notes.map((n) => (
            <span className="note" key={n}>
              {n}
            </span>
          ))}
          {c.wall_time_s != null && <span className="wall">{c.wall_time_s.toFixed(2)} s</span>}
        </div>
      )}
    </div>
  )
}
