import { create } from 'zustand'
import type {
  NormalizedEvent,
  ArbitrageResult,
  CompareResult,
  CacheStats,
  BtcSnapshot,
  BtcTimeSeriesPoint,
  OrderConfirmation,
  TrackedOrder,
  FillEvent,
  PositionsState,
} from './types'

export type View = 'IDLE' | 'PM' | 'KS' | 'ARB' | 'CMP' | 'HELP' | 'CACHE' | 'CATS' | 'HIST' | 'BTC'
export type CenterView = 'IDLE' | 'ARB' | 'CMP' | 'HELP' | 'CACHE' | 'CATS' | 'HIST' | 'BTC'
export type WsStatus = 'connecting' | 'connected' | 'disconnected' | 'error'
export type SocketStatus = 'connecting' | 'connected' | 'disconnected'

export type CenterSnapshot = {
  view: 'ARB' | 'CMP'
  arbResults: ArbitrageResult[]
  compareResults: CompareResult[]
  timestamp: string   // ISO datetime
  label: string       // command that produced it, e.g. "ARB 200 SPORTS"
  resultCount: number // for display in HIST view
}

interface TerminalState {
  // Market data
  pmEvents: NormalizedEvent[]
  ksEvents: NormalizedEvent[]
  arbResults: ArbitrageResult[]
  compareResults: CompareResult[]
  cacheStats: CacheStats | null
  cacheStatsBar: CacheStats | null   // polled for status bar
  categories: { polymarket: string[]; kalshi: string[] } | null
  btcSnapshot: BtcSnapshot | null
  btcAutoRefresh: boolean
  btcTimeSeries: { points: BtcTimeSeriesPoint[]; windowId: string }
  fundKs: number       // available cash on Kalshi
  fundPm: number       // available cash on Polymarket
  fundPct: number      // percentage of funds to use (0-1)
  pendingOrder: OrderConfirmation | null
  activeOrders: Map<string, TrackedOrder>
  fillHistory: FillEvent[]
  showPm: boolean
  showKs: boolean
  showDetail: boolean
  showPositions: boolean
  showOrders: boolean
  positions: PositionsState
  recentOrders: TrackedOrder[]

  // UI state
  activeView: View
  activeCategory: string | null      // category filter for ARB/CMP (e.g. 'Sports')
  selectedIndex: number | null       // index into active result list
  activePanel: 0 | 1 | 2 | 3        // 0=PM, 1=KS, 2=center, 3=detail

  // Center panel — independent view + history
  centerView: CenterView
  centerHistory: CenterSnapshot[]
  centerHistoryIndex: number

  // Status / feedback
  loading: boolean
  progressMsg: string
  errorMsg: string
  lastCommand: string
  defaultLimit: number
  wsStatus: WsStatus
  cmdWsStatus: SocketStatus
  btcWsStatus: SocketStatus
  tradeWsStatus: SocketStatus

  // Setters
  setPmEvents: (v: NormalizedEvent[]) => void
  setKsEvents: (v: NormalizedEvent[]) => void
  setArbResults: (v: ArbitrageResult[]) => void
  setCompareResults: (v: CompareResult[]) => void
  setCacheStats: (v: CacheStats | null) => void
  setCacheStatsBar: (v: CacheStats | null) => void
  setCategories: (v: { polymarket: string[]; kalshi: string[] } | null) => void
  setBtcSnapshot: (v: BtcSnapshot | null) => void
  setBtcAutoRefresh: (v: boolean) => void
  appendBtcTick: (point: BtcTimeSeriesPoint) => void
  resetBtcTimeSeries: (windowId: string) => void
  setFundKs: (v: number) => void
  setFundPm: (v: number) => void
  setFundPct: (v: number) => void
  setPendingOrder: (v: OrderConfirmation | null) => void
  setShowPm: (v: boolean) => void
  setShowKs: (v: boolean) => void
  setShowDetail: (v: boolean) => void
  setShowPositions: (v: boolean) => void
  setShowOrders: (v: boolean) => void
  setPositions: (v: Partial<PositionsState>) => void
  retireOrder: (orderId: string) => void
  clearRecentOrders: () => void
  togglePanel: (panel: 'pm' | 'ks' | 'detail' | 'positions' | 'orders') => void
  setActiveView: (v: View) => void
  setActiveCategory: (v: string | null) => void
  setSelectedIndex: (v: number | null) => void
  setActivePanel: (v: 0 | 1 | 2 | 3) => void
  setLoading: (v: boolean) => void
  setProgressMsg: (v: string) => void
  setErrorMsg: (v: string) => void
  setLastCommand: (v: string) => void
  setDefaultLimit: (v: number) => void
  setWsStatus: (v: WsStatus) => void
  setCmdWsStatus: (v: SocketStatus) => void
  setBtcWsStatus: (v: SocketStatus) => void
  setTradeWsStatus: (v: SocketStatus) => void
  setCenterView: (v: CenterView) => void
  pushCenterSnapshot: (snap: CenterSnapshot) => void
  navigateCenterHistory: (delta: -1 | 1) => void
  jumpToCenterHistory: (index: number) => void
  trackOrder: (order: TrackedOrder) => void
  updateOrderStatus: (orderId: string, status: TrackedOrder['status'], fillCount?: number) => void
  addFill: (fill: FillEvent) => void
  removeOrder: (orderId: string) => void
}

export const useStore = create<TerminalState>((set) => ({
  pmEvents: [],
  ksEvents: [],
  arbResults: [],
  compareResults: [],
  cacheStats: null,
  cacheStatsBar: null,
  categories: null,
  btcSnapshot: null,
  btcAutoRefresh: false,
  btcTimeSeries: { points: [], windowId: '' },
  fundKs: 0,
  fundPm: 0,
  fundPct: 1.0,
  pendingOrder: null,
  activeOrders: new Map(),
  fillHistory: [],
  showPm: true,
  showKs: true,
  showDetail: true,
  showPositions: false,
  showOrders: false,
  positions: { kalshi: [], polymarket: [], loading: false, error: null, lastFetched: 0 },
  recentOrders: [],
  activeView: 'IDLE',
  activeCategory: null,
  selectedIndex: null,
  activePanel: 1,
  loading: false,
  progressMsg: '',
  errorMsg: '',
  lastCommand: '',
  defaultLimit: 200,
  wsStatus: 'connecting',
  cmdWsStatus: 'disconnected' as SocketStatus,
  btcWsStatus: 'disconnected' as SocketStatus,
  tradeWsStatus: 'disconnected' as SocketStatus,
  centerView: 'IDLE',
  centerHistory: [],
  centerHistoryIndex: -1,

  setPmEvents: (pmEvents) => set({ pmEvents }),
  setKsEvents: (ksEvents) => set({ ksEvents }),
  setArbResults: (arbResults) => set({ arbResults }),
  setCompareResults: (compareResults) => set({ compareResults }),
  setCacheStats: (cacheStats) => set({ cacheStats }),
  setCacheStatsBar: (cacheStatsBar) => set({ cacheStatsBar }),
  setCategories: (categories) => set({ categories }),
  setBtcSnapshot: (btcSnapshot) => set({ btcSnapshot }),
  setBtcAutoRefresh: (btcAutoRefresh) => set({ btcAutoRefresh }),
  appendBtcTick: (point) => set((state) => {
    const pts = state.btcTimeSeries.points
    const next = pts.length >= 2000 ? [...pts.slice(-1999), point] : [...pts, point]
    return { btcTimeSeries: { ...state.btcTimeSeries, points: next } }
  }),
  resetBtcTimeSeries: (windowId) => set((state) => ({
    btcTimeSeries: { points: [], windowId },
  })),
  setFundKs: (fundKs) => set({ fundKs }),
  setFundPm: (fundPm) => set({ fundPm }),
  setFundPct: (fundPct) => set({ fundPct }),
  setPendingOrder: (pendingOrder) => set({ pendingOrder }),
  setShowPm: (showPm) => set({ showPm }),
  setShowKs: (showKs) => set({ showKs }),
  setShowDetail: (showDetail) => set({ showDetail }),
  setShowPositions: (showPositions) => set({ showPositions }),
  setShowOrders: (showOrders) => set({ showOrders }),
  setPositions: (v) => set((s) => ({ positions: { ...s.positions, ...v } })),
  retireOrder: (orderId) => set((s) => {
    const order = s.activeOrders.get(orderId)
    if (!order) return {}
    const next = new Map(s.activeOrders)
    next.delete(orderId)
    return {
      activeOrders: next,
      recentOrders: [...s.recentOrders.slice(-49), order],
    }
  }),
  clearRecentOrders: () => set({ recentOrders: [] }),
  togglePanel: (panel) => set((s) => {
    if (panel === 'pm') return { showPm: !s.showPm }
    if (panel === 'ks') return { showKs: !s.showKs }
    if (panel === 'positions') return { showPositions: !s.showPositions }
    if (panel === 'orders') return { showOrders: !s.showOrders }
    return { showDetail: !s.showDetail }
  }),
  setActiveView: (activeView) => set({ activeView }),
  setActiveCategory: (activeCategory) => set({ activeCategory }),
  setSelectedIndex: (selectedIndex) => set({ selectedIndex }),
  setActivePanel: (activePanel) => set({ activePanel }),
  setLoading: (loading) => set({ loading }),
  setProgressMsg: (progressMsg) => set({ progressMsg }),
  setErrorMsg: (errorMsg) => set({ errorMsg }),
  setLastCommand: (lastCommand) => set({ lastCommand }),
  setDefaultLimit: (defaultLimit) => set({ defaultLimit }),
  setWsStatus: (wsStatus) => set({ wsStatus }),
  setCmdWsStatus: (cmdWsStatus) => set({ cmdWsStatus }),
  setBtcWsStatus: (btcWsStatus) => set({ btcWsStatus }),
  setTradeWsStatus: (tradeWsStatus) => set({ tradeWsStatus }),
  setCenterView: (centerView) => set({ centerView }),

  pushCenterSnapshot: (snap) => set((state) => {
    // Truncate forward history if we're looking at a past entry
    const truncated = state.centerHistory.slice(0, state.centerHistoryIndex + 1)
    const newHistory = [...truncated, snap]
    const newIndex = newHistory.length - 1
    return {
      centerHistory: newHistory,
      centerHistoryIndex: newIndex,
      centerView: snap.view,
      activeView: snap.view as View,
      arbResults: snap.arbResults,
      compareResults: snap.compareResults,
    }
  }),

  navigateCenterHistory: (delta) => set((state) => {
    if (state.centerHistory.length === 0) return {}
    const newIndex = Math.max(0, Math.min(state.centerHistory.length - 1, state.centerHistoryIndex + delta))
    if (newIndex === state.centerHistoryIndex) return {}
    const snap = state.centerHistory[newIndex]
    return {
      centerHistoryIndex: newIndex,
      centerView: snap.view,
      activeView: snap.view as View,
      arbResults: snap.arbResults,
      compareResults: snap.compareResults,
      selectedIndex: null,
    }
  }),

  jumpToCenterHistory: (index) => set((state) => {
    if (state.centerHistory.length === 0) return {}
    const clamped = Math.max(0, Math.min(state.centerHistory.length - 1, index))
    const snap = state.centerHistory[clamped]
    return {
      centerHistoryIndex: clamped,
      centerView: snap.view,
      activeView: snap.view as View,
      arbResults: snap.arbResults,
      compareResults: snap.compareResults,
      selectedIndex: null,
    }
  }),

  trackOrder: (order) => set((state) => {
    const next = new Map(state.activeOrders)
    next.set(order.orderId, order)
    return { activeOrders: next }
  }),

  updateOrderStatus: (orderId, status, fillCount) => set((state) => {
    const existing = state.activeOrders.get(orderId)
    if (!existing) return {}
    const next = new Map(state.activeOrders)
    next.set(orderId, {
      ...existing,
      status,
      fillCount: fillCount ?? existing.fillCount,
    })
    return { activeOrders: next }
  }),

  addFill: (fill) => set((state) => ({
    fillHistory: [...state.fillHistory.slice(-49), fill],
  })),

  removeOrder: (orderId) => set((state) => {
    const next = new Map(state.activeOrders)
    next.delete(orderId)
    return { activeOrders: next }
  }),
}))
