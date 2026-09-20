import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { DemoInfo, Episode, EpisodeStatus, Outcome } from '../api/types'
import { clock, duration } from '../lib/format'
import { Icon, type IconName } from './Icon'
import { Section } from './Section'

interface Props {
  episode: Episode | null
  busy: string | null
  onRun: (name: string, fn: () => Promise<unknown>) => Promise<void>
}

// The happy path of an episode (EpisodeStatus in backend/app/models/episode.py); the other statuses end it early.
const FLOW: EpisodeStatus[] = ['armed', 'detected', 'analyzing', 'monitoring', 'reviewing', 'completed']
const FLOW_LABEL: Record<string, string> = {
  armed: 'Armed',
  detected: 'Detect',
  analyzing: 'Analyze',
  monitoring: 'Monitor',
  reviewing: 'Review',
  completed: 'Learn',
}

const STATUS_TAG: Record<EpisodeStatus, { tone: string; icon: IconName }> = {
  armed: { tone: 'tag-minimal', icon: 'pending' },
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
  const working = ['detected', 'analyzing', 'monitoring', 'reviewing'].includes(episode.status)
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
        <dd className="mono">{episode.recalled.join(', ') || 'none'}</dd>
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

/** Autonomous, self-learning episodes: pick a demo script, watch the agent respond, see what it learned. */
export function EpisodePanel({ episode, busy, onRun }: Props) {
  const [info, setInfo] = useState<DemoInfo | null>(null)
  const [refreshes, setRefreshes] = useState(0)
  const [picked, setPicked] = useState('')

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    // Retry like App's network loader: with no episode yet nothing else triggers a fetch, so a request that
    // loses the startup race would leave the script list empty and Run disabled until a reload.
    const load = () =>
      api
        .demo()
        .then((next) => !cancelled && setInfo(next))
        .catch(() => {
          if (!cancelled) timer = setTimeout(load, 1500)
        })
    load()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [episode?.id, episode?.status, refreshes])

  const act = (name: string, fn: () => Promise<unknown>) => onRun(name, fn).then(() => setRefreshes((n) => n + 1))
  const script = picked || info?.armed || info?.scripts[0]?.id || ''
  const description = info?.scripts.find((s) => s.id === script)?.description
  const memory = info?.memory

  return (
    <Section
      key={episode?.id ?? 'idle'}
      title="Autonomous agent"
      icon="bolt"
      meta={info?.armed ? <span className="tag tag-primary">armed</span> : 'off'}
      defaultOpen={episode != null}
    >
      <div className="episode-controls">
        <select
          className="episode-select"
          value={script}
          aria-label="Demo script"
          onChange={(e) => setPicked(e.target.value)}
        >
          {info?.scripts.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
        <button
          className="btn btn-sm btn-primary"
          disabled={!!busy || !script}
          title={description ?? 'Reset the city and play this script'}
          onClick={() => act('demo', () => api.demoStart(script))}
        >
          <Icon name="play" size={12} /> Run
        </button>
        <button
          className="btn btn-sm"
          disabled={!!busy || !info?.armed}
          title="Disarm: no more scripted crashes and no autonomous response"
          onClick={() => act('demo', api.demoStop)}
        >
          Stop
        </button>
      </div>
      <div className="episode-meta">
        Analyst <span className="mono">{info?.analyst ?? '—'}</span>
        <span className="sep">·</span>
        Memory{' '}
        {memory?.enabled ? `${memory.episodes} lesson${memory.episodes === 1 ? '' : 's'}` : 'off'}
        {memory?.enabled && memory.episodes > 0 && (
          <button
            className="episode-link"
            disabled={!!busy}
            title="Forget every lesson (a cold run)"
            onClick={() => act('memory', api.clearMemory)}
          >
            clear
          </button>
        )}
      </div>
      {episode ? (
        <EpisodeCard episode={episode} />
      ) : (
        <p className="episode-last">
          Run a script: a crash plays on the live twin, the agent tests plans in branches, applies the best one,
          watches the result and stores a lesson for the next incident.
        </p>
      )}
    </Section>
  )
}
