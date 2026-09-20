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
  const [sizes, setSizes] = useState<PanelSizes>(load)

  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify(sizes))
    } catch {
      /* not remembered */
    }
  }, [sizes])

  const set = useCallback((key: keyof PanelSizes, px: number) => setSizes((prev) => ({ ...prev, [key]: Math.round(px) })), [])
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
