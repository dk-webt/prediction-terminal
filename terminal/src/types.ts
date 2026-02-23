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

// Extend Window for Electron bridge
declare global {
  interface Window {
    electronAPI?: {
      quit: () => void
    }
  }
}
