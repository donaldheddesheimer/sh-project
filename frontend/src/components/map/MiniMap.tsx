import type { FeatureCollection, Geometry } from 'geojson'
import { GeoJSONSource, LngLatBounds, Map as MapLibreMap, type FilterSpecification } from 'maplibre-gl'
import { useEffect, useRef } from 'react'
import type { NetworkGeometry } from '../../api/types'
import { ROUTE_COLOR, type RouteGeometry } from '../../lib/routes'
import { staticSources } from './CityMap'
import { baseStyle, contextLayers, layers } from './style'

interface Props {
  network: NetworkGeometry
  geometry: RouteGeometry
}

const PADDING = { top: 24, bottom: 24, left: 24, right: 24 }
const ROAD_LAYERS = new Set(['road-casing', 'road-surface'])

type Features = FeatureCollection<Geometry, Record<string, unknown>>

function lineFeatures(geometry: RouteGeometry): Features {
  const kinds = ['diversion', 'ems', 'blocked'] as const // drawn in this order, blocked last
  return {
    type: 'FeatureCollection',
    features: kinds.flatMap((kind) =>
      geometry[kind].map((coordinates) => ({
        type: 'Feature' as const,
        properties: { kind },
        geometry: { type: 'LineString' as const, coordinates },
      })),
    ),
  }
}

function pointFeatures(geometry: RouteGeometry): Features {
  const point = (kind: string, coordinates: [number, number]) => ({
    type: 'Feature' as const,
    properties: { kind },
    geometry: { type: 'Point' as const, coordinates },
  })
  return {
    type: 'FeatureCollection',
    features: [...geometry.retimed.map((c) => point('retimed', c)), ...geometry.corridor.map((c) => point('corridor', c))],
  }
}

/** A small, non-interactive map that frames the plan's routes; the whole network when the plan has none. */
export function MiniMap({ network, geometry }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const ready = useRef(false)
  const latest = useRef(geometry)

  // frames what the plan acts on, or the whole network while it acts on nothing
  const frame = (map: MapLibreMap, animate: boolean) => {
    const g = latest.current
    const bounds = new LngLatBounds()
    let any = false
    for (const coordinates of [...g.diversion, ...g.ems, ...g.blocked, g.retimed, g.corridor]) {
      for (const c of coordinates) {
        bounds.extend(c)
        any = true
      }
    }
    map.fitBounds(any ? bounds : network.bounds, { padding: PADDING, maxZoom: 17, duration: animate ? 350 : 0 })
  }

  const paint = (map: MapLibreMap, animate: boolean) => {
    ;(map.getSource('mini-lines') as GeoJSONSource | undefined)?.setData(lineFeatures(latest.current))
    ;(map.getSource('mini-points') as GeoJSONSource | undefined)?.setData(pointFeatures(latest.current))
    frame(map, animate)
  }

  useEffect(() => {
    const map = new MapLibreMap({
      container: container.current!,
      style: baseStyle,
      bounds: network.bounds,
      fitBoundsOptions: { padding: PADDING },
      attributionControl: false,
      interactive: false,
    })
    mapRef.current = map
    ready.current = false
    map.on('resize', () => {
      if (ready.current) frame(map, false)
    })
    map.on('load', () => {
      if (network.id === 'pittsburgh_oakland') {
        map.addSource('city-context', { type: 'geojson', data: '/oakland-context.geojson' })
        for (const layer of contextLayers) map.addLayer(layer)
      }
      const sources = staticSources(network)
      map.addSource('roads', { type: 'geojson', data: sources.roads, promoteId: 'id' })
      for (const layer of layers.filter((l) => ROAD_LAYERS.has(l.id))) map.addLayer(layer)
      const empty: Features = { type: 'FeatureCollection', features: [] }
      map.addSource('mini-lines', { type: 'geojson', data: empty })
      map.addSource('mini-points', { type: 'geojson', data: empty })
      const line = (id: string, kind: string | null, color: string, width: number, dash?: number[]) =>
        map.addLayer({
          id,
          type: 'line',
          source: 'mini-lines',
          ...(kind ? { filter: ['==', ['get', 'kind'], kind] satisfies FilterSpecification } : {}),
          layout: { 'line-cap': dash ? 'butt' : 'round', 'line-join': 'round' },
          paint: { 'line-color': color, 'line-width': width, ...(dash ? { 'line-dasharray': dash } : {}) },
        })
      line('mini-casing', null, '#05070a', 9)
      line('mini-diversion', 'diversion', ROUTE_COLOR.diversion, 5)
      line('mini-ems', 'ems', ROUTE_COLOR.ems, 5)
      line('mini-blocked', 'blocked', ROUTE_COLOR.blocked, 3, [1, 1.2])
      map.addLayer({
        id: 'mini-points',
        type: 'circle',
        source: 'mini-points',
        paint: {
          'circle-radius': 8,
          'circle-color': 'rgba(0,0,0,0)',
          'circle-stroke-width': 3,
          'circle-stroke-color': ['match', ['get', 'kind'], 'corridor', ROUTE_COLOR.corridor, ROUTE_COLOR.retimed],
        },
      })
      ready.current = true
      paint(map, false)
    })
    return () => {
      ready.current = false
      mapRef.current = null
      map.remove()
    }
  }, [network])

  useEffect(() => {
    latest.current = geometry
    const map = mapRef.current
    if (map && ready.current) paint(map, true)
  }, [geometry])

  return <div className="mini-map" ref={container} aria-label="Response route map" role="img" />
}
