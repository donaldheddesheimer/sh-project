import { useEffect, useState } from 'react'

// Cosmetic labels for the map's thinking overlay. They name the kind of work a response
// analysis does, never a real stage, candidate or number: `analyzeState()` owns the truthful
// progress, in the command bar and the Response plans panel.
const SUB_LABELS = [
  'Tracing alternate routes',
  'Estimating flow',
  'Checking corridor access',
  'Weighing queue spillback',
]

const ROTATE_MS = 3200

/** Hidden from assistive technology: the real status is announced by the side panel. */
export function ThinkingCaption() {
  const [index, setIndex] = useState(0)
  useEffect(() => {
    const timer = setInterval(() => setIndex((i) => (i + 1) % SUB_LABELS.length), ROTATE_MS)
    return () => clearInterval(timer)
  }, [])
  return (
    <div className="think-hud overlay-card" aria-hidden="true">
      <span className="think-dots">
        <span />
        <span />
        <span />
      </span>
      <span className="think-hud-text">
        <strong>Evaluating detours…</strong>
        <span className="think-hud-sub">{SUB_LABELS[index]}</span>
      </span>
      <span className="think-hud-note">illustration · not the agent’s plan</span>
    </div>
  )
}
