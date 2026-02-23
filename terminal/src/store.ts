import { create } from 'zustand'
import type {
  NormalizedEvent,
  ArbitrageResult,
  CompareResult,
  CacheStats,
} from './types'

export type View = 'IDLE' | 'PM' | 'KS' | 'ARB' | 'CMP' | 'HELP' | 'CACHE'
export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error'

interface TerminalState {
  // Market data
  pmEvents: NormalizedEvent[]
  ksEvents: NormalizedEvent[]
  arbResults: ArbitrageResult[]
  compareResults: CompareResult[]
  cacheStats: CacheStats | null
  cacheStatsBar: CacheStats | null   // polled for status bar

  // UI state
  activeView: View
  selectedIndex: number | null       // index into active result list
  activePanel: 0 | 1 | 2 | 3        // 0=PM, 1=KS, 2=center, 3=detail

  // Status / feedback
  loading: boolean
  progressMsg: string
  errorMsg: string
  lastCommand: string
  defaultLimit: number
  wsStatus: WsStatus

  // Setters
  setPmEvents: (v: NormalizedEvent[]) => void
  setKsEvents: (v: NormalizedEvent[]) => void
  setArbResults: (v: ArbitrageResult[]) => void
  setCompareResults: (v: CompareResult[]) => void
  setCacheStats: (v: CacheStats | null) => void
  setCacheStatsBar: (v: CacheStats | null) => void
  setActiveView: (v: View) => void
  setSelectedIndex: (v: number | null) => void
  setActivePanel: (v: 0 | 1 | 2 | 3) => void
  setLoading: (v: boolean) => void
  setProgressMsg: (v: string) => void
  setErrorMsg: (v: string) => void
  setLastCommand: (v: string) => void
  setDefaultLimit: (v: number) => void
  setWsStatus: (v: WsStatus) => void
}

export const useStore = create<TerminalState>((set) => ({
  pmEvents: [],
  ksEvents: [],
  arbResults: [],
  compareResults: [],
  cacheStats: null,
  cacheStatsBar: null,
  activeView: 'IDLE',
  selectedIndex: null,
  activePanel: 1,
  loading: false,
  progressMsg: '',
  errorMsg: '',
  lastCommand: '',
  defaultLimit: 200,
  wsStatus: 'connecting',

  setPmEvents: (pmEvents) => set({ pmEvents }),
  setKsEvents: (ksEvents) => set({ ksEvents }),
  setArbResults: (arbResults) => set({ arbResults }),
  setCompareResults: (compareResults) => set({ compareResults }),
  setCacheStats: (cacheStats) => set({ cacheStats }),
  setCacheStatsBar: (cacheStatsBar) => set({ cacheStatsBar }),
  setActiveView: (activeView) => set({ activeView }),
  setSelectedIndex: (selectedIndex) => set({ selectedIndex }),
  setActivePanel: (activePanel) => set({ activePanel }),
  setLoading: (loading) => set({ loading }),
  setProgressMsg: (progressMsg) => set({ progressMsg }),
  setErrorMsg: (errorMsg) => set({ errorMsg }),
  setLastCommand: (lastCommand) => set({ lastCommand }),
  setDefaultLimit: (defaultLimit) => set({ defaultLimit }),
  setWsStatus: (wsStatus) => set({ wsStatus }),
}))
