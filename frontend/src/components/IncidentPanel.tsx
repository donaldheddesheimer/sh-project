import type { EmergencyVehicleState, Incident, RoadSegmentState } from '../api/types'
import { clock, CONGESTION_LABEL, duration, mph } from '../lib/format'
import { Icon } from './Icon'
import { Section } from './Section'

interface Props {
  incidents: Incident[]
  segments: RoadSegmentState[]
  responders: EmergencyVehicleState[]
  busy: string | null
  onDispatch: () => void
  onClear: (id: string) => void
}

function LaneDiagram({ total, blocked }: { total: number; blocked: number[] }) {
  // Drawn left to right as seen by a driver: leftmost lane first, index 0 (rightmost) last.
  const lanes = Array.from({ length: total }, (_, i) => total - 1 - i)
  return (
    <div className="lanes" aria-label={`${blocked.length} of ${total} lanes blocked`}>
      {lanes.map((lane) => (
        <div key={lane} className={`lane ${blocked.includes(lane) ? 'blocked' : 'open'}`}>
          {blocked.includes(lane) ? '✕ BLOCKED' : '↑ OPEN'}
        </div>
      ))}
    </div>
  )
}

export function IncidentPanel({ incidents, segments, responders, busy, onDispatch, onClear }: Props) {
  const incident = incidents.length ? incidents.reduce((a, b) => (a.timestamp > b.timestamp ? a : b)) : null

  if (!incident) {
    return (
      <Section title="Active incident" icon="warning" meta="0 active">
        <div className="object-card all-clear">
          <span className="object-icon success">
            <Icon name="check" />
          </span>
          <div>
            <div className="object-title">No active incidents</div>
            <div className="muted small">Network operating normally. Camera analytics monitoring 9 intersections.</div>
          </div>
        </div>
      </Section>
    )
  }

  const segment = segments.find((s) => s.id === incident.location.segment_id)
  const responder = responders.find((r) => r.destination_segment === incident.location.segment_id && r.status !== 'completed')
  return (
    <Section
      title="Active incident"
      icon="warning"
      meta={<span className="tag tag-danger">{incidents.length} active</span>}
    >
      <div className={`object-card severity-${incident.severity}`}>
        <div className="object-head">
          <span className="object-icon danger">
            <Icon name="warning" />
          </span>
          <div className="object-heading">
            <div className="object-title">{incident.location.description}</div>
            <div className="object-sub">
              {incident.id} · {incident.type.replace('_', ' ').toUpperCase()}
            </div>
          </div>
          <span className={`tag severity-tag severity-${incident.severity}`}>{incident.severity}</span>
        </div>
        <p className="object-desc">{incident.description}</p>

        {incident.total_lanes != null && <LaneDiagram total={incident.total_lanes} blocked={incident.affected_lanes} />}

        <dl className="props">
          <dt>Detected</dt>
          <dd className="mono">{incident.sim_time != null ? clock(incident.sim_time) : '—'}</dd>
          <dt>Cameras</dt>
          <dd className="mono">{incident.sensor_ids.join(', ') || '—'}</dd>
          {segment && (
            <>
              <dt>Roadway</dt>
              <dd>
                {CONGESTION_LABEL[segment.level]} · {mph(segment.average_speed).toFixed(0)} mph · {segment.halting_count} queued
              </dd>
            </>
          )}
          <dt>Responder</dt>
          <dd>
            {responder
              ? responder.status === 'on_scene'
                ? `${responder.id} on scene`
                : `${responder.id} en route · ETA ${duration(responder.eta_s)}`
              : 'none assigned'}
          </dd>
          <dt>Source</dt>
          <dd>Smart City · {incident.source}</dd>
        </dl>

        <div className="object-actions">
          <button className="btn btn-sm" disabled={!!busy || !!responder} onClick={onDispatch}>
            <Icon name="medical" size={12} /> Dispatch EMS
          </button>
          <button className="btn btn-sm" disabled={!!busy} onClick={() => onClear(incident.id)}>
            <Icon name="check" size={12} /> Clear scene
          </button>
        </div>
      </div>
    </Section>
  )
}
