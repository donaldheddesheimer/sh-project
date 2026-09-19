// Mirrors backend/app/models (domain.py + api.py). Units: metres, m/s, seconds.

export type Approach = 'NB' | 'SB' | 'EB' | 'WB'
export type SignalColor = 'green' | 'yellow' | 'red'
export type CongestionLevel = 'free' | 'moderate' | 'heavy' | 'severe'
export type VehicleKind = 'car' | 'truck' | 'bus' | 'emergency' | 'disabled'
export type RunStatus = 'starting' | 'running' | 'paused' | 'error'
export type Severity = 'minor' | 'major' | 'critical'
export type EventLevel = 'info' | 'warning' | 'alert'
export type EmergencyStatus = 'en_route' | 'on_scene' | 'completed'

export interface GeoPoint {
  lat: number
  lon: number
}

export interface IntersectionState {
  id: string
  name: string
  location: GeoPoint
  signalized: boolean
  current_phase: number | null
  phase_label: string | null
  phase_remaining: number | null
  cycle_length: number | null
  program_id: string | null
  approach_signals: Partial<Record<Approach, SignalColor>>
  queue_lengths: Partial<Record<Approach, number>>
  average_speed: number
  vehicle_count: number
  congestion: number
}

export interface RoadSegmentState {
  id: string
  name: string
  source: string
  destination: string
  direction: Approach
  average_speed: number
  speed_limit: number
  vehicle_count: number
  halting_count: number
  occupancy: number
  congestion: number
  level: CongestionLevel
  blocked_lanes: number[]
}

export interface VehicleState {
  id: string
  lat: number
  lon: number
  angle: number
  speed: number
  kind: VehicleKind
}

export interface EmergencyVehicleState {
  id: string
  status: EmergencyStatus
  location: GeoPoint | null
  speed: number
  origin_segment: string
  destination_segment: string
  dispatched_at: number
  arrived_at: number | null
  eta_s: number | null
}

export interface TrafficMetrics {
  sim_time: number
  window_s: number | null
  mean_vehicle_delay: number
  max_queue_length: number
  max_queue_segment: string | null
  throughput: number
  mean_speed: number
  emergency_vehicle_eta: number | null
  vehicles_in_network: number
  vehicles_waiting_to_enter: number
}

export interface Incident {
  id: string
  type: 'collision' | 'stalled_vehicle' | 'wrong_way' | 'congestion'
  status: 'active' | 'cleared'
  severity: Severity
  location: {
    segment_id: string | null
    intersection_id: string | null
    position_m: number | null
    point: GeoPoint
    description: string
  }
  timestamp: string
  sim_time: number | null
  affected_lanes: number[]
  total_lanes: number | null
  description: string
  source: string
  sensor_ids: string[]
  object_ids: string[]
  confidence: number | null
  cleared_at: string | null
}

export interface ProviderInfo {
  smart_city: string
  agent: string
  simulator: string
}

export interface CityState {
  status: RunStatus
  speed: number
  sim_time: number
  intersections: IntersectionState[]
  segments: RoadSegmentState[]
  vehicles: VehicleState[]
  emergency_vehicles: EmergencyVehicleState[]
  incidents: Incident[]
  metrics: TrafficMetrics
  providers: ProviderInfo
}

export interface StatusInfo {
  status: RunStatus
  speed: number
  error: string | null
}

export interface OpsEvent {
  id: number
  timestamp: string
  sim_time: number | null
  level: EventLevel
  message: string
  incident_id: string | null
}

export interface SegmentGeometry {
  id: string
  name: string
  source: string
  destination: string
  direction: Approach
  lanes: number
  length: number
  speed_limit: number
  coordinates: [number, number][]
}

export interface IntersectionGeometry {
  id: string
  name: string
  lon: number
  lat: number
  signalized: boolean
  approaches: { segment_id: string; approach: Approach; signal_point: [number, number] }[]
}

export interface NetworkGeometry {
  id: string
  name: string
  center: [number, number]
  bounds: [[number, number], [number, number]]
  segments: SegmentGeometry[]
  intersections: IntersectionGeometry[]
  stations: { id: string; name: string; segment_id: string; lon: number; lat: number }[]
}

export interface MetricSample {
  t: number
  delay: number
  queue: number
  throughput: number
  speed: number
  vehicles: number
}

export type StreamMessage =
  | { type: 'hello'; data: { status: StatusInfo; state: CityState | null; events: OpsEvent[]; history: MetricSample[] } }
  | { type: 'state'; data: CityState }
  | { type: 'status'; data: StatusInfo }
  | { type: 'event'; data: OpsEvent }
