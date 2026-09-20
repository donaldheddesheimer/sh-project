import type { Episode, ScenarioRun } from '../api/types'
import { branchProgress, isRunning, KPIS, kpiDelta } from '../lib/plans'
import { Icon } from './Icon'
import { Section } from './Section'

interface Props {
  run: ScenarioRun | null
  episode: Episode | null
  colors: Record<string, string>
}

// mock is used only when episode_analyst is set to it in code (providers.build_episode_teams)
const analystName = (name: string) => (name === 'mock' ? 'Mock' : name === 'claude' ? 'Claude' : 'Nemotron')

/** What the agent decided, in words and numbers: the recommendation, why, and what it costs or saves. */
export function DecisionCard({ run, episode, colors }: Props) {
  const analyst = analystName(episode?.analyst.split('→').at(-1) ?? run?.agent ?? 'nemotron')
  const recommendation = run?.recommendation ?? null
  const chosen = run?.candidates.find((c) => c.id === recommendation?.candidate_id) ?? null
  const baseline = run?.candidates.find((c) => c.id === 'baseline')?.metrics ?? null
  const applied = episode?.implementation ?? run?.implementation ?? null

  let body
  if (recommendation && chosen) {
    body = (
      <>
        <div className="decision-plan">
          <span className="decision-dot" style={{ background: colors[chosen.id] }} />
          <strong>{chosen.name || chosen.id}</strong>
          {applied ? (
            <span className="tag tag-success"><Icon name="check" size={11} /> applied by {applied.implemented_by}</span>
          ) : (
            <span className="tag tag-minimal">not applied</span>
          )}
        </div>
        <p className="rec-summary">{recommendation.summary}</p>
        {recommendation.rationale.length > 0 && (
          <ul className="rec-rationale">
            {recommendation.rationale.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
        )}
        {chosen.metrics && baseline && (
          <dl className="decision-kpis">
            {KPIS.map((kpi) => {
              const value = kpi.value(chosen.metrics!)
              const delta = kpiDelta(kpi, value, kpi.value(baseline))
              return (
                <div key={kpi.key}>
                  <dt>{kpi.label}</dt>
                  <dd>
                    {value == null ? '—' : `${kpi.format(value)}${kpi.unit ? ` ${kpi.unit}` : ''}`}
                    {delta && <span className={`delta ${delta.tone === 'same' ? 'flat' : delta.tone}`}> {delta.text} vs no action</span>}
                  </dd>
                </div>
              )
            })}
          </dl>
        )}
        {run && run.recalled.length > 0 && (
          <p className="muted decision-memory">
            <Icon name="database" size={11} /> Used {run.recalled.length} remembered lesson{run.recalled.length === 1 ? '' : 's'}:{' '}
            {run.recalled.join(', ')}
          </p>
        )}
      </>
    )
  } else if (run?.status === 'failed') {
    body = <p className="muted">Analysis {run.id} failed: {run.error ?? 'no reason recorded'}</p>
  } else if (isRunning(run)) {
    const { done, total } = branchProgress(run)
    body = (
      <p className="muted">
        <Icon name="spinner" size={12} className="spin" /> {analyst} is {run.status} ({done}/{total} plans simulated)
      </p>
    )
  } else if (episode?.status === 'awaiting') {
    body = <p className="muted">Collision detected. Press Analyze on the map to have {analyst} respond.</p>
  } else {
    body = <p className="muted">No decision yet. It appears here after a collision is analyzed.</p>
  }

  return (
    <Section title="Agent decision" icon="star" meta={<span className="tag tag-minimal">{analyst}</span>}>
      {body}
    </Section>
  )
}
