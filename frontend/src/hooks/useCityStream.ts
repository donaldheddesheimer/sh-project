import { useEffect, useRef, useState } from 'react'
import { streamUrl } from '../api/client'
import type { CityState, MetricSample, OpsEvent, StatusInfo, StreamMessage } from '../api/types'

export type { MetricSample }

// Must match the server's sampling (TREND_SAMPLE_S) so the backfilled history continues seamlessly.
const SAMPLE_EVERY_S = 5 // simulated seconds between trend samples
const MAX_SAMPLES = 180 // 15 simulated minutes
const MAX_EVENTS = 100

export interface CityStream {
  state: CityState | null
  status: StatusInfo | null
  events: OpsEvent[]
  history: MetricSample[]
  connected: boolean
}

/** Live city state over /ws/state, with automatic reconnect. */
export function useCityStream(): CityStream {
  const [state, setState] = useState<CityState | null>(null)
  const [status, setStatus] = useState<StatusInfo | null>(null)
  const [events, setEvents] = useState<OpsEvent[]>([])
  const [history, setHistory] = useState<MetricSample[]>([])
  const [connected, setConnected] = useState(false)
  const lastSample = useRef<number>(-Infinity)

  useEffect(() => {
    let socket: WebSocket | null = null
    let retry: ReturnType<typeof setTimeout> | undefined
    let disposed = false

    const record = (next: CityState) => {
      const t = next.sim_time
      if (t < lastSample.current) {
        // simulation was reset: start a fresh trend
        lastSample.current = -Infinity
        setHistory([])
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

    const connect = () => {
      socket = new WebSocket(streamUrl())
      socket.onopen = () => setConnected(true)
      socket.onmessage = (message) => {
        const msg = JSON.parse(message.data) as StreamMessage
        switch (msg.type) {
          case 'hello':
            setStatus(msg.data.status)
            setEvents(msg.data.events)
            setHistory(msg.data.history)
            lastSample.current = msg.data.history.at(-1)?.t ?? -Infinity
            if (msg.data.state) {
              setState(msg.data.state)
              record(msg.data.state)
            }
            break
          case 'state':
            setState(msg.data)
            setStatus((prev) => ({ status: msg.data.status, speed: msg.data.speed, error: prev?.error ?? null }))
            record(msg.data)
            break
          case 'status':
            setStatus(msg.data)
            break
          case 'event':
            setEvents((prev) => [...prev.slice(-(MAX_EVENTS - 1)), msg.data])
            break
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
  }, [])

  return { state, status, events, history, connected }
}
