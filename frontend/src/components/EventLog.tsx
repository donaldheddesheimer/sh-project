import type { OpsEvent } from '../api/types'
import { clock } from '../lib/format'

const ICON = { info: '●', warning: '▲', alert: '⚠' } as const

export function EventLog({ events }: { events: OpsEvent[] }) {
  const newestFirst = [...events].reverse()
  return (
    <div className="event-log">
      <h2 className="section-title">Operations log</h2>
      <ol>
        {newestFirst.map((e) => (
          <li key={e.id} className={`event level-${e.level}`}>
            <span className="event-time mono">{e.sim_time != null ? clock(e.sim_time) : '--:--:--'}</span>
            <span className="event-icon" aria-label={e.level}>
              {ICON[e.level]}
            </span>
            <span className="event-msg">{e.message}</span>
          </li>
        ))}
      </ol>
    </div>
  )
}
