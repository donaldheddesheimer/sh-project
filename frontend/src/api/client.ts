import type { NetworkGeometry, ScenarioRun, ScenarioRunRequest } from './types'

/** A non-2xx response; `message` is the server's `detail` when it sent one. */
export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function request<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
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
}

export function streamUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/ws/state`
}
