import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DemoInfo, Episode, EpisodeStatus, LearningReport, Outcome } from '../api/types'
import { clock, duration } from '../lib/format'
import { Icon, type IconName } from './Icon'
import { Section } from './Section'

interface Props {
  episode: Episode | null
  demo: DemoInfo | null
  busy: string | null
  onClearMemory: () => void
}

// The happy path of an episode (EpisodeStatus in backend/app/models/episode.py); the other statuses end it early.
const FLOW: EpisodeStatus[] = ['armed', 'awaiting', 'detected', 'analyzing', 'monitoring', 'reviewing', 'completed']
const FLOW_LABEL: Record<string, string> = {
  armed: 'Armed',
  awaiting: 'Paused',
  detected: 'Detect',
  analyzing: 'Analyze',
  monitoring: 'Monitor',
  reviewing: 'Review',
  completed: 'Learn',
}

const STATUS_TAG: Record<EpisodeStatus, { tone: string; icon: IconName }> = {
  armed: { tone: 'tag-minimal', icon: 'pending' },
  awaiting: { tone: 'tag-caution', icon: 'pause' },
  detected: { tone: 'tag-primary', icon: 'spinner' },
  analyzing: { tone: 'tag-primary', icon: 'spinner' },
  monitoring: { tone: 'tag-primary', icon: 'spinner' },
  reviewing: { tone: 'tag-primary', icon: 'spinner' },
  completed: { tone: 'tag-success', icon: 'check' },
  superseded: { tone: 'tag-caution', icon: 'ban' },
  aborted: { tone: 'tag-caution', icon: 'ban' },
  failed: { tone: 'tag-danger', icon: 'error' },
}

const VERDICT_TONE: Record<Outcome, string> = {
  effective: 'tag-success',
  ineffective: 'tag-danger',
  inconclusive: 'tag-minimal',
}

const signed = (value: number | null | undefined) =>
  value == null ? '—' : `${value > 0 ? '+' : ''}${Math.round(value)}%`

function Flow({ episode }: { episode: Episode }) {
  // how far the episode got; superseded / aborted / failed stop at that step
  const reached = Math.max(0, ...episode.steps.map((s) => FLOW.indexOf(s.status)))
  const stopped = !FLOW.includes(episode.status)
  return (
    <ol className="episode-flow">
      {FLOW.map((status, i) => {
        const state =
          episode.status === 'completed' || i < reached ? 'done' : i === reached ? (stopped ? 'stopped' : 'current') : ''
        return (
          <li key={status} className={`episode-step ${state}`}>
            {FLOW_LABEL[status]}
          </li>
        )
      })}
    </ol>
  )
}

function EpisodeCard({ episode }: { episode: Episode }) {
  const tag = STATUS_TAG[episode.status]
  const working = ['detected', 'analyzing', 'monitoring', 'reviewing'].includes(episode.status) // awaiting is idle, not busy
  const impl = episode.implementation
  const sc = episode.scorecard
  const lesson = episode.lesson
  const last = episode.steps.at(-1)
  return (
    <div className="episode">
      <div className="run-title">
        <span className={`tag ${tag.tone}`}>
          <Icon name={tag.icon} size={11} className={working ? 'spin' : undefined} />
          {episode.status}
        </span>
        <span className="run-id">{episode.id}</span>
        {episode.script_id && <span className="tag tag-minimal">{episode.script_id}</span>}
      </div>
      <Flow episode={episode} />
      {last && <p className="episode-last">{last.message}</p>}
      {episode.status === 'monitoring' && (
        <div
          className="run-bar"
          role="progressbar"
          aria-label="Monitor window"
          aria-valuemin={0}
          aria-valuemax={Math.round(episode.monitor_s)}
          aria-valuenow={Math.round(Math.min(episode.monitor_progress_s, episode.monitor_s))}
          aria-valuetext={`${duration(episode.monitor_progress_s)} of ${duration(episode.monitor_s)} simulated`}
        >
          <span style={{ width: `${Math.min(100, (100 * episode.monitor_progress_s) / episode.monitor_s)}%` }} />
        </div>
      )}
      <dl className="props">
        <dt>Incidents</dt>
        <dd className="mono">{episode.incident_ids.join(' + ') || 'waiting for the crash'}</dd>
        <dt>Analyst</dt>
        <dd className="mono">{episode.analyst}</dd>
        {episode.run_id && (
          <>
            <dt>Analysis</dt>
            <dd className="mono">
              {episode.run_id}
              {episode.candidates > 0 && ` · ${episode.candidates} plans · ${episode.rounds} round${episode.rounds === 1 ? '' : 's'}`}
              {episode.analysis_wall_s != null && ` · ${episode.analysis_wall_s.toFixed(0)} s`}
            </dd>
          </>
        )}
        <dt>Lessons used</dt>
        <dd className="mono">
          {episode.recalled.join(', ') || 'none'} · memory {episode.memory_mode}
        </dd>
        {impl && (
          <>
            <dt>Applied</dt>
            <dd>
              {impl.candidate_name} by {impl.implemented_by} at <span className="mono">{clock(impl.implemented_at)}</span>
            </dd>
          </>
        )}
        {episode.status === 'monitoring' && (
          <>
            <dt>Watching</dt>
            <dd className="mono">
              {duration(episode.monitor_progress_s)} / {duration(episode.monitor_s)} sim
            </dd>
          </>
        )}
        {sc && (
          <>
            <dt>Mean delay</dt>
            <dd>
              {sc.realised.mean_delay.toFixed(0)} s real
              {sc.predicted && ` · ${sc.predicted.mean_delay.toFixed(0)} s predicted`}
              {sc.predicted_baseline && ` · ${sc.predicted_baseline.mean_delay.toFixed(0)} s doing nothing`}
            </dd>
            <dt>Vs nothing</dt>
            <dd>
              {signed(sc.realised_vs_baseline.delay_pct)} delay · twin off by {signed(sc.prediction_error.delay_pct)}
            </dd>
          </>
        )}
        {episode.supersedes && (
          <>
            <dt>Took over</dt>
            <dd className="mono">{episode.supersedes}</dd>
          </>
        )}
        {episode.superseded_by && (
          <>
            <dt>Replaced by</dt>
            <dd className="mono">{episode.superseded_by}</dd>
          </>
        )}
      </dl>
      {lesson && (
        <div className="episode-lesson">
          <div className="rec-head">
            <Icon name="star" size={12} /> Lesson
            <span className={`tag ${VERDICT_TONE[lesson.verdict]}`}>{lesson.verdict}</span>
            <span className="rec-agent">reviewer: {lesson.reviewer}</span>
          </div>
          <p className="rec-summary">{lesson.summary}</p>
          {lesson.next_time.length > 0 && (
            <ul className="rec-rationale">
              {lesson.next_time.map((tip) => (
                <li key={tip}>{tip}</li>
              ))}
            </ul>
          )}
        </div>
      )}
      {episode.status === 'failed' && episode.error && (
        <div className="callout callout-danger">
          <Icon name="error" size={14} />
          <div className="callout-body">{episode.error}</div>
        </div>
      )}
    </div>
  )
}

function Learning({ report }: { report: LearningReport | null }) {
  if (!report || report.episodes.length === 0) return null
  return (
    <details className="learning-report">
      <summary>
        Learning <span>{report.episodes.length} episode{report.episodes.length === 1 ? '' : 's'}</span>
      </summary>
      <div className="learning-scroll">
        <table>
          <thead>
            <tr>
              <th>Episode</th>
              <th>Mode</th>
              <th>Recall</th>
              <th>Plans</th>
              <th>Verdict</th>
              <th>Delay</th>
            </tr>
          </thead>
          <tbody>
            {report.episodes.map((row) => (
              <tr key={row.id}>
                <td className="mono">{row.id}</td>
                <td>{row.memory_mode}</td>
                <td>{row.recalled_sources.join(', ') || 'cold'}</td>
                <td>{row.candidates_tried} / {row.rounds}r</td>
                <td>{row.verdict}{row.provisional ? ' · provisional' : ''}</td>
                <td>{signed(row.delay_vs_baseline_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {report.comparisons.map((comparison) => (
        <p key={`${comparison.warm_episode_id}-${comparison.control_episode_id}`} className="learning-comparison">
          {comparison.transfer ? 'Transfer' : 'Same-script'} {comparison.warm_episode_id} vs {comparison.control_episode_id}:{' '}
          {comparison.useful == null ? 'reported, not rated' : comparison.useful ? 'useful by the configured rule' : 'not useful by the configured rule'}
          {comparison.behavior_changes.length > 0 && ` · ${comparison.behavior_changes.join('; ')}`}
        </p>
      ))}
    </details>
  )
}

/**
 * Readout for the autonomous episode. It has no controls: the whole loop is the one Arm agent button
 * in the top bar, which arms AUTONOMOUS_SCRIPT with the configured analyst and memory on. What is left
 * here is what the agent did — the step flow, the applied plan, the scorecard, the lesson and the
 * learning table. `clear` stays because a cold first episode is a demo state, not a mode.
 */
export function EpisodePanel({ episode, demo, busy, onClearMemory }: Props) {
  const [report, setReport] = useState<LearningReport | null>(null)

  useEffect(() => {
    let cancelled = false
    api.learningReport().then((next) => !cancelled && setReport(next)).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [episode?.id, episode?.status])

  const armedId = demo?.armed ?? null
  const armedScript = demo?.scripts.find((s) => s.id === armedId) ?? null
  // No scheduled crash: the agent is waiting for the operator to inject one.
  const awaitingOperator = armedScript != null && armedScript.crashes_at.length === 0
  const memory = demo?.memory

  return (
    <Section
      key={episode?.id ?? 'idle'}
      title="Autonomous agent"
      icon="bolt"
      meta={demo?.armed ? <span className="tag tag-primary">armed</span> : 'off'}
      defaultOpen
    >
      <div className="episode-meta">
        <span className="episode-provider">Analyst {demo?.analyst === 'mock' ? 'Mock' : (demo?.analyst ?? '—')}</span>
        {demo?.analyst_model && (
          <span className="episode-model mono" title={demo.analyst_model}>
            analyst: {demo.analyst_model}
          </span>
        )}
        {demo?.reviewer_model && (
          <span className="episode-model mono" title={demo.reviewer_model}>
            reviewer: {demo.reviewer_model}
          </span>
        )}
        <span className="sep">·</span>
        Memory {memory?.enabled ? `on · ${memory.episodes} stored episode${memory.episodes === 1 ? '' : 's'}` : 'off'}
        {memory?.enabled && memory.episodes > 0 && (
          <button
            className="episode-link"
            disabled={!!busy}
            title="Forget every lesson (a cold run)"
            onClick={onClearMemory}
          >
            clear
          </button>
        )}
      </div>
      {awaitingOperator && episode?.status === 'armed' && (
        <div className="episode-next-action" role="status">
          Agent armed. Click <strong>Inject collision</strong> in the top bar; the city pauses and an{' '}
          <strong>Analyze</strong> button appears on the map.
        </div>
      )}
      {episode?.status === 'awaiting' && (
        <div className="episode-next-action" role="status">
          Collision detected and the simulation is paused. Press <strong>Analyze</strong> on the map to start the response.
        </div>
      )}
      {episode ? (
        <EpisodeCard episode={episode} />
      ) : (
        <p className="episode-last">
          Press <strong>Arm agent</strong> in the top bar, then inject a collision: the agent tests plans in
          branches, applies the best one, watches the result and stores a lesson for the next incident.
        </p>
      )}
      <Learning report={report} />
    </Section>
  )
}
