import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from './api/client'
import type { Camera } from './api/types'
import { EpisodePanel } from './components/EpisodePanel'
import { Icon } from './components/Icon'
import { IncidentPanel } from './components/IncidentPanel'
import { CityMap, type Selection } from './components/map/CityMap'
import { MapLegend } from './components/MapLegend'
import { MetricsPanel } from './components/MetricsPanel'
import { AnalysisDock } from './components/plans/AnalysisDock'
import { MapPlanCard } from './components/plans/MapPlanCard'
import { ResponsePlans } from './components/plans/ResponsePlans'
import { SelectionPanel } from './components/SelectionPanel'
import { TopBar } from './components/TopBar'
import { WorkspaceRail, type WorkspaceView } from './components/WorkspaceRail'
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
  const { state, network, status, events, history, connected, scenario, episode, phaseLabels, acceptScenario } =
    useCityStream()
  const [cameras, setCameras] = useState<Camera[]>([])
  const [selection, setSelection] = useState<Selection | null>(null)
  const [view, setView] = useState<WorkspaceView>('live')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | null>(null)
  const [hoveredPlanId, setHoveredPlanId] = useState<string | null>(null)
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [pendingMapId, setPendingMapId] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)
  const cancelReplay = useRef<(() => void) | null>(null)

  useEffect(() => {
    setCameras([])
    if (!network) {
      return
    }
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    // Cameras exist only on the VSS path and appear once its first poll succeeds, so an empty
    // list is normal at first. Back off to a minute so a mock backend, or a VSS endpoint that
    // stays down, is not polled every 5 s for the life of the page.
    let delay = 5000
    const retry = () => {
      timer = setTimeout(load, delay)
      delay = Math.min(delay * 2, 60000)
    }
    const load = () =>
      api
        .cameras()
        .then((items) => {
          if (cancelled) return
          // Not calling setCameras on an empty payload keeps the array identity stable while waiting.
          if (items.length === 0) {
            retry()
            return
          }
          setCameras(items)
        })
        .catch(() => {
          if (!cancelled) retry()
        })
    void load()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [network])

  useEffect(() => {
    setSelection(null)
    setSelectedIncidentId(null)
    setHoveredPlanId(null)
    setSelectedPlanId(null)
    setView('live')
    setDrawerOpen(false)
    cancelReplay.current?.()
    cancelReplay.current = null
  }, [network?.id])

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
  const focusedIncident = activeIncidents.find((incident) => incident.id === selectedIncidentId) ?? latestIncident
  // an analysis over several incidents belongs to each of them (its incident_id is only the primary one)
  const scopedScenario =
    scenario && focusedIncident && (scenario.incident_ids ?? [scenario.incident_id]).includes(focusedIncident.id)
      ? scenario
      : null
  const incidentTime = focusedIncident?.sim_time ?? null
  // pre-incident reference for KPI deltas: the last trend sample before detection
  const reference = useMemo(
    () => (incidentTime == null ? null : (history.filter((s) => s.t <= incidentTime - 5).at(-1) ?? null)),
    [history, incidentTime],
  )
  const markers = incidents.filter((i) => i.sim_time != null).map((i) => ({ t: i.sim_time!, label: i.id }))
  const colors = useMemo(() => (scopedScenario ? candidateColors(scopedScenario) : {}), [scopedScenario])
  const activePlanId = hoveredPlanId ?? selectedPlanId
  const activeCandidate = scopedScenario?.candidates.find((candidate) => candidate.id === activePlanId) ?? null
  const incidentSegmentId = focusedIncident?.location.segment_id ?? null
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
    hasIncident: !!focusedIncident,
    busy: !!busy,
    scenario,
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
        const run = await api.runScenario({ incident_id: focusedIncident?.id ?? null, horizon_s: 600, ems_probe: true })
        acceptScenario(run)
      }
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(null)
    }
  }

  const switchMap = async (mapId: string) => {
    if (mapId === network?.id) return
    setBusy('map')
    // Keep the controlled select on the operator's choice while the replacement warms up.
    // Otherwise React can restore the old network id and some browsers emit a reverse change.
    setPendingMapId(mapId)
    setSelection(null)
    setSelectedIncidentId(null)
    setHoveredPlanId(null)
    setSelectedPlanId(null)
    setView('live')
    cancelReplay.current?.()
    cancelReplay.current = null
    try {
      await api.selectMap(mapId)
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err))
    } finally {
      setPendingMapId(null)
      setBusy(null)
    }
  }

  const showView = (next: WorkspaceView) => {
    setView(next)
    setDrawerOpen(true)
  }

  const incidentPanel = (
    <IncidentPanel
      incidents={incidents}
      selectedId={focusedIncident?.id ?? null}
      onSelectIncident={setSelectedIncidentId}
      segments={state?.segments ?? []}
      responders={state?.emergency_vehicles ?? []}
      cameraCount={cameras.length}
      busy={busy}
      canDispatch={!!focusedIncident && focusedIncident.id === latestIncident?.id}
      onDispatch={() => run('dispatch', api.dispatchEmergency)}
      onClear={(id) => run('clear', () => api.clearIncident(id))}
    />
  )

  const responsePlans = (
    <ResponsePlans
      run={scopedScenario}
      incidentId={focusedIncident?.id ?? null}
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
  )

  const viewInfo = {
    live: { title: 'Live network', description: 'Incident command and network state' },
    analysis: { title: 'Response analysis', description: 'Simulated plans, safety and recommendation' },
    autonomous: { title: 'Autonomous agent', description: 'Scripted response, monitoring and learning' },
  }[view]

  return (
    <div className="app" data-drawer-open={drawerOpen}>
      <TopBar
        networkName={network?.name ?? null}
        mapId={pendingMapId ?? network?.id ?? null}
        simTime={state?.sim_time ?? null}
        status={status}
        connected={connected}
        hasIncident={!!focusedIncident}
        canDispatch={!!focusedIncident && focusedIncident.id === latestIncident?.id}
        busy={busy}
        analyze={analyze}
        fixture={!!FIXTURE_MODE}
        onAction={(action) => {
          if (action === 'reset') {
            cancelReplay.current?.()
            cancelReplay.current = null
            setView('live')
            setSelectedIncidentId(null)
            setSelectedPlanId(null)
            setSelection(null)
          }
          void run(action, ACTIONS[action])
        }}
        onSpeed={(speed) => run('speed', () => api.speed(speed))}
        onMap={(mapId) => void switchMap(mapId)}
        onAnalyze={() => {
          showView('analysis')
          void startAnalysis()
        }}
      />

      <WorkspaceRail
        view={view}
        activeIncidents={activeIncidents.length}
        candidateCount={scopedScenario?.candidates.length ?? 0}
        episodeActive={episode != null && ['armed', 'detected', 'analyzing', 'monitoring', 'reviewing'].includes(episode.status)}
        onChange={showView}
      />

      <main className="map-area">
        {network ? (
          <CityMap
            network={network}
            state={state}
            cameras={cameras}
            selection={selection}
            planOverlay={overlay}
            onSelect={(next) => {
              setSelection(next)
              if (next) showView('live')
            }}
            onSelectIncident={(id) => {
              setSelectedIncidentId(id)
              showView('live')
            }}
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
        {busy === 'map' ? (
          <div className="map-banner">Switching traffic map…</div>
        ) : status?.status === 'starting' ? (
          <div className="map-banner">Warming up simulation…</div>
        ) : null}
        {status?.status === 'error' && <div className="map-banner error">Simulation error: {status.error}</div>}
      </main>

      <aside className="side">
        <div className="workspace-head">
          <div>
            <span className="workspace-eyebrow">OPERATIONS WORKSPACE</span>
            <h1>{viewInfo.title}</h1>
            <p>{viewInfo.description}</p>
          </div>
          <button
            className="drawer-close"
            type="button"
            aria-label="Close workspace drawer"
            onClick={() => setDrawerOpen(false)}
          >
            <Icon name="cross" size={16} />
          </button>
        </div>
        {view === 'live' && (
          <>
            {incidentPanel}
            <MetricsPanel
              metrics={state?.metrics ?? null}
              history={history}
              reference={reference}
              segments={state?.segments ?? []}
            />
            <SelectionPanel selection={selection} state={state} />
          </>
        )}
        {view === 'analysis' && (
          <>
            {responsePlans}
            {incidentPanel}
          </>
        )}
        {view === 'autonomous' && (
          <>
            <EpisodePanel episode={episode} busy={busy} onRun={run} />
            {responsePlans}
            {incidentPanel}
          </>
        )}
      </aside>

      <AnalysisDock
        preferredTab={view === 'live' ? 'live' : 'comparison'}
        history={history}
        events={events}
        markers={markers}
        metrics={state?.metrics ?? null}
        activeIncidents={activeIncidents.length}
        networkStatus={connected ? status?.status ?? 'connecting' : 'offline'}
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
