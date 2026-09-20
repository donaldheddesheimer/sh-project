import type { FeatureCollection, Geometry } from 'geojson'
import type { ExpressionSpecification } from 'maplibre-gl'
import { GeoJSONSource, Map as MapLibreMap, Marker, NavigationControl, setWorkerUrl } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef } from 'react'
import type { Camera, CityState, Incident, NetworkGeometry } from '../../api/types'
import { duration, shortName } from '../../lib/format'
import { BASELINE_COLOR, type PlanOverlay } from '../../lib/plans'
import { pointAlong, type ThinkingRoute } from '../../lib/thinkingRoutes'
import { baseStyle, contextLayers, layers, THINKING_COLORS, THINKING_SLOTS } from './style'
import { VEHICLE_ICON_PIXEL_RATIO, vehicleIconImages } from './vehicleIcons'

setWorkerUrl(workerUrl)

export type Selection = { kind: 'intersection' | 'segment'; id: string }

/** The cosmetic "thinking" overlay: routes to fan out, and the crash they fan out from. */
export interface ThinkingOverlay {
  routes: ThinkingRoute[]
  point: [number, number]
}

const FIT_PADDING = { top: 56, bottom: 24, left: 24, right: 24 }

interface Props {
  network: NetworkGeometry
  state: CityState | null
  cameras: Camera[]
  selection: Selection | null
  planOverlay: PlanOverlay | null
  thinking: ThinkingOverlay | null
  onSelect: (selection: Selection | null) => void
  onSelectIncident: (id: string) => void
}

type Features = FeatureCollection<Geometry, Record<string, unknown>>

const EMPTY: Features = { type: 'FeatureCollection', features: [] }

export function staticSources(network: NetworkGeometry): Record<string, Features> {
  return {
    roads: {
      type: 'FeatureCollection',
      features: network.segments.map((s) => ({
        type: 'Feature',
        properties: { id: s.id, lanes: s.lanes, name: s.name },
        geometry: { type: 'LineString', coordinates: s.coordinates },
      })),
    },
    intersections: {
      type: 'FeatureCollection',
      features: network.intersections.map((i) => ({
        type: 'Feature',
        properties: { id: i.id },
        geometry: { type: 'Point', coordinates: [i.lon, i.lat] },
      })),
    },
    signals: {
      type: 'FeatureCollection',
      features: network.intersections.flatMap((i) =>
        i.approaches.map((a) => ({
          type: 'Feature' as const,
          properties: { id: `${i.id}:${a.segment_id}` },
          geometry: { type: 'Point' as const, coordinates: a.signal_point },
        })),
      ),
    },
  }
}

function streetLabels(network: NetworkGeometry): { name: string; lngLat: [number, number]; vertical: boolean }[] {
  // One label per street, on its entry road outside the grid (west or south), where the map is quiet.
  const junctions = new Set(network.intersections.map((i) => i.id))
  const seen = new Set<string>()
  const labels: { name: string; lngLat: [number, number]; vertical: boolean }[] = []
  const entries = network.segments.filter(
    (s) => !junctions.has(s.source) && ((s.direction === 'EB') || (s.direction === 'NB')),
  )
  for (const s of entries) {
    if (seen.has(s.name)) continue
    seen.add(s.name)
    const [a, b] = [s.coordinates[0], s.coordinates[s.coordinates.length - 1]]
    labels.push({ name: s.name, lngLat: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], vertical: s.direction === 'NB' })
  }
  return labels
}

/** A marker body built from static markup only. Anything that came from the network or OSM goes in as text. */
function markerElement(className: string, html: string): HTMLDivElement {
  const el = document.createElement('div')
  el.className = className
  el.innerHTML = html
  return el
}

/** A marker whose label is text (a street or station name from the OSM extract), never parsed as markup. */
function labelElement(className: string, text: string): HTMLDivElement {
  const el = document.createElement('div')
  el.className = className
  el.textContent = text
  return el
}

export function CityMap({ network, state, cameras, selection, planOverlay, thinking, onSelect, onSelectIncident }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const ready = useRef(false)
  const incidentMarkers = useRef(new Map<string, Marker>())
  const emsMarkers = useRef(new Map<string, Marker>())
  const corridorMarkers = useRef(new Map<string, Marker>())
  const cameraMarkers = useRef(new Map<string, Marker>())
  const onSelectRef = useRef(onSelect)
  const onSelectIncidentRef = useRef(onSelectIncident)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])
  useEffect(() => {
    onSelectIncidentRef.current = onSelectIncident
  }, [onSelectIncident])

  // ---- create the map once per network --------------------------------------------------
  useEffect(() => {
    const map = new MapLibreMap({
      container: container.current!,
      style: baseStyle,
      bounds: network.bounds,
      fitBoundsOptions: { padding: FIT_PADDING },
      attributionControl: false,
      maxPitch: 0,
      dragRotate: false,
    })
    mapRef.current = map
    const incidents = incidentMarkers.current
    const responders = emsMarkers.current
    const corridors = corridorMarkers.current
    const camerasOnMap = cameraMarkers.current
    map.addControl(new NavigationControl({ showCompass: false }), 'top-right')
    map.touchZoomRotate.disableRotation()

    // Keep the whole network in view as the layout settles or the window resizes,
    // until the operator pans or zooms by hand.
    let userMoved = false
    const fit = () => map.fitBounds(network.bounds, { padding: FIT_PADDING, duration: 0 })
    map.on('movestart', (e) => {
      if (e.originalEvent) userMoved = true
    })
    map.on('resize', () => {
      if (!userMoved) fit()
    })

    map.on('load', () => {
      fit()
      if (network.id === 'pittsburgh_oakland') {
        map.addSource('city-context', { type: 'geojson', data: '/oakland-context.geojson' })
        for (const layer of contextLayers) map.addLayer(layer)
      }
      for (const [id, data] of Object.entries(staticSources(network))) {
        map.addSource(id, { type: 'geojson', data, promoteId: 'id' })
      }
      map.addSource('vehicles', { type: 'geojson', data: EMPTY })
      // `lineMetrics` is what lets the thinking overlay drive `line-gradient` along a route.
      map.addSource('thinking-routes', { type: 'geojson', lineMetrics: true, data: EMPTY })
      map.addSource('thinking-particles', { type: 'geojson', data: EMPTY })
      for (const { id, image } of vehicleIconImages()) {
        map.addImage(id, image, { pixelRatio: VEHICLE_ICON_PIXEL_RATIO })
      }
      for (const layer of layers) map.addLayer(layer)

      for (const label of streetLabels(network)) {
        new Marker({
          element: labelElement(`street-label${label.vertical ? ' vertical' : ''}`, label.name),
          rotation: label.vertical ? -90 : 0,
          rotationAlignment: 'map',
          offset: label.vertical ? [-16, 0] : [0, -16],
        })
          .setLngLat(label.lngLat)
          .addTo(map)
      }
      for (const station of network.stations) {
        const el = markerElement('station-marker', '<span class="station-icon">✚</span>')
        el.append(station.name) // appended as a text node: the name is data, not markup
        new Marker({ element: el, anchor: 'right' }).setLngLat([station.lon, station.lat]).addTo(map)
      }
      ready.current = true
    })

    const select = (kind: Selection['kind']) => (e: { features?: { properties: Record<string, unknown> }[] }) => {
      const id = e.features?.[0]?.properties.id
      if (typeof id === 'string') onSelectRef.current({ kind, id })
    }
    map.on('click', 'intersections', select('intersection'))
    map.on('click', 'traffic-hit', (e) => {
      if (map.queryRenderedFeatures(e.point, { layers: ['intersections'] }).length === 0) select('segment')(e)
    })
    map.on('click', (e) => {
      if (map.queryRenderedFeatures(e.point, { layers: ['intersections', 'traffic-hit'] }).length === 0) {
        onSelectRef.current(null)
      }
    })
    for (const layer of ['intersections', 'traffic-hit']) {
      map.on('mouseenter', layer, () => (map.getCanvas().style.cursor = 'pointer'))
      map.on('mouseleave', layer, () => (map.getCanvas().style.cursor = ''))
    }

    return () => {
      ready.current = false
      incidents.clear()
      responders.clear()
      corridors.clear()
      camerasOnMap.clear()
      map.remove()
      mapRef.current = null
    }
    // Cameras arrive asynchronously and are synced by their own effect below; rebuilding the
    // whole map for them would tear down every marker and the operator's pan and zoom.
  }, [network])

  // ---- cameras ------------------------------------------------------------------------------
  // Their own effect, because the list is fetched after the first render and can be retried.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const apply = () => syncCameraMarkers(map, cameraMarkers.current, cameras)
    if (ready.current) apply()
    else map.once('load', apply)
    return () => {
      map.off('load', apply)
    }
  }, [cameras])

  // ---- live state -------------------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current
    if (!map || !ready.current || !state) return

    for (const s of state.segments) {
      map.setFeatureState({ source: 'roads', id: s.id }, { level: s.level, blocked: s.blocked_lanes.length > 0 })
    }
    for (const i of state.intersections) {
      map.setFeatureState({ source: 'intersections', id: i.id }, { congestion: i.congestion })
      for (const [segment, signal] of Object.entries(i.approach_signals)) {
        map.setFeatureState({ source: 'signals', id: `${i.id}:${segment}` }, { signal })
      }
    }
    ;(map.getSource('vehicles') as GeoJSONSource).setData({
      type: 'FeatureCollection',
      features: state.vehicles.map((v) => ({
        type: 'Feature',
        properties: { kind: v.kind, angle: v.angle },
        geometry: { type: 'Point', coordinates: [v.lon, v.lat] },
      })),
    })

    syncIncidentMarkers(map, incidentMarkers.current, state.incidents, (id) => onSelectIncidentRef.current(id))

    const active = new Set<string>()
    for (const ev of state.emergency_vehicles) {
      if (!ev.location || ev.status === 'completed') continue
      active.add(ev.id)
      const text =
        ev.status === 'on_scene' ? `${ev.id} · ON SCENE` : `${ev.id} · ETA ${duration(ev.eta_s)}`
      let marker = emsMarkers.current.get(ev.id)
      if (!marker) {
        const el = markerElement('ems-marker', '<span class="siren"></span>')
        el.append('') // the label: a text node that the ETA rewrites, so nothing is re-parsed on every frame
        marker = new Marker({ element: el, anchor: 'bottom', offset: [0, -10] })
        marker.setLngLat([ev.location.lon, ev.location.lat]).addTo(map)
        emsMarkers.current.set(ev.id, marker)
      }
      marker.setLngLat([ev.location.lon, ev.location.lat])
      const label = marker.getElement().lastChild
      if (label && label.textContent !== text) label.textContent = text
    }
    for (const [id, marker] of emsMarkers.current) {
      if (!active.has(id)) {
        marker.remove()
        emsMarkers.current.delete(id)
      }
    }
  }, [state])

  // ---- selection highlight ------------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const apply = () => {
      for (const i of network.intersections) {
        map.setFeatureState(
          { source: 'intersections', id: i.id },
          { selected: selection?.kind === 'intersection' && selection.id === i.id },
        )
      }
      map.setFilter('segment-selected', ['==', ['get', 'id'], selection?.kind === 'segment' ? selection.id : ''])
    }
    if (ready.current) apply()
    else map.once('load', apply)
  }, [selection, network])

  // ---- analysis-plan preview ---------------------------------------------------------------
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const corridors = corridorMarkers.current
    const clearBadges = () => {
      for (const marker of corridors.values()) marker.remove()
      corridors.clear()
    }
    const apply = () => {
      const retimed = planOverlay?.retimed ?? []
      const avoid = planOverlay?.avoid ?? []
      map.setFilter('plan-halo', ['in', ['get', 'id'], ['literal', retimed]])
      map.setFilter('plan-reroute', ['in', ['get', 'id'], ['literal', avoid]])
      if (planOverlay) {
        map.setPaintProperty('plan-halo', 'circle-stroke-color', planOverlay.color)
        map.setPaintProperty('plan-reroute', 'line-color', planOverlay.color)
      }
      clearBadges()
      if (!planOverlay?.corridor) return
      for (const id of planOverlay.corridor.ids) {
        const intersection = network.intersections.find((item) => item.id === id)
        if (!intersection) continue
        const el = markerElement(`corridor-badge${planOverlay.corridor.assumed ? ' assumed' : ''}`, '')
        el.style.setProperty('--plan-color', planOverlay.color)
        el.textContent = `⚡ ${shortName(intersection.name)}`
        corridors.set(id, new Marker({ element: el, anchor: 'bottom', offset: [0, -12] }).setLngLat([intersection.lon, intersection.lat]).addTo(map))
      }
    }
    if (ready.current) apply()
    else map.once('load', apply)
    return () => {
      map.off('load', apply)
      clearBadges()
    }
  }, [planOverlay, network])

  // ---- "thinking" overlay ------------------------------------------------------------------
  // Purely cosmetic: while the agent is deciding, plausible detours fan out from the crash and
  // carry a flowing pulse. It reads no candidate, agent or MCP data and is never a recommendation.
  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const routes = (thinking?.routes ?? []).slice(0, THINKING_SLOTS)
    let frame = 0
    let ring: Marker | null = null
    const faded = new Array<number>(THINKING_SLOTS).fill(-1) // last opacity written, per slot

    const apply = () => {
      if (!thinking) return
      ring = new Marker({ element: markerElement('think-ring', '<span></span><span></span><span></span>') })
        .setLngLat(thinking.point)
        .addTo(map)
      if (routes.length === 0) return // dead end: the crash pulse carries the overlay on its own
      setThinkingRoutes(map, routes)
      for (let slot = 0; slot < routes.length; slot++) {
        map.setPaintProperty(`thinking-glow-${slot}`, 'line-color', routeColor(routes[slot], slot))
      }
      if (reducedMotion()) {
        paintThinking(map, routes, null, faded) // one static frame: the routes, no flow, no particles
        return
      }
      const started = performance.now()
      let last = 0
      const step = (now: number) => {
        frame = requestAnimationFrame(step)
        if (now - last < FRAME_MS) return
        last = now
        paintThinking(map, routes, (now - started) / 1000, faded)
      }
      frame = requestAnimationFrame(step)
    }

    if (ready.current) apply()
    else map.once('load', apply)
    return () => {
      map.off('load', apply)
      cancelAnimationFrame(frame)
      // On a network change the map itself is torn down — its effect above runs first — and it
      // takes this overlay's marker and layers with it. Only tidy up while the map is still ours.
      if (mapRef.current !== map) return
      ring?.remove()
      if (!map.getLayer('thinking-particles')) return
      ;(map.getSource('thinking-particles') as GeoJSONSource).setData(EMPTY)
      // The route geometry stays until the next overlay replaces it, so the layers' opacity
      // transition has something to fade out; the crash ring and the particles cut straight away.
      for (let slot = 0; slot < THINKING_SLOTS; slot++) {
        map.setPaintProperty(`thinking-glow-${slot}`, 'line-opacity', 0)
        map.setPaintProperty(`thinking-line-${slot}`, 'line-opacity', 0)
      }
    }
  }, [thinking])

  return <div ref={container} className="map-canvas" />
}

function syncCameraMarkers(map: MapLibreMap, markers: Map<string, Marker>, cameras: Camera[]) {
  const active = new Set(cameras.filter((camera) => camera.location).map((camera) => camera.id))
  for (const camera of cameras) {
    if (!camera.location || markers.has(camera.id)) continue
    const el = markerElement('camera-marker', '')
    el.title = `${camera.id} · ${camera.name}`
    markers.set(camera.id, new Marker({ element: el }).setLngLat([camera.location.lon, camera.location.lat]).addTo(map))
  }
  for (const [id, marker] of markers) {
    if (!active.has(id)) {
      marker.remove()
      markers.delete(id)
    }
  }
}

function syncIncidentMarkers(
  map: MapLibreMap,
  markers: Map<string, Marker>,
  incidents: Incident[],
  onSelectIncident: (id: string) => void,
) {
  const current = incidents.filter((incident) => incident.status === 'active')
  const active = new Set(current.map((incident) => incident.id))
  for (const incident of current) {
    if (!incident.location.point) continue
    if (markers.has(incident.id)) continue
    const el = markerElement(
      `incident-marker severity-${incident.severity}`,
      '<span class="pulse"></span><span class="glyph">!</span><span class="tag"></span>',
    )
    el.querySelector<HTMLElement>('.tag')!.textContent = incident.id
    el.tabIndex = 0
    el.setAttribute('role', 'button')
    el.setAttribute('aria-label', `Show incident ${incident.id}`)
    el.addEventListener('click', (event) => {
      event.stopPropagation()
      onSelectIncident(incident.id)
    })
    el.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        onSelectIncident(incident.id)
      }
    })
    markers.set(
      incident.id,
      new Marker({ element: el }).setLngLat([incident.location.point.lon, incident.location.point.lat]).addTo(map),
    )
  }
  for (const [id, marker] of markers) {
    if (!active.has(id)) {
      marker.remove()
      markers.delete(id)
    }
  }
}

// ---- "thinking" overlay drawing ------------------------------------------------------------
// One shared timeline drives every route: the crash pulses alone, then each route draws itself
// out in turn and settles into a looping flow, so a wait of minutes never looks frozen.

const FRAME_MS = 33 // ~30 fps; each frame rebuilds one `line-gradient` expression per route
const LEAD_S = 0.35 // the crash ring alone before the first route appears
const STAGGER_S = 0.6 // between one route's reveal and the next
const REVEAL_S = 0.9 // for a route to draw itself from the fork to the rejoin
const FADE_S = 0.45
const FLOW_S = 2.6 // one pass of the bright band along a route
const LAP_S = 4.5 // one lap of the decorative particles
const BAND = 0.08 // half-width of the bright band, as a share of the route
const PARTICLES = 4
const EMS_COLOR = '#ff3b3b' // the console's EMS red, so the approach route reads as the responder

/** Read per overlay, not once at import, so changing the OS setting takes effect on the next crash. */
const reducedMotion = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true

/** Line alpha at rest, at the bright band, and for the glow beneath. */
const LOOK: Record<ThinkingRoute['kind'], { dim: number; bright: number; glow: number; particles: boolean }> = {
  detour: { dim: 0.45, bright: 1, glow: 0.3, particles: true },
  ems: { dim: 0.5, bright: 1, glow: 0.34, particles: true },
  // "considered and dropped": faint, neutral, and no traffic flowing along it.
  rejected: { dim: 0.16, bright: 0.3, glow: 0, particles: false },
}

const routeColor = (route: ThinkingRoute, slot: number) =>
  route.kind === 'ems' ? EMS_COLOR : route.kind === 'rejected' ? BASELINE_COLOR : THINKING_COLORS[slot]

const clamp01 = (v: number) => (v < 0 ? 0 : v > 1 ? 1 : v)

function rgba(hex: string, alpha: number): string {
  const n = parseInt(hex.slice(1), 16)
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${alpha.toFixed(3)})`
}

function setThinkingRoutes(map: MapLibreMap, routes: ThinkingRoute[]) {
  ;(map.getSource('thinking-routes') as GeoJSONSource).setData({
    type: 'FeatureCollection',
    features: routes.map((route, slot) => ({
      type: 'Feature',
      properties: { slot },
      geometry: { type: 'LineString', coordinates: route.coordinates },
    })),
  })
}

/**
 * Colors along one route: dim where it is drawn, bright at the travelling band, transparent
 * past `reveal`. A negative `head` leaves out the band altogether (reduced motion). The band
 * is clipped to the drawn part, so while a route is still drawing itself its leading edge is
 * the bright bit; `interpolate` needs strictly increasing stops, so duplicates are dropped.
 */
function lineGradient(
  color: string,
  reveal: number,
  head: number,
  dim: number,
  bright: number,
): ExpressionSpecification {
  const base = rgba(color, dim)
  const clear = rgba(color, 0)
  const stops: { at: number; color: string }[] = [{ at: 0, color: base }]
  if (head >= 0) {
    const band = [
      { at: head - BAND, color: base },
      { at: head, color: rgba(color, bright) },
      { at: head + BAND, color: base },
    ]
    for (const stop of band) if (stop.at > 0 && stop.at <= reveal) stops.push(stop)
  }
  if (reveal < 1) {
    stops.push({ at: reveal, color: base }, { at: Math.min(reveal + 0.01, 1), color: clear }, { at: 1, color: clear })
  } else {
    stops.push({ at: 1, color: base })
  }
  const out: (number | string)[] = []
  let last = -1
  for (const stop of stops) {
    const at = clamp01(stop.at)
    if (at <= last) continue
    out.push(at, stop.color)
    last = at
  }
  return ['interpolate', ['linear'], ['line-progress'], ...out] as ExpressionSpecification
}

/**
 * One frame. `t` is seconds since the overlay appeared, or null for the single static frame
 * drawn under `prefers-reduced-motion`. `faded` carries the last opacity set per slot, so the
 * cheap constant paint properties are only written while they are actually changing.
 */
function paintThinking(map: MapLibreMap, routes: ThinkingRoute[], t: number | null, faded: number[]) {
  const particles: Features['features'] = []
  routes.forEach((route, slot) => {
    const look = LOOK[route.kind]
    const color = routeColor(route, slot)
    const age = t == null ? REVEAL_S : t - (LEAD_S + slot * STAGGER_S)
    const reveal = clamp01(age / REVEAL_S)
    const opacity = clamp01(age / FADE_S)
    // While drawing, the leading edge is the bright bit; afterwards a band loops along the route.
    const head = t == null ? -1 : reveal < 1 ? reveal : (((age - REVEAL_S) / FLOW_S + slot * 0.17) % 1)
    const gradient = lineGradient(color, reveal, head, look.dim, look.bright)
    map.setPaintProperty(`thinking-line-${slot}`, 'line-gradient', gradient)
    if (Math.abs(faded[slot] - opacity) >= 0.01) {
      faded[slot] = opacity
      map.setPaintProperty(`thinking-line-${slot}`, 'line-opacity', opacity)
      map.setPaintProperty(`thinking-glow-${slot}`, 'line-opacity', opacity * look.glow)
    }
    if (t == null || reveal < 1 || !look.particles) return
    for (let i = 0; i < PARTICLES; i++) {
      const at = ((t - REVEAL_S) / LAP_S + i / PARTICLES + slot * 0.23) % 1
      particles.push({
        type: 'Feature',
        properties: { color, opacity: clamp01(Math.min(at, 1 - at) / 0.08) * opacity },
        geometry: { type: 'Point', coordinates: pointAlong(route, at * route.length) },
      })
    }
  })
  ;(map.getSource('thinking-particles') as GeoJSONSource).setData({ type: 'FeatureCollection', features: particles })
}
