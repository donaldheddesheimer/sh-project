import type { FeatureCollection, Geometry } from 'geojson'
import { GeoJSONSource, Map as MapLibreMap, Marker, NavigationControl, setWorkerUrl } from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { useEffect, useRef } from 'react'
import type { Camera, CityState, Incident, NetworkGeometry } from '../../api/types'
import { duration } from '../../lib/format'
import type { PlanOverlay } from '../../lib/plans'
import { baseStyle, layers } from './style'
import { VEHICLE_ICON_PIXEL_RATIO, vehicleIconImages } from './vehicleIcons'

setWorkerUrl(workerUrl)

export type Selection = { kind: 'intersection' | 'segment'; id: string }

const FIT_PADDING = { top: 56, bottom: 24, left: 24, right: 24 }

interface Props {
  network: NetworkGeometry
  state: CityState | null
  cameras: Camera[]
  selection: Selection | null
  planOverlay: PlanOverlay | null
  onSelect: (selection: Selection | null) => void
}

type Features = FeatureCollection<Geometry, Record<string, unknown>>

function staticSources(network: NetworkGeometry): Record<string, Features> {
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

function markerElement(className: string, html: string): HTMLDivElement {
  const el = document.createElement('div')
  el.className = className
  el.innerHTML = html
  return el
}

export function CityMap({ network, state, cameras, selection, planOverlay, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const ready = useRef(false)
  const incidentMarkers = useRef(new Map<string, Marker>())
  const emsMarkers = useRef(new Map<string, Marker>())
  const corridorMarkers = useRef(new Map<string, Marker>())
  const cameraMarkers = useRef(new Map<string, Marker>())
  const onSelectRef = useRef(onSelect)
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])

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
      for (const [id, data] of Object.entries(staticSources(network))) {
        map.addSource(id, { type: 'geojson', data, promoteId: 'id' })
      }
      map.addSource('vehicles', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } })
      for (const { id, image } of vehicleIconImages()) {
        map.addImage(id, image, { pixelRatio: VEHICLE_ICON_PIXEL_RATIO })
      }
      for (const layer of layers) map.addLayer(layer)

      for (const label of streetLabels(network)) {
        new Marker({
          element: markerElement(`street-label${label.vertical ? ' vertical' : ''}`, label.name),
          rotation: label.vertical ? -90 : 0,
          rotationAlignment: 'map',
          offset: label.vertical ? [-16, 0] : [0, -16],
        })
          .setLngLat(label.lngLat)
          .addTo(map)
      }
      for (const station of network.stations) {
        new Marker({ element: markerElement('station-marker', `<span class="station-icon">✚</span>${station.name}`), anchor: 'right' })
          .setLngLat([station.lon, station.lat])
          .addTo(map)
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

    syncIncidentMarkers(map, incidentMarkers.current, state.incidents)

    const active = new Set<string>()
    for (const ev of state.emergency_vehicles) {
      if (!ev.location || ev.status === 'completed') continue
      active.add(ev.id)
      const text =
        ev.status === 'on_scene' ? `${ev.id} · ON SCENE` : `${ev.id} · ETA ${duration(ev.eta_s)}`
      let marker = emsMarkers.current.get(ev.id)
      if (!marker) {
        marker = new Marker({ element: markerElement('ems-marker', ''), anchor: 'bottom', offset: [0, -10] })
        marker.setLngLat([ev.location.lon, ev.location.lat]).addTo(map)
        emsMarkers.current.set(ev.id, marker)
      }
      marker.setLngLat([ev.location.lon, ev.location.lat])
      marker.getElement().innerHTML = `<span class="siren"></span>${text}`
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
        el.textContent = `⚡ ${id}`
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

function syncIncidentMarkers(map: MapLibreMap, markers: Map<string, Marker>, incidents: Incident[]) {
  const active = new Set(incidents.map((i) => i.id))
  for (const incident of incidents) {
    if (!incident.location.point) continue
    if (markers.has(incident.id)) continue
    const el = markerElement(
      `incident-marker severity-${incident.severity}`,
      '<span class="pulse"></span><span class="glyph">!</span><span class="tag"></span>',
    )
    el.querySelector<HTMLElement>('.tag')!.textContent = incident.id
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
