import { useState, type KeyboardEvent, type PointerEvent } from 'react'

interface Props {
  /** x drags horizontally (resizes a width), y drags vertically (resizes a height). */
  axis: 'x' | 'y'
  label: string
  className?: string
  /** The panel's size right now, read when a drag starts. */
  current: () => number
  onResize: (px: number) => void
  onReset?: () => void
  min: number
  max: () => number
  /** True when moving toward the panel's own edge grows it (a panel on the right or bottom). */
  invert?: boolean
}

const STEP = 16

/** A draggable edge between two panels: pointer drag, arrow keys, double-click to restore the default. */
export function Splitter({ axis, label, className, current, onResize, onReset, min, max, invert }: Props) {
  const [drag, setDrag] = useState<{ from: number; size: number } | null>(null)
  const sign = invert ? -1 : 1
  const clamp = (px: number) => Math.min(Math.max(px, min), Math.max(min, max()))
  const pos = (e: PointerEvent) => (axis === 'x' ? e.clientX : e.clientY)

  const onKey = (e: KeyboardEvent) => {
    const keys = axis === 'x' ? ['ArrowLeft', 'ArrowRight'] : ['ArrowUp', 'ArrowDown']
    const index = keys.indexOf(e.key)
    if (index < 0) return
    e.preventDefault()
    onResize(clamp(current() + sign * (index === 0 ? -STEP : STEP)))
  }

  return (
    <div
      className={`splitter splitter-${axis}${className ? ` ${className}` : ''}`}
      data-active={drag != null}
      role="separator"
      tabIndex={0}
      aria-orientation={axis === 'x' ? 'vertical' : 'horizontal'}
      aria-label={label}
      aria-valuenow={clamp(current())}
      aria-valuemin={min}
      aria-valuemax={Math.max(min, max())}
      onPointerDown={(e) => {
        e.currentTarget.setPointerCapture(e.pointerId)
        setDrag({ from: pos(e), size: current() })
      }}
      onPointerMove={(e) => {
        if (drag) onResize(clamp(drag.size + sign * (pos(e) - drag.from)))
      }}
      onPointerUp={() => setDrag(null)}
      onPointerCancel={() => setDrag(null)}
      onKeyDown={onKey}
      onDoubleClick={onReset}
    />
  )
}
