import { useStore } from '../store'

interface Props {
  style?: React.CSSProperties
}

export default function PositionsPanel({ style }: Props) {
  const { positions, showPositions } = useStore()

  if (!showPositions) return null

  return (
    <div className="events-panel-dynamic" style={style}>
      <div className="panel-header">
        <span className="panel-title">POSITIONS</span>
        <span className="panel-count">{positions.kalshi.length + positions.polymarket.length}</span>
        <button
          className="panel-close"
          onClick={() => useStore.getState().setShowPositions(false)}
          title="Close positions panel"
        >
          ✕
        </button>
      </div>
      <div className="panel-body" style={{ overflow: 'auto' }}>
        {positions.loading && (
          <div style={{ color: 'var(--amber-dim)', padding: 8, fontSize: 11 }}>Loading...</div>
        )}
        {positions.error && (
          <div style={{ color: 'var(--red)', padding: 8, fontSize: 11 }}>{positions.error}</div>
        )}

        <div style={{ color: 'var(--amber)', padding: '6px 8px 2px', fontSize: 10, letterSpacing: 1 }}>
          KALSHI
        </div>
        <table className="pos-table">
          <thead>
            <tr>
              <th>Contract</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Avg</th>
            </tr>
          </thead>
          <tbody>
            {positions.kalshi.length > 0 ? positions.kalshi.map((p, i) => (
              <tr key={`ks-${i}`}>
                <td title={p.title}>{p.ticker}</td>
                <td className={p.side === 'yes' ? 'pos-yes' : 'pos-no'}>
                  {p.side.toUpperCase()}
                </td>
                <td>{p.size}</td>
                <td>${p.avgPrice.toFixed(2)}</td>
              </tr>
            )) : (
              <tr><td colSpan={4} style={{ color: 'var(--amber-dim)' }}>No open positions</td></tr>
            )}
          </tbody>
        </table>

        <div style={{ color: 'var(--amber)', padding: '6px 8px 2px', fontSize: 10, letterSpacing: 1, marginTop: 8 }}>
          POLYMARKET
        </div>
        <table className="pos-table">
          <thead>
            <tr>
              <th>Market</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Avg</th>
            </tr>
          </thead>
          <tbody>
            {positions.polymarket.length > 0 ? positions.polymarket.map((p, i) => (
              <tr key={`pm-${i}`}>
                <td title={p.title}>{p.title.slice(0, 25)}{p.title.length > 25 ? '...' : ''}</td>
                <td className={p.side === 'yes' || p.side === 'up' ? 'pos-yes' : 'pos-no'}>
                  {p.side.toUpperCase()}
                </td>
                <td>{p.size.toFixed(1)}</td>
                <td>${p.avgPrice.toFixed(2)}</td>
              </tr>
            )) : (
              <tr><td colSpan={4} style={{ color: 'var(--amber-dim)' }}>No open positions</td></tr>
            )}
          </tbody>
        </table>

        {positions.lastFetched > 0 && (
          <div style={{ color: 'var(--amber-dim)', padding: '4px 8px', fontSize: 9, textAlign: 'right' }}>
            Updated {new Date(positions.lastFetched).toLocaleTimeString()}
          </div>
        )}
      </div>
    </div>
  )
}
