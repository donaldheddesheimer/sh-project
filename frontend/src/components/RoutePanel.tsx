import { useMemo, useRef } from 'react'
import type { NetworkGeometry, ScenarioRun, SimulationCandidate } from '../api/types'
import { routeGeometry } from '../lib/routes'
import { MiniMap } from './map/MiniMap'
import { RouteLegend } from './RouteLegend'
import { Section } from './Section'
import { Splitter } from './Splitter'

interface Props {
  network: NetworkGeometry
  run: ScenarioRun | null
  candidate: SimulationCandidate | null
  recommended: boolean
  incidentSegmentId: string | null
  onHeight: (px: number) => void
  onResetHeight: () => void
}

const FALLBACK_H = 220

/** A miniature map of the plan being shown (the recommendation, or the one hovered/selected) with its key. */
export function RoutePanel({ network, run, candidate, recommended, incidentSegmentId, onHeight, onResetHeight }: Props) {
  const frame = useRef<HTMLDivElement>(null)
  const geometry = useMemo(
    () => routeGeometry(network, run, candidate, incidentSegmentId),
    [network, run, candidate, incidentSegmentId],
  )
  const meta = candidate ? `${candidate.name || candidate.id}${recommended ? ' · recommended' : ''}` : 'incident only'
  return (
    <Section title="Response routes" icon="branch" meta={<span className="tag tag-minimal">{meta}</span>}>
      <div className="mini-map-frame" ref={frame}>
        <MiniMap network={network} geometry={geometry} />
      </div>
      <Splitter
        axis="y"
        label="Resize the route map"
        current={() => frame.current?.getBoundingClientRect().height ?? FALLBACK_H}
        onResize={onHeight}
        onReset={onResetHeight}
        min={120}
        max={() => window.innerHeight * 0.6}
      />
      <RouteLegend geometry={geometry} />
    </Section>
  )
}
