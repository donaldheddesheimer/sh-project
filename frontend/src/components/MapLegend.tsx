import type { CongestionLevel } from '../api/types'
import { CONGESTION_COLOR, CONGESTION_LABEL, SIGNAL_COLOR } from '../lib/format'

const LEVELS: CongestionLevel[] = ['free', 'moderate', 'heavy', 'severe']

export function MapLegend() {
  return (
    <div className="legend overlay-card">
      <div className="overlay-head">Legend</div>
      <div className="legend-body">
        <div className="legend-title">Traffic</div>
        {LEVELS.map((level) => (
          <div className="legend-row" key={level}>
            <span className="legend-line" style={{ background: CONGESTION_COLOR[level] }} />
            {CONGESTION_LABEL[level]}
          </div>
        ))}
        <div className="legend-row">
          <span className="legend-line dashed" />
          Blocked lane
        </div>
        <div className="legend-title">Signals</div>
        <div className="legend-row">
          <span className="legend-dot" style={{ background: SIGNAL_COLOR.green }} />
          <span className="legend-dot" style={{ background: SIGNAL_COLOR.yellow }} />
          <span className="legend-dot" style={{ background: SIGNAL_COLOR.red }} />
          through movement
        </div>
        <div className="legend-title">Vehicles</div>
        <div className="legend-row">
          <span className="legend-veh" style={{ background: '#c5d0de' }} /> Traffic
          <span className="legend-veh" style={{ background: '#ff3b3b' }} /> EMS
          <span className="legend-veh" style={{ background: '#ff8a3d' }} /> Disabled
        </div>
      </div>
    </div>
  )
}
