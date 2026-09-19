import { useEffect, useMemo, useState } from 'react'
import { api } from './api/client'
import type { NetworkGeometry } from './api/types'
import { EventLog } from './components/EventLog'
import { IncidentPanel } from './components/IncidentPanel'
import { CityMap, type Selection } from './components/map/CityMap'
import { MapLegend } from './components/MapLegend'
import { MetricsPanel } from './components/MetricsPanel'
import { SelectionPanel } from './components/SelectionPanel'
import { TopBar } from './components/TopBar'
import { TrendChart } from './components/TrendChart'
import { useCityStream } from './hooks/useCityStream'
import { mph } from './lib/format'

type Action = 'start' | 'pause' | 'reset' | 'inject' | 'dispatch'

const ACTIONS: Record<Action, () => Promise<unknown>> = {
  start: api.start,
  pause: api.pause,
  reset: api.reset,
  inject: api.injectCollision,
  dispatch: api.dispatchEmergency,
}

export default function App() {
  const { state, status, events, history, connected } = useCityStream()
  const [network, setNetwork] = useState<NetworkGeometry | null>(null)
  const [selection, setSelection] = useState<Selection | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () =>
      api
        .network()
        .then((n) => !cancelled && setNetwork(n))
        .catch(() => {
          if (!cancelled) timer = setTimeout(load, 1500)
        })
    load()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  useEffect(() => {
    if (!toast) return
    const timer = setTimeout(() => setToast(null), 5000)
    return () => clearTimeout(timer)
  }, [toast])

  const run = async (name: string, fn: () => Promise<unknown>) => {
    setBusy(name)
    try {
      await fn()
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const incidents = state?.incidents ?? []
  const latestIncident = incidents.length ? incidents.reduce((a, b) => (a.timestamp > b.timestamp ? a : b)) : null
  const incidentTime = latestIncident?.sim_time ?? null
  // pre-incident reference for KPI deltas: the last trend sample before detection
  const reference = useMemo(
    () => (incidentTime == null ? null : (history.filter((s) => s.t <= incidentTime - 5).at(-1) ?? null)),
    [history, incidentTime],
  )
  const markers = incidents.filter((i) => i.sim_time != null).map((i) => ({ t: i.sim_time!, label: i.id }))

  return (
    <div className="app">
      <TopBar
        simTime={state?.sim_time ?? null}
        status={status}
        connected={connected}
        hasIncident={incidents.length > 0}
        busy={busy}
        onAction={(action) => run(action, ACTIONS[action])}
        onSpeed={(speed) => run('speed', () => api.speed(speed))}
      />

      <main className="map-area">
        {network ? (
          <CityMap network={network} state={state} selection={selection} onSelect={setSelection} />
        ) : (
          <div className="map-loading">Connecting to traffic simulation…</div>
        )}
        <div className="map-caption">
          <span className="live-dot" data-live={connected && status?.status === 'running'} />
          {network?.name ?? 'Network'} · {network?.intersections.length ?? 0} signalized intersections ·{' '}
          {state?.vehicles.length ?? 0} vehicles tracked
          {state && (
            <span className="sources">
              {state.providers.simulator} · Smart City: {state.providers.smart_city} · Agent: {state.providers.agent}
            </span>
          )}
        </div>
        <MapLegend />
        {status?.status === 'starting' && <div className="map-banner">Warming up simulation…</div>}
        {status?.status === 'error' && <div className="map-banner error">Simulation error: {status.error}</div>}
      </main>

      <aside className="side">
        <IncidentPanel
          incidents={incidents}
          segments={state?.segments ?? []}
          responders={state?.emergency_vehicles ?? []}
          busy={busy}
          onDispatch={() => run('dispatch', api.dispatchEmergency)}
          onClear={(id) => run('clear', () => api.clearIncident(id))}
        />
        <MetricsPanel
          metrics={state?.metrics ?? null}
          history={history}
          reference={reference}
          segments={state?.segments ?? []}
        />
        <SelectionPanel selection={selection} state={state} />
        <section className="panel-section">
          <h2 className="section-title">Response plans</h2>
          <div className="plans-placeholder">
            Next milestone: <strong>Analyze Response</strong> branches SUMO from the current state and compares
            baseline, upstream metering, split rebalancing and an emergency green corridor over a 10-minute horizon.
          </div>
        </section>
      </aside>

      <section className="bottom">
        <div className="trends">
          <TrendChart title="Mean vehicle delay" unit="s" points={history.map((s) => ({ t: s.t, v: s.delay }))} markers={markers} />
          <TrendChart title="Max queue" unit="veh" points={history.map((s) => ({ t: s.t, v: s.queue }))} markers={markers} />
          <TrendChart
            title="Throughput"
            unit="veh/h"
            points={history.map((s) => ({ t: s.t, v: s.throughput }))}
            markers={markers}
          />
          <TrendChart
            title="Mean speed"
            unit="mph"
            points={history.map((s) => ({ t: s.t, v: mph(s.speed) }))}
            markers={markers}
            format={(v) => v.toFixed(0)}
          />
        </div>
        <EventLog events={events} />
      </section>

      {toast && (
        <div className="toast" role="alert" onClick={() => setToast(null)}>
          {toast}
        </div>
      )}
    </div>
  )
}
