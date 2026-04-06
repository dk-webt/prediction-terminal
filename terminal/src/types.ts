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
  btc_coinbase?: number
  btc_chainlink?: number
  btc_price_gap?: number
  brti_active_exchanges?: number
  ks_uptime_pct?: number
  pm_uptime_pct?: number
  oracle_aligned?: {
    aligned_ticks: number
    latest_spread: number
    avg_spread: number
    latest_brti: number
    latest_chainlink: number
    latency_brti_ms: number
    latency_chainlink_ms: number
    bin_ts: number
  } | null
  model_state?: ModelStateData | null
}

export interface ModelStateData {
  n_aligned_ticks: number
  sigma_15m: number | null
  tau: number | null
  tau_min: number | null
  brti_price: number | null
  chainlink_price: number | null
  ks_strike: number | null
  pm_strike: number | null
  staleness?: {
    oracle_stale: boolean; oracle_age_s: number | null
    prices_stale: boolean; prices_age_s: number | null
    sigma_stale: boolean; sigma_age_s: number | null
  }
  model_a_ks?: { p_above: number; p_below: number; d2: number }
  model_a_pm?: { p_above: number; p_below: number; d2: number }
  adf?: { statistic: number; pvalue: number; is_stationary: boolean; n_obs: number }
  ou?: { theta: number; mu: number; sigma: number; half_life_s: number }
  copula?: { rho: number; nu: number; kendall_tau: number; n_obs: number }
  model_c_a?: { p_ww: number; p_wl: number; p_lw: number; p_ll: number }
  model_c_b?: { p_ww: number; p_wl: number; p_lw: number; p_ll: number }
  model_d?: {
    ev_a_raw: number; ev_b_raw: number
    ev_a: number; ev_b: number; chosen: string | null; ev: number
    cost_a: number; cost_b: number
    fee_ks: number; fee_pm_a: number; fee_pm_b: number
    gates: Record<string, { passed: boolean; reason: string }>
    all_gates_passed: boolean
  }
}

export interface BtcTimeSeriesPoint {
  time: number             // Unix seconds (monotonic, for lightweight-charts)
  priceGap: number | null  // coinbase - kraken
  comboA: number | null    // KS yes_ask + PM down_ask
  comboB: number | null    // KS no_ask + PM up_ask
  coinbase: number | null   // BRTI estimate (6-exchange CF Benchmarks replication, Kalshi settlement source)
  chainlink: number | null  // Chainlink BTC/USD via PM RTDS (PM settlement source)
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

export interface PositionData {
  platform: 'kalshi' | 'polymarket'
  ticker: string
  title: string
  side: string
  size: number
  avgPrice: number
  currentValue: number | null
  pnl: number | null
}

export interface PositionsState {
  kalshi: PositionData[]
  polymarket: PositionData[]
  loading: boolean
  error: string | null
  lastFetched: number
}

// Extend Window for Electron bridge
declare global {
  interface Window {
    electronAPI?: {
      quit: () => void
    }
  }
}
