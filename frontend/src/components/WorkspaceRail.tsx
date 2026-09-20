import { Icon, type IconName } from './Icon'

export type WorkspaceView = 'live' | 'analysis' | 'autonomous'

interface Props {
  view: WorkspaceView
  activeIncidents: number
  candidateCount: number
  episodeActive: boolean
  onChange: (view: WorkspaceView) => void
}

const VIEWS: { id: WorkspaceView; label: string; icon: IconName }[] = [
  { id: 'live', label: 'Live', icon: 'grid' },
  { id: 'analysis', label: 'Analysis', icon: 'branch' },
  { id: 'autonomous', label: 'Agent', icon: 'bolt' },
]

export function WorkspaceRail({ view, activeIncidents, candidateCount, episodeActive, onChange }: Props) {
  const counts: Record<WorkspaceView, number | null> = {
    live: activeIncidents || null,
    analysis: candidateCount || null,
    autonomous: episodeActive ? 1 : null,
  }

  return (
    <nav className="workspace-rail" aria-label="Operations views">
      <div className="rail-label">Workspace</div>
      {VIEWS.map(({ id, label, icon }) => (
        <button
          key={id}
          type="button"
          className={`rail-item${view === id ? ' active' : ''}`}
          aria-pressed={view === id}
          onClick={() => onChange(id)}
        >
          <Icon name={icon} size={18} />
          <span>{label}</span>
          {counts[id] != null && <span className="rail-count">{counts[id]}</span>}
        </button>
      ))}
      <div className="rail-spacer" />
      <div className="rail-foot" title="Simulation-backed traffic operations">OPS</div>
    </nav>
  )
}
