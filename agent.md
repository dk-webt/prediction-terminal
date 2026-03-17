# Agent Reference: Prediction Market Terminal

## Purpose of This File
This file exists for future Claude instances (and any new developer) to quickly understand the project without reading all the code. It documents the goal, architecture, key algorithms, API quirks, design decisions, known issues, and past mistakes. **Read this before making changes.** Update it when you add features, fix bugs, or discover new API behaviour.

---

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

---

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

---

## Data Flow

### Startup
1. `python3 api_server.py` → FastAPI on 127.0.0.1:8081
2. `cd terminal && npm run dev` → Electron opens, renderer connects to `ws://localhost:8081/ws/status`

### PM/KS Commands (REST)
- `GET /events/polymarket?limit=N[&category=CAT][&max_days=N]`
- `GET /events/kalshi?limit=N[&category=CAT][&max_days=N]`
- `max_days` filters events by `end_date` after fetching (via `_filter_by_days()` in `api_server.py`)

### ARB/CMP Commands (WebSocket)
1. User types `ARB 200 30D` → App sends `{"type":"arb","limit":200,"max_days":30,"category":null}`
2. Server runs `_run_arb()` in thread pool (blocking), sends `{"type":"progress","msg":"..."}` updates
3. Worker: fetch PM → fetch KS → `find_market_matches(max_days=30)` → `find_arbitrage(max_days=30)`
4. Server sends `{"type":"done","data":[...],"pm_events":[...],"ks_events":[...]}`
5. Store updates, ResultsPanel re-renders

---

## Key Algorithms

### Two-Level Semantic Matching (`comparator.py:find_market_matches`)
1. Optionally pre-filter both event lists by `max_days + 365` (loose cutoff — see Kalshi date quirk below)
2. Embed event titles → cosine similarity matrix → greedy best-first assignment (event pairs)
3. For each matched event pair, embed sub-market questions → greedy assign (bracket pairs)
4. Single-market shortcut: if both sides have exactly 1 market, inherit event-level score (no re-embedding)
5. Cache check before embedding: load cached scores + live prices on hit; re-embed on miss or invalidation

### Cache Strategy (`cache.py`)
- Schema: `event_pairs` + `market_pairs` tables keyed on `(pm_event_id, ks_event_ticker)`
- On cache hit: reconstruct `MarketMatchResult` with **live prices** + **cached scores**
- Invalidation: if any current market ID absent from cached set → re-embed entire pair
- Cache is valid across `max_days` changes — scores don't depend on the date filter

### Arbitrage Detection (`comparator.py:find_arbitrage`)
- Two leg combos: `pm_yes + ks_no` vs `ks_yes + pm_no`
- `spread` = sum of ask prices for chosen leg; `profit = 1.0 - spread`
- `days_to_resolution` = `min(pm_close, ks_close)` — uses the **earlier** of the two platform dates
- `annualized_return = (profit / days_to_resolution) * 365`
- `max_days` filter applied here (strict) using the cross-platform minimum — this is the correct enforcement point
- Sort: dated entries by `annualized_return` desc; undated by `profit` desc

### `max_days` / `ND` Filter Flow
For `ARB 200 30D` the filter is applied in two stages:
1. **Pre-filter (loose)** in `find_market_matches`: drops events with `end_date > max_days + 365`. The +365 buffer exists because Kalshi settlement dates are often 1+ year past the true event date (see Known Issues). Avoids discarding valid KS candidates before matching.
2. **Strict filter** in `find_arbitrage`: drops any result where `min(pm_close, ks_close) > max_days`. This is the authoritative cutoff and always correct since it uses the earlier of the two dates.

For `PM 50 30D` / `KS 50 30D` (REST): filtered directly by `end_date <= max_days` after fetch. Subject to the Kalshi settlement date issue (see below).

---

## API Quirks

### Polymarket (`gamma-api.polymarket.com/events`)
- `outcomePrices` is a **JSON-encoded string** — must `json.loads()` it
- Use `bestAsk` for yes_price; derive no_price from `1 - bestBid`
- Fallback to `outcomePrices[0]` / `[1]` if ask/bid unavailable
- Filter out settled markets (price at 0 or 1) and closed markets
- Paginate with `limit` + `offset`, sort by `volume24hr` desc — returns near-term active markets first

### Kalshi (`api.elections.kalshi.com/trade-api/v2/events`)
- **`api.kalshi.com` and `trading-api.kalshi.com` do NOT resolve from this server** — always use `api.elections.kalshi.com`
- **Price fields**: `yes_ask_dollars`, `no_ask_dollars`, `no_bid_dollars`, `last_price_dollars` — all in **0.0–1.0 range** (dollar price on a $1 contract). Do NOT divide by 100. The `response_price_units: usd_cent` field in the response is misleading.
- **Volume field**: `volume_fp` (not `volume`)
- **Pagination**: cursor-based; use `with_nested_markets=true` to get sub-market brackets inline
- **URL format**: `kalshi.com/events/{event_ticker_lower}` — direct link, no extra series API call needed
- **Sub-market questions**: combine event `title` + `no_sub_title` field for embedding-ready question
- **`end_date` derivation**: `min(close_time for active markets)` — use the earliest active sub-market close, NOT `e["markets"][0].close_time` (raw list includes inactive/future markets that may have far-out dates)
- **Default sort**: Kalshi returns long-horizon novelty markets first (e.g. "Will Elon Musk visit Mars?" closing 2099). With only 50 events fetched, there is typically **zero topic overlap** with Polymarket's near-term events. Use 200+ for ARB/CMP to get meaningful matches.

#### ⚠️ Known Issue: Kalshi Settlement Dates vs. Actual Event Dates
Kalshi's `close_time` is a **catch-all settlement deadline**, not the true expected resolution date. The Kalshi UI says: *"Market closes: After the outcome occurs. Projected payout: 5 minutes after closing."* Kalshi sets `close_time` 1–2 years out as a hard outer bound; the market actually resolves as soon as the outcome is known.

**Consequence**: a Kalshi event for "March 2026 Fed decision" may have `close_time = 2027-03-01`, while the equivalent Polymarket event ends `2026-03-20`.

**Impact on filtering**:
- `KS 50 30D` will include far-out events because `end_date` (derived from `close_time`) exceeds 30 days even if the underlying event resolves next week.
- For ARB, this is handled: `find_market_matches` pre-filters at `max_days + 365` (loose) and `find_arbitrage` enforces the strict cutoff using `min(pm_close, ks_close)`, so ARB results are always accurate.
- For the KS panel list view, there is no equivalent fix yet.

**Ideal fix**: use `expected_expiration_time` from the market data instead of `close_time` for `end_date`. This field tracks the anticipated resolution date and updates dynamically as the event approaches.

---

## Design Decisions & Past Mistakes

### Date Proximity Guard — Attempted and Removed
A check was added to reject event matches where PM and KS `end_date` differed by more than 5 days, on the theory that semantically equivalent events should resolve around the same time.

**This completely broke ARB** — it produced 0 matches. Root cause: Kalshi settlement dates are systematically ~1 year past PM dates for the same event (e.g. 2028 US election: PM closes 2028-11-07, KS closes 2029-11-07). The 5-day buffer rejected every valid pair.

**Decision**: do not add a date proximity guard to event-level matching. The semantic similarity score already captures date information embedded in event titles (e.g. "2028 election" won't match "2030 election"). Cross-platform date alignment should be enforced at the ARB output stage via `min(pm_close, ks_close)`, not at the matching stage.

### Why `_get_series_slug()` Was Removed
The original Kalshi client made one extra HTTP request to `/series/{series_ticker}` per unique series in order to build a pretty URL (`/markets/{series}/{slug}/{event_ticker}`). With 100 events this was up to 100 extra API calls — the main reason `KS 100` was significantly slower than `PM 100`. The slug was used only for display (clickable links), not for any analytical operation. Replaced with `kalshi.com/events/{event_ticker}` which requires no extra call.

### Kalshi Price Field Rename
Kalshi silently renamed all price fields from `yes_ask`, `no_ask`, `no_bid`, `last_price` (in cents, 0–100) to `yes_ask_dollars`, `no_ask_dollars`, `no_bid_dollars`, `last_price_dollars` (in dollars, 0.0–1.0). The old fields return nothing, causing all prices to read as 0. If prices appear as 0 again, check whether Kalshi has renamed fields again.

---

## State Management (`store.ts`)
- Zustand store; **always use `useStore.getState().setter()`** inside WS callbacks/async code to avoid stale closures
- `pendingCmdRef` in `App.tsx` tracks which WS command is in-flight (ARB vs CMP) to route `done` messages correctly
- Center history: array of snapshots (view + results + timestamp); Alt+←/→ navigates

---

## UI Commands
Tokens after the command are order-independent. `N` = integer limit, `ND` = max days to expiry (e.g. `30D`), `CAT` = category name (e.g. `SPORTS`).
```
PM [N] [ND]          Fetch N Polymarket events; ND filters by days to expiry
KS [N] [ND]          Fetch N Kalshi events (subject to settlement date caveat above)
ARB [N] [CAT] [ND]   Run arbitrage scan (WebSocket, streams progress)
CMP [N] [CAT] [ND]   Run semantic comparison (WebSocket, streams progress)
CATS                 Show available categories on both platforms
CACHE                Show cache statistics
CLEAR                Clear the semantic match cache
LIMIT N              Set default event fetch limit
HIST [N]             Show result history / jump to entry N
R                    Re-run last command
HELP / ?             Show in-terminal command reference
Q                    Quit
```

## UI Keyboard Shortcuts
```
/ or :         Focus command bar
Esc            Unfocus command bar
Tab            Cycle panels forward (PM → KS → center → detail)
Shift+Tab      Cycle panels backward
↑ / ↓          Navigate rows in active panel
Alt+← / →     Navigate center panel history (back / forward)
```

---

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

---

## Running the App
```bash
# Python analytics server
cd /home/dastiger/prediciton && python3 api_server.py

# Electron terminal (separate terminal)
cd /home/dastiger/prediciton/terminal && npm install && npm run dev

# Install Python deps if needed (no bare `pip` or `pip3` available on this server)
python3 -m pip install -r requirements.txt --break-system-packages
```

---

## Roadmap
- **Next**: use `expected_expiration_time` instead of `close_time` for Kalshi `end_date` to fix `KS ND` filtering
- **Phase 2**: Go service replaces Python clients for market data; WebSocket price streaming
- **Phase 3**: C++ execution engine via gRPC; order placement (Polymarket CLOB + Kalshi REST); risk checks
- **Long-term**: Stocks, sports betting, sentiment analysis, ML-based edge detection
