# Agent Reference: Prediction Market Terminal

## Project Goal
Bloomberg-style desktop trading terminal for finding and eventually executing arbitrage across prediction markets (Kalshi, Polymarket). Planned expansion to stocks and sports betting.

## Current State
Phase 1 complete: Electron + React frontend communicating with Python FastAPI backend. Semantic matching via Gemini embeddings. No order execution yet.

## Planned Architecture
```
Electron (React + TS)   ← Bloomberg amber UI, 4-panel layout
        ↕ REST + WebSocket
Python FastAPI :8081    ← analytics, semantic matching, arbitrage
        ↓ (future)
Go Service :8080        ← high-throughput data fetching, price streaming (Phase 2)
        ↓ (future)
C++ Engine              ← sub-ms order execution, CLOB, signing (Phase 3)
```

## File Map

### Python Backend (`/`)
```
main.py           CLI entry — Rich tables, argparse; commands: list, compare, arb, cache
api_server.py     FastAPI :8081 — REST endpoints + WebSocket streaming for ARB/CMP
comparator.py     Core logic — two-level semantic matching + arbitrage detection
cache.py          SQLite cache at .cache/market_matches.db — caches embedding scores
models.py         Dataclasses: NormalizedEvent, NormalizedMarket, MatchResult,
                  MarketMatchResult, ArbitrageResult
config.py         Loads .env — KALSHI_API_KEY, GEMINI_API_KEY
clients/
  polymarket.py   Polymarket REST client (gamma-api.polymarket.com)
  kalshi.py       Kalshi REST client (api.elections.kalshi.com)
  embeddings.py   Gemini embedding-001 client with rate limiting + batching
```

### Electron Terminal (`terminal/`)
```
electron/main.ts      Electron main — spawns Python server, window lifecycle
electron/preload.ts   Context bridge — exposes window.electronAPI.quit()
src/App.tsx           Root component — WS lifecycle, command router, keyboard shortcuts
src/store.ts          Zustand store — all shared state + center history management
src/types.ts          TypeScript interfaces mirroring Python dataclasses
src/components/
  CommandBar.tsx      Bottom command input with history (↑/↓)
  StatusBar.tsx       Top bar — clock, WS status, cache stats, last command
  PanelGrid.tsx       4-panel layout container
  EventsPanel.tsx     Left/right PM and KS event lists
  ResultsPanel.tsx    Center — ARB table, CMP table, HELP, CACHE, CATS, HIST views
  DetailPanel.tsx     Right — detailed view of selected ARB/CMP/event row
src/styles/bloomberg.css  Amber-on-black theme
```

## Data Flow

### Startup
1. `python3 api_server.py` → FastAPI on 127.0.0.1:8081
2. `cd terminal && npm run dev` → Electron opens, renderer connects to `ws://localhost:8081/ws/status`

### ARB/CMP Command (WebSocket)
1. User types `ARB 200` → App sends `{"type":"arb","limit":200,...}`
2. Server runs `_run_arb()` in thread pool (blocking), sends progress messages
3. Worker: fetch PM → fetch KS → `find_market_matches()` → `find_arbitrage()`
4. Server sends `{"type":"done","data":[...],"pm_events":[...],"ks_events":[...]}`
5. Store updates, ResultsPanel re-renders

### PM/KS Command (REST)
- `GET /events/polymarket?limit=N` or `GET /events/kalshi?limit=N`

## Key Algorithms

### Two-Level Semantic Matching (`comparator.py`)
1. Embed event titles → cosine similarity matrix → greedy best-first assignment (event pairs)
2. For each matched event pair, embed sub-market questions → greedy assign (bracket pairs)
3. Single-market shortcut: if both sides have exactly 1 market, inherit event-level score

### Cache Strategy (`cache.py`)
- Schema: `event_pairs` + `market_pairs` tables keyed on (pm_event_id, ks_event_ticker)
- On cache hit: reconstruct MarketMatchResult with **live prices** + **cached scores**
- Invalidation: if any current market ID absent from cached set → re-embed entire pair

### Arbitrage Detection (`comparator.py:find_arbitrage`)
- Two leg combos: `pm_yes + ks_no` vs `ks_yes + pm_no`
- spread = sum of ask prices for chosen leg pair; profit = 1.0 - spread
- annualized_return = (profit / days_to_resolution) * 365
- Sort by annualized_return desc

## API Quirks

### Polymarket (`gamma-api.polymarket.com/events`)
- `outcomePrices` is a **JSON-encoded string** — must `json.loads()` it
- Use `bestAsk` for yes_price; derive no_price from `1 - bestBid`
- Fallback to `outcomePrices[0]` / `[1]` if ask/bid unavailable
- Filter out settled markets (price at 0 or 1) and closed markets
- Paginate with `limit` + `offset`, sort by `volume24hr` desc

### Kalshi (`api.elections.kalshi.com/trade-api/v2/events`)
- **`api.kalshi.com` and `trading-api.kalshi.com` do NOT resolve from this server** — use `api.elections.kalshi.com`
- Prices are in **dollars (0.0–1.0)** — `yes_ask_dollars`, `no_ask_dollars`, `no_bid_dollars`, `last_price_dollars` (no /100 needed; `response_price_units: usd_cent` is misleading)
- Cursor-based pagination; `with_nested_markets=true` for brackets
- Volume field is `volume_fp` (not `volume`)
- URL format: `kalshi.com/events/{event_ticker_lower}` — no extra series API call needed
- Sub-market questions: combine event title + `no_sub_title` field
- **`end_date` = `min(close_time)` across active sub-markets** — use earliest active market close, not raw `e["markets"][0]` which may be inactive/future

#### ⚠️ Known issue: Kalshi settlement dates vs. actual event dates
Kalshi's `close_time` on markets is a **settlement deadline**, not the actual event date. The UI says "Market closes: After the outcome occurs / Projected payout: 5 minutes after closing" — meaning the market can resolve whenever the outcome is known, but Kalshi sets a hard outer deadline (often 1–2 years out) as a catch-all.

**Impact on `max_days` filtering (e.g. `ARB 200 30D`):**
- A Kalshi event about "March 2026 Fed decision" may have `close_time = 2027-03-01` (one year out as a catch-all), while the PM equivalent ends `2026-03-20`.
- `_filter_events_by_days` on KS will drop this event even though it's a near-term opportunity.
- **Current workaround**: `find_market_matches` pre-filters at `max_days + 365` (loose) and relies on `find_arbitrage`'s `min(pm_close, ks_close)` as the strict cutoff. This means the ARB result's `days_to_resolution` uses the PM date (the earlier one), which is accurate.
- **Remaining gap**: the KS panel (`KS 50 30D`) will still show far-out events whose Kalshi settlement date exceeds `max_days`. The REST endpoint filters on `end_date` which is the Kalshi close_time, not the true expected resolution date.
- **Ideal fix**: use Kalshi's `expected_expiration_time` field (present in market data) instead of `close_time` for `end_date`. `expected_expiration_time` tracks the actual anticipated resolution and updates as the event approaches.

### Gemini Embeddings (`gemini-embedding-001`)
- Batch size 80 texts, sleep 1.6s between batches (stays under 3000 texts/min)
- Exponential backoff on 429, max 5 retries

## UI Commands
Tokens after the command are order-independent. `N` = integer limit, `ND` = max days to expiry (e.g. `30D`), `CAT` = category (e.g. `SPORTS`).
```
PM [N] [ND]          Fetch N Polymarket events; ND filters by days to expiry
KS [N] [ND]          Fetch N Kalshi events
ARB [N] [CAT] [ND]   Run arbitrage (WebSocket, streams progress)
CMP [N] [CAT] [ND]   Run comparison (WebSocket, streams progress)
CATS                 Show available categories
CACHE                Show cache stats
CLEAR                Clear cache
LIMIT N              Set default fetch limit
HIST [N]             Show/navigate result history
R                    Re-run last command
HELP / ?             Show help
Q                    Quit
```

## UI Keyboard Shortcuts
```
/ or :         Focus command bar
Esc            Unfocus command bar
Tab            Cycle panels (PM → KS → center → detail)
Shift+Tab      Cycle backward
↑ / ↓          Navigate rows in active panel
Alt+← / →     Navigate center history
```

## State Management (`store.ts`)
- Zustand store; **always use `useStore.getState().setter()`** inside WS callbacks/async code to avoid stale closures
- `pendingCmdRef` in App.tsx tracks which WS command is in-flight (ARB vs CMP) to route `done` messages correctly
- Center history: array of snapshots (view + results + timestamp); Alt+←/→ navigates

## Color Scheme
```css
--bg:        #000000
--bg-panel:  #0a0800
--bg-header: #0f0a00
--border:    #2a1a00
--amber:     #ffb000   /* primary text */
--amber-dim: #996800   /* headers */
--amber-hi:  #ffcc44   /* selected */
--green:     #00cc44   /* profit / active */
--red:       #cc2200   /* loss / error */
```

## Running the App
```bash
# Python analytics server
cd /home/dastiger/prediciton && python3 api_server.py

# Electron terminal (separate terminal)
cd /home/dastiger/prediciton/terminal && npm install && npm run dev

# Install Python deps if needed
python3 -m pip install -r requirements.txt --break-system-packages
```

## Roadmap
- **Phase 2**: Go service replaces Python clients for market data; WebSocket price streaming
- **Phase 3**: C++ execution engine via gRPC; order placement (Polymarket CLOB + Kalshi REST); risk checks
- **Long-term**: Stocks, sports betting, sentiment analysis, ML-based edge detection
