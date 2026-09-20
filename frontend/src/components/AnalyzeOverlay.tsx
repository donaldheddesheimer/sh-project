import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { Episode, MemoryMode, MemoryStats } from '../api/types'
import { Icon } from './Icon'

interface Props {
  episode: Episode | null
  busy: string | null
  onAnalyze: (memoryMode: MemoryMode) => void
}

/** Centered over the map while a collision has paused the city and the agent waits for the operator's go-ahead. */
export function AnalyzeOverlay({ episode, busy, onAnalyze }: Props) {
  const [memory, setMemory] = useState<MemoryStats | null>(null)
  const [mode, setMode] = useState<MemoryMode>('use')
  const awaiting = episode?.status === 'awaiting'

  useEffect(() => {
    if (!awaiting) return
    let cancelled = false
    api.demo().then((info) => !cancelled && setMemory(info.memory)).catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [awaiting, episode?.id])

  if (!awaiting) return null
  const lessons = memory?.enabled ? memory.episodes : 0
  return (
    <div className="analyze-overlay" role="dialog" aria-label="Analyze the collision">
      <div className="analyze-card">
        <span className="analyze-eyebrow">
          <Icon name="pause" size={11} /> SIMULATION PAUSED
        </span>
        <h2>{episode.incident_ids.join(' + ')} detected</h2>
        <p>
          The city is frozen at the moment of the crash. Analyzing simulates candidate responses in parallel branches
          before anything is applied.
        </p>
        <div className="analyze-memory">
          <select value={mode} aria-label="Memory mode" onChange={(e) => setMode(e.target.value as MemoryMode)}>
            <option value="use">Use memory</option>
            <option value="ignore">Ignore memory</option>
          </select>
          <span className="muted">
            {mode === 'ignore'
              ? 'cold start: no lessons consulted'
              : lessons > 0
                ? `${lessons} stored lesson${lessons === 1 ? '' : 's'} will be consulted`
                : 'no lessons stored yet: cold start'}
          </span>
        </div>
        <button className="btn btn-primary btn-analyze-main" disabled={!!busy} onClick={() => onAnalyze(mode)}>
          <Icon name={busy === 'analyze' ? 'spinner' : 'branch'} className={busy === 'analyze' ? 'spin' : undefined} />
          Analyze
        </button>
      </div>
    </div>
  )
}
