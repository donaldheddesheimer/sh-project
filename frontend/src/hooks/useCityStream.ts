import { useCallback, useEffect, useRef, useState } from 'react'
import { streamUrl } from '../api/client'
import type {
  CityState,
  Episode,
  MetricSample,
  NetworkGeometry,
  OpsEvent,
  ScenarioRun,
  ScenarioStatus,
  StatusInfo,
  StreamMessage,
} from '../api/types'
import type { PhaseLabels } from '../lib/plans'

export type { MetricSample }

// Must match the server's sampling (TREND_SAMPLE_S) so the backfilled history continues seamlessly.
const SAMPLE_EVERY_S = 5 // simulated seconds between trend samples
const MAX_SAMPLES = 180 // 15 simulated minutes
const MAX_EVENTS = 100

const STAGE: Record<ScenarioStatus, number> = {
  queued: 0,
  proposing: 1,
  simulating: 2,
  recommending: 3,
  completed: 4,
  failed: 4,
}

/** A newer run replaces the current one; an update to the same run never moves it backwards. */
function supersedes(next: ScenarioRun, prev: ScenarioRun | null): boolean {
  if (!prev) return true
  if (next.id === prev.id) return STAGE[next.status] >= STAGE[prev.status]
  return Date.parse(next.created_at) >= Date.parse(prev.created_at)
}

/** The newest episode wins; an older one still reviewing only updates itself if it is the one shown. */
function newerEpisode(next: Episode, prev: Episode | null): boolean {
  return !prev || next.id === prev.id || Date.parse(next.created_at) >= Date.parse(prev.created_at)
}

export interface CityStream {
  state: CityState | null
  network: NetworkGeometry | null
  status: StatusInfo | null
  events: OpsEvent[]
  history: MetricSample[]
  connected: boolean
  scenario: ScenarioRun | null
  episode: Episode | null
  phaseLabels: PhaseLabels
  acceptScenario: (run: ScenarioRun) => void
}

/** Live city state over /ws/state, with automatic reconnect. */
export function useCityStream(): CityStream {
  const [state, setState] = useState<CityState | null>(null)
  const [network, setNetwork] = useState<NetworkGeometry | null>(null)
  const [status, setStatus] = useState<StatusInfo | null>(null)
  const [events, setEvents] = useState<OpsEvent[]>([])
  const [history, setHistory] = useState<MetricSample[]>([])
  const [connected, setConnected] = useState(false)
  const [scenario, setScenario] = useState<ScenarioRun | null>(null)
  const [episode, setEpisode] = useState<Episode | null>(null)
  const [phaseLabels, setPhaseLabels] = useState<PhaseLabels>({})
  const lastSample = useRef<number>(-Infinity)

  const acceptScenario = useCallback((run: ScenarioRun) => {
    setScenario((prev) => (supersedes(run, prev) ? run : prev))
  }, [])

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let disposed = false

    const record = (next: CityState) => {
      const t = next.sim_time
      if (t < lastSample.current) {
        // simulation was reset: start a fresh trend, and drop an analysis whose incident is gone
        lastSample.current = -Infinity
        setHistory([])
        setScenario((prev) => (prev && !next.incidents.some((i) => i.id === prev.incident_id) ? null : prev))
      }
      if (t - lastSample.current < SAMPLE_EVERY_S) return
      lastSample.current = t
      const m = next.metrics
      setHistory((prev) => {
        const sample: MetricSample = {
          t,
          delay: m.mean_vehicle_delay,
          queue: m.max_queue_length,
          throughput: m.throughput,
          speed: m.mean_speed,
          vehicles: m.vehicles_in_network,
        }
        const out = [...prev, sample]
        return out.length > MAX_SAMPLES ? out.slice(out.length - MAX_SAMPLES) : out
      })
    }

    // Plans refer to phases by index; remember what each green phase serves as the live signals cycle.
    const learnPhases = (next: CityState) =>
      setPhaseLabels((prev) => {
        let out = prev
        for (const i of next.intersections) {
          const label = i.phase_label?.match(/^(.+) green$/)?.[1]
          if (i.current_phase == null || !label || prev[i.id]?.[i.current_phase] === label) continue
          if (out === prev) out = { ...prev }
          out[i.id] = { ...out[i.id], [i.current_phase]: label }
        }
        return out
      })

    const connect = () => {
      socket = new WebSocket(streamUrl())
      socket.onopen = () => setConnected(true)
      socket.onmessage = (message) => {
        const msg = JSON.parse(message.data) as StreamMessage
        switch (msg.type) {
          case 'hello':
            setStatus(msg.data.status)
            setNetwork(msg.data.network)
            setEvents(msg.data.events)
            setHistory(msg.data.history)
            setScenario(msg.data.scenario ?? null)
            setEpisode(msg.data.episode ?? null)
            setPhaseLabels({})
            lastSample.current = msg.data.history.at(-1)?.t ?? -Infinity
            if (msg.data.state) {
              setState(msg.data.state)
              record(msg.data.state)
              learnPhases(msg.data.state)
            } else {
              setState(null)
            }
            break
          case 'state':
            setState(msg.data)
            setStatus((prev) => ({ status: msg.data.status, speed: msg.data.speed, error: prev?.error ?? null }))
            record(msg.data)
            learnPhases(msg.data)
            break
          case 'status':
            setStatus(msg.data)
            break
          case 'event':
            setEvents((prev) => [...prev.slice(-(MAX_EVENTS - 1)), msg.data])
            break
          case 'scenario':
            acceptScenario(msg.data)
            break
          case 'episode': {
            const next = msg.data
            setEpisode((prev) => (newerEpisode(next, prev) ? next : prev))
            break
          }
        }
      }
      socket.onclose = () => {
        setConnected(false)
        if (!disposed) retry = setTimeout(connect, 1000)
      }
    }

    connect()
    return () => {
      disposed = true
      clearTimeout(retry)
      socket?.close()
    }
  }, [acceptScenario])

  return { state, network, status, events, history, connected, scenario, episode, phaseLabels, acceptScenario }
}
