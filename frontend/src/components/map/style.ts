import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { CONGESTION_COLOR, SIGNAL_COLOR } from '../../lib/format'
import { SERIES } from '../../lib/plans'

// Road half-width (one travel direction) in px per lane, by zoom. Exaggerated at
// overview zooms the way web maps do, approaching true lane width when zoomed in.
const LANE_PX: [number, number][] = [
  [13, 1.6],
  [15, 3.4],
  [16, 4.8],
  [17, 7],
  [18, 10],
  [20, 34],
]

function byZoom(scale: (lanePx: number) => ExpressionSpecification | number): ExpressionSpecification {
  const stops = LANE_PX.flatMap(([zoom, px]) => [zoom, scale(px)])
  return ['interpolate', ['exponential', 1.6], ['zoom'], ...stops] as ExpressionSpecification
}

const lanes: ExpressionSpecification = ['get', 'lanes']

const levelColor: ExpressionSpecification = [
  'match',
  ['feature-state', 'level'],
  'moderate',
  CONGESTION_COLOR.moderate,
  'heavy',
  CONGESTION_COLOR.heavy,
  'severe',
  CONGESTION_COLOR.severe,
  CONGESTION_COLOR.free,
]

export const BACKGROUND = '#101a24'

export const baseStyle: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': BACKGROUND } }],
}

// A bundled OSM extract adds texture to Oakland without a network tile dependency.
// The synthetic downtown grid deliberately keeps its schematic map.
export const contextLayers: LayerSpecification[] = [
  {
    id: 'context-water',
    type: 'fill',
    source: 'city-context',
    filter: ['==', ['get', 'kind'], 'water'],
    paint: { 'fill-color': '#122a3b', 'fill-opacity': 0.9 },
  },
  {
    id: 'context-parks',
    type: 'fill',
    source: 'city-context',
    filter: ['==', ['get', 'kind'], 'park'],
    paint: { 'fill-color': '#1b342e', 'fill-opacity': 0.85, 'fill-outline-color': '#365047' },
  },
  {
    id: 'context-buildings',
    type: 'fill',
    source: 'city-context',
    filter: ['==', ['get', 'kind'], 'building'],
    paint: {
      'fill-color': '#2a3946',
      'fill-opacity': ['interpolate', ['linear'], ['zoom'], 13, 0.55, 16, 0.85],
      'fill-outline-color': '#41515f',
    },
  },
]

// ---- "thinking" overlay ------------------------------------------------------------------
// A cosmetic loading effect: routes fanning out from a crash while the agent decides. It is
// decoration, so the colors are a fixed subset of the shared series palette in a deliberately
// non-candidate order — a route's color must never read as a rank.

/** Routes are drawn one per slot, because `line-gradient` is per layer and cannot be data-driven. */
export const THINKING_SLOTS = 5
export const THINKING_COLORS = [SERIES[0], SERIES[2], SERIES[6], SERIES[3], SERIES[4]]

const CLEAR_GRADIENT: ExpressionSpecification = [
  'interpolate',
  ['linear'],
  ['line-progress'],
  0,
  'rgba(0,0,0,0)',
  1,
  'rgba(0,0,0,0)',
]

function thinkingLayers(): LayerSpecification[] {
  const out: LayerSpecification[] = []
  for (let slot = 0; slot < THINKING_SLOTS; slot++) {
    const only: ExpressionSpecification = ['==', ['get', 'slot'], slot]
    out.push({
      id: `thinking-glow-${slot}`,
      type: 'line',
      source: 'thinking-routes',
      filter: only,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      // Opacity and gradient are driven per frame by CityMap; they start invisible. The opacity
      // transition is what fades the overlay out when the decision lands and the real plan
      // overlay takes over.
      paint: {
        'line-color': THINKING_COLORS[slot],
        'line-width': ['interpolate', ['exponential', 1.6], ['zoom'], 13, 5, 16, 13, 18, 24],
        'line-blur': ['interpolate', ['linear'], ['zoom'], 13, 3, 18, 10],
        'line-opacity': 0,
        'line-opacity-transition': { duration: 320, delay: 0 },
      },
    })
    out.push({
      id: `thinking-line-${slot}`,
      type: 'line',
      source: 'thinking-routes',
      filter: only,
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-width': ['interpolate', ['exponential', 1.6], ['zoom'], 13, 1.8, 16, 3.6, 18, 6.5],
        'line-gradient': CLEAR_GRADIENT,
        'line-opacity': 0,
        'line-opacity-transition': { duration: 320, delay: 0 },
      },
    })
  }
  out.push({
    id: 'thinking-particles',
    type: 'circle',
    source: 'thinking-particles',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 13, 1.6, 16, 3, 18, 5],
      'circle-color': ['get', 'color'],
      'circle-opacity': ['get', 'opacity'],
      'circle-blur': 0.35,
    },
  })
  return out
}

const cityLayers: LayerSpecification[] = [
  {
    id: 'road-casing',
    type: 'line',
    source: 'roads',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#0a1118', 'line-width': byZoom((px) => ['+', ['*', lanes, px * 2], 3]) },
  },
  {
    id: 'road-surface',
    type: 'line',
    source: 'roads',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#27333f', 'line-width': byZoom((px) => ['*', lanes, px * 2]) },
  },
  {
    id: 'road-divider',
    type: 'line',
    source: 'roads',
    paint: { 'line-color': '#3a4552', 'line-width': 1, 'line-dasharray': [3, 3] },
  },
  {
    id: 'traffic',
    type: 'line',
    source: 'roads',
    layout: { 'line-cap': 'butt' },
    paint: {
      'line-color': levelColor,
      'line-width': byZoom((px) => ['max', 2.5, ['*', lanes, px * 0.75]]),
      'line-offset': byZoom((px) => ['*', lanes, px * 0.5]),
      'line-opacity': 0.9,
    },
  },
  {
    id: 'blocked',
    type: 'line',
    source: 'roads',
    paint: {
      'line-color': CONGESTION_COLOR.severe,
      'line-width': byZoom((px) => ['+', 3, ['*', lanes, px * 0.9]]),
      'line-offset': byZoom((px) => ['*', lanes, px * 0.5]),
      'line-dasharray': [0.6, 0.6],
      'line-opacity': ['case', ['boolean', ['feature-state', 'blocked'], false], 0.85, 0],
    },
  },
  {
    id: 'segment-selected',
    type: 'line',
    source: 'roads',
    filter: ['==', ['get', 'id'], ''],
    paint: {
      'line-color': '#ffffff',
      'line-width': byZoom((px) => ['+', 4, ['*', lanes, px]]),
      'line-offset': byZoom((px) => ['*', lanes, px * 0.5]),
      'line-opacity': 0.35,
    },
  },
  {
    id: 'plan-reroute',
    type: 'line',
    source: 'roads',
    filter: ['==', ['get', 'id'], ''],
    layout: { 'line-cap': 'butt', 'line-join': 'round' },
    paint: {
      'line-color': '#3987e5',
      'line-width': byZoom((px) => ['+', 5, ['*', lanes, px]]),
      'line-opacity': 0.9,
      'line-dasharray': [1.5, 1.5],
    },
  },
  {
    id: 'traffic-hit',
    type: 'line',
    source: 'roads',
    paint: {
      'line-color': '#000',
      'line-opacity': 0,
      'line-width': byZoom((px) => ['max', 10, ['*', lanes, px]]),
      'line-offset': byZoom((px) => ['*', lanes, px * 0.5]),
    },
  },
  {
    id: 'intersections',
    type: 'circle',
    source: 'intersections',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 5, 16, 9, 18, 16],
      'circle-color': '#0f151c',
      'circle-stroke-width': ['case', ['boolean', ['feature-state', 'selected'], false], 3, 1.5],
      'circle-stroke-color': [
        'case',
        ['boolean', ['feature-state', 'selected'], false],
        '#ffffff',
        ['>=', ['coalesce', ['feature-state', 'congestion'], 0], 0.8],
        CONGESTION_COLOR.severe,
        ['>=', ['coalesce', ['feature-state', 'congestion'], 0], 0.55],
        CONGESTION_COLOR.heavy,
        '#4b5968',
      ],
    },
  },
  {
    id: 'plan-halo',
    type: 'circle',
    source: 'intersections',
    filter: ['==', ['get', 'id'], ''],
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 8, 16, 13, 18, 21],
      'circle-color': 'rgba(0,0,0,0)',
      'circle-stroke-width': 3,
      'circle-stroke-color': '#3987e5',
      'circle-stroke-opacity': 0.95,
    },
  },
  {
    id: 'vehicles',
    type: 'symbol',
    source: 'vehicles',
    layout: {
      'icon-image': ['concat', 'veh-', ['get', 'kind']],
      'icon-rotate': ['get', 'angle'],
      'icon-rotation-alignment': 'map',
      'icon-allow-overlap': true,
      'icon-ignore-placement': true,
      'icon-size': ['interpolate', ['exponential', 1.6], ['zoom'], 13, 0.3, 15, 0.55, 16, 0.75, 17, 1.1, 18, 1.6, 20, 5],
      'symbol-sort-key': ['match', ['get', 'kind'], 'emergency', 2, 'disabled', 1, 0],
    },
  },
  {
    id: 'signals',
    type: 'circle',
    source: 'signals',
    paint: {
      'circle-radius': ['interpolate', ['linear'], ['zoom'], 14, 1.8, 16, 3.2, 18, 6],
      'circle-color': [
        'match',
        ['feature-state', 'signal'],
        'green',
        SIGNAL_COLOR.green,
        'yellow',
        SIGNAL_COLOR.yellow,
        SIGNAL_COLOR.red,
      ],
      'circle-stroke-color': '#05070a',
      'circle-stroke-width': 1,
    },
  },
]

/**
 * Draw order. The thinking overlay sits directly under `vehicles`, so it reads as an overlay
 * on the city while real traffic and the EMS responder stay on top of it.
 */
export const layers: LayerSpecification[] = cityLayers.flatMap((layer) =>
  layer.id === 'vehicles' ? [...thinkingLayers(), layer] : [layer],
)
