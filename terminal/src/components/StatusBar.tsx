import { useEffect, useState } from 'react'
import { useStore } from '../store'

function useClock() {
  const [time, setTime] = useState(() => new Date().toLocaleTimeString('en-US', { hour12: false }))
  useEffect(() => {
    const id = setInterval(
      () => setTime(new Date().toLocaleTimeString('en-US', { hour12: false })),
      1000
    )
    return () => clearInterval(id)
  }, [])
  return time
}

export default function StatusBar() {
  const time = useClock()
  const { wsStatus, cacheStatsBar, lastCommand, progressMsg, errorMsg, defaultLimit,
          pmEvents, ksEvents, centerHistory, centerHistoryIndex } = useStore()

  return (
    <div className="status-bar">
      <span className="logo">PMT</span>
      <span className="separator">│</span>
      <span className="clock">{time}</span>
      <span className="separator">│</span>
      <span>
        <span className={`status-dot ${wsStatus}`} title={`WS: ${wsStatus}`} />
        {' '}WS
      </span>
      {cacheStatsBar && (
        <>
          <span className="separator">│</span>
          <span>CACHE {cacheStatsBar.event_pairs}P / {cacheStatsBar.market_pairs}M</span>
        </>
      )}
      <span className="separator">│</span>
      <span>LIMIT {defaultLimit}</span>
      <span className="separator">│</span>
      <span>
        <span style={{ color: pmEvents.length > 0 ? 'var(--green)' : 'var(--amber-dim)' }}>
          PM {pmEvents.length > 0 ? pmEvents.length : '—'}
        </span>
        {' / '}
        <span style={{ color: ksEvents.length > 0 ? 'var(--green)' : 'var(--amber-dim)' }}>
          KS {ksEvents.length > 0 ? ksEvents.length : '—'}
        </span>
      </span>
      {centerHistory.length > 0 && (
        <>
          <span className="separator">│</span>
          <span title="Alt+← / Alt+→ to navigate history">
            {centerHistoryIndex > 0 ? '◀ ' : ''}
            HIST {centerHistoryIndex + 1}/{centerHistory.length}
            {centerHistoryIndex < centerHistory.length - 1 ? ' ▶' : ''}
          </span>
        </>
      )}
      {lastCommand && (
        <>
          <span className="separator">│</span>
          <span>CMD: {lastCommand}</span>
        </>
      )}
      <span className="spacer" />
      {errorMsg ? (
        <span style={{ color: 'var(--red)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          ✗ {errorMsg}
        </span>
      ) : progressMsg ? (
        <span style={{ color: 'var(--amber-dim)', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          ⟳ {progressMsg}
        </span>
      ) : null}
    </div>
  )
}
