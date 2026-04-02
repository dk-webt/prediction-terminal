import { useState, useEffect, useRef } from 'react'
import { createChart, CrosshairMode, LineStyle, LineSeries } from 'lightweight-charts'
import type { IChartApi, ISeriesApi, UTCTimestamp } from 'lightweight-charts'
import { useStore } from '../store'
import type { BtcSnapshot } from '../types'

const STALE_THRESHOLD_MS = 10_000  // 10 seconds

function useLiveStatus(snapshot: BtcSnapshot) {
  const btcWsStatus = useStore((s) => s.btcWsStatus)
  const [now, setNow] = useState(Date.now())

  // Tick every 2s to re-evaluate staleness
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 2000)
    return () => clearInterval(id)
  }, [])

  if (btcWsStatus !== 'connected') {
    return { label: 'DISCONNECTED', color: 'var(--red)' }
  }

  const ksAge = snapshot.kalshi_last_update
    ? now - new Date(snapshot.kalshi_last_update).getTime()
    : Infinity
  const pmAge = snapshot.polymarket_last_update
    ? now - new Date(snapshot.polymarket_last_update).getTime()
    : Infinity

  if (ksAge > STALE_THRESHOLD_MS || pmAge > STALE_THRESHOLD_MS) {
    return { label: 'STALE', color: 'var(--amber)' }
  }

  return { label: 'LIVE', color: 'var(--green)' }
}

function usePlatformStatus(lastUpdate: string | undefined): { label: string; color: string } {
  const btcWsStatus = useStore((s) => s.btcWsStatus)
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 2000)
    return () => clearInterval(id)
  }, [])

  if (btcWsStatus !== 'connected') {
    return { label: 'DISCONNECTED', color: 'var(--red)' }
  }

  const age = lastUpdate ? now - new Date(lastUpdate).getTime() : Infinity
  if (age > STALE_THRESHOLD_MS) {
    return { label: 'STALE', color: 'var(--amber)' }
  }
  return { label: 'LIVE', color: 'var(--green)' }
}

function PlatformStatusDot({ lastUpdate }: { lastUpdate: string | undefined }) {
  const { label, color } = usePlatformStatus(lastUpdate)
  return (
    <span style={{ color, fontSize: 9, marginLeft: 'auto', fontWeight: 'normal' }}>{label}</span>
  )
}

function BtcFooter({ snapshot }: { snapshot: BtcSnapshot }) {
  const { label, color } = useLiveStatus(snapshot)

  return (
    <div className="btc-footer">
      <span className="btc-dim">
        Last updated: {new Date(snapshot.timestamp).toLocaleTimeString()}
        {' | '}
        <span style={{ color }}>{label}</span>
        {' — PM: WebSocket, KS: '}
        {snapshot.kalshi_mode === 'websocket' ? 'WebSocket' : 'polling 3s'}
      </span>
    </div>
  )
}

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
    if (diff <= 0) return 'ROLLING...'
    const mins = Math.floor(diff / 60000)
    const secs = Math.floor((diff % 60000) / 1000)
    return `${mins}m ${secs.toString().padStart(2, '0')}s`
  } catch {
    return '---'
  }
}

const CHART_OPTIONS = {
  layout: {
    background: { color: '#0a0800' },
    textColor: '#996800',
    fontFamily: "'Courier New', monospace",
    fontSize: 10,
  },
  grid: {
    vertLines: { color: '#2a1a00' },
    horzLines: { color: '#2a1a00' },
  },
  crosshair: { mode: CrosshairMode.Normal },
  timeScale: { timeVisible: true, secondsVisible: false },
  rightPriceScale: { borderColor: '#2a1a00' },
} as const

function BtcPriceGapChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...CHART_OPTIONS,
      height: 200,
      width: containerRef.current.clientWidth,
    })
    const series = chart.addSeries(LineSeries, {
      color: '#ffb000',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      title: '',
    })
    // Zero baseline reference line
    series.createPriceLine({
      price: 0,
      color: '#555544',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
      title: '',
    })

    chartRef.current = chart
    seriesRef.current = series

    const ro = new ResizeObserver(entries => {
      const { width } = entries[0].contentRect
      chart.applyOptions({ width })
    })
    ro.observe(containerRef.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [])

  // Subscribe to store for incremental updates
  useEffect(() => {
    let lastLen = 0
    const unsub = useStore.subscribe((state) => {
      const pts = state.btcTimeSeries.points
      if (!seriesRef.current) return
      if (pts.length < lastLen) {
        // Window reset — re-set all data
        seriesRef.current.setData(
          pts.filter(p => p.priceGap != null).map(p => ({
            time: p.time as UTCTimestamp,
            value: p.priceGap!,
          }))
        )
        lastLen = pts.length
      } else if (pts.length > lastLen) {
        for (let i = lastLen; i < pts.length; i++) {
          if (pts[i].priceGap != null) {
            seriesRef.current.update({
              time: pts[i].time as UTCTimestamp,
              value: pts[i].priceGap!,
            })
          }
        }
        lastLen = pts.length
      }
    })
    return unsub
  }, [])

  return (
    <div className="btc-chart-container">
      <div className="btc-chart-title">PRICE GAP: BRTI - CHAINLINK (USD)</div>
      <div ref={containerRef} />
    </div>
  )
}

function BtcArbitrageChart() {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const comboARef = useRef<ISeriesApi<'Line'> | null>(null)
  const comboBRef = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...CHART_OPTIONS,
      height: 200,
      width: containerRef.current.clientWidth,
    })

    const seriesA = chart.addSeries(LineSeries, {
      color: '#5599dd',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 3, minMove: 0.001 },
      title: '',
    })
    const seriesB = chart.addSeries(LineSeries, {
      color: '#ddaa44',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 3, minMove: 0.001 },
      title: '',
    })

    // Break-even reference line at 1.0
    seriesA.createPriceLine({
      price: 1.0,
      color: '#555544',
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: '',
    })

    chartRef.current = chart
    comboARef.current = seriesA
    comboBRef.current = seriesB

    const ro = new ResizeObserver(entries => {
      const { width } = entries[0].contentRect
      chart.applyOptions({ width })
    })
    ro.observe(containerRef.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [])

  useEffect(() => {
    let lastLen = 0
    const unsub = useStore.subscribe((state) => {
      const pts = state.btcTimeSeries.points
      if (!comboARef.current || !comboBRef.current) return
      if (pts.length < lastLen) {
        // Window reset
        comboARef.current.setData(
          pts.filter(p => p.comboA != null).map(p => ({
            time: p.time as UTCTimestamp, value: p.comboA!,
          }))
        )
        comboBRef.current.setData(
          pts.filter(p => p.comboB != null).map(p => ({
            time: p.time as UTCTimestamp, value: p.comboB!,
          }))
        )
        lastLen = pts.length
      } else if (pts.length > lastLen) {
        for (let i = lastLen; i < pts.length; i++) {
          if (pts[i].comboA != null) {
            comboARef.current.update({
              time: pts[i].time as UTCTimestamp, value: pts[i].comboA!,
            })
          }
          if (pts[i].comboB != null) {
            comboBRef.current.update({
              time: pts[i].time as UTCTimestamp, value: pts[i].comboB!,
            })
          }
        }
        lastLen = pts.length
      }
    })
    return unsub
  }, [])

  return (
    <div className="btc-chart-container">
      <div className="btc-chart-title">
        ARB COMBOS: <span style={{ color: '#5599dd' }}>KS YES + PM DOWN</span>
        {' / '}
        <span style={{ color: '#ddaa44' }}>KS NO + PM UP</span>
        {' (1.0 = break-even)'}
      </div>
      <div ref={containerRef} />
    </div>
  )
}

function BtcSpotChart({ field, title, color }: { field: 'coinbase' | 'chainlink'; title: string; color: string }) {
  const containerRef = useRef<HTMLDivElement>(null)
  const seriesRef = useRef<ISeriesApi<'Line'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return
    const chart = createChart(containerRef.current, {
      ...CHART_OPTIONS,
      height: 200,
      width: containerRef.current.clientWidth,
    })
    const series = chart.addSeries(LineSeries, {
      color,
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    })
    seriesRef.current = series

    const ro = new ResizeObserver(entries => {
      const { width } = entries[0].contentRect
      chart.applyOptions({ width })
    })
    ro.observe(containerRef.current)

    return () => { ro.disconnect(); chart.remove() }
  }, [color])

  useEffect(() => {
    let lastLen = 0
    const unsub = useStore.subscribe((state) => {
      const pts = state.btcTimeSeries.points
      if (!seriesRef.current) return
      if (pts.length < lastLen) {
        seriesRef.current.setData(
          pts.filter(p => p[field] != null).map(p => ({
            time: p.time as UTCTimestamp, value: p[field]!,
          }))
        )
        lastLen = pts.length
      } else if (pts.length > lastLen) {
        for (let i = lastLen; i < pts.length; i++) {
          if (pts[i][field] != null) {
            seriesRef.current.update({
              time: pts[i].time as UTCTimestamp, value: pts[i][field]!,
            })
          }
        }
        lastLen = pts.length
      }
    })
    return unsub
  }, [field])

  return (
    <div className="btc-chart-container">
      <div className="btc-chart-title">{title}</div>
      <div ref={containerRef} />
    </div>
  )
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
  const rolling = btcSnapshot.rolling

  // During roll, prefer PM times (PM updates faster); otherwise prefer KS
  const windowStart = rolling
    ? (pm?.event_start_time || '')
    : (ks?.open_time || pm?.event_start_time || '')
  const windowEnd = rolling
    ? (pm?.end_time || '')
    : (ks?.close_time || pm?.end_time || '')

  return (
    <div className="btc-panel">
      {/* Header */}
      <div className="btc-header">
        <span className="btc-title">BTC 15-MIN BINARY OPTIONS</span>
        <span className="btc-window">
          {rolling && !pm ? 'ROLLING...' : <>{fmtTime(windowStart)} - {fmtTime(windowEnd)}</>}
        </span>
        <span className="btc-remaining" style={rolling ? { color: 'var(--amber)' } : undefined}>
          {rolling && !pm ? 'SWITCHING' : timeRemaining(windowEnd)}
        </span>
      </div>

      <div className="btc-grid">
        {/* Kalshi */}
        <div className="btc-platform-card">
          <div className="btc-platform-header" style={{ display: 'flex', alignItems: 'center' }}>
            KALSHI{rolling && ks ? ' (waiting for new contract...)' : ''}
            <PlatformStatusDot lastUpdate={btcSnapshot.kalshi_last_update} />
          </div>
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
                <span className="btc-label">BRTI Live</span>
                <span className="btc-value" style={{ color: btcSnapshot.btc_coinbase != null && ks.floor_strike != null
                  ? (btcSnapshot.btc_coinbase >= ks.floor_strike ? '#00cc44' : '#ff4444')
                  : 'var(--amber)' }}>
                  {btcSnapshot.btc_coinbase != null ? `$${btcSnapshot.btc_coinbase.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}` : '---'}
                </span>
              </div>
              <div className="btc-field">
                <span className="btc-label">Source</span>
                <span className="btc-value btc-dim">CF Benchmarks BRTI ({btcSnapshot.brti_active_exchanges ?? '?'} of 6 exchanges)</span>
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
          <div className="btc-platform-header" style={{ display: 'flex', alignItems: 'center' }}>
            POLYMARKET
            <PlatformStatusDot lastUpdate={btcSnapshot.polymarket_last_update} />
          </div>
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

      {/* Time Series Charts */}
      <div className="btc-charts-section">
        <BtcPriceGapChart />
        <BtcArbitrageChart />
        <BtcSpotChart field="coinbase" title={`KALSHI SOURCE: BRTI ESTIMATE (${btcSnapshot.brti_active_exchanges ?? '?'} of 6)`} color="#ffcc44" />
        <BtcSpotChart field="chainlink" title="PM SOURCE: CHAINLINK BTC/USD" color="#00cc44" />
      </div>

      <BtcFooter snapshot={btcSnapshot} />
    </div>
  )
}
