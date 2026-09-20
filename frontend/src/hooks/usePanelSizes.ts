import { useCallback, useEffect, useState, type CSSProperties } from 'react'

const KEY = 'traffic-ops.layout.v1'

/** Sizes the operator dragged, in px. A null size keeps the stylesheet's default (which depends on the window). */
export interface PanelSizes {
  sideW: number | null // right sidebar width
  dockH: number | null // bottom dock height
  miniH: number | null // route mini-map height inside the sidebar
  activityW: number | null // activity column width inside the dock
}

const DEFAULT: PanelSizes = { sideW: null, dockH: null, miniH: null, activityW: null }

function clamp(key: keyof PanelSizes, value: number): number {
  const limits: Record<keyof PanelSizes, [number, number]> = {
    sideW: [300, Math.min(680, window.innerWidth - 396)],
    dockH: [150, window.innerHeight * 0.6],
    miniH: [120, window.innerHeight * 0.6],
    activityW: [200, window.innerWidth * 0.5],
  }
  const [min, max] = limits[key]
  return Math.round(Math.min(Math.max(value, min), Math.max(min, max)))
}

function bounded(sizes: PanelSizes): PanelSizes {
  return Object.fromEntries(Object.entries(sizes).map(([key, value]) => [key, value == null ? null : clamp(key as keyof PanelSizes, value)])) as PanelSizes
}

function load(): PanelSizes {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? 'null') as Partial<PanelSizes> | null
    if (!saved) return DEFAULT
    const size = (v: unknown) => (typeof v === 'number' && Number.isFinite(v) ? v : null)
    return { sideW: size(saved.sideW), dockH: size(saved.dockH), miniH: size(saved.miniH), activityW: size(saved.activityW) }
  } catch {
    return DEFAULT // storage blocked or corrupt: the layout still works, it just is not remembered
  }
}

/** Drag-resized panel sizes, remembered in this browser and exposed as CSS variables on the app grid. */
export function usePanelSizes() {
  const [sizes, setSizes] = useState<PanelSizes>(() => bounded(load()))

  useEffect(() => {
    const onResize = () => setSizes((previous) => bounded(previous))
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(sizes))
    } catch {
      /* not remembered */
    }
  }, [sizes])

  const set = useCallback((key: keyof PanelSizes, px: number) => setSizes((prev) => ({ ...prev, [key]: clamp(key, px) })), [])
  const clear = useCallback((key: keyof PanelSizes) => setSizes((prev) => ({ ...prev, [key]: null })), [])
  const reset = useCallback(() => setSizes(DEFAULT), [])
  const custom = Object.values(sizes).some((v) => v != null)

  const vars: Record<string, string> = {}
  if (sizes.sideW != null) vars['--side-w'] = `${sizes.sideW}px`
  if (sizes.dockH != null) vars['--dock-h'] = `${sizes.dockH}px`
  if (sizes.miniH != null) vars['--mini-h'] = `${sizes.miniH}px`
  if (sizes.activityW != null) vars['--activity-w'] = `${sizes.activityW}px`

  return { style: vars as CSSProperties, set, clear, reset, custom }
}
