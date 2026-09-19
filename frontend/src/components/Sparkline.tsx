interface Props {
  values: number[]
  width?: number
  height?: number
}

/** Trend in the de-emphasis hue with the current value marked in the accent. */
export function Sparkline({ values, width = 96, height = 24 }: Props) {
  if (values.length < 2) return <svg className="sparkline" width={width} height={height} aria-hidden />
  const min = Math.min(...values)
  const max = Math.max(...values)
  const span = max - min || 1
  const pad = 3
  const x = (i: number) => pad + (i / (values.length - 1)) * (width - pad * 2)
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2)
  const d = values.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join('')
  const last = values.length - 1
  return (
    <svg className="sparkline" width={width} height={height} aria-hidden>
      <path d={d} fill="none" stroke="var(--spark)" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={x(last)} cy={y(values[last])} r={2.5} fill="var(--accent)" stroke="var(--panel)" strokeWidth={1.5} />
    </svg>
  )
}
