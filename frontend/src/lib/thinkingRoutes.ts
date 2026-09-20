import type { NetworkGeometry, SegmentGeometry } from '../api/types'

/**
 * Plausible alternative routes around a blocked segment, for the map's "thinking" overlay.
 *
 * Decoration, not analysis. Nothing here reads a candidate plan, an agent message or an MCP
 * tool, and the routes are never the agent's reasoning: they exist so the map has something
 * to say while a decision takes seconds to minutes. The search runs on the geometry the
 * console already has (`NetworkGeometry`), so it works on every bundled map without a
 * backend call.
 */

export type ThinkingRouteKind = 'detour' | 'ems' | 'rejected'

export interface ThinkingRoute {
  id: string
  kind: ThinkingRouteKind
  /** Merged [lon, lat] polyline of the segments on the route. */
  coordinates: [number, number][]
  /** Metres from the start at each coordinate; one entry per coordinate. */
  cumulative: number[]
  /** Route length in metres. */
  length: number
}

/** The crash, as primitives, so a caller can memoize on values rather than on a live frame. */
export interface ThinkingIncident {
  segmentId: string | null
  positionM: number | null
  lon: number
  lat: number
}

// Search bounds. They keep Oakland (~900 directed segments, ~320 junctions) cheap and stop a
// detour from wandering off across the map; the numbers are chosen to look right, not to be optimal.
const MAX_HOPS = 12
const MAX_ROUTES = 4 // detours, the last of which becomes the "rejected" branch
const MAX_ATTEMPTS = 8
const REUSE_PENALTY = 3.5 // multiplies an already-used segment's weight on the next search
const MAX_COST_RATIO = 2.5 // drop an alternative this much slower than the best detour
const MAX_OVERLAP = 0.7 // drop an alternative sharing this much of its length with a kept one
const ARTERIAL_BONUS = 0.85 // discount for multi-lane roads, so Oakland detours follow through streets

const EARTH_M_PER_DEG = 111_320
const SAME_POINT_DEG = 1e-7 // ~1 cm: a shared junction vertex between two segments

const CACHE_MAX = 12
const cache = new Map<string, ThinkingRoute[]>()

/**
 * Up to four detours from the junction upstream of the crash to the one downstream, plus an
 * EMS approach from the nearest station. Empty when the crash has no segment or no way around
 * it; callers then show the crash pulse alone.
 */
export function buildThinkingRoutes(network: NetworkGeometry, incident: ThinkingIncident): ThinkingRoute[] {
  const key = `${network.id}|${incident.segmentId}|${Math.round(incident.positionM ?? -1)}`
  const hit = cache.get(key)
  if (hit) return hit
  const routes = compute(network, incident)
  const oldest = cache.keys().next().value
  if (cache.size >= CACHE_MAX && oldest != null) cache.delete(oldest)
  cache.set(key, routes)
  return routes
}

function compute(network: NetworkGeometry, incident: ThinkingIncident): ThinkingRoute[] {
  const blocked = network.segments.find((s) => s.id === incident.segmentId)
  if (!blocked) return []
  const cosLat = Math.cos((network.center[1] * Math.PI) / 180)
  const graph = buildGraph(network, blocked.id)
  const routes: ThinkingRoute[] = []
  // The crashed segment's inbound flow has to get past it: fork upstream, rejoin downstream.
  const paths = alternatives(graph, blocked.source, blocked.destination)
  paths.forEach((path, i) => {
    // The longest surviving alternative stands in for a branch that gets looked at and dropped.
    const kind: ThinkingRouteKind = paths.length >= 3 && i === paths.length - 1 ? 'rejected' : 'detour'
    push(routes, toRoute(`detour-${i}`, kind, polyline(path), cosLat))
  })
  const ems = emsPath(network, graph, blocked, incident, cosLat)
  if (ems) push(routes, toRoute('ems', 'ems', ems, cosLat))
  return routes
}

const push = (routes: ThinkingRoute[], route: ThinkingRoute | null) => {
  if (route) routes.push(route)
}

// ---- graph ------------------------------------------------------------------------------

interface Edge {
  segment: SegmentGeometry
  cost: number
}

/** Free-flow seconds, discounted on multi-lane roads so detours prefer arterials to side streets. */
const edgeCost = (s: SegmentGeometry) =>
  (s.length / Math.max(s.speed_limit, 1)) * (s.lanes >= 2 ? ARTERIAL_BONUS : 1)

/** Directed junction graph over the whole network, with the blocked segment removed. */
function buildGraph(network: NetworkGeometry, blockedId: string): Map<string, Edge[]> {
  const graph = new Map<string, Edge[]>()
  for (const segment of network.segments) {
    if (segment.id === blockedId) continue
    const edge = { segment, cost: edgeCost(segment) }
    const edges = graph.get(segment.source)
    if (edges) edges.push(edge)
    else graph.set(segment.source, [edge])
  }
  return graph
}

type HeapEntry = { cost: number; node: string; hops: number }

function heapPush(heap: HeapEntry[], entry: HeapEntry) {
  heap.push(entry)
  for (let i = heap.length - 1; i > 0; ) {
    const parent = (i - 1) >> 1
    if (heap[parent].cost <= heap[i].cost) break
    ;[heap[parent], heap[i]] = [heap[i], heap[parent]]
    i = parent
  }
}

function heapPop(heap: HeapEntry[]): HeapEntry | null {
  if (heap.length === 0) return null
  const top = heap[0]
  const last = heap.pop() as HeapEntry
  if (heap.length === 0) return top
  heap[0] = last
  for (let i = 0; ; ) {
    const left = i * 2 + 1
    let small = i
    if (left < heap.length && heap[left].cost < heap[small].cost) small = left
    if (left + 1 < heap.length && heap[left + 1].cost < heap[small].cost) small = left + 1
    if (small === i) break
    ;[heap[small], heap[i]] = [heap[i], heap[small]]
    i = small
  }
  return top
}

/**
 * Dijkstra on travel time, with per-segment penalties so a re-run finds a different way round.
 * The hop cap is applied while expanding, which makes it an approximation — a junction first
 * settled by a long chain of cheap hops can hide a costlier route that would fit the cap. That
 * is acceptable for an overlay whose only job is to look plausible.
 */
function shortestPath(
  graph: Map<string, Edge[]>,
  from: string,
  to: string,
  penalties: Map<string, number>,
): SegmentGeometry[] | null {
  if (from === to) return []
  const best = new Map<string, number>([[from, 0]])
  const cameFrom = new Map<string, { via: SegmentGeometry; from: string }>()
  const settled = new Set<string>()
  const heap: HeapEntry[] = []
  heapPush(heap, { cost: 0, node: from, hops: 0 })
  for (let entry = heapPop(heap); entry; entry = heapPop(heap)) {
    if (settled.has(entry.node)) continue
    settled.add(entry.node)
    if (entry.node === to) break
    if (entry.hops >= MAX_HOPS) continue
    for (const edge of graph.get(entry.node) ?? []) {
      const next = edge.segment.destination
      const cost = entry.cost + edge.cost * (penalties.get(edge.segment.id) ?? 1)
      if (cost >= (best.get(next) ?? Infinity)) continue
      best.set(next, cost)
      cameFrom.set(next, { via: edge.segment, from: entry.node })
      heapPush(heap, { cost, node: next, hops: entry.hops + 1 })
    }
  }
  if (!settled.has(to)) return null
  const path: SegmentGeometry[] = []
  for (let node = to; node !== from; ) {
    const step = cameFrom.get(node)
    if (!step) return null
    path.unshift(step.via)
    node = step.from
  }
  return path
}

const pathCost = (path: SegmentGeometry[]) => path.reduce((sum, s) => sum + edgeCost(s), 0)

/** Share of `path`'s length that also lies on `other`. */
function overlap(path: SegmentGeometry[], other: SegmentGeometry[]): number {
  const ids = new Set(other.map((s) => s.id))
  const total = path.reduce((sum, s) => sum + s.length, 0)
  if (total <= 0) return 0
  return path.reduce((sum, s) => sum + (ids.has(s.id) ? s.length : 0), 0) / total
}

/** Visibly different ways round: shortest first, then re-runs that pay to reuse what is drawn. */
function alternatives(graph: Map<string, Edge[]>, from: string, to: string): SegmentGeometry[][] {
  const penalties = new Map<string, number>()
  const kept: SegmentGeometry[][] = []
  const seen = new Set<string>()
  for (let attempt = 0; attempt < MAX_ATTEMPTS && kept.length < MAX_ROUTES; attempt++) {
    const path = shortestPath(graph, from, to, penalties)
    if (!path || path.length === 0) break
    for (const s of path) penalties.set(s.id, (penalties.get(s.id) ?? 1) * REUSE_PENALTY)
    const signature = path.map((s) => s.id).join('>')
    if (seen.has(signature)) continue
    seen.add(signature)
    if (kept.length === 0) {
      kept.push(path)
      continue
    }
    if (pathCost(path) > pathCost(kept[0]) * MAX_COST_RATIO) continue
    if (kept.some((other) => overlap(path, other) > MAX_OVERLAP)) continue
    kept.push(path)
  }
  return kept
}

// ---- EMS approach -----------------------------------------------------------------------

/** Nearest station to the crash, from the station road to the crash point itself. */
function emsPath(
  network: NetworkGeometry,
  graph: Map<string, Edge[]>,
  blocked: SegmentGeometry,
  incident: ThinkingIncident,
  cosLat: number,
): [number, number][] | null {
  const station = network.stations
    .map((s) => ({ station: s, away: metresBetween([s.lon, s.lat], [incident.lon, incident.lat], cosLat) }))
    .sort((a, b) => a.away - b.away)[0]?.station
  if (!station) return null
  // The crash segment is out of the graph, so the search stops at its upstream junction and the
  // last leg is that segment cut at the crash.
  const tail = truncate(blocked.coordinates, incident.positionM, cosLat)
  const from = network.segments.find((s) => s.id === station.segment_id)
  if (!from) return null
  if (from.id === blocked.id) return tail
  const path = shortestPath(graph, from.destination, blocked.source, new Map())
  if (!path) return null
  return dedupe([...polyline([from, ...path]), ...tail])
}

// ---- geometry ---------------------------------------------------------------------------

/** Equirectangular metres; fine at city scale and the same approximation the map draws with. */
function metresBetween(a: [number, number], b: [number, number], cosLat: number): number {
  return Math.hypot((a[0] - b[0]) * cosLat, a[1] - b[1]) * EARTH_M_PER_DEG
}

const polyline = (segments: SegmentGeometry[]) => dedupe(segments.flatMap((s) => s.coordinates))

/** Drops the repeated vertex where two segments meet; real gaps at junctions are left alone. */
function dedupe(coords: [number, number][]): [number, number][] {
  const out: [number, number][] = []
  for (const c of coords) {
    const last = out[out.length - 1]
    if (last && Math.abs(last[0] - c[0]) < SAME_POINT_DEG && Math.abs(last[1] - c[1]) < SAME_POINT_DEG) continue
    out.push(c)
  }
  return out
}

/** The first `metres` of a polyline, with an interpolated last vertex. */
function truncate(coords: [number, number][], metres: number | null, cosLat: number): [number, number][] {
  if (metres == null || metres <= 0 || coords.length < 2) return coords
  const out: [number, number][] = [coords[0]]
  let walked = 0
  for (let i = 1; i < coords.length; i++) {
    const step = metresBetween(coords[i - 1], coords[i], cosLat)
    if (walked + step >= metres) {
      const t = step > 0 ? (metres - walked) / step : 0
      out.push([
        coords[i - 1][0] + (coords[i][0] - coords[i - 1][0]) * t,
        coords[i - 1][1] + (coords[i][1] - coords[i - 1][1]) * t,
      ])
      return out
    }
    walked += step
    out.push(coords[i])
  }
  return out
}

function toRoute(
  id: string,
  kind: ThinkingRouteKind,
  coordinates: [number, number][],
  cosLat: number,
): ThinkingRoute | null {
  if (coordinates.length < 2) return null
  const cumulative = [0]
  for (let i = 1; i < coordinates.length; i++) {
    cumulative.push(cumulative[i - 1] + metresBetween(coordinates[i - 1], coordinates[i], cosLat))
  }
  const length = cumulative[cumulative.length - 1]
  return length > 0 ? { id, kind, coordinates, cumulative, length } : null
}

/** [lon, lat] a given number of metres along a route; used to walk the decorative particles. */
export function pointAlong(route: ThinkingRoute, metres: number): [number, number] {
  const { coordinates, cumulative } = route
  const target = Math.min(Math.max(metres, 0), route.length)
  let i = 1
  while (i < cumulative.length - 1 && cumulative[i] < target) i++
  const span = cumulative[i] - cumulative[i - 1]
  const t = span > 0 ? (target - cumulative[i - 1]) / span : 0
  return [
    coordinates[i - 1][0] + (coordinates[i][0] - coordinates[i - 1][0]) * t,
    coordinates[i - 1][1] + (coordinates[i][1] - coordinates[i - 1][1]) * t,
  ]
}
