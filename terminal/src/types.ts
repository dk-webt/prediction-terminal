export interface NormalizedMarket {
  question: string
  yes_price: number
  no_price: number
  volume: number
  source: string
  market_id: string
  parent_event_id: string
  parent_event_title: string
  close_time: string
  url: string
}

export interface NormalizedEvent {
  source: string
  id: string
  title: string
  category: string
  volume: number
  liquidity: number
  end_date: string
  url: string
  markets: NormalizedMarket[]
}

export interface MatchResult {
  poly_event: NormalizedEvent
  kalshi_event: NormalizedEvent
  score: number
}

export interface MarketMatchResult {
  poly_market: NormalizedMarket
  kalshi_market: NormalizedMarket
  score: number
}

export interface ArbitrageResult {
  poly_market: NormalizedMarket
  kalshi_market: NormalizedMarket
  match_score: number
  best_leg: 'pm_yes_ks_no' | 'ks_yes_pm_no'
  spread: number
  profit: number
  days_to_resolution: number | null
  annualized_return: number | null
}

export interface CompareResult {
  event_match: MatchResult
  market_matches: MarketMatchResult[]
}

export interface CacheStats {
  event_pairs: number
  market_pairs: number
  oldest_entry: string | null
  newest_entry: string | null
  db_path: string
}

export interface BtcPlatformData {
  platform: string
  // Kalshi fields
  ticker?: string
  title?: string
  floor_strike?: number
  open_time?: string
  close_time?: string
  yes_ask?: number
  yes_bid?: number
  no_ask?: number
  no_bid?: number
  last_price?: number
  volume?: number
  open_interest?: number
  rules?: string
  url?: string
  // Polymarket fields
  slug?: string
  event_start_time?: string
  end_time?: string
  up_ask?: number
  up_bid?: number
  down_ask?: number
  down_bid?: number
  fee_schedule?: { exponent: number; rate: number; takerOnly: boolean; rebateRate: number }
  description?: string
  resolution_source?: string
  // PM WebSocket token IDs
  token_ids?: string[]
  // Error case
  error?: string
}

export interface BtcSnapshot {
  kalshi: BtcPlatformData | null
  polymarket: BtcPlatformData | null
  timestamp: string
  streaming?: boolean
  kalshi_mode?: 'websocket' | 'polling'
  rolling?: boolean
  kalshi_last_update?: string
  polymarket_last_update?: string
}

export interface OrderConfirmation {
  order_id: string
  summary: string
}

export interface OrderResult {
  success: boolean
  error?: string
  data?: Record<string, unknown>
}

export interface TrackedOrder {
  platform: 'kalshi' | 'polymarket'
  orderId: string
  ticker: string
  action: string
  side: string
  count: number
  price: number | null
  status: 'submitted' | 'resting' | 'partial' | 'filled' | 'canceled'
  fillCount: number
  timestamp: number
}

export interface FillEvent {
  platform: 'kalshi' | 'polymarket'
  orderId: string
  ticker: string
  side: string
  price: string
  count: string
  action: string
  tracked: boolean
  timestamp: number
}

// Extend Window for Electron bridge
declare global {
  interface Window {
    electronAPI?: {
      quit: () => void
    }
  }
}
