import type { StatusInfo } from '../api/types'
import { clock } from '../lib/format'

const SPEEDS = [1, 2, 4, 8, 16]

interface Props {
  simTime: number | null
  status: StatusInfo | null
  connected: boolean
  hasIncident: boolean
  busy: string | null
  onAction: (action: 'start' | 'pause' | 'reset' | 'inject' | 'dispatch') => void
  onSpeed: (speed: number) => void
}

export function TopBar({ simTime, status, connected, hasIncident, busy, onAction, onSpeed }: Props) {
  const run = status?.status ?? 'starting'
  const live = connected && run !== 'starting' && run !== 'error'
  return (
    <header className="topbar">
      <div className="brand">
        <div className="brand-mark" aria-hidden>
          <span />
          <span />
          <span />
        </div>
        <div>
          <div className="brand-title">Traffic Operations Center</div>
          <div className="brand-sub">Simulation-backed incident response</div>
        </div>
      </div>

      <div className="sim-clock">
        <div className={`run-pill run-${connected ? run : 'offline'}`}>
          <span className="dot" />
          {connected ? run.toUpperCase() : 'OFFLINE'}
        </div>
        <div className="clock">
          <span className="clock-label">SIM</span>
          <span className="clock-value">{simTime == null ? '--:--:--' : clock(simTime)}</span>
        </div>
        <div className="speed" role="group" aria-label="Simulation speed">
          {SPEEDS.map((s) => (
            <button
              key={s}
              className={status?.speed === s ? 'active' : ''}
              disabled={!live}
              onClick={() => onSpeed(s)}
            >
              {s}×
            </button>
          ))}
        </div>
      </div>

      <div className="controls">
        <div className="control-group">
          <button className="btn" disabled={!live || run === 'running' || !!busy} onClick={() => onAction('start')}>
            ▶ Start
          </button>
          <button className="btn" disabled={!live || run === 'paused' || !!busy} onClick={() => onAction('pause')}>
            ❚❚ Pause
          </button>
          <button className="btn" disabled={!connected || run === 'starting' || !!busy} onClick={() => onAction('reset')}>
            ↺ Reset
          </button>
        </div>
        <div className="control-group">
          <button className="btn btn-danger" disabled={!live || !!busy} onClick={() => onAction('inject')}>
            ⚠ Inject Collision
          </button>
          <button
            className="btn"
            disabled={!live || !hasIncident || !!busy}
            title={hasIncident ? 'Send a responder from Fire Station 3 to the active incident' : 'No active incident'}
            onClick={() => onAction('dispatch')}
          >
            ✚ Dispatch EMS
          </button>
          <button className="btn btn-primary" disabled title="Candidate simulation arrives in the next milestone">
            ◇ Analyze Response
          </button>
        </div>
      </div>
    </header>
  )
}
