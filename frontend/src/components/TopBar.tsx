import type { StatusInfo } from '../api/types'
import { clock } from '../lib/format'
import type { AnalyzeState } from '../lib/plans'
import { Icon } from './Icon'

const SPEEDS = [1, 2, 4, 8, 16]
const MAPS = [
  { id: 'downtown_grid', name: '3×3 Grid' },
  { id: 'pittsburgh_oakland', name: 'Pittsburgh' },
]

type Action = 'start' | 'pause' | 'reset' | 'inject' | 'dispatch'

interface Props {
  networkName: string | null
  mapId: string | null
  simTime: number | null
  status: StatusInfo | null
  connected: boolean
  hasIncident: boolean
  canDispatch: boolean
  busy: string | null
  analyze: AnalyzeState
  fixture: boolean
  onAction: (action: Action) => void
  onSpeed: (speed: number) => void
  onMap: (mapId: string) => void
  onAnalyze: () => void
  layoutCustom: boolean
  onResetLayout: () => void
}

export function TopBar(props: Props) {
  const {
    networkName, mapId, simTime, status, connected, hasIncident, canDispatch,
    busy, analyze, fixture, onAction, onSpeed, onMap, onAnalyze, layoutCustom, onResetLayout,
  } = props
  const run = status?.status ?? 'starting'
  const live = connected && run !== 'starting' && run !== 'error'
  const running = run === 'running'
  const dispatchTitle = !hasIncident
    ? 'No active incident'
    : canDispatch
      ? 'Send a responder from the configured station to the newest active incident'
      : 'Select the newest active incident to dispatch EMS'
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden>
          <Icon name="grid" size={18} />
        </span>
        <span className="brand-lockup">
          <span className="brand-eyebrow">CITY SYSTEMS / COMMAND</span>
          <span className="brand-name">Traffic Operations</span>
          <span className="crumbs">
            <span>{networkName ?? 'Network'}</span>
            <Icon name="chevronRight" size={10} />
            <span className="crumb-current">Live digital twin</span>
          </span>
        </span>
      </div>

      {fixture && (
        <span className="fixture-badge" title="Analysis runs are replayed from src/dev/scenario-run.json: synthetic numbers, not simulation output">
          <Icon name="database" size={12} /> Fixture data
        </span>
      )}

      <div className="sim-clock">
        <div className="topbar-stack map-stack">
          <label className="topbar-caption" htmlFor="map-select">Map</label>
          <select
            id="map-select"
            className="map-select"
            value={mapId ?? ''}
            disabled={!connected || !mapId || !!busy}
            title="Switching maps starts a fresh simulation"
            onChange={(event) => onMap(event.target.value)}
          >
            {!mapId && <option value="">Loading…</option>}
            {MAPS.map((map) => <option key={map.id} value={map.id}>{map.name}</option>)}
          </select>
        </div>
        <div className="topbar-stack">
          <span className="topbar-caption">Network state</span>
          <div className="topbar-row">
            <span className={`run-tag run-${connected ? run : 'offline'}`}>
              <span className="dot" />
              {connected ? run.toUpperCase() : 'OFFLINE'}
            </span>
            <span className="clock">
              <span className="clock-label">SIM</span>
              <span className="clock-value">{simTime == null ? '--:--:--' : clock(simTime)}</span>
            </span>
          </div>
        </div>
        <div className="topbar-stack speed-stack">
          <span className="topbar-caption">Time scale</span>
          <div className="seg" role="group" aria-label="Simulation speed">
            {SPEEDS.map((s) => (
              <button key={s} className={status?.speed === s ? 'active' : ''} disabled={!live} onClick={() => onSpeed(s)}>
                {s}×
              </button>
            ))}
          </div>
          <select
            className="speed-select"
            aria-label="Simulation speed"
            value={status?.speed ?? SPEEDS[2]}
            disabled={!live}
            onChange={(event) => onSpeed(Number(event.target.value))}
          >
            {SPEEDS.map((s) => <option key={s} value={s}>{s}×</option>)}
          </select>
        </div>
      </div>

      <div className="controls">
        <div className="control-cluster">
          <span className="topbar-caption">Simulation</span>
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
            {layoutCustom && (
              <button className="btn btn-sm" title="Restore the default panel sizes" onClick={onResetLayout}>
                Reset layout
              </button>
            )}
          </div>
        </div>
        <div className="control-cluster response-cluster">
          <span className="topbar-caption">Incident response {hasIncident && <span className="incident-ready">/ incident active</span>}</span>
          <div className="control-group">
            <button className="btn btn-danger" disabled={!live || !!busy} onClick={() => onAction('inject')}>
              <Icon name="warning" /> Inject collision
            </button>
            <button
              className="btn"
              disabled={!live || !canDispatch || !!busy}
              title={dispatchTitle}
              aria-label="Dispatch EMS"
              onClick={() => onAction('dispatch')}
            >
              <Icon name="medical" /> Dispatch EMS
            </button>
            {/* Live analysis starts from the Analyze button that appears over the paused map after a collision;
                this one only replays the recorded fixture run. */}
            {fixture && (
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
            )}
          </div>
        </div>
      </div>
    </header>
  )
}
