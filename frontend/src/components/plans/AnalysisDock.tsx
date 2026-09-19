import { useState } from 'react'
import type { MetricSample, OpsEvent, ScenarioRun } from '../../api/types'
import { mph } from '../../lib/format'
import { Icon } from '../Icon'
import { EventLog } from '../EventLog'
import { TrendChart } from '../TrendChart'
import { HorizonChart } from './HorizonChart'
import { KpiComparison } from './KpiComparison'

interface Props {
  history: MetricSample[]
  events: OpsEvent[]
  markers: { t: number; label: string }[]
  run: ScenarioRun | null
  colors: Record<string, string>
  activeId: string | null
  selectedId: string | null
  onHover: (id: string | null) => void
  onSelect: (id: string) => void
}

export function AnalysisDock(props: Props) {
  const { history, events, markers, run, colors, activeId, selectedId, onHover, onSelect } = props
  const [tab, setTab] = useState<'live' | 'comparison'>('live')
  const visibleTab = run ? tab : 'live'

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
        <div className="dock-meta">
          {visibleTab === 'live' ? (
            <span className="dock-live"><span className="live-dot" data-live /> live network</span>
          ) : (
            run && <span>{run.id} · {run.status}</span>
          )}
        </div>
      </div>

      {visibleTab === 'live' || !run ? (
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
          <EventLog events={events} />
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
