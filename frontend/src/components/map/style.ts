import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from 'maplibre-gl'
import { CONGESTION_COLOR, SIGNAL_COLOR } from '../../lib/format'

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

export const BACKGROUND = '#0a0e13'

export const baseStyle: StyleSpecification = {
  version: 8,
  sources: {},
  layers: [{ id: 'background', type: 'background', paint: { 'background-color': BACKGROUND } }],
}

export const layers: LayerSpecification[] = [
  {
    id: 'road-casing',
    type: 'line',
    source: 'roads',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#05070a', 'line-width': byZoom((px) => ['+', ['*', lanes, px * 2], 3]) },
  },
  {
    id: 'road-surface',
    type: 'line',
    source: 'roads',
    layout: { 'line-cap': 'round', 'line-join': 'round' },
    paint: { 'line-color': '#1a2129', 'line-width': byZoom((px) => ['*', lanes, px * 2]) },
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
