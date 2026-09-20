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

// --- Smart City input (feature/vss-input) ---

export interface Camera {
  id: string
  name: string
  location: GeoPoint | null
  intersection_id: string | null
  status: string
}

export interface IncidentMatch {
  method: 'geometry' | 'place' | 'sensor' | null
  distance_m: number | null
  confidence: number
  notes: string[]
  lane_assumed: boolean
  mirrored: boolean
  reason: string | null
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
  // keyed by incoming segment id; the segment's direction is only a label and can repeat at one junction
  approach_signals: Record<string, SignalColor>
  queue_lengths: Record<string, number>
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

// --- twin engine: per-responder EMS response (backend EmergencyResponse) ---
export interface EmergencyResponse {
  vehicle_id: string
  destination_segment: string
  dispatched_at: number
  arrived_at: number | null
  response_s: number | null // arrived_at - max(window start, dispatched_at); null until it arrives
}

export interface TrafficMetrics {
  sim_time: number
  window_s: number | null
  mean_vehicle_delay: number
  max_queue_length: number
  max_queue_segment: string | null
  throughput: number
  mean_speed: number
  // measured window: the last responder's realised response time (null until every responder has arrived);
  // live metrics: the soonest estimated time to scene over en-route responders (null when none)
  emergency_vehicle_eta: number | null
  // measured window: each responder of that same window (same set as the ETA); always empty on live metrics
  emergency_responses: EmergencyResponse[]
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
    point: GeoPoint | null
    description: string
    match: IncidentMatch | null
  }
  timestamp: string
  sim_time: number | null
  affected_lanes: number[]
  total_lanes: number | null
  description: string
  source: string
  external_id: string | null
  external_category: string | null
  type_mapping_note: string | null
  sensor_ids: string[]
  object_ids: string[]
  confidence: number | null
  vlm_confirmed: boolean | null
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
  attribution: string | null
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

// --- Scenario analysis (milestone-2 contract: backend/app/models/scenario.py) ---

export interface SignalPolicy {
  intersection_id: string
  phase_durations: Record<string, number> // phase index -> seconds (JSON object keys are strings)
  offset_s: number | null
  reason: string
}

export interface EmergencyCorridor {
  intersection_ids: string[] // empty = every signal on a responder's route
  detection_distance_m: number
  min_served_green_s: number
  max_hold_s: number
  reason: string
}

export interface RerouteAction {
  avoid_segment_ids: string[]
  compliance: number // 0..1 share of affected drivers who divert
  reason: string
}

export type CandidateStatus = 'pending' | 'running' | 'completed' | 'rejected' | 'failed'

export interface SimulationCandidate {
  id: string // "baseline" = do-nothing reference
  name: string
  description: string
  policies: SignalPolicy[]
  corridor: EmergencyCorridor | null
  reroutes: RerouteAction[]
  status: CandidateStatus
  metrics: TrafficMetrics | null // horizon metrics (window_s set) once completed
  timeline: MetricSample[] // live metrics sampled during the horizon (t = simulation time)
  violations: string[] // safety-validator findings when rejected
  notes: string[]
  wall_time_s: number | null
}

export interface Recommendation {
  candidate_id: string
  summary: string
  rationale: string[]
}

export type ScenarioStatus = 'queued' | 'proposing' | 'simulating' | 'recommending' | 'completed' | 'failed'
export type MemoryMode = 'use' | 'ignore'

export interface ScenarioRunRequest {
  incident_id?: string | null
  incident_ids?: string[] | null // analyze several incidents together (overrides incident_id)
  horizon_s?: number
  ems_probe?: boolean
  memory_mode?: MemoryMode
}

export interface ScenarioRun {
  id: string
  incident_id: string // the primary incident (earliest detected)
  incident_ids: string[] // every incident analyzed together
  status: ScenarioStatus
  agent: string
  created_at: string
  completed_at: string | null
  snapshot_sim_time: number | null
  horizon_s: number
  ems_probe: boolean
  candidates: SimulationCandidate[]
  recommendation: Recommendation | null
  error: string | null
  rounds: number // simulation rounds (an MCP agent may run several)
  memory_mode: MemoryMode
  recalled: string[] // remembered episodes given to the agent as lessons
  recall_provenance: RecalledExperience[]
  implementation: Implementation | null // set once the recommendation was applied to the live city
}

// --- Autonomous episode (backend/app/models/episode.py) ---

export interface Implementation {
  run_id: string
  candidate_id: string
  candidate_name: string
  implemented_by: string // agent | operator | coordinator
  incident_ids: string[]
  implemented_at: number // simulation time the plan went live
  snapshot_sim_time: number | null
  staleness_s: number | null // live time between the branch snapshot and the apply
  policies: Record<string, string> // intersection -> program id now running
  corridor: boolean
  diverted: number
  ems_dispatch_ids: string[] // responders dispatched with the plan
  ems_en_route_ids: string[] // responders already on the way at the snapshot; the monitor times them too
  notes: string[]
}

export type EpisodeStatus =
  | 'armed'
  | 'detected'
  | 'analyzing'
  | 'monitoring'
  | 'reviewing'
  | 'completed'
  | 'superseded'
  | 'aborted'
  | 'failed'

export type Outcome = 'effective' | 'ineffective' | 'inconclusive'

export interface ResponseCheck {
  kind: 'corridor' | 'diversion'
  ok: boolean | null
  detail: string
}

export interface RecalledExperience {
  id: string
  similarity: number
  structured_score: number
  semantic_score: number | null
  combined_score: number
  ranking_score: number
  provisional: boolean
  trusted: boolean
  incidents: string[]
  chosen: string
  chosen_name: string
  kinds: string[]
  verdict: Outcome
  summary: string
  what_worked: string[]
  what_didnt: string[]
  next_time: string[]
  numbers: Record<string, number | null>
}

export interface WindowStats {
  samples: number
  mean_delay: number
  end_delay: number
  peak_queue: number
  mean_throughput: number
  mean_speed_mps: number
  incident_queue_end: number
  ems_response_s: number | null
}

export interface Scorecard {
  candidate_id: string
  candidate_name: string
  window_s: number
  realised: WindowStats
  predicted: WindowStats | null
  predicted_baseline: WindowStats | null
  pre: WindowStats | null
  predicted_gain: Record<string, number | null>
  prediction_error: Record<string, number | null> // realised minus predicted: delay_pct, queue, ems_s
  realised_vs_baseline: Record<string, number | null> // realised minus the predicted do-nothing baseline
  staleness_s: number | null
  candidates_tried: number
  rejected: number
  picked_best: boolean | null
  best_by_rubric: string | null
  material: boolean
  outcome: Outcome
  checks: ResponseCheck[]
  provisional: boolean
  notes: string[]
}

export interface Lesson {
  verdict: Outcome
  summary: string
  what_worked: string[]
  what_didnt: string[]
  next_time: string[]
  confidence: number
  reviewer: string
}

export interface EpisodeStep {
  status: EpisodeStatus
  at: string
  sim_time: number | null
  message: string
}

export interface Episode {
  id: string // "EP-0001"
  script_id: string | null
  status: EpisodeStatus
  analyst: string
  reviewer: string
  created_at: string
  completed_at: string | null
  incident_ids: string[]
  supersedes: string | null
  superseded_by: string | null
  run_id: string | null
  rounds: number
  candidates: number
  memory_mode: MemoryMode
  analysis_wall_s: number | null
  detected_sim_time: number | null
  implemented_sim_time: number | null
  monitor_s: number
  monitor_progress_s: number
  implementation: Implementation | null
  scorecard: Scorecard | null
  lesson: Lesson | null
  recalled: string[]
  recall_provenance: RecalledExperience[]
  memory_path: string | null
  error: string | null
  steps: EpisodeStep[]
}

export interface DemoScriptInfo {
  id: string
  name: string
  description: string
  crashes_at: number[]
  monitor_s: number | null
}

export interface MemoryStats {
  enabled: boolean
  directory: string
  episodes: number
  latest: string[]
}

export interface DemoInfo {
  scripts: DemoScriptInfo[]
  armed: string | null // autonomous response is on while a script is armed
  analyst: string
  current: Episode | null
  memory: MemoryStats
}

export interface LearningReportEpisode {
  id: string
  script_id: string | null
  analyst: string
  memory_mode: MemoryMode
  eligible_for_recall: boolean
  recalled_sources: string[]
  recall_provenance: RecalledExperience[]
  warm: boolean
  transfer: boolean
  candidate_order: string[]
  rounds: number
  candidates_tried: number
  analysis_wall_s: number | null
  selected_plan: string
  verdict: Outcome
  delay_vs_baseline_pct: number | null
  prediction_error: Record<string, number | null>
  staleness_s: number | null
  checks: ResponseCheck[]
  provisional: boolean
}

export interface LearningComparison {
  script_id: string | null
  warm_episode_id: string
  control_episode_id: string
  transfer: boolean
  useful: boolean | null
  behavior_changes: string[]
  deltas: Record<string, number | null>
}

export interface LearningReport {
  episodes: LearningReportEpisode[]
  comparisons: LearningComparison[]
}

export type StreamMessage =
  | {
      type: 'hello'
      data: {
        status: StatusInfo
        state: CityState | null
        events: OpsEvent[]
        history: MetricSample[]
        scenario?: ScenarioRun | null // latest analysis run
        episode?: Episode | null // latest autonomous episode
      }
    }
  | { type: 'state'; data: CityState }
  | { type: 'status'; data: StatusInfo }
  | { type: 'event'; data: OpsEvent }
  | { type: 'scenario'; data: ScenarioRun } // any change to an analysis run
  | { type: 'episode'; data: Episode } // any change to an episode
