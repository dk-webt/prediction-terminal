import { useEffect, useRef, useCallback } from 'react'
import { useStore } from './store'
import StatusBar from './components/StatusBar'
import PanelGrid from './components/PanelGrid'
import CommandBar from './components/CommandBar'

const API = 'http://localhost:8081'
const WS_URL = 'ws://localhost:8081/ws/status'

export default function App() {
  const wsRef = useRef<WebSocket | null>(null)
  const pendingCmdRef = useRef<string>('')   // which command is awaiting WS done
  const prevCmdRef = useRef<string>('')      // last runnable command (for R)
  const cmdBarRef = useRef<HTMLInputElement | null>(null)

  const {
    setLoading, setProgressMsg, setErrorMsg,
    setPmEvents, setKsEvents,
    setCacheStats, setCategories, setActiveView, setActiveCategory, setLastCommand, setWsStatus,
    setCacheStatsBar, setSelectedIndex, setActivePanel, setDefaultLimit,
    setCenterView, navigateCenterHistory, jumpToCenterHistory,
    activePanel, defaultLimit,
  } = useStore()

  // ── WebSocket ────────────────────────────────────────────────────────────────

  const connectWs = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return

    setWsStatus('connecting')
    const ws = new WebSocket(WS_URL)

    ws.onopen = () => setWsStatus('connected')

    ws.onclose = () => {
      setWsStatus('disconnected')
      wsRef.current = null
      setTimeout(connectWs, 2000)
    }

    ws.onerror = () => setWsStatus('error')

    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data as string)
      const cmd = pendingCmdRef.current

      if (msg.type === 'progress') {
        useStore.getState().setProgressMsg(msg.msg as string)
      } else if (msg.type === 'done') {
        const state = useStore.getState()
        state.setLoading(false)
        state.setProgressMsg('')
        if (cmd === 'ARB') {
          if (msg.pm_events) state.setPmEvents(msg.pm_events)
          if (msg.ks_events) state.setKsEvents(msg.ks_events)
          state.pushCenterSnapshot({
            view: 'ARB',
            arbResults: msg.data,
            compareResults: state.compareResults,
            timestamp: new Date().toISOString(),
            label: state.lastCommand,
            resultCount: (msg.data as unknown[]).length,
          })
        } else if (cmd === 'CMP') {
          if (msg.pm_events) state.setPmEvents(msg.pm_events)
          if (msg.ks_events) state.setKsEvents(msg.ks_events)
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          const cmpData = msg.data as any[]
          const resultCount = cmpData.reduce((s: number, cr: { market_matches: unknown[] }) => s + cr.market_matches.length, 0)
          state.pushCenterSnapshot({
            view: 'CMP',
            arbResults: state.arbResults,
            compareResults: cmpData,
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

    wsRef.current = ws
  }, [setWsStatus])

  // ── Cache stats polling ──────────────────────────────────────────────────────

  const fetchCacheStats = useCallback(() => {
    fetch(`${API}/cache/stats`)
      .then((r) => r.json())
      .then((data) => useStore.getState().setCacheStatsBar(data))
      .catch(() => {}) // silently ignore — server may not be ready yet
  }, [])

  useEffect(() => {
    // Retry WS until server is up
    const tryConnect = () => {
      fetch(`${API}/health`)
        .then(() => connectWs())
        .catch(() => setTimeout(tryConnect, 1500))
    }
    tryConnect()

    fetchCacheStats()
    const interval = setInterval(fetchCacheStats, 30_000)
    return () => {
      clearInterval(interval)
      wsRef.current?.close()
    }
  }, [connectWs, fetchCacheStats])

  // ── Command runner ───────────────────────────────────────────────────────────

  const runCommand = useCallback(
    (input: string) => {
      const trimmed = input.trim()
      if (!trimmed) return

      const parts = trimmed.toUpperCase().split(/\s+/)
      const cmd = parts[0]
      // Parse limit and category from remaining tokens in any order
      // e.g. "PM SPORTS 10", "PM 10 SPORTS", "ARB 200", "ARB SPORTS"
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

      if (cmd === 'R') {
        if (prevCmdRef.current) runCommand(prevCmdRef.current)
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
          if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
            setErrorMsg('WebSocket not connected. Retry in a moment.')
            return
          }
          pendingCmdRef.current = 'ARB'
          setLoading(true)
          setActiveView('ARB')
          setCenterView('ARB')
          setActiveCategory(category ?? null)
          setProgressMsg('Starting arbitrage scan…')
          setSelectedIndex(null)
          wsRef.current.send(JSON.stringify({ type: 'arb', limit, category: category ?? null, max_days: maxDays ?? null }))
          break
        }

        case 'CMP': {
          if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
            setErrorMsg('WebSocket not connected. Retry in a moment.')
            return
          }
          pendingCmdRef.current = 'CMP'
          setLoading(true)
          setActiveView('CMP')
          setCenterView('CMP')
          setActiveCategory(category ?? null)
          setProgressMsg('Starting comparison…')
          setSelectedIndex(null)
          wsRef.current.send(JSON.stringify({ type: 'compare', limit, category: category ?? null, max_days: maxDays ?? null }))
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
          if (!isNaN(numArg) && numArg > 0) {
            setDefaultLimit(numArg)
            setProgressMsg(`Default limit set to ${numArg}`)
            setTimeout(() => useStore.getState().setProgressMsg(''), 2000)
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
          // 4 panels: 0=PM, 1=KS, 2=center, 3=detail
          const next = e.shiftKey
            ? ((s.activePanel + 3) % 4) as 0 | 1 | 2 | 3   // backward
            : ((s.activePanel + 1) % 4) as 0 | 1 | 2 | 3   // forward
          s.setActivePanel(next)
          s.setSelectedIndex(null)   // reset row selection when switching panels
        }
      } else if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
        if (!inInput) {
          e.preventDefault()
          const s = useStore.getState()
          // Derive navigable count from activePanel, not activeView
          // so Tab focus drives ↑↓ independently of what the center panel shows
          let navCount = 0
          if (s.activePanel === 0) navCount = s.pmEvents.length
          else if (s.activePanel === 1) navCount = s.ksEvents.length
          else if (s.activePanel === 2) {
            if (s.activeView === 'ARB') navCount = s.arbResults.length
            else if (s.activeView === 'CMP')
              navCount = s.compareResults.reduce((sum, cr) => sum + cr.market_matches.length, 0)
          }
          // panel 3 (detail) has no navigable rows
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
