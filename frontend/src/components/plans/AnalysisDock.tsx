import { useEffect, useRef, useState } from 'react'
import type { MetricSample, OpsEvent, RoadSegmentState, ScenarioRun, TrafficMetrics } from '../../api/types'
import { compact, duration, mph } from '../../lib/format'
import { Icon } from '../Icon'
import { EventLog } from '../EventLog'
import { TrendChart } from '../TrendChart'
import { Splitter } from '../Splitter'
import { HorizonChart } from './HorizonChart'
import { KpiComparison } from './KpiComparison'

interface Props {
  preferredTab: 'live' | 'comparison'
  history: MetricSample[]
  events: OpsEvent[]
  markers: { t: number; label: string }[]
  metrics: TrafficMetrics | null
  reference: MetricSample | null // last sample before detection, for the change beside each value
  segments: RoadSegmentState[]
  networkStatus: string
  run: ScenarioRun | null
  colors: Record<string, string>
  activeId: string | null
  selectedId: string | null
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
  onActivityResize: (px: number) => void
  onActivityReset: () => void
}

/** Change against the pre-incident reference, in the chart's own unit. */
function change(
  now: number | null | undefined,
  before: number | null | undefined,
  upIsGood: boolean,
  fmt: (v: number) => string,
): { text: string; tone: 'better' | 'worse' | 'same' } | null {
  if (now == null || before == null) return null
  const d = now - before
  if (fmt(Math.abs(d)) === fmt(0)) return { text: '± 0', tone: 'same' }
  return { text: `${d > 0 ? '▲' : '▼'} ${fmt(Math.abs(d))}`, tone: d > 0 === upIsGood ? 'better' : 'worse' }
}

const whole = (v: number) => Math.round(v).toString()
const tenth = (v: number) => v.toFixed(1)

export function AnalysisDock(props: Props) {
  const {
    preferredTab, history, events, markers, metrics, reference, segments, networkStatus,
    run, colors, activeId, selectedId, onHover, onSelect, onActivityResize, onActivityReset,
  } = props
  const activityRef = useRef<HTMLDivElement>(null)
  const [tab, setTab] = useState<'live' | 'comparison' | 'activity'>(preferredTab)
  useEffect(() => setTab(preferredTab), [preferredTab])
  const visibleTab = tab === 'comparison' && !run ? 'live' : tab
  const worstQueue = segments.find((s) => s.id === metrics?.max_queue_segment)
  const activity = (
    <div className="dock-activity" ref={activityRef}>
      <Splitter
        axis="x"
        className="splitter-activity"
        label="Resize the activity log"
        current={() => activityRef.current?.getBoundingClientRect().width ?? 340}
        onResize={onActivityResize}
        onReset={onActivityReset}
        min={200}
        max={() => window.innerWidth * 0.5}
        invert
      />
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
          <div className="trends-column">
            <div className="trend-chips">
              <span className="trend-chip">
                <span className="muted">EMS ETA</span>
                <strong>{metrics?.emergency_vehicle_eta == null ? 'no unit' : `${duration(metrics.emergency_vehicle_eta)} min`}</strong>
              </span>
              <span className="trend-chip">
                <span className="muted">Vehicles</span>
                <strong>{metrics?.vehicles_in_network.toLocaleString('en-US') ?? '—'}</strong>
                {!!metrics?.vehicles_waiting_to_enter && <span className="muted">+{metrics.vehicles_waiting_to_enter} waiting</span>}
              </span>
              {reference && <span className="trend-chip muted">Change is vs pre-incident</span>}
            </div>
            <div className="trends">
              <TrendChart
                title="Mean vehicle delay"
                unit="s"
                points={history.map((s) => ({ t: s.t, v: s.delay }))}
                markers={markers}
                delta={change(metrics?.mean_vehicle_delay, reference?.delay, false, whole)}
              />
              <TrendChart
                title="Max queue"
                unit="veh"
                points={history.map((s) => ({ t: s.t, v: s.queue }))}
                markers={markers}
                note={worstQueue ? `${worstQueue.name} ${worstQueue.direction}` : undefined}
                delta={change(metrics?.max_queue_length, reference?.queue, false, whole)}
              />
              <TrendChart
                title="Throughput"
                unit="veh/h"
                points={history.map((s) => ({ t: s.t, v: s.throughput }))}
                markers={markers}
                delta={change(metrics?.throughput, reference?.throughput, true, compact)}
              />
              <TrendChart
                title="Mean speed"
                unit="mph"
                points={history.map((s) => ({ t: s.t, v: mph(s.speed) }))}
                markers={markers}
                format={(value) => value.toFixed(0)}
                delta={change(metrics && mph(metrics.mean_speed), reference && mph(reference.speed), true, tenth)}
              />
            </div>
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
