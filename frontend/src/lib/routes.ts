import type { NetworkGeometry, ScenarioRun, SimulationCandidate } from '../api/types'

type LonLat = [number, number]

/** One color per meaning on the mini map; the legend reads from the same table. */
export const ROUTE_COLOR = {
  ems: '#ff453a',
  diversion: '#30d158',
  blocked: '#f2f4f7',
  retimed: '#ffb020',
  corridor: '#ff453a',
}

export interface RouteGeometry {
  ems: LonLat[][]
  diversion: LonLat[][]
  blocked: LonLat[][]
  retimed: LonLat[]
  corridor: LonLat[]
}

/** What one plan acts on, as map geometry. Paths come from the run's `routes`; signals from the plan itself. */
export function routeGeometry(
  network: NetworkGeometry,
  run: ScenarioRun | null,
  candidate: SimulationCandidate | null,
  incidentSegmentId: string | null,
): RouteGeometry {
  const segments = new Map(network.segments.map((s) => [s.id, s]))
  const junctions = new Map(network.intersections.map((i) => [i.id, i]))
  const lines = (ids: string[]) => ids.flatMap((id) => (segments.get(id) ? [segments.get(id)!.coordinates as LonLat[]] : []))
  const point = (id: string): LonLat[] => (junctions.has(id) ? [[junctions.get(id)!.lon, junctions.get(id)!.lat]] : [])

  const routes = candidate ? run?.routes?.[candidate.id] : undefined
  const blockedIds = routes?.blocked_segments.length ? routes.blocked_segments : incidentSegmentId ? [incidentSegmentId] : []
  const emsIds = routes?.ems_segments ?? []

  // an empty corridor means "every signal on the responder's route": the signalized junctions along the EMS path
  let corridorIds: string[] = []
  if (candidate?.corridor) {
    corridorIds = candidate.corridor.intersection_ids.length
      ? candidate.corridor.intersection_ids
      : emsIds.slice(0, -1).flatMap((id) => {
          const destination = segments.get(id)?.destination
          return destination && junctions.get(destination)?.signalized ? [destination] : []
        })
  }

  return {
    ems: lines(emsIds),
    diversion: lines(routes?.diversion_segments ?? []),
    blocked: lines(blockedIds),
    retimed: (candidate?.policies ?? []).flatMap((p) => point(p.intersection_id)),
    corridor: corridorIds.flatMap(point),
  }
}
