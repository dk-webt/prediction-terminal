import { useStore } from '../store'
import type { NormalizedEvent } from '../types'

function fmtPrice(p: number) {
  return `${(p * 100).toFixed(0)}¢`
}

function topPrice(ev: NormalizedEvent) {
  if (!ev.markets.length) return '—'
  const m = ev.markets[0]
  return `Y:${fmtPrice(m.yes_price)} N:${fmtPrice(m.no_price)}`
}

interface Props {
  source: 'PM' | 'KS'
  className: string
  runCommand: (cmd: string) => void
  focused: boolean
}

export default function EventsPanel({ source, className, runCommand, focused }: Props) {
  const { pmEvents, ksEvents, activeView, selectedIndex, setSelectedIndex, setActiveView } =
    useStore()

  const events = source === 'PM' ? pmEvents : ksEvents
  const isActive = activeView === source
  const label = source === 'PM' ? 'POLYMARKET' : 'KALSHI'
  const cmdKey = source

  const onRowClick = (i: number) => {
    setActiveView(source)
    setSelectedIndex(i)
  }

  return (
    <div className={`panel ${className}${focused ? ' focused' : ''}`}>
      <div className="panel-header">
        <span className="panel-title">{label}</span>
        {events.length > 0 && (
          <span className="panel-count">{events.length}</span>
        )}
        <span
          style={{ marginLeft: 'auto', cursor: 'pointer', color: 'var(--amber-dim)', fontSize: 10 }}
          onClick={() => runCommand(cmdKey)}
          title={`Fetch ${label} events`}
        >
          ↻
        </span>
      </div>

      <div className="panel-body">
        {events.length === 0 ? (
          <div
            style={{
              padding: '12px 8px',
              color: 'var(--gray)',
              fontSize: 11,
              cursor: 'pointer',
            }}
            onClick={() => runCommand(cmdKey)}
          >
            Type {cmdKey} to load events
          </div>
        ) : (
          events.map((ev, i) => (
            <div
              key={ev.id}
              className={`event-row ${isActive && selectedIndex === i ? 'selected' : ''}`}
              onClick={() => onRowClick(i)}
            >
              <span className="ev-title" title={ev.title}>
                {ev.title}
              </span>
              <span className="ev-price">{topPrice(ev)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
