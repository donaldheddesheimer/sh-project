import type { Approach, CityState, IntersectionState, RoadSegmentState } from '../api/types'
import { CONGESTION_COLOR, CONGESTION_LABEL, DIRECTION_LABEL, mph, SIGNAL_COLOR } from '../lib/format'
import { Section } from './Section'
import type { Selection } from './map/CityMap'

const APPROACHES: Approach[] = ['NB', 'SB', 'EB', 'WB']

// One row per approach (incoming segment). A direction that repeats at the junction also names the street.
function approachRows(i: IntersectionState, segments: RoadSegmentState[]): { id: string; label: string }[] {
  const legs = segments.filter((s) => s.id in i.queue_lengths)
  legs.sort((a, b) => APPROACHES.indexOf(a.direction) - APPROACHES.indexOf(b.direction))
  return legs.map((s) => {
    const repeated = legs.some((o) => o !== s && o.direction === s.direction)
    return { id: s.id, label: repeated ? `${DIRECTION_LABEL[s.direction]} · ${s.name}` : DIRECTION_LABEL[s.direction] }
  })
}

export function SelectionPanel({ selection, state }: { selection: Selection | null; state: CityState | null }) {
  if (!selection || !state) {
    return (
      <Section title="Inspector" icon="eye">
        <div className="muted hint">Select an intersection or road segment on the map.</div>
      </Section>
    )
  }

  if (selection.kind === 'intersection') {
    const i = state.intersections.find((x) => x.id === selection.id)
    if (!i) return null
    return (
      <Section title="Inspector · intersection" icon="signal" meta={i.id}>
        <div className="inspector-name">{i.name}</div>
        {i.signalized && (
          <div className="phase">
            <span className="phase-label">{i.phase_label}</span>
            <span className="mono">{i.phase_remaining != null ? `${Math.ceil(i.phase_remaining)}s` : ''}</span>
            <span className="muted">
              cycle {i.cycle_length}s · {i.program_id === '0' ? 'base plan' : i.program_id}
            </span>
          </div>
        )}
        <table className="approach-table">
          <thead>
            <tr>
              <th>Approach</th>
              <th>Signal</th>
              <th className="num">Queue</th>
            </tr>
          </thead>
          <tbody>
            {approachRows(i, state.segments).map(({ id, label }) => {
              const signal = i.approach_signals[id]
              return (
                <tr key={id}>
                  <td>{label}</td>
                  <td>
                    {signal && (
                      <>
                        <span className="signal-dot" style={{ background: SIGNAL_COLOR[signal] }} />
                        {signal}
                      </>
                    )}
                  </td>
                  <td className="num">{i.queue_lengths[id]} veh</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Section>
    )
  }

  const s = state.segments.find((x) => x.id === selection.id)
  if (!s) return null
  const from = state.intersections.find((x) => x.id === s.source)?.name ?? s.source
  const to = state.intersections.find((x) => x.id === s.destination)?.name ?? s.destination
  return (
    <Section title="Inspector · road segment" icon="eye" meta={s.id}>
      <div className="inspector-name">
        {s.name} · {DIRECTION_LABEL[s.direction]}
      </div>
      <div className="muted small">
        {from} → {to}
      </div>
      <dl className="props boxed">
        <dt>Condition</dt>
        <dd>
          <span className="signal-dot" style={{ background: CONGESTION_COLOR[s.level] }} />
          {CONGESTION_LABEL[s.level]}
        </dd>
        <dt>Speed</dt>
        <dd>
          {mph(s.average_speed).toFixed(0)} mph (limit {mph(s.speed_limit).toFixed(0)})
        </dd>
        <dt>Vehicles</dt>
        <dd>
          {s.vehicle_count} · {s.halting_count} stopped
        </dd>
        <dt>Occupancy</dt>
        <dd>{Math.round(s.occupancy * 100)}%</dd>
        {s.blocked_lanes.length > 0 && (
          <>
            <dt>Blocked</dt>
            <dd className="text-critical">lane {s.blocked_lanes.map((l) => l + 1).join(', ')}</dd>
          </>
        )}
      </dl>
    </Section>
  )
}
