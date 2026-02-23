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
    setPmEvents, setKsEvents, setArbResults, setCompareResults,
    setCacheStats, setActiveView, setLastCommand, setWsStatus,
    setCacheStatsBar, setSelectedIndex, setActivePanel, setDefaultLimit,
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
          state.setArbResults(msg.data)
          if (msg.pm_events) state.setPmEvents(msg.pm_events)
          if (msg.ks_events) state.setKsEvents(msg.ks_events)
        } else if (cmd === 'CMP') {
          state.setCompareResults(msg.data)
          if (msg.pm_events) state.setPmEvents(msg.pm_events)
          if (msg.ks_events) state.setKsEvents(msg.ks_events)
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
      const numArg = parts[1] ? parseInt(parts[1], 10) : NaN
      const limit = !isNaN(numArg) ? numArg : useStore.getState().defaultLimit

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
          setActiveView('PM')
          setProgressMsg(`Fetching ${limit} Polymarket events…`)
          setSelectedIndex(null)
          fetch(`${API}/events/polymarket?limit=${limit}`)
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
          setActiveView('KS')
          setProgressMsg(`Fetching ${limit} Kalshi events…`)
          setSelectedIndex(null)
          fetch(`${API}/events/kalshi?limit=${limit}`)
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
          setProgressMsg('Starting arbitrage scan…')
          setSelectedIndex(null)
          wsRef.current.send(JSON.stringify({ type: 'arb', limit }))
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
          setProgressMsg('Starting comparison…')
          setSelectedIndex(null)
          wsRef.current.send(JSON.stringify({ type: 'compare', limit }))
          break
        }

        case 'CACHE': {
          setActiveView('CACHE')
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

        case '?':
        case 'HELP': {
          setActiveView('HELP')
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
          // Keep activeView in sync so DetailPanel + center title stay correct
          if (s.activePanel === 0) s.setActiveView('PM')
          else if (s.activePanel === 1) s.setActiveView('KS')
        }
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="app">
      <StatusBar />
      <PanelGrid runCommand={runCommand} />
      <CommandBar runCommand={runCommand} inputRef={cmdBarRef} />
    </div>
  )
}
