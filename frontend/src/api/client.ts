import type { NetworkGeometry } from './types'

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
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
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
}

export function streamUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}/ws/state`
}
