# Next Steps: Prediction Market Terminal

## Completed

### Arbitrage Detection ✅
- `find_arbitrage()` in `comparator.py` computes pm_yes+ks_no vs ks_yes+pm_no spreads
- `ARB` command in terminal, sorted by annualized return
- Category filtering (e.g. `ARB SPORTS`)

### Time-to-Resolution Sorting ✅
- `annualized_return = (profit / days_remaining) * 365`
- `--max-days N` and `--min-profit N` filtering
- Days remaining column in ARB table

### Persistent ID Cache ✅
- SQLite cache at `.cache/market_matches.db`
- Caches (pm_event_id, ks_event_ticker) -> market pair scores
- `--refresh-cache` flag, `CACHE` / `CLEAR` commands
- Auto-invalidation when new markets appear in a bracket

### Trade Execution ✅
- `clients/executor.py` with Kalshi (RSA-PSS auth) and Polymarket (py-clob-client)
- `BUY` / `SELL` commands with Y/N confirmation flow
- `POS` command for positions
- `FUND KS/PM/PCT` for available cash tracking + max contract calculations
- Polymarket proxy wallet support (signature_type=1)

### BTC 15-Min Binary Options ✅
- Live WebSocket streaming from both Kalshi and Polymarket
- Auto-rolling between 15-minute windows
- Synthetic options analysis (combined cost, profit, strike gap, contracts)
- Rolling state indicator during Kalshi transition delay

### Closable Panels ✅
- `SHOW` / `HIDE` / `TOGGLE` commands for PM, KS, DETAIL panels
- Close button (X) on each panel header
- Dynamic grid layout — center panel expands when side panels hidden

---

## Remaining

### 1. Resolution Clause Comparison

**Goal:** Verify that semantically similar events actually resolve the same way before flagging as arbitrage.

**Problem:** "Trump out before 2027?" (PM) and "Will Trump resign during his term?" (KS) score highly on semantic similarity but resolve differently — the first includes removal by impeachment or death, the second is resignation-only.

**How it works:**
- Fetch resolution text from each platform:
  - Polymarket: `resolutionSource`, `rules_primary`, `rules_secondary` fields on market objects
  - Kalshi: `rules_primary`, `rules_secondary` fields on market objects
- Use Gemini (or a lightweight LLM call) to compare clauses and flag mismatches
- Add `resolution_compatible: bool | None` to `MarketMatchResult`

**Implementation plan:**
- New `resolution.py` module with `compare_resolution_clauses(pm_rules, ks_rules) -> float`
- Returns a compatibility score (0-1); threshold ~0.85 to flag as compatible
- Show a warning symbol in bracket output when clauses diverge significantly
- `--strict-resolution` flag to exclude incompatible pairs from arbitrage output

---

### 2. Go Data Service (Phase 2)

**Goal:** Replace Python clients for market data fetching with a high-throughput Go service.

- Concurrent API fetching with goroutines
- WebSocket price streaming from Go to Electron
- Port 8080, proxied by the Electron app
- Python service remains for ML/NLP (semantic matching, sentiment)

---

### 3. C++ Execution Engine (Phase 3)

**Goal:** Ultra-low latency order execution.

- gRPC interface between Go/Electron and C++ engine
- Order signing (Polymarket ECDSA, Kalshi RSA-PSS)
- CLOB interaction and risk checks
- Position limits and exposure management

---

### 4. Additional Features

- **Order book depth display** — show bid/ask ladders in detail panel
- **Position P&L tracking** — real-time unrealized P&L based on live prices
- **Multi-window support** — detach panels into separate windows
- **Alerts** — notify when arbitrage opportunities exceed threshold
- **Historical price charts** — time series of contract prices within the BTC panel
- **Sports betting integration** — extend beyond prediction markets

---

## Priority Order

| Priority | Feature | Status |
|----------|---------|--------|
| 1 | Arbitrage detection | Done |
| 2 | Time-to-resolution sort | Done |
| 3 | Persistent ID cache | Done |
| 4 | BTC 15-min streaming | Done |
| 5 | Trade execution | Done |
| 6 | Closable panels | Done |
| 7 | Resolution clause check | Next |
| 8 | Go data service | Planned |
| 9 | C++ execution engine | Future |
