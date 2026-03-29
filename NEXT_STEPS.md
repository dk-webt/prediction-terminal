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

### Trade Execution (Flow Only) ✅
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

## Active

### 1. Fix Trade Execution

**Goal:** Get BUY/SELL orders actually executing on both platforms.

**Issues:**
- Order execution errors on confirm (Y) — need to capture and debug server-side errors
- Polymarket CLOB client credential derivation needs testing with proxy wallet
- Kalshi RSA-PSS signed POST requests need live validation
- Added server-side logging for order flow (INFO/WARNING/ERROR)

**Next steps:**
- Test with server logs visible to capture exact error
- Validate Polymarket `derive_api_key()` succeeds with proxy wallet private key
- Validate Kalshi order placement with RSA-PSS auth
- Test small orders on both platforms

---

## Backlog

### Resolution Clause Comparison

Verify that semantically similar events actually resolve the same way before flagging as arbitrage. Use Gemini to compare resolution clauses and flag mismatches.

### Go Data Service (Phase 2)

Replace Python clients for market data fetching with a high-throughput Go service. Concurrent API fetching with goroutines, WebSocket price streaming.

### C++ Execution Engine (Phase 3)

Ultra-low latency order execution. gRPC interface, order signing, CLOB interaction, risk checks, position limits.

### Additional Features

- Order book depth display — bid/ask ladders in detail panel
- Position P&L tracking — real-time unrealized P&L
- Multi-window support — detach panels into separate windows
- Alerts — notify when arbitrage opportunities exceed threshold
- Historical price charts — time series within BTC panel
- Sports betting integration

---

## Priority Order

| Priority | Feature | Status |
|----------|---------|--------|
| 1 | Arbitrage detection | Done |
| 2 | Time-to-resolution sort | Done |
| 3 | Persistent ID cache | Done |
| 4 | BTC 15-min streaming | Done |
| 5 | Trade execution flow | Done |
| 6 | Closable panels | Done |
| 7 | **Fix trade execution** | **Active** |
| 8 | Resolution clause check | Backlog |
| 9 | Go data service | Backlog |
| 10 | C++ execution engine | Backlog |
