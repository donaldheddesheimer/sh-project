import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client'
import type { Camera, NetworkGeometry } from './api/types'
import { EpisodePanel } from './components/EpisodePanel'
import { IncidentPanel } from './components/IncidentPanel'
import { CityMap, type Selection } from './components/map/CityMap'
import { MapLegend } from './components/MapLegend'
import { MetricsPanel } from './components/MetricsPanel'
import { AnalysisDock } from './components/plans/AnalysisDock'
import { MapPlanCard } from './components/plans/MapPlanCard'
import { ResponsePlans } from './components/plans/ResponsePlans'
import { SelectionPanel } from './components/SelectionPanel'
import { TopBar } from './components/TopBar'
import { FIXTURE_MODE } from './dev/fixture'
import { useCityStream } from './hooks/useCityStream'
import { analyzeState, candidateColors, planOverlay } from './lib/plans'

type Action = 'start' | 'pause' | 'reset' | 'inject' | 'dispatch'

const ACTIONS: Record<Action, () => Promise<unknown>> = {
  start: api.start,
  pause: api.pause,
  reset: api.reset,
  inject: api.injectCollision,
  dispatch: api.dispatchEmergency,
}

export default function App() {
  const { state, status, events, history, connected, scenario, episode, phaseLabels, acceptScenario } = useCityStream()
  const [network, setNetwork] = useState<NetworkGeometry | null>(null)
  const [cameras, setCameras] = useState<Camera[]>([])
  const [selection, setSelection] = useState<Selection | null>(null)
  const [hoveredPlanId, setHoveredPlanId] = useState<string | null>(null)
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const cancelReplay = useRef<(() => void) | null>(null)

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
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = () =>
      api
        .cameras()
        .then((items) => {
          if (cancelled) return
          setCameras(items)
          if (items.length === 0) timer = setTimeout(load, 5000)
        })
        .catch(() => {
          if (!cancelled) timer = setTimeout(load, 5000)
        })
    void load()
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

  useEffect(() => () => cancelReplay.current?.(), [])

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
  const activeIncidents = incidents.filter((incident) => incident.status === 'active')
  const latestIncident = activeIncidents.length
    ? activeIncidents.reduce((a, b) => (a.timestamp > b.timestamp ? a : b))
    : null
  // an analysis over several incidents belongs to each of them (its incident_id is only the primary one)
  const scopedScenario =
    scenario && latestIncident && (scenario.incident_ids ?? [scenario.incident_id]).includes(latestIncident.id)
      ? scenario
      : null
  const incidentTime = latestIncident?.sim_time ?? null
  // pre-incident reference for KPI deltas: the last trend sample before detection
  const reference = useMemo(
    () => (incidentTime == null ? null : (history.filter((s) => s.t <= incidentTime - 5).at(-1) ?? null)),
    [history, incidentTime],
  )
  const markers = incidents.filter((i) => i.sim_time != null).map((i) => ({ t: i.sim_time!, label: i.id }))
  const colors = useMemo(() => (scopedScenario ? candidateColors(scopedScenario) : {}), [scopedScenario])
  const activePlanId = hoveredPlanId ?? selectedPlanId
  const activeCandidate = scopedScenario?.candidates.find((candidate) => candidate.id === activePlanId) ?? null
  const incidentSegmentId = latestIncident?.location.segment_id ?? null
  const overlay = useMemo(
    () =>
      activeCandidate && network
        ? planOverlay(activeCandidate, colors[activeCandidate.id], network, incidentSegmentId)
        : null,
    [activeCandidate, colors, incidentSegmentId, network],
  )
  const analyze = analyzeState({
    connected,
    runStatus: status?.status ?? null,
    hasIncident: !!latestIncident,
    busy: !!busy,
    scenario: scopedScenario,
    episode,
    fixture: !!FIXTURE_MODE,
  })

  const selectPlan = (id: string) => setSelectedPlanId((current) => (current === id ? null : id))

  const startAnalysis = async () => {
    setBusy('analyze')
    try {
      if (FIXTURE_MODE) {
        const { replayFixture } = await import('./dev/replay')
        cancelReplay.current?.()
        cancelReplay.current = replayFixture(FIXTURE_MODE, acceptScenario)
      } else {
        const run = await api.runScenario({ incident_id: latestIncident?.id ?? null, horizon_s: 600, ems_probe: true })
        acceptScenario(run)
      }
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  return (
    <div className="app">
      <TopBar
        networkName={network?.name ?? null}
        simTime={state?.sim_time ?? null}
        status={status}
        connected={connected}
        hasIncident={!!latestIncident}
        busy={busy}
        analyze={analyze}
        fixture={!!FIXTURE_MODE}
onAction={(action) => {
  if (action === 'reset') {
    cancelReplay.current?.()
    cancelReplay.current = null
  }
  void run(action, ACTIONS[action])
}}
        onSpeed={(speed) => run('speed', () => api.speed(speed))}
        onAnalyze={startAnalysis}
      />

      <main className="map-area">
        {network ? (
          <CityMap
            network={network}
            state={state}
            cameras={cameras}
            selection={selection}
            planOverlay={overlay}
            onSelect={setSelection}
          />
        ) : (
          <div className="map-loading">Connecting to traffic simulation…</div>
        )}
        <div className="map-caption">
          <span className="live-dot" data-live={connected && status?.status === 'running'} />
          {network?.name ?? 'Network'} · {network?.intersections.filter((i) => i.signalized).length ?? 0} signalized intersections ·{' '}
          {state?.vehicles.length ?? 0} vehicles tracked
          {state && (
            <span className="sources">
              {state.providers.simulator} · Smart City: {state.providers.smart_city} · Agent: {state.providers.agent}
            </span>
          )}
        </div>
        <MapLegend hasCameras={cameras.some((camera) => camera.location != null)} />
        {network?.attribution && <div className="map-attribution">{network.attribution}</div>}
        {activeCandidate && overlay && <MapPlanCard candidate={activeCandidate} overlay={overlay} />}
        {status?.status === 'starting' && <div className="map-banner">Warming up simulation…</div>}
        {status?.status === 'error' && <div className="map-banner error">Simulation error: {status.error}</div>}
      </main>

      <aside className="side">
        <EpisodePanel episode={episode} busy={busy} onRun={run} />
        <IncidentPanel
          incidents={incidents}
          segments={state?.segments ?? []}
          responders={state?.emergency_vehicles ?? []}
          cameraCount={cameras.length}
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
        <ResponsePlans
          run={scopedScenario}
          incidentId={latestIncident?.id ?? null}
          fixture={!!FIXTURE_MODE}
          busy={!!busy}
          colors={colors}
          phaseLabels={phaseLabels}
          activeId={activePlanId}
          selectedId={selectedPlanId}
          onHover={setHoveredPlanId}
          onSelect={selectPlan}
          onImplement={(runId) => run('implement', () => api.implement(runId))}
        />
      </aside>

      <AnalysisDock
        history={history}
        events={events}
        markers={markers}
        run={scopedScenario}
        colors={colors}
        activeId={activePlanId}
        selectedId={selectedPlanId}
        onHover={setHoveredPlanId}
        onSelect={selectPlan}
      />

      {toast && (
        <div className="toast" role="alert" onClick={() => setToast(null)}>
          {toast}
        </div>
      )}
    </div>
  )
}
