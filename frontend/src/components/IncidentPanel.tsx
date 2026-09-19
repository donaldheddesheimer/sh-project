import type { EmergencyVehicleState, Incident, RoadSegmentState } from '../api/types'
import { clock, CONGESTION_LABEL, duration, mph } from '../lib/format'

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
      <section className="panel-section">
        <h2 className="section-title">Active incident</h2>
        <div className="all-clear">
          <span className="all-clear-icon">✓</span>
          <div>
            <div className="all-clear-title">No active incidents</div>
            <div className="muted">Network operating normally. Camera analytics monitoring 9 intersections.</div>
          </div>
        </div>
      </section>
    )
  }

  const segment = segments.find((s) => s.id === incident.location.segment_id)
  const responder = responders.find((r) => r.destination_segment === incident.location.segment_id && r.status !== 'completed')
  return (
    <section className="panel-section">
      <h2 className="section-title">
        Active incident {incidents.length > 1 && <span className="count">{incidents.length}</span>}
      </h2>
      <div className={`incident-card severity-${incident.severity}`}>
        <div className="incident-head">
          <span className="badge badge-type">⚠ {incident.type.replace('_', ' ').toUpperCase()}</span>
          <span className={`badge badge-${incident.severity}`}>{incident.severity.toUpperCase()}</span>
          <span className="incident-id">{incident.id}</span>
        </div>
        <div className="incident-location">{incident.location.description}</div>
        <p className="incident-desc">{incident.description}</p>

        {incident.total_lanes != null && <LaneDiagram total={incident.total_lanes} blocked={incident.affected_lanes} />}

        <dl className="facts">
          <dt>Detected</dt>
          <dd>{incident.sim_time != null ? clock(incident.sim_time) : '—'}</dd>
          <dt>Cameras</dt>
          <dd>{incident.sensor_ids.join(', ') || '—'}</dd>
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

        <div className="incident-actions">
          <button className="btn" disabled={!!busy || !!responder} onClick={onDispatch}>
            ✚ Dispatch EMS
          </button>
          <button className="btn" disabled={!!busy} onClick={() => onClear(incident.id)}>
            Clear scene
          </button>
        </div>
      </div>
    </section>
  )
}
