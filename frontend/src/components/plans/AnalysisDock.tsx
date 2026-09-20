import { useEffect, useState } from 'react'
import type { MetricSample, OpsEvent, ScenarioRun, TrafficMetrics } from '../../api/types'
import { mph } from '../../lib/format'
import { Icon } from '../Icon'
import { EventLog } from '../EventLog'
import { TrendChart } from '../TrendChart'
import { HorizonChart } from './HorizonChart'
import { KpiComparison } from './KpiComparison'

interface Props {
  preferredTab: 'live' | 'comparison'
  history: MetricSample[]
  events: OpsEvent[]
  markers: { t: number; label: string }[]
  metrics: TrafficMetrics | null
  activeIncidents: number
  networkStatus: string
  run: ScenarioRun | null
  colors: Record<string, string>
  activeId: string | null
  selectedId: string | null
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
}

export function AnalysisDock(props: Props) {
  const {
    preferredTab, history, events, markers, metrics, activeIncidents, networkStatus,
    run, colors, activeId, selectedId, onHover, onSelect,
  } = props
  const [tab, setTab] = useState<'live' | 'comparison' | 'activity'>(preferredTab)
  useEffect(() => setTab(preferredTab), [preferredTab])
  const visibleTab = tab === 'comparison' && !run ? 'live' : tab
  const activity = (
    <div className="dock-activity">
      <div className="dock-overview">
        <span className="subhead">City overview</span>
        <div className="dock-overview-values">
          <div><strong>{metrics?.vehicles_in_network.toLocaleString('en-US') ?? '—'}</strong><span>Vehicles</span></div>
          <div><strong>{activeIncidents}</strong><span>Active incidents</span></div>
          <div><strong>{metrics ? `${Math.round(metrics.mean_vehicle_delay)} s` : '—'}</strong><span>Mean delay</span></div>
        </div>
      </div>
      <EventLog events={events} />
    </div>
  )

  return (
    <section className="dock">
      <div className="dock-tabs" role="tablist" aria-label="Performance views">
        <button
          type="button"
          className={`dock-tab${visibleTab === 'live' ? ' active' : ''}`}
          role="tab"
          aria-selected={visibleTab === 'live'}
          onClick={() => setTab('live')}
        >
          <Icon name="chart" size={12} /> Live trends
        </button>
        <button
          type="button"
          className={`dock-tab${visibleTab === 'comparison' ? ' active' : ''}`}
          role="tab"
          aria-selected={visibleTab === 'comparison'}
          disabled={!run}
          onClick={() => setTab('comparison')}
        >
          <Icon name="branch" size={12} /> Scenario comparison
          {run && <span className="tag tag-minimal">{run.candidates.length}</span>}
        </button>
        <button
          type="button"
          className={`dock-tab${visibleTab === 'activity' ? ' active' : ''}`}
          role="tab"
          aria-selected={visibleTab === 'activity'}
          onClick={() => setTab('activity')}
        >
          <Icon name="list" size={12} /> Activity
          {events.length > 0 && <span className="tag tag-minimal">{events.length}</span>}
        </button>
        <div className="dock-meta">
          {visibleTab !== 'comparison' ? (
            <span className="dock-live">
              <span className="live-dot" data-live={networkStatus === 'running'} /> network {networkStatus}
            </span>
          ) : (
            run && <span>{run.id} · {run.status}</span>
          )}
        </div>
      </div>

      {visibleTab === 'activity' ? (
        <div className="dock-body activity-body">{activity}</div>
      ) : visibleTab === 'live' || !run ? (
        <div className="dock-body">
          <div className="trends">
            <TrendChart title="Mean vehicle delay" unit="s" points={history.map((s) => ({ t: s.t, v: s.delay }))} markers={markers} />
            <TrendChart title="Max queue" unit="veh" points={history.map((s) => ({ t: s.t, v: s.queue }))} markers={markers} />
            <TrendChart title="Throughput" unit="veh/h" points={history.map((s) => ({ t: s.t, v: s.throughput }))} markers={markers} />
            <TrendChart
              title="Mean speed"
              unit="mph"
              points={history.map((s) => ({ t: s.t, v: mph(s.speed) }))}
              markers={markers}
              format={(value) => value.toFixed(0)}
            />
          </div>
          {activity}
        </div>
      ) : (
        <div className="dock-body compare-body">
          <KpiComparison
            run={run}
            colors={colors}
            activeId={activeId}
            selectedId={selectedId}
            onHover={onHover}
            onSelect={onSelect}
          />
          <HorizonChart run={run} colors={colors} activeId={activeId} />
        </div>
      )}
    </section>
  )
}
