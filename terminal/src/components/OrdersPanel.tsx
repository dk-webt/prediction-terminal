import { useStore } from '../store'

interface Props {
  style?: React.CSSProperties
}

const STATUS_COLORS: Record<string, string> = {
  submitted: '#ffb000',
  resting: '#ffcc44',
  partial: '#ff8800',
  filled: '#00cc44',
  canceled: '#ff4444',
}

function statusLabel(status: string): string {
  return status.toUpperCase()
}

export default function OrdersPanel({ style }: Props) {
  const { showOrders, activeOrders, recentOrders } = useStore()

  if (!showOrders) return null

  const active = Array.from(activeOrders.values()).sort((a, b) => b.timestamp - a.timestamp)
  const recent = [...recentOrders].sort((a, b) => b.timestamp - a.timestamp)

  return (
    <div className="panel events-panel-dynamic" style={style}>
      <div className="panel-header">
        <span className="panel-title">ORDERS</span>
        <span className="panel-count">{active.length + recent.length}</span>
        <span
          className="panel-close"
          onClick={() => useStore.getState().setShowOrders(false)}
          title="Close orders panel"
        >
          ✕
        </span>
      </div>
      <div className="panel-body" style={{ overflow: 'auto' }}>

        <div style={{ color: 'var(--amber)', padding: '6px 8px 2px', fontSize: 10, letterSpacing: 1 }}>
          ACTIVE
        </div>
        <table className="pos-table">
          <thead>
            <tr>
              <th>Contract</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {active.length > 0 ? active.map((o) => (
              <tr key={o.orderId}>
                <td title={o.ticker}>
                  {o.platform === 'kalshi' ? 'KS' : 'PM'} {o.ticker.slice(-8)}
                </td>
                <td className={o.side === 'yes' || o.side === 'up' ? 'pos-yes' : 'pos-no'}>
                  {o.side.toUpperCase()}
                </td>
                <td>{o.fillCount > 0 ? `${o.fillCount}/${o.count}` : o.count}</td>
                <td style={{ color: STATUS_COLORS[o.status] || 'var(--amber)' }}>
                  {statusLabel(o.status)}
                </td>
              </tr>
            )) : (
              <tr><td colSpan={4} style={{ color: 'var(--amber-dim)' }}>No active orders</td></tr>
            )}
          </tbody>
        </table>

        <div style={{ color: 'var(--amber)', padding: '6px 8px 2px', fontSize: 10, letterSpacing: 1, marginTop: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
          RECENT
          {recent.length > 0 && (
            <span
              style={{ cursor: 'pointer', color: 'var(--amber-dim)', fontSize: 9 }}
              onClick={() => useStore.getState().clearRecentOrders()}
              title="Clear recent orders (BTC ORDERS CLEAR)"
            >
              [clear]
            </span>
          )}
        </div>
        <table className="pos-table">
          <thead>
            <tr>
              <th>Contract</th>
              <th>Side</th>
              <th>Qty</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {recent.length > 0 ? recent.map((o, i) => (
              <tr key={`${o.orderId}-${i}`} style={{ opacity: 0.7 }}>
                <td title={o.ticker}>
                  {o.platform === 'kalshi' ? 'KS' : 'PM'} {o.ticker.slice(-8)}
                </td>
                <td className={o.side === 'yes' || o.side === 'up' ? 'pos-yes' : 'pos-no'}>
                  {o.side.toUpperCase()}
                </td>
                <td>{o.fillCount > 0 ? o.fillCount : o.count}</td>
                <td style={{ color: STATUS_COLORS[o.status] || 'var(--amber-dim)' }}>
                  {statusLabel(o.status)}
                </td>
              </tr>
            )) : (
              <tr><td colSpan={4} style={{ color: 'var(--amber-dim)' }}>No recent orders</td></tr>
            )}
          </tbody>
        </table>

      </div>
    </div>
  )
}
