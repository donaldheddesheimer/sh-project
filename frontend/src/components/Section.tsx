import { useState, type ReactNode } from 'react'
import { Icon, type IconName } from './Icon'

interface Props {
  id?: string
  title: string
  icon?: IconName
  meta?: ReactNode
  className?: string
  defaultOpen?: boolean
  children: ReactNode
}

/** Collapsible side-panel section with a sticky title bar. */
export function Section({ id, title, icon, meta, className, defaultOpen = true, children }: Props) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <section id={id} className={className ? `panel ${className}` : 'panel'}>
      <header className="panel-head">
        <button className="panel-toggle" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
          <Icon name={open ? 'chevronDown' : 'chevronRight'} size={12} className="panel-chevron" />
          {icon && <Icon name={icon} size={14} className="panel-icon" />}
          <h2 className="panel-title">{title}</h2>
        </button>
        {meta && <div className="panel-meta">{meta}</div>}
      </header>
      {open && <div className="panel-body">{children}</div>}
    </section>
  )
}
