import { ROUTE_COLOR, type RouteGeometry } from '../lib/routes'

interface Props {
  geometry: RouteGeometry
}

/** The key to the mini map: only the colors the shown plan actually draws. */
export function RouteLegend({ geometry }: Props) {
  const items = [
    { on: geometry.ems.length > 0, kind: 'line', color: ROUTE_COLOR.ems, label: 'EMS route', hint: 'Path the responder takes to the scene' },
    {
      on: geometry.diversion.length > 0,
      kind: 'line',
      color: ROUTE_COLOR.diversion,
      label: 'Suggested detour',
      hint: 'Where most diverted traffic goes (free-flow shortest way round the blocked road)',
    },
    { on: geometry.blocked.length > 0, kind: 'dash', color: ROUTE_COLOR.blocked, label: 'Blocked', hint: 'Road blocked by the incident' },
    { on: geometry.corridor.length > 0, kind: 'ring', color: ROUTE_COLOR.corridor, label: 'Pre-empted signal', hint: 'Green corridor: turns green for the responder' },
    { on: geometry.retimed.length > 0, kind: 'ring', color: ROUTE_COLOR.retimed, label: 'Retimed signal', hint: 'Signal timing changed by the plan' },
  ].filter((item) => item.on)

  if (items.length === 0) return <p className="legend-empty muted">This plan changes no routes or signals.</p>
  return (
    <ul className="route-legend" aria-label="Route map key">
      {items.map((item) => (
        <li key={item.label} title={item.hint}>
          <span className={`route-swatch route-${item.kind}`} style={{ '--swatch': item.color } as React.CSSProperties} />
          {item.label}
        </li>
      ))}
    </ul>
  )
}
