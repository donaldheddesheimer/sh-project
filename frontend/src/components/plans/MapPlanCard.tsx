import type { CSSProperties } from 'react'
import type { SimulationCandidate } from '../../api/types'
import type { PlanOverlay } from '../../lib/plans'
import { Icon } from '../Icon'

export function MapPlanCard({ candidate, overlay }: { candidate: SimulationCandidate; overlay: PlanOverlay }) {
  const hasMarks = overlay.retimed.length > 0 || overlay.avoid.length > 0 || !!overlay.corridor
  if (!hasMarks) return null
  return (
    <div className="plan-card overlay-card" style={{ '--plan-color': overlay.color } as CSSProperties}>
      <div className="overlay-head">
        <div className="plan-card-name">
          <span className="swatch" style={{ '--swatch': overlay.color } as CSSProperties} />
          <span>{candidate.name}</span>
        </div>
        <span className="tag tag-minimal">plan overlay</span>
      </div>
      <div className="plan-card-body">
        {overlay.retimed.length > 0 && (
          <div className="plan-key">
            <span className="key-ring" />
            <span>Retimes <span className="mono">{overlay.retimed.join(', ')}</span></span>
          </div>
        )}
        {overlay.avoid.length > 0 && (
          <div className="plan-key">
            <span className="key-dash" />
            <span>Avoids <span className="mono">{overlay.avoid.join(', ')}</span></span>
          </div>
        )}
        {overlay.corridor && (
          <div className="plan-key">
            <span className="key-bolt"><Icon name="bolt" size={11} /></span>
            <span>
              Green corridor {overlay.corridor.ids.length ? `at ${overlay.corridor.ids.join(', ')}` : 'route unavailable'}
              {overlay.corridor.assumed && <span className="plan-assumed"> · estimated station-to-incident path</span>}
            </span>
          </div>
        )}
      </div>
      <div className="plan-card-foot">Preview only · no changes are applied to live signals</div>
    </div>
  )
}
