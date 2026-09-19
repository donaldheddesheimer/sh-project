import type { OpsEvent } from '../api/types'
import { clock } from '../lib/format'
import { Icon, type IconName } from './Icon'

const ICON: Record<OpsEvent['level'], IconName> = { info: 'dot', warning: 'warning', alert: 'warning' }

export function EventLog({ events }: { events: OpsEvent[] }) {
  const newestFirst = [...events].reverse()
  return (
    <div className="event-log">
      <div className="subhead">
        <Icon name="list" size={12} /> Operations log <span className="subhead-meta">{events.length}</span>
      </div>
      <ol>
        {newestFirst.map((e) => (
          <li key={e.id} className={`event level-${e.level}`}>
            <span className="event-time mono">{e.sim_time != null ? clock(e.sim_time) : '--:--:--'}</span>
            <span className="event-icon" aria-label={e.level}>
              <Icon name={ICON[e.level]} size={12} />
            </span>
            <span className="event-msg">{e.message}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
