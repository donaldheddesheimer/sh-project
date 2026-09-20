import type {
  Camera,
  DemoInfo,
  Episode,
  Implementation,
  LearningReport,
  MemoryMode,
  NetworkGeometry,
  ScenarioRun,
  ScenarioRunRequest,
} from './types'

/** The backend is always same-origin: Vite's dev proxy locally, the FastAPI app itself on Cloud Run. */
const API_BASE_URL = ''

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
  cameras: () => request<Camera[]>('GET', '/api/cameras'),
  start: () => request('POST', '/api/simulation/start'),
  pause: () => request('POST', '/api/simulation/pause'),
  reset: () => request('POST', '/api/simulation/reset'),
  speed: (multiplier: number) => request('POST', '/api/simulation/speed', { multiplier }),
  selectMap: (mapId: string) => request<NetworkGeometry>('POST', '/api/simulation/map', { map_id: mapId }),
  injectCollision: () => request('POST', '/api/incidents/inject', { type: 'collision' }),
  clearIncident: (id: string) => request('POST', `/api/incidents/${encodeURIComponent(id)}/clear`),
  dispatchEmergency: () => request('POST', '/api/emergency/dispatch', {}),
  runScenario: (req: ScenarioRunRequest = {}) => request<ScenarioRun>('POST', '/api/scenarios/run', req),
  getScenario: (id: string) => request<ScenarioRun>('GET', `/api/scenarios/${encodeURIComponent(id)}`),
  listScenarios: () => request<ScenarioRun[]>('GET', '/api/scenarios'),
  implement: (id: string) => request<Implementation>('POST', `/api/scenarios/${encodeURIComponent(id)}/implement`),
  demo: () => request<DemoInfo>('GET', '/api/demo'),
  // The console has one Arm agent button, so it sends no script and no memory mode: the backend arms
  // AUTONOMOUS_SCRIPT with memory on. Other scripts and the no-recall control run are an API-only path.
  armAgent: () => request<Episode>('POST', '/api/demo/start', {}),
  disarmAgent: () => request<DemoInfo>('POST', '/api/demo/stop'),
  demoAnalyze: (memoryMode?: MemoryMode) =>
    request<Episode>('POST', '/api/demo/analyze', memoryMode ? { memory_mode: memoryMode } : {}),
  clearMemory: () => request<{ removed: number }>('DELETE', '/api/memory'),
  learningReport: () => request<LearningReport>('GET', '/api/learning/report'),
}

/** Absolute WebSocket origin of the backend: this page's origin (the dev proxy, or Cloud Run). */
function wsBaseUrl(): string {
  const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${scheme}://${window.location.host}`
}

export function streamUrl(): string {
  return `${wsBaseUrl()}/ws/state`
}
