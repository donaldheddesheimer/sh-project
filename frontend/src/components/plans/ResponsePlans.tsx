import type { CSSProperties, ReactNode } from 'react'
import type { ScenarioRun, ScenarioStatus } from '../../api/types'
import { clock } from '../../lib/format'
import { branchProgress, isRunning, runWallTime, type PhaseLabels } from '../../lib/plans'
import { Icon, type IconName } from '../Icon'
import { Section } from '../Section'
import { CandidateCard } from './CandidateCard'

interface Props {
  run: ScenarioRun | null
  incidentId: string | null
  fixture: boolean
  busy: boolean
  colors: Record<string, string>
  phaseLabels: PhaseLabels
  activeId: string | null
  selectedId: string | null
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
  onImplement: (runId: string) => void
}

const RUN_TAG: Record<ScenarioStatus, { tone: string; icon: IconName; label: string }> = {
  queued: { tone: 'tag-primary', icon: 'spinner', label: 'Queued' },
  proposing: { tone: 'tag-primary', icon: 'spinner', label: 'Proposing' },
  simulating: { tone: 'tag-primary', icon: 'spinner', label: 'Simulating' },
  recommending: { tone: 'tag-primary', icon: 'spinner', label: 'Recommending' },
  completed: { tone: 'tag-success', icon: 'check', label: 'Completed' },
  failed: { tone: 'tag-danger', icon: 'error', label: 'Failed' },
}

const PIPELINE: { title: string; desc: string; safety?: boolean }[] = [
  { title: 'Snapshot', desc: 'Capture traffic and signal state now.' },
  { title: 'Propose', desc: 'Draft timing, corridor and diversion options.' },
  {
    title: 'Validate',
    desc: 'Block unsafe timings before any branch runs.',
    safety: true,
  },
  { title: 'Simulate', desc: 'Test safe plans in parallel for 10 minutes.' },
  { title: 'Recommend', desc: 'Compare results against doing nothing.' },
]

function EmptyState({ incidentId, fixture }: { incidentId: string | null; fixture: boolean }) {
  return (
    <div className="empty-state">
      <p>
        <strong>From incident to decision.</strong> Test response plans against a live twin snapshot.
      </p>
      <ol className="pipeline">
        {PIPELINE.map((step, i) => (
          <li key={step.title} className={`pipe-step${step.safety ? ' safety' : ''}`}>
            <span className="pipe-num mono">{step.safety ? <Icon name="shield" size={12} /> : i + 1}</span>
            <div>
              <div className="pipe-title">{step.title}</div>
              <div className="pipe-desc">{step.desc}</div>
            </div>
          </li>
        ))}
      </ol>
      {fixture ? (
        <div className="callout callout-caution">
          <Icon name="database" size={14} />
          <div className="callout-body">
            <strong>Fixture mode.</strong> Analyze Response replays a recorded run with synthetic numbers. No backend
            call is made.
          </div>
        </div>
      ) : incidentId ? (
        <div className="callout callout-success">
          <Icon name="check" size={14} />
          <div className="callout-body">
            <strong>{incidentId}</strong> is active. Ready to analyze.
          </div>
        </div>
      ) : (
        <div className="callout callout-caution">
          <Icon name="warning" size={14} />
          <div className="callout-body">
            <strong>Needs an active incident.</strong> Inject a collision, then click Analyze Response.
          </div>
        </div>
      )}
    </div>
  )
}

function RunHeader({ run }: { run: ScenarioRun }) {
  const tag = RUN_TAG[run.status]
  const running = isRunning(run)
  const { done, total } = branchProgress(run)
  const rejected = run.candidates.filter((c) => c.status === 'rejected').length
  const failed = run.candidates.filter((c) => c.status === 'failed').length
  const simulated = run.candidates.filter((c) => c.status === 'completed').length
  const wall = runWallTime(run)
  const progress = run.status === 'simulating' && total ? done / total : null

  let summary: ReactNode
  if (run.status === 'queued') summary = 'Snapshotting the live network…'
  else if (run.status === 'proposing') summary = `${run.agent} agent is proposing plans…`
  else if (run.status === 'simulating') summary = `Simulating ${done} of ${total} branches…`
  else if (run.status === 'recommending') summary = 'All branches done. Ranking the results…'
  else
    summary = (
      <>
        <strong>
          {simulated} of {run.candidates.length} plans simulated
        </strong>
        {wall != null && <> in {wall.toFixed(1)} s</>}
        {rejected > 0 && (
          <>
            <span className="sep">·</span>
            {rejected} rejected by the safety validator
          </>
        )}
        {failed > 0 && (
          <>
            <span className="sep">·</span>
            {failed} failed
          </>
        )}
      </>
    )

  return (
    <div className="run-head">
      <div className="run-title">
        <span className={`tag ${tag.tone}`}>
          <Icon name={tag.icon} size={11} className={running ? 'spin' : undefined} />
          {tag.label}
        </span>
        <span className="run-id">{run.id}</span>
        <span className="tag tag-minimal">{(run.incident_ids.length ? run.incident_ids : [run.incident_id]).join(' + ')}</span>
      </div>
      <dl className="props">
        <dt>Branched at</dt>
        <dd className="mono">{run.snapshot_sim_time != null ? clock(run.snapshot_sim_time) : '—'}</dd>
        <dt>Horizon</dt>
        <dd>
          {Math.round(run.horizon_s / 60)} min{run.ems_probe ? ' · with EMS probe vehicle' : ''}
        </dd>
        <dt>Agent</dt>
        <dd className="mono">{run.agent}</dd>
      </dl>
      <div className="run-summary">{summary}</div>
      {running && (
        <div className="run-bar">
          <span style={{ width: `${progress != null ? 20 + progress * 75 : run.status === 'recommending' ? 95 : 10}%` }} />
        </div>
      )}
    </div>
  )
}

export function ResponsePlans(props: Props) {
  const { run, incidentId, fixture, busy, colors, phaseLabels, activeId, selectedId, onHover, onSelect, onImplement } =
    props

  if (!run) {
    return (
      <Section id="response-plans" title="Response plans" icon="branch">
        <EmptyState incidentId={incidentId} fixture={fixture} />
      </Section>
    )
  }

  const baseline = run.candidates.find((c) => c.id === 'baseline')
  const rec = run.recommendation
  const recommended = rec ? run.candidates.find((c) => c.id === rec.candidate_id) : undefined

  return (
    <Section id="response-plans" title="Response plans" icon="branch" meta={run.id}>
      <RunHeader run={run} />

      {run.error && (
        <div className="callout callout-danger" style={{ marginBottom: 10 }}>
          <Icon name="error" size={14} />
          <div className="callout-body">
            <strong>Analysis {run.status === 'failed' ? 'failed' : 'error'}.</strong> {run.error}
          </div>
        </div>
      )}

      {rec && (
        <div
          className="recommendation"
          role="button"
          tabIndex={0}
          onClick={() => onSelect(rec.candidate_id)}
onKeyDown={(e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    onSelect(rec.candidate_id)
  }
}}
          onMouseEnter={() => onHover(rec.candidate_id)}
          onMouseLeave={() => onHover(null)}
        >
          <div className="rec-head">
            <Icon name="star" size={12} /> Recommended plan
            <span className="rec-agent">agent: {run.agent}</span>
          </div>
          <div className="rec-name">
            <span className="swatch" style={{ '--swatch': colors[rec.candidate_id] } as CSSProperties} />
            {recommended?.name ?? rec.candidate_id}
          </div>
          <p className="rec-summary">{rec.summary}</p>
          {rec.rationale.length > 0 && (
            <ul className="rec-rationale">
              {rec.rationale.map((line) => (
                <li key={line}>{line.replaceAll('->', '→')}</li>
              ))}
            </ul>
          )}
          {run.implementation ? (
            <div className="rec-foot applied">
              <Icon name="bolt" size={12} /> Applied to live signals by {run.implementation.implemented_by} at{' '}
              {clock(run.implementation.implemented_at)}
              {run.implementation.staleness_s != null &&
                `, ${Math.round(run.implementation.staleness_s)} s after the snapshot`}
              .
            </div>
          ) : (
            <div className="rec-foot">
              <Icon name="shield" size={12} /> Advisory until applied. This plan passed the safety validator and is
              re-checked against the live signals when applied.
            </div>
          )}
        </div>
      )}

      {rec && !run.implementation && run.status === 'completed' && !fixture && (
        <div className="rec-apply">
          <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => onImplement(run.id)}>
            <Icon name="bolt" size={12} /> Apply to live signals
          </button>
          <span>Operator path. An autonomous agent applies its own recommendation.</span>
        </div>
      )}

      <div className="list-head">
        <span className="subhead" style={{ margin: 0 }}>
          <Icon name="layers" size={12} /> Candidate plans
        </span>
        <span className="subhead-meta">{run.candidates.length}</span>
      </div>
      {run.candidates.length === 0 ? (
        <div className="cand-wait">
          <div className="bar-indet" />
        </div>
      ) : (
        <div className="cand-list">
          {run.candidates.map((c) => (
            <CandidateCard
              key={c.id}
              run={run}
              candidate={c}
              baseline={baseline}
              color={colors[c.id]}
              recommended={rec?.candidate_id === c.id}
              active={activeId === c.id}
              selected={selectedId === c.id}
              phaseLabels={phaseLabels}
              onHover={onHover}
              onSelect={onSelect}
            />
          ))}
        </div>
      )}
    </Section>
  )
}
