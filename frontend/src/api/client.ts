import type { DemoInfo, Episode, Implementation, NetworkGeometry, ScenarioRun, ScenarioRunRequest } from './types'

/**
 * Absolute origin of the backend, e.g. 'https://traffic-ops-backend-xxxx.run.app'. Set
 * VITE_API_BASE_URL when the frontend is hosted apart from the backend (Vercel + Cloud Run).
 * Empty means same-origin: Vite's dev proxy locally, a reverse proxy in production.
 */
const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

/** A non-2xx response; `message` is the server's `detail` when it sent one. */
export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: 'GET' | 'POST' | 'DELETE', path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch {
      // non-JSON error body
    }
    throw new ApiError(typeof detail === 'string' ? detail : JSON.stringify(detail), response.status)
  }
  return response.json() as Promise<T>
}

export const api = {
  network: () => request<NetworkGeometry>('GET', '/api/network'),
  start: () => request('POST', '/api/simulation/start'),
  pause: () => request('POST', '/api/simulation/pause'),
  reset: () => request('POST', '/api/simulation/reset'),
  speed: (multiplier: number) => request('POST', '/api/simulation/speed', { multiplier }),
  injectCollision: () => request('POST', '/api/incidents/inject', { type: 'collision' }),
  clearIncident: (id: string) => request('POST', `/api/incidents/${encodeURIComponent(id)}/clear`),
  dispatchEmergency: () => request('POST', '/api/emergency/dispatch', {}),
  runScenario: (req: ScenarioRunRequest = {}) => request<ScenarioRun>('POST', '/api/scenarios/run', req),
  getScenario: (id: string) => request<ScenarioRun>('GET', `/api/scenarios/${encodeURIComponent(id)}`),
  listScenarios: () => request<ScenarioRun[]>('GET', '/api/scenarios'),
  implement: (id: string) => request<Implementation>('POST', `/api/scenarios/${encodeURIComponent(id)}/implement`),
  demo: () => request<DemoInfo>('GET', '/api/demo'),
  demoStart: (script: string) => request<Episode>('POST', '/api/demo/start', { script }),
  demoStop: () => request<DemoInfo>('POST', '/api/demo/stop'),
  clearMemory: () => request<{ removed: number }>('DELETE', '/api/memory'),
}

/**
 * Absolute WebSocket origin of the backend. VITE_WS_BASE_URL wins; otherwise it is derived
 * from VITE_API_BASE_URL; otherwise it falls back to this page's origin (the dev proxy).
 */
function wsBaseUrl(): string {
  const explicit = import.meta.env.VITE_WS_BASE_URL as string | undefined
  if (explicit) return explicit.replace(/\/$/, '')
  if (API_BASE_URL) return API_BASE_URL.replace(/^http/, 'ws')
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}`
}

export function streamUrl(): string {
  return `${wsBaseUrl()}/ws/state`
}
