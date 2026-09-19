import type { StatusInfo } from '../api/types'
import { clock } from '../lib/format'
import type { AnalyzeState } from '../lib/plans'
import { Icon } from './Icon'

const SPEEDS = [1, 2, 4, 8, 16]

type Action = 'start' | 'pause' | 'reset' | 'inject' | 'dispatch'

interface Props {
  networkName: string | null
  simTime: number | null
  status: StatusInfo | null
  connected: boolean
  hasIncident: boolean
  busy: string | null
  analyze: AnalyzeState
  fixture: boolean
  onAction: (action: Action) => void
  onSpeed: (speed: number) => void
  onAnalyze: () => void
}

export function TopBar(props: Props) {
  const { networkName, simTime, status, connected, hasIncident, busy, analyze, fixture, onAction, onSpeed, onAnalyze } =
    props
  const run = status?.status ?? 'starting'
  const live = connected && run !== 'starting' && run !== 'error'
  const running = run === 'running'
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          <Icon name="grid" size={14} />
        </span>
        <span className="brand-name">Traffic Ops</span>
        <span className="crumbs">
          <span>{networkName ?? 'Network'}</span>
          <Icon name="chevronRight" size={10} />
          <span className="crumb-current">Live operations</span>
        </span>
      </div>

      {fixture && (
        <span className="fixture-badge" title="Analysis runs are replayed from src/dev/scenario-run.json: synthetic numbers, not simulation output">
          <Icon name="database" size={12} /> Fixture data
        </span>
      )}

      <div className="sim-clock">
        <span className={`run-tag run-${connected ? run : 'offline'}`}>
          <span className="dot" />
          {connected ? run.toUpperCase() : 'OFFLINE'}
        </span>
        <span className="clock">
          <span className="clock-label">SIM</span>
          <span className="clock-value">{simTime == null ? '--:--:--' : clock(simTime)}</span>
        </span>
        <div className="seg" role="group" aria-label="Simulation speed">
          {SPEEDS.map((s) => (
            <button key={s} className={status?.speed === s ? 'active' : ''} disabled={!live} onClick={() => onSpeed(s)}>
              {s}×
            </button>
          ))}
        </div>
      </div>

      <div className="controls">
        <div className="control-group">
          <button
            className="btn btn-icon"
            disabled={!live || !!busy}
            title={running ? 'Pause simulation' : 'Start simulation'}
            aria-label={running ? 'Pause' : 'Start'}
            onClick={() => onAction(running ? 'pause' : 'start')}
          >
            <Icon name={running ? 'pause' : 'play'} />
          </button>
          <button
            className="btn btn-icon"
            disabled={!connected || run === 'starting' || !!busy}
            title="Reset simulation"
            aria-label="Reset"
            onClick={() => onAction('reset')}
          >
            <Icon name="reset" />
          </button>
        </div>
        <span className="vsep" />
        <div className="control-group">
          <button className="btn btn-danger" disabled={!live || !!busy} onClick={() => onAction('inject')}>
            <Icon name="warning" /> Inject collision
          </button>
          <button
            className="btn"
            disabled={!live || !hasIncident || !!busy}
            title={hasIncident ? 'Send a responder from Fire Station 3 to the active incident' : 'No active incident'}
            onClick={() => onAction('dispatch')}
          >
            <Icon name="medical" /> Dispatch EMS
          </button>
        </div>
        <button
          className={`btn btn-primary btn-analyze${analyze.progress != null ? ' btn-busy' : ''}`}
          disabled={!analyze.enabled}
          title={analyze.reason}
          onClick={onAnalyze}
        >
          <Icon name={analyze.progress != null ? 'spinner' : 'branch'} className={analyze.progress != null ? 'spin' : undefined} />
          {analyze.label}
          {analyze.progress != null && <span className="btn-progress" style={{ width: `${analyze.progress * 100}%` }} />}
        </button>
      </div>
    </header>
  )
}
