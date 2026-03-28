import { useStore } from '../store'

function fmtPrice(v: number | undefined) {
  if (v === undefined || v === 0) return '---'
  return `$${v.toFixed(2)}`
}

function fmtStrike(v: number | undefined) {
  if (v === undefined || v === 0) return 'N/A'
  return `$${v.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}

function fmtTime(iso: string | undefined) {
  if (!iso) return '---'
  try {
    const d = new Date(iso)
    return d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', timeZoneName: 'short' })
  } catch {
    return iso
  }
}

function timeRemaining(endIso: string | undefined): string {
  if (!endIso) return '---'
  try {
    const end = new Date(endIso).getTime()
    const now = Date.now()
    const diff = end - now
    if (diff <= 0) return 'EXPIRED'
    const mins = Math.floor(diff / 60000)
    const secs = Math.floor((diff % 60000) / 1000)
    return `${mins}m ${secs.toString().padStart(2, '0')}s`
  } catch {
    return '---'
  }
}

export default function BtcPanel() {
  const { btcSnapshot, loading } = useStore()

  if (loading && !btcSnapshot) {
    return (
      <div className="progress-bar" style={{ color: 'var(--amber-dim)' }}>
        Fetching BTC 15-min contracts...
      </div>
    )
  }

  if (!btcSnapshot) {
    return (
      <div className="progress-bar" style={{ color: 'var(--amber-dim)' }}>
        No BTC data. Run: BTC
      </div>
    )
  }

  const ks = btcSnapshot.kalshi
  const pm = btcSnapshot.polymarket

  // Determine window times from whichever platform has data
  const windowStart = ks?.open_time || pm?.event_start_time || ''
  const windowEnd = ks?.close_time || pm?.end_time || ''

  return (
    <div className="btc-panel">
      {/* Header */}
      <div className="btc-header">
        <span className="btc-title">BTC 15-MIN BINARY OPTIONS</span>
        <span className="btc-window">
          {fmtTime(windowStart)} - {fmtTime(windowEnd)}
        </span>
        <span className="btc-remaining">
          {timeRemaining(windowEnd)}
        </span>
      </div>

      <div className="btc-grid">
        {/* Kalshi */}
        <div className="btc-platform-card">
          <div className="btc-platform-header">KALSHI</div>
          {ks?.error ? (
            <div className="btc-error">{ks.error}</div>
          ) : ks ? (
            <>
              <div className="btc-field">
                <span className="btc-label">Contract</span>
                <a href={ks.url} target="_blank" rel="noreferrer" className="btc-value btc-link">{ks.ticker}</a>
              </div>
              <div className="btc-field">
                <span className="btc-label">Title</span>
                <span className="btc-value">{ks.title}</span>
              </div>
              <div className="btc-field">
                <span className="btc-label">Window</span>
                <span className="btc-value">{fmtTime(ks.open_time)} - {fmtTime(ks.close_time)}</span>
              </div>
              <div className="btc-field btc-strike">
                <span className="btc-label">Strike Price</span>
                <span className="btc-value btc-highlight">{fmtStrike(ks.floor_strike)}</span>
              </div>
              <div className="btc-field">
                <span className="btc-label">Source</span>
                <span className="btc-value btc-dim">CF Benchmarks BRTI (60s avg)</span>
              </div>
              <div className="btc-divider" />
              <table className="btc-price-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>YES (Up)</th>
                    <th>NO (Down)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="btc-label">Ask</td>
                    <td className="btc-ask">{fmtPrice(ks.yes_ask)}</td>
                    <td className="btc-ask">{fmtPrice(ks.no_ask)}</td>
                  </tr>
                  <tr>
                    <td className="btc-label">Bid</td>
                    <td className="btc-bid">{fmtPrice(ks.yes_bid)}</td>
                    <td className="btc-bid">{fmtPrice(ks.no_bid)}</td>
                  </tr>
                  <tr>
                    <td className="btc-label">Last</td>
                    <td className="btc-last">{fmtPrice(ks.last_price)}</td>
                    <td className="btc-last">{ks.last_price ? fmtPrice(1 - ks.last_price) : '---'}</td>
                  </tr>
                  <tr>
                    <td className="btc-label">Spread</td>
                    <td colSpan={2} className="btc-spread">
                      {ks.yes_ask && ks.yes_bid ? fmtPrice(ks.yes_ask - ks.yes_bid) : '---'}
                    </td>
                  </tr>
                </tbody>
              </table>
              <div className="btc-divider" />
              <div className="btc-field">
                <span className="btc-label">Volume</span>
                <span className="btc-value">{ks.volume?.toLocaleString('en-US', { maximumFractionDigits: 0 })} contracts</span>
              </div>
              <div className="btc-field">
                <span className="btc-label">Open Interest</span>
                <span className="btc-value">{ks.open_interest?.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
              </div>
            </>
          ) : (
            <div className="btc-error">No active Kalshi BTC 15-min market found</div>
          )}
        </div>

        {/* Polymarket */}
        <div className="btc-platform-card">
          <div className="btc-platform-header">POLYMARKET</div>
          {pm?.error ? (
            <div className="btc-error">{pm.error}</div>
          ) : pm ? (
            <>
              <div className="btc-field">
                <span className="btc-label">Contract</span>
                <a href={pm.url} target="_blank" rel="noreferrer" className="btc-value btc-link">{pm.slug}</a>
              </div>
              <div className="btc-field">
                <span className="btc-label">Title</span>
                <span className="btc-value">{pm.title}</span>
              </div>
              <div className="btc-field">
                <span className="btc-label">Window</span>
                <span className="btc-value">{fmtTime(pm.event_start_time)} - {fmtTime(pm.end_time)}</span>
              </div>
              <div className="btc-field btc-strike">
                <span className="btc-label">Strike Price</span>
                <span className="btc-value btc-highlight">
                  {pm.floor_strike ? fmtStrike(pm.floor_strike) : '---'}
                </span>
              </div>
              <div className="btc-field">
                <span className="btc-label">Source</span>
                <span className="btc-value btc-dim">Chainlink BTC/USD data stream</span>
              </div>
              <div className="btc-divider" />
              <table className="btc-price-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>UP</th>
                    <th>DOWN</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td className="btc-label">Ask</td>
                    <td className="btc-ask">{fmtPrice(pm.up_ask)}</td>
                    <td className="btc-ask">{fmtPrice(pm.down_ask)}</td>
                  </tr>
                  <tr>
                    <td className="btc-label">Bid</td>
                    <td className="btc-bid">{fmtPrice(pm.up_bid)}</td>
                    <td className="btc-bid">{fmtPrice(pm.down_bid)}</td>
                  </tr>
                  <tr>
                    <td className="btc-label">Spread</td>
                    <td colSpan={2} className="btc-spread">
                      {pm.up_ask && pm.up_bid ? fmtPrice(pm.up_ask - pm.up_bid) : '---'}
                    </td>
                  </tr>
                </tbody>
              </table>
              {pm.fee_schedule && (
                <>
                  <div className="btc-divider" />
                  <div className="btc-field">
                    <span className="btc-label">Fee Rate</span>
                    <span className="btc-value btc-dim">
                      {(pm.fee_schedule.rate * 100).toFixed(0)}% peak (taker only)
                    </span>
                  </div>
                </>
              )}
            </>
          ) : (
            <div className="btc-error">No active Polymarket BTC 15-min market found</div>
          )}
        </div>
      </div>

      <div className="btc-footer">
        <span className="btc-dim">
          Last updated: {new Date(btcSnapshot.timestamp).toLocaleTimeString()}
          {' | '}
          {btcSnapshot.streaming
            ? <><span style={{ color: 'var(--green)' }}>LIVE</span> — PM: WebSocket, KS: {btcSnapshot.kalshi_mode === 'websocket' ? 'WebSocket' : 'polling 3s'}</>
            : 'Snapshot (not streaming)'}
        </span>
      </div>
    </div>
  )
}
