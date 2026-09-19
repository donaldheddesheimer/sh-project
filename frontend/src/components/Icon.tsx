import type { ReactNode } from 'react'

// 16px line icons drawn on a 16×16 grid (stroke = currentColor, 1.5px).
const PATHS = {
  play: <path d="M5 3.2v9.6L12.6 8z" fill="currentColor" stroke="none" />,
  pause: (
    <>
      <rect x="4" y="3" width="2.6" height="10" rx="0.6" fill="currentColor" stroke="none" />
      <rect x="9.4" y="3" width="2.6" height="10" rx="0.6" fill="currentColor" stroke="none" />
    </>
  ),
  reset: (
    <>
      <path d="M3.3 8.6a4.8 4.8 0 1 0 1.4-4" />
      <path d="M4.4 1.9v2.8h2.8" />
    </>
  ),
  warning: (
    <>
      <path d="M8 2.2 14.3 13.3H1.7z" />
      <path d="M8 6.3v3.3" />
      <circle cx="8" cy="11.4" r="0.5" fill="currentColor" />
    </>
  ),
  medical: <path d="M6.3 2.4h3.4v3.9h3.9v3.4H9.7v3.9H6.3V9.7H2.4V6.3h3.9z" />,
  branch: (
    <>
      <circle cx="4" cy="3.5" r="1.6" />
      <circle cx="4" cy="12.5" r="1.6" />
      <circle cx="12" cy="4.5" r="1.6" />
      <path d="M4 5.1v5.8M12 6.1c0 3.4-3.6 3.2-7 5" />
    </>
  ),
  chevronDown: <path d="M4 6l4 4 4-4" />,
  chevronRight: <path d="M6 4l4 4-4 4" />,
  check: <path d="M3.2 8.4l3 3 6.6-6.6" />,
  cross: <path d="M4.3 4.3l7.4 7.4M11.7 4.3l-7.4 7.4" />,
  ban: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M4 12l8-8" />
    </>
  ),
  error: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M6 6l4 4M10 6l-4 4" />
    </>
  ),
  pending: <circle cx="8" cy="8" r="5" strokeDasharray="2.2 2" />,
  spinner: <path d="M8 2.4a5.6 5.6 0 1 1-5.6 5.6" />,
  star: (
    <path
      d="M8 1.9l1.8 3.8 4.2.5-3.1 2.9.8 4.1L8 11.2l-3.7 2 .8-4.1L2 6.2l4.2-.5z"
      fill="currentColor"
      stroke="none"
    />
  ),
  shield: (
    <>
      <path d="M8 1.8l5.2 1.9v3.9c0 3.2-2.2 5.5-5.2 6.6-3-1.1-5.2-3.4-5.2-6.6V3.7z" />
      <path d="M5.8 8.1l1.6 1.6 3-3" />
    </>
  ),
  bolt: <path d="M9 1.8 3.6 9.2h4l-.8 5 5.6-7.4h-4z" fill="currentColor" stroke="none" />,
  divert: (
    <>
      <path d="M3.5 14V9.5A3.5 3.5 0 0 1 7 6h6" />
      <path d="M10.4 3.4 13 6l-2.6 2.6" />
    </>
  ),
  signal: (
    <>
      <rect x="5.2" y="1.6" width="5.6" height="12.8" rx="1.6" />
      <circle cx="8" cy="4.6" r="0.9" fill="currentColor" />
      <circle cx="8" cy="8" r="0.9" fill="currentColor" />
      <circle cx="8" cy="11.4" r="0.9" fill="currentColor" />
    </>
  ),
  chart: <path d="M2 13.5h12M3.2 11l3.1-4.2 2.6 2.1 4.1-5.6" />,
  list: <path d="M6 4h8M6 8h8M6 12h8M2.5 4h.5M2.5 8h.5M2.5 12h.5" />,
  gauge: (
    <>
      <path d="M2.4 12a5.6 5.6 0 1 1 11.2 0" />
      <path d="M8 12l2.8-3.4" />
    </>
  ),
  eye: (
    <>
      <path d="M1.6 8S4 3.6 8 3.6 14.4 8 14.4 8 12 12.4 8 12.4 1.6 8 1.6 8z" />
      <circle cx="8" cy="8" r="1.9" />
    </>
  ),
  info: (
    <>
      <circle cx="8" cy="8" r="5.6" />
      <path d="M8 7.3v3.6" />
      <circle cx="8" cy="5.2" r="0.5" fill="currentColor" />
    </>
  ),
  dot: <circle cx="8" cy="8" r="2.4" fill="currentColor" stroke="none" />,
  grid: (
    <>
      {[3, 8, 13].flatMap((y) =>
        [3, 8, 13].map((x) => <rect key={`${x}-${y}`} x={x - 1.5} y={y - 1.5} width="3" height="3" fill="currentColor" stroke="none" />),
      )}
    </>
  ),
  database: (
    <>
      <ellipse cx="8" cy="3.8" rx="5" ry="1.9" />
      <path d="M3 3.8v8.4c0 1 2.2 1.9 5 1.9s5-.9 5-1.9V3.8M3 8c0 1 2.2 1.9 5 1.9S13 9 13 8" />
    </>
  ),
  layers: <path d="M8 2 14 5 8 8 2 5zM2 8l6 3 6-3M2 11l6 3 6-3" />,
} satisfies Record<string, ReactNode>

export type IconName = keyof typeof PATHS

export function Icon({ name, size = 14, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg
      className={className ? `icon ${className}` : 'icon'}
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      {PATHS[name]}
    </svg>
  )
}
