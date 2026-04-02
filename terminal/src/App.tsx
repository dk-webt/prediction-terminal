import { useEffect, useRef, useCallback } from 'react'
import { useStore } from './store'
import StatusBar from './components/StatusBar'
import PanelGrid from './components/PanelGrid'
import CommandBar from './components/CommandBar'
import { ConnectionManager } from './ws/ConnectionManager'
import type { SocketStatus } from './ws/ConnectionManager'

const API = 'http://localhost:8081'

export default function App() {
  const managerRef = useRef<ConnectionManager | null>(null)
  const pendingCmdRef = useRef<string>('')   // which command is awaiting WS done
  const prevCmdRef = useRef<string>('')      // last runnable command (for R)
  const cmdBarRef = useRef<HTMLInputElement | null>(null)

  const {
    setLoading, setProgressMsg, setErrorMsg,
    setPmEvents, setKsEvents,
    setCacheStats, setCategories, setActiveView, setActiveCategory, setLastCommand,
    setCacheStatsBar, setSelectedIndex, setActivePanel, setDefaultLimit,
    setCenterView, navigateCenterHistory, jumpToCenterHistory,
    setBtcSnapshot, setBtcAutoRefresh,
    setFundKs, setFundPm, setFundPct,
    setPendingOrder,
    togglePanel,
    setCmdWsStatus, setBtcWsStatus, setTradeWsStatus,
    activePanel, defaultLimit,
  } = useStore()

  // ── Connection Manager ──────────────────────────────────────────────────────

  useEffect(() => {
    const onStatusChange = (socket: 'cmd' | 'btc' | 'trade', status: SocketStatus) => {
      const s = useStore.getState()
      if (socket === 'cmd') s.setCmdWsStatus(status)
      else if (socket === 'btc') s.setBtcWsStatus(status)
      else s.setTradeWsStatus(status)

      // Derive overall wsStatus for backward compat
      const cmd = socket === 'cmd' ? status : s.cmdWsStatus
      const btc = socket === 'btc' ? status : s.btcWsStatus
      const trade = socket === 'trade' ? status : s.tradeWsStatus
      if (cmd === 'connected' && btc === 'connected' && trade === 'connected') {
        s.setWsStatus('connected')
      } else if (cmd === 'disconnected' && btc === 'disconnected' && trade === 'disconnected') {
        s.setWsStatus('disconnected')
      } else {
        s.setWsStatus('connecting')
      }
    }

    // ── CMD message handler (ARB, CMP progress/done) ──────────────────────
    const onCmdMessage = (msg: Record<string, unknown>) => {
      const cmd = pendingCmdRef.current

      if (msg.type === 'progress') {
        useStore.getState().setProgressMsg(msg.msg as string)
      } else if (msg.type === 'done') {
        const state = useStore.getState()
        state.setLoading(false)
        state.setProgressMsg('')
        if (cmd === 'ARB') {
          if (msg.pm_events) state.setPmEvents(msg.pm_events as never[])
          if (msg.ks_events) state.setKsEvents(msg.ks_events as never[])
          state.pushCenterSnapshot({
            view: 'ARB',
            arbResults: msg.data as never[],
            compareResults: state.compareResults,
            timestamp: new Date().toISOString(),
            label: state.lastCommand,
            resultCount: (msg.data as unknown[]).length,
          })
        } else if (cmd === 'CMP') {
          if (msg.pm_events) state.setPmEvents(msg.pm_events as never[])
          if (msg.ks_events) state.setKsEvents(msg.ks_events as never[])
          const cmpData = msg.data as { market_matches: unknown[] }[]
          const resultCount = cmpData.reduce((s: number, cr) => s + cr.market_matches.length, 0)
          state.pushCenterSnapshot({
            view: 'CMP',
            arbResults: state.arbResults,
            compareResults: cmpData as never[],
            timestamp: new Date().toISOString(),
            label: state.lastCommand,
            resultCount,
          })
        }
        state.setSelectedIndex(null)
        pendingCmdRef.current = ''
      } else if (msg.type === 'error') {
        const state = useStore.getState()
        state.setLoading(false)
        state.setProgressMsg('')
        state.setErrorMsg(msg.msg as string)
        pendingCmdRef.current = ''
      }
    }

    // ── BTC message handler (streaming + debug) ───────────────────────────
    const onBtcMessage = (msg: Record<string, unknown>) => {
      if (msg.type === 'btc_update') {
        const state = useStore.getState()
        state.setBtcSnapshot(msg as never)
        state.setLoading(false)
        state.setProgressMsg('')

        // ── Time series derivation ──────────────────────────────────────
        const snap = msg as Record<string, unknown>
        const ks = snap.kalshi as Record<string, unknown> | null
        const pm = snap.polymarket as Record<string, unknown> | null

        // Detect window roll (close_time changed)
        const windowEnd = (ks?.close_time || pm?.end_time || '') as string
        if (windowEnd && windowEnd !== state.btcTimeSeries.windowId) {
          state.resetBtcTimeSeries(windowEnd)
        }

        // Compute derived values
        const yesBid = typeof ks?.yes_bid === 'number' ? ks.yes_bid : null
        const upBid = typeof pm?.up_bid === 'number' ? pm.up_bid : null
        const noBid = typeof ks?.no_bid === 'number' ? ks.no_bid : null
        const downBid = typeof pm?.down_bid === 'number' ? pm.down_bid : null
        const coinbase = typeof snap.btc_coinbase === 'number' ? snap.btc_coinbase : null
        const chainlink = typeof snap.btc_chainlink === 'number' ? snap.btc_chainlink : null

        const priceGap = (coinbase !== null && chainlink !== null) ? coinbase - chainlink : null
        const comboA = (yesBid !== null && downBid !== null) ? yesBid + downBid : null
        const comboB = (noBid !== null && upBid !== null) ? noBid + upBid : null

        // Ensure monotonic timestamps for lightweight-charts
        const pts = useStore.getState().btcTimeSeries.points
        const lastTime = pts.length > 0 ? pts[pts.length - 1].time : 0
        let time = Math.floor(Date.now() / 1000)
        if (time <= lastTime) time = lastTime + 1

        if (priceGap !== null || comboA !== null || comboB !== null || coinbase !== null || chainlink !== null) {
          state.appendBtcTick({ time, priceGap, comboA, comboB, coinbase, chainlink })
        }
      } else if (msg.type === 'btc_stopped') {
        useStore.getState().setBtcAutoRefresh(false)
      } else if (msg.type === 'btc_debug_status') {
        const enabled = msg.enabled as boolean
        const state = useStore.getState()
        state.setProgressMsg(`BTC debug logging ${enabled ? 'ON' : 'OFF'}`)
        state.setLoading(false)
        setTimeout(() => useStore.getState().setProgressMsg(''), 2000)
      } else if (msg.type === 'btc_debug_log') {
        const logText = msg.log as string
        const blob = new Blob([logText], { type: 'text/plain' })
        const url = URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = 'btc_debug.log'
        a.click()
        URL.revokeObjectURL(url)
        const state = useStore.getState()
        state.setProgressMsg(`Debug log downloaded (${logText.split('\n').length} lines)`)
        state.setLoading(false)
        setTimeout(() => useStore.getState().setProgressMsg(''), 3000)
      } else if (msg.type === 'ate_status') {
        const state = useStore.getState()
        const enabled = msg.enabled as boolean
        if (enabled) {
          const minProfit = msg.min_profit as number
          const count = msg.count as number
          state.setProgressMsg(`ATE: ENABLED — monitoring for >= $${minProfit.toFixed(2)} profit, ${count} contracts per leg`)
        } else {
          state.setProgressMsg('ATE: DISABLED')
        }
        setTimeout(() => useStore.getState().setProgressMsg(''), 5000)
      }
    }

    // ── Trade message handler (order confirm/result/cancel/fill/order) ────
    const onTradeMessage = (msg: Record<string, unknown>) => {
      if (msg.type === 'btc_order_confirm') {
        const state = useStore.getState()
        state.setPendingOrder({ order_id: msg.order_id as string, summary: msg.summary as string })
        state.setProgressMsg(`${msg.summary} — Type Y to confirm, N to cancel`)
        state.setLoading(false)
      } else if (msg.type === 'btc_order_result') {
        const state = useStore.getState()
        state.setPendingOrder(null)
        if (msg.success) {
          state.setProgressMsg('Order placed successfully')
          // Track the order for WS fill/order correlation
          const data = (msg.data || {}) as Record<string, unknown>
          const order = (data.order || data) as Record<string, unknown>
          const orderId = (order.order_id || '') as string
          if (orderId) {
            state.trackOrder({
              platform: 'kalshi',
              orderId,
              ticker: (order.ticker || '') as string,
              action: (order.action || '') as string,
              side: (order.side || '') as string,
              count: Number(order.count || 0),
              price: order.yes_price_dollars ? Number(order.yes_price_dollars) : null,
              status: 'submitted',
              fillCount: 0,
              timestamp: Date.now(),
            })
          }
        } else {
          state.setErrorMsg(`Order failed: ${msg.error || 'unknown error'}`)
        }
        state.setLoading(false)
        setTimeout(() => { useStore.getState().setProgressMsg(''); useStore.getState().setErrorMsg('') }, 5000)
      } else if (msg.type === 'btc_order_cancelled') {
        const state = useStore.getState()
        state.setPendingOrder(null)
        state.setProgressMsg('Order cancelled')
        state.setLoading(false)
        setTimeout(() => useStore.getState().setProgressMsg(''), 2000)

      } else if (msg.type === 'ks_fill') {
        const state = useStore.getState()
        const data = (msg.data || {}) as Record<string, unknown>
        const orderId = (data.order_id || '') as string
        const count = (data.count_fp || '0') as string
        const price = (data.yes_price_dollars || '?') as string
        const side = (data.side || '') as string
        const action = (data.action || '') as string

        state.addFill({
          platform: 'kalshi',
          orderId,
          ticker: (data.market_ticker || '') as string,
          side,
          price,
          count,
          action,
          tracked: !!(data._tracked),
          timestamp: Date.now(),
        })

        // Update active order fill count
        const existing = state.activeOrders.get(orderId)
        if (existing) {
          const newFillCount = existing.fillCount + Number(count)
          if (newFillCount >= existing.count) {
            state.updateOrderStatus(orderId, 'filled', newFillCount)
          } else {
            state.updateOrderStatus(orderId, 'partial', newFillCount)
          }
        }

        state.setProgressMsg(`KS FILL: ${action.toUpperCase()} ${count} ${side.toUpperCase()} @ $${price}`)
        setTimeout(() => useStore.getState().setProgressMsg(''), 5000)

      } else if (msg.type === 'ks_order_update') {
        const state = useStore.getState()
        const data = (msg.data || {}) as Record<string, unknown>
        const orderId = (data.order_id || '') as string
        const status = (data.status || '') as string

        if (orderId && state.activeOrders.has(orderId)) {
          if (status === 'resting') {
            state.updateOrderStatus(orderId, 'resting')
          } else if (status === 'canceled') {
            state.updateOrderStatus(orderId, 'canceled')
            state.setProgressMsg('KS ORDER: canceled')
            setTimeout(() => {
              useStore.getState().retireOrder(orderId)
              useStore.getState().setProgressMsg('')
            }, 3000)
          } else if (status === 'executed') {
            const fillCount = Number(data.fill_count_fp || 0)
            state.updateOrderStatus(orderId, 'filled', fillCount)
            setTimeout(() => useStore.getState().retireOrder(orderId), 3000)
          }
        }

      } else if (msg.type === 'pm_fill') {
        const state = useStore.getState()
        const data = (msg.data || {}) as Record<string, unknown>
        const status = (data.status || '') as string
        const side = (data.side || '') as string
        const size = (data.size || '0') as string
        const price = (data.price || '?') as string
        const outcome = (data.outcome || '') as string

        if (status === 'CONFIRMED') {
          state.addFill({
            platform: 'polymarket',
            orderId: (data.id || '') as string,
            ticker: outcome,
            side,
            price,
            count: size,
            action: side,
            tracked: !!(data._pm_confirmed),
            timestamp: Date.now(),
          })
          state.setProgressMsg(`PM FILL: ${side.toUpperCase()} ${size} ${outcome} @ $${price}`)
          setTimeout(() => useStore.getState().setProgressMsg(''), 5000)
        } else if (status === 'FAILED') {
          state.setErrorMsg(`PM trade FAILED: ${side} ${size} ${outcome}`)
          setTimeout(() => useStore.getState().setErrorMsg(''), 5000)
        }

      } else if (msg.type === 'pm_order_update') {
        const state = useStore.getState()
        const data = (msg.data || {}) as Record<string, unknown>
        const orderId = (data.id || '') as string
        const eventType = (data.type || '') as string

        if (orderId && state.activeOrders.has(orderId)) {
          if (eventType === 'CANCELLATION') {
            state.updateOrderStatus(orderId, 'canceled')
            state.setProgressMsg('PM ORDER: canceled')
            setTimeout(() => {
              useStore.getState().retireOrder(orderId)
              useStore.getState().setProgressMsg('')
            }, 3000)
          } else if (eventType === 'UPDATE') {
            const matched = Number(data.size_matched || 0)
            const original = Number(data.original_size || 0)
            if (matched >= original && original > 0) {
              state.updateOrderStatus(orderId, 'filled', matched)
              setTimeout(() => useStore.getState().retireOrder(orderId), 3000)
            } else {
              state.updateOrderStatus(orderId, 'partial', matched)
            }
          }
        }
      } else if (msg.type === 'ate_triggered') {
        const state = useStore.getState()
        const combo = msg.combo as string
        const profit = msg.profit as number
        const count = msg.count as number
        state.setProgressMsg(`ATE TRIGGERED: ${combo} | profit=$${profit.toFixed(3)}/contract | ${count} contracts — executing...`)
      } else if (msg.type === 'ate_done') {
        const state = useStore.getState()
        const combo = msg.combo as string
        const status = msg.status as string
        if (status === 'success') {
          state.setProgressMsg(`ATE COMPLETE: ${combo} — both legs executed. ATE auto-disabled.`)
        } else if (status === 'partial_ks') {
          state.setErrorMsg(`ATE PARTIAL: ${combo} — KS filled but PM FAILED. Unwinding KS...`)
        } else if (status === 'partial_pm') {
          state.setErrorMsg(`ATE PARTIAL: ${combo} — PM filled but KS FAILED. Unwinding PM...`)
        } else if (status === 'failed') {
          state.setErrorMsg(`ATE FAILED: ${combo} — both legs failed. No position.`)
        } else {
          state.setErrorMsg(`ATE ERROR: ${combo} — execution error. Check logs.`)
        }
        setTimeout(() => { useStore.getState().setProgressMsg(''); useStore.getState().setErrorMsg('') }, 15000)
      } else if (msg.type === 'ate_unwind') {
        const state = useStore.getState()
        const platform = (msg.platform as string).toUpperCase()
        const success = msg.success as boolean
        const count = msg.count as number
        if (success) {
          state.setProgressMsg(`ATE UNWIND: sold ${count} ${platform} contracts — position closed`)
        } else {
          state.setErrorMsg(`ATE UNWIND FAILED: could not sell ${count} ${platform} — MANUAL CLOSE NEEDED: ${msg.error || ''}`)
        }
        setTimeout(() => { useStore.getState().setProgressMsg(''); useStore.getState().setErrorMsg('') }, 15000)
      }
    }

    const manager = new ConnectionManager(API, {
      onCmdMessage,
      onBtcMessage,
      onTradeMessage,
      onStatusChange,
    })
    managerRef.current = manager

    // Retry connection until server is up
    const tryConnect = () => {
      fetch(`${API}/health`)
        .then(() => manager.connect())
        .catch(() => setTimeout(tryConnect, 1500))
    }
    tryConnect()

    return () => {
      // Unsubscribe BTC on unmount
      if (useStore.getState().btcAutoRefresh) {
        manager.unsubscribeBtc()
      }
      manager.disconnect()
      managerRef.current = null
    }
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  // ── Cache stats polling ──────────────────────────────────────────────────────

  const fetchCacheStats = useCallback(() => {
    fetch(`${API}/cache/stats`)
      .then((r) => r.json())
      .then((data) => useStore.getState().setCacheStatsBar(data))
      .catch(() => {})
  }, [])

  useEffect(() => {
    fetchCacheStats()
    const interval = setInterval(fetchCacheStats, 30_000)
    return () => clearInterval(interval)
  }, [fetchCacheStats])

  // ── Command runner ───────────────────────────────────────────────────────────

  const runSingleCommand = useCallback(
    (input: string) => {
      const trimmed = input.trim()
      if (!trimmed) return

      const manager = managerRef.current

      const parts = trimmed.toUpperCase().split(/\s+/)
      const cmd = parts[0]
      let limit = useStore.getState().defaultLimit
      let category: string | undefined = undefined
      let maxDays: number | undefined = undefined
      for (const part of parts.slice(1)) {
        const dMatch = part.match(/^(\d+)D$/)
        if (dMatch) { maxDays = parseInt(dMatch[1], 10); continue }
        const n = parseInt(part, 10)
        if (!isNaN(n) && n > 0) limit = n
        else if (/^[A-Z]/.test(part)) category = part
      }

      useStore.getState().setErrorMsg('')

      // Handle Y/N confirmation for pending orders → trade socket
      const pending = useStore.getState().pendingOrder
      if (pending && (cmd === 'Y' || cmd === 'N')) {
        if (manager?.tradeReady) {
          if (cmd === 'Y') {
            manager.sendTrade({ type: 'btc_order_execute', order_id: pending.order_id })
            setProgressMsg('Executing order...')
            setLoading(true)
          } else {
            manager.sendTrade({ type: 'btc_order_cancel', order_id: pending.order_id })
          }
        }
        return
      }

      // Only unsubscribe from BTC when switching to a different view
      // Keep BTC alive for commands that don't change the view (FUND, BUY, SELL, POS, etc.)
      const viewChangingCmds = ['PM', 'KS', 'ARB', 'CMP', 'HELP', 'CACHE', 'CATS', 'HIST']
      if (viewChangingCmds.includes(cmd) && useStore.getState().btcAutoRefresh) {
        manager?.unsubscribeBtc()
        setBtcAutoRefresh(false)
      }

      if (cmd === 'R') {
        if (prevCmdRef.current) runSingleCommand(prevCmdRef.current)
        return
      }

      prevCmdRef.current = trimmed
      setLastCommand(trimmed.toUpperCase())

      switch (cmd) {
        case 'PM': {
          setLoading(true)
          setProgressMsg(`Fetching ${limit} Polymarket events${category ? ` [${category}]` : ''}${maxDays ? ` ≤${maxDays}d` : ''}…`)
          setSelectedIndex(null)
          const pmUrl = `${API}/events/polymarket?limit=${limit}${category ? `&category=${category}` : ''}${maxDays ? `&max_days=${maxDays}` : ''}`
          fetch(pmUrl)
            .then((r) => r.json())
            .then((data) => {
              setPmEvents(data)
              setLoading(false)
              setProgressMsg('')
            })
            .catch((e) => {
              setLoading(false)
              setErrorMsg(String(e))
            })
          break
        }

        case 'KS': {
          setLoading(true)
          setProgressMsg(`Fetching ${limit} Kalshi events${category ? ` [${category}]` : ''}${maxDays ? ` ≤${maxDays}d` : ''}…`)
          setSelectedIndex(null)
          const ksUrl = `${API}/events/kalshi?limit=${limit}${category ? `&category=${category}` : ''}${maxDays ? `&max_days=${maxDays}` : ''}`
          fetch(ksUrl)
            .then((r) => r.json())
            .then((data) => {
              setKsEvents(data)
              setLoading(false)
              setProgressMsg('')
            })
            .catch((e) => {
              setLoading(false)
              setErrorMsg(String(e))
            })
          break
        }

        case 'ARB': {
          if (!manager?.cmdReady) {
            setErrorMsg('Command socket not connected. Retry in a moment.')
            return
          }
          pendingCmdRef.current = 'ARB'
          setLoading(true)
          setActiveView('ARB')
          setCenterView('ARB')
          setActiveCategory(category ?? null)
          setProgressMsg('Starting arbitrage scan…')
          setSelectedIndex(null)
          manager.sendCmd({ type: 'arb', limit, category: category ?? null, max_days: maxDays ?? null })
          break
        }

        case 'CMP': {
          if (!manager?.cmdReady) {
            setErrorMsg('Command socket not connected. Retry in a moment.')
            return
          }
          pendingCmdRef.current = 'CMP'
          setLoading(true)
          setActiveView('CMP')
          setCenterView('CMP')
          setActiveCategory(category ?? null)
          setProgressMsg('Starting comparison…')
          setSelectedIndex(null)
          manager.sendCmd({ type: 'compare', limit, category: category ?? null, max_days: maxDays ?? null })
          break
        }

        case 'HIST': {
          const histArg = parts[1]
          const histN = histArg ? parseInt(histArg, 10) : NaN
          if (!isNaN(histN) && histN > 0) {
            jumpToCenterHistory(histN - 1)
          } else {
            setActiveView('HIST')
            setCenterView('HIST')
          }
          break
        }

        case 'CACHE': {
          setActiveView('CACHE')
          setCenterView('CACHE')
          fetch(`${API}/cache/stats`)
            .then((r) => r.json())
            .then((data) => setCacheStats(data))
            .catch((e) => setErrorMsg(String(e)))
          break
        }

        case 'CLEAR': {
          fetch(`${API}/cache`, { method: 'DELETE' })
            .then(() => {
              setCacheStats(null)
              setCacheStatsBar(null)
              setProgressMsg('Cache cleared.')
              setTimeout(() => useStore.getState().setProgressMsg(''), 2000)
            })
            .catch((e) => setErrorMsg(String(e)))
          break
        }

        case 'LIMIT': {
          if (!isNaN(limit) && limit > 0) {
            setDefaultLimit(limit)
            setProgressMsg(`Default limit set to ${limit}`)
            setTimeout(() => useStore.getState().setProgressMsg(''), 2000)
          }
          break
        }

        case 'FUND': {
          const sub = (parts[1] || '').toUpperCase()
          const val = parseFloat(parts[2] || '')
          if (sub === 'KS' && !isNaN(val) && val >= 0) {
            setFundKs(val)
            setProgressMsg(`Kalshi funds set to $${val.toFixed(2)}`)
          } else if (sub === 'PM' && !isNaN(val) && val >= 0) {
            setFundPm(val)
            setProgressMsg(`Polymarket funds set to $${val.toFixed(2)}`)
          } else if (sub === 'PCT' && !isNaN(val) && val >= 0 && val <= 1) {
            setFundPct(val)
            setProgressMsg(`Fund usage set to ${(val * 100).toFixed(0)}%`)
          } else if (!sub) {
            const s = useStore.getState()
            setProgressMsg(`KS: $${s.fundKs.toFixed(2)} | PM: $${s.fundPm.toFixed(2)} | Use: ${(s.fundPct * 100).toFixed(0)}%`)
          } else {
            setErrorMsg('Usage: FUND KS <amount> | FUND PM <amount> | FUND PCT <0-1>')
          }
          setTimeout(() => { useStore.getState().setProgressMsg(''); useStore.getState().setErrorMsg('') }, 3000)
          break
        }

        case 'SHOW':
        case 'HIDE':
        case 'TOGGLE': {
          const target = (parts[1] || '').toUpperCase()
          const panelMap: Record<string, 'pm' | 'ks' | 'detail' | 'positions' | 'orders'> = {
            PM: 'pm', POLY: 'pm', POLYMARKET: 'pm',
            KS: 'ks', KALSHI: 'ks',
            DETAIL: 'detail', DET: 'detail',
            POS: 'positions', POSITIONS: 'positions',
            ORDERS: 'orders', ORD: 'orders',
          }
          const panel = panelMap[target]
          if (!panel) {
            setErrorMsg(`Usage: ${cmd} PM|KS|DETAIL|POS|ORDERS`)
            setTimeout(() => useStore.getState().setErrorMsg(''), 3000)
            break
          }
          if (cmd === 'TOGGLE') {
            togglePanel(panel)
          } else {
            const show = cmd === 'SHOW'
            if (panel === 'pm') useStore.getState().setShowPm(show)
            else if (panel === 'ks') useStore.getState().setShowKs(show)
            else if (panel === 'positions') useStore.getState().setShowPositions(show)
            else if (panel === 'orders') useStore.getState().setShowOrders(show)
            else useStore.getState().setShowDetail(show)
          }
          break
        }

        case 'BUY':
        case 'SELL': {
          if (!manager?.tradeReady) {
            setErrorMsg('Trade socket not connected.')
            return
          }
          const action = cmd.toLowerCase() as 'buy' | 'sell'
          const platToken = parts[1]
          const sideToken = parts[2]
          const countStr = parts[3]
          const priceOrMkt = parts[4]

          if (!platToken || !sideToken || !countStr) {
            setErrorMsg(`Usage: ${cmd} KS/PM YES/NO/UP/DOWN <count> [price|MKT]`)
            setTimeout(() => useStore.getState().setErrorMsg(''), 4000)
            break
          }

          const rawCount = parseFloat(countStr)
          if (isNaN(rawCount) || rawCount <= 0) {
            setErrorMsg('Count must be a positive number')
            break
          }
          // Kalshi uses whole contracts, PM supports fractional shares
          const orderCount = platToken === 'KS' ? Math.floor(rawCount) : rawCount

          const isMkt = priceOrMkt === 'MKT'
          const orderPrice = isMkt ? undefined : priceOrMkt ? parseFloat(priceOrMkt) : undefined
          const orderType = isMkt ? 'market' : 'limit'

          if (orderType === 'limit' && action === 'buy' && orderPrice === undefined) {
            setErrorMsg('Limit buy requires a price. Use MKT for market order.')
            setTimeout(() => useStore.getState().setErrorMsg(''), 4000)
            break
          }

          const snap = useStore.getState().btcSnapshot
          if (!snap) {
            setErrorMsg('No BTC data. Run BTC first.')
            break
          }

          let platform = ''
          let side = ''
          let ticker = ''
          let tokenId = ''

          if (platToken === 'KS') {
            platform = 'kalshi'
            side = sideToken === 'YES' || sideToken === 'UP' ? 'yes' : 'no'
            ticker = snap.kalshi?.ticker || ''
            if (!ticker) { setErrorMsg('No active Kalshi contract'); break }
          } else if (platToken === 'PM') {
            platform = 'polymarket'
            const tokens = snap.polymarket?.token_ids || []
            if (sideToken === 'UP' || sideToken === 'YES') {
              side = 'up'
              tokenId = tokens[0] || ''
            } else {
              side = 'down'
              tokenId = tokens[1] || ''
            }
            if (!tokenId) { setErrorMsg('No active Polymarket contract'); break }
          } else {
            setErrorMsg('Platform must be KS or PM')
            break
          }

          manager.sendTrade({
            type: 'btc_order', platform, action, side,
            count: orderCount,
            price: orderPrice ?? null,
            order_type: orderType,
            ticker, token_id: tokenId,
            auto_execute: isPipedRef.current,
          })
          break
        }

        case 'POS': {
          const state = useStore.getState()
          state.setPositions({ loading: true, error: null })
          state.setShowPositions(true)
          setProgressMsg('Fetching positions...')
          fetch(`${API}/btc/positions`)
            .then((r) => r.json())
            .then((data) => {
              const ksRaw = data.kalshi?.data || []
              const pmRaw = data.polymarket?.data || []
              const ksErr = data.kalshi?.error
              const pmErr = data.polymarket?.error

              const kalshi = ksRaw.map((p: Record<string, unknown>) => {
                // position_fp: positive = YES contracts, negative = NO contracts
                const positionFp = Number(p.position_fp) || 0
                return {
                  platform: 'kalshi' as const,
                  ticker: (p.ticker || '') as string,
                  title: (p.ticker || '') as string,
                  side: positionFp >= 0 ? 'yes' : 'no',
                  size: Math.abs(positionFp),
                  avgPrice: 0,
                  currentValue: Number(p.market_exposure_dollars) || null,
                  pnl: Number(p.realized_pnl_dollars) || null,
                }
              }).filter((p: { size: number }) => p.size >= 1)

              const polymarket = pmRaw.map((p: Record<string, unknown>) => ({
                platform: 'polymarket' as const,
                ticker: (p.asset || p.market || '') as string,
                title: (p.title || p.market_slug || p.asset || '') as string,
                side: (p.outcome || 'yes') as string,
                size: Number(p.size) || 0,
                avgPrice: Number(p.avgPrice) || 0,
                currentValue: Number(p.currentValue) || null,
                pnl: Number(p.cashPnl) || null,
              })).filter((p: { size: number }) => p.size >= 0.01)

              const error = [ksErr, pmErr].filter(Boolean).join(' | ') || null

              useStore.getState().setPositions({
                kalshi, polymarket, loading: false, error,
                lastFetched: Date.now(),
              })

              const total = kalshi.length + polymarket.length
              setProgressMsg(`${total} position(s) loaded`)
              setTimeout(() => useStore.getState().setProgressMsg(''), 3000)
            })
            .catch((e) => {
              useStore.getState().setPositions({ loading: false, error: String(e) })
              setErrorMsg(String(e))
            })
          break
        }

        case 'SETUP': {
          const sub = (parts[1] || '').toUpperCase()
          if (sub === 'PM') {
            setLoading(true)
            setProgressMsg('Setting Polymarket allowances (one-time)...')
            fetch(`${API}/pm/setup`, { method: 'POST' })
              .then((r) => r.json())
              .then((data) => {
                setLoading(false)
                if (data.success) {
                  setProgressMsg('Polymarket allowances set successfully')
                } else {
                  setErrorMsg(`Setup failed: ${data.error || 'unknown'}`)
                }
                setTimeout(() => { useStore.getState().setProgressMsg(''); useStore.getState().setErrorMsg('') }, 5000)
              })
              .catch((e) => { setLoading(false); setErrorMsg(String(e)) })
          } else {
            setErrorMsg('Usage: SETUP PM')
            setTimeout(() => useStore.getState().setErrorMsg(''), 3000)
          }
          break
        }

        case 'CATS': {
          setActiveView('CATS')
          setCenterView('CATS')
          setLoading(true)
          setProgressMsg('Fetching categories from both platforms…')
          fetch(`${API}/categories`)
            .then((r) => r.json())
            .then((data) => {
              setCategories(data)
              setLoading(false)
              setProgressMsg('')
            })
            .catch((e) => {
              setLoading(false)
              setErrorMsg(String(e))
            })
          break
        }

        case 'BTC': {
          if (!manager?.btcReady) {
            setErrorMsg('BTC socket not connected. Retry in a moment.')
            return
          }
          const btcSub = (parts[1] || '').toUpperCase()
          if (btcSub === 'ATE') {
            const ateAction = (parts[2] || '').toUpperCase()
            if (ateAction === 'ON') {
              manager.sendBtc({ type: 'btc_ate', action: 'on' })
              setProgressMsg('ATE: Auto Trade Executor ENABLED — monitoring for arb...')
            } else if (ateAction === 'OFF') {
              manager.sendBtc({ type: 'btc_ate', action: 'off' })
              setProgressMsg('ATE: Auto Trade Executor DISABLED')
              setTimeout(() => useStore.getState().setProgressMsg(''), 3000)
            } else {
              manager.sendBtc({ type: 'btc_ate', action: 'status' })
              setProgressMsg('ATE: checking status...')
              setTimeout(() => useStore.getState().setProgressMsg(''), 2000)
            }
          } else if (btcSub === 'ORDERS') {
            const ordersAction = (parts[2] || '').toUpperCase()
            if (ordersAction === 'CLEAR') {
              useStore.getState().clearRecentOrders()
              setProgressMsg('Recent orders cleared')
              setTimeout(() => useStore.getState().setProgressMsg(''), 2000)
            } else {
              useStore.getState().setShowOrders(true)
            }
          } else {
            setActiveView('BTC')
            setCenterView('BTC')
            setLoading(true)
            setProgressMsg('Connecting to BTC 15-min live stream...')
            setBtcAutoRefresh(true)
            manager.subscribeBtc()
          }
          break
        }

        case 'DBG': {
          if (!manager?.btcReady) {
            setErrorMsg('BTC socket not connected.')
            return
          }
          const dbgSub = (parts[1] || '').toUpperCase()
          if (dbgSub === 'ON' || dbgSub === 'OFF') {
            manager.sendBtc({ type: 'btc_debug', action: dbgSub.toLowerCase() })
            setProgressMsg(`Debug logging ${dbgSub}...`)
            setTimeout(() => useStore.getState().setProgressMsg(''), 2000)
          } else if (dbgSub === 'CLEAR') {
            manager.sendBtc({ type: 'btc_debug', action: 'clear' })
            setProgressMsg('Clearing BTC debug log...')
          } else {
            setLoading(true)
            setProgressMsg('Fetching BTC debug log...')
            manager.sendBtc({ type: 'btc_debug', action: 'get' })
          }
          break
        }

        case '?':
        case 'HELP': {
          setActiveView('HELP')
          setCenterView('HELP')
          break
        }

        case 'Q': {
          window.electronAPI?.quit()
          break
        }

        default:
          setErrorMsg(`Unknown command: "${cmd}" — type HELP or ? for reference`)
      }
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    []
  )

  // Pipe-separated multi-command: "buy pm yes 2 mkt | buy ks no 2 mkt"
  // Piped orders auto-execute without Y/N confirmation
  const isPipedRef = useRef(false)
  const runCommand = useCallback(
    (input: string) => {
      const commands = input.split('|').map((s) => s.trim()).filter(Boolean)
      isPipedRef.current = commands.length > 1
      for (const cmd of commands) {
        runSingleCommand(cmd)
      }
      isPipedRef.current = false
    },
    [runSingleCommand]
  )

  // ── Global keyboard shortcuts ────────────────────────────────────────────────

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName
      const inInput = tag === 'INPUT'

      if (e.key === '/' || e.key === ':') {
        if (!inInput) {
          e.preventDefault()
          cmdBarRef.current?.focus()
        }
      } else if (e.key === 'Escape') {
        cmdBarRef.current?.blur()
      } else if (e.key === 'Tab') {
        if (!inInput) {
          e.preventDefault()
          const s = useStore.getState()
          const next = e.shiftKey
            ? ((s.activePanel + 3) % 4) as 0 | 1 | 2 | 3
            : ((s.activePanel + 1) % 4) as 0 | 1 | 2 | 3
          s.setActivePanel(next)
          s.setSelectedIndex(null)
        }
      } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        if (!inInput) {
          e.preventDefault()
          const s = useStore.getState()
          let navCount = 0
          if (s.activePanel === 0) navCount = s.pmEvents.length
          else if (s.activePanel === 1) navCount = s.ksEvents.length
          else if (s.activePanel === 2) {
            if (s.activeView === 'ARB') navCount = s.arbResults.length
            else if (s.activeView === 'CMP')
              navCount = s.compareResults.reduce((sum, cr) => sum + cr.market_matches.length, 0)
          }
          if (!navCount) return
          const cur = s.selectedIndex ?? -1
          const next =
            e.key === 'ArrowDown'
              ? Math.min(cur + 1, navCount - 1)
              : Math.max(cur - 1, 0)
          s.setSelectedIndex(next)
        }
      } else if (e.altKey && (e.key === 'ArrowLeft' || e.key === 'ArrowRight')) {
        e.preventDefault()
        const s = useStore.getState()
        s.navigateCenterHistory(e.key === 'ArrowLeft' ? -1 : 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [navigateCenterHistory])

  return (
    <div className="app">
      <StatusBar />
      <PanelGrid runCommand={runCommand} />
      <CommandBar runCommand={runCommand} inputRef={cmdBarRef} />
    </div>
  )
}
