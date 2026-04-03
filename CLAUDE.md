# CLAUDE.md — Instructions for Claude Code

## Project Overview

Prediction Market Terminal: Bloomberg-style Electron desktop app for cross-platform arbitrage detection and trade execution across Polymarket and Kalshi. Includes live BTC 15-min binary options streaming with real-time WebSocket price feeds, BRTI replication for settlement price tracking, and auto-trade execution (ATE).

## Stack

- **Frontend**: Electron 33 + React 18 + TypeScript 5.6 (electron-vite, Zustand 5, lightweight-charts)
- **Backend**: Python FastAPI on port 8081 (REST + 3 WebSockets)
- **Matching**: Gemini embeddings (V2 rich matcher with composite scoring, V1 fuzzy fallback)
- **Trading**: Kalshi RSA-PSS auth, Polymarket py-clob-client (Safe wallet, signature_type=2 GNOSIS_SAFE)
- **Live Pricing**: RedundantWSPool (N=2 parallel connections with dedup), BRTI tracker (6 exchange feeds)

## Key Commands

```bash
# Python server
cd /home/dastiger/prediciton && python3 api_server.py

# Electron dev
cd /home/dastiger/prediciton/terminal && npm run dev

# Install deps
python3 -m pip install -r requirements.txt --break-system-packages
cd terminal && npm install

# PM stream test harness (no API keys needed)
python3 tests/test_pm_stream.py              # 2 redundant connections
python3 tests/test_pm_stream.py --pool-size 1  # single connection baseline
python3 tests/test_pm_stream.py -v           # verbose: every price update
```

## File Structure

```
prediciton/
  api_server.py          # FastAPI server (REST + 3 WebSocket endpoints)
  config.py              # Environment variable loader
  models.py              # NormalizedEvent, NormalizedMarket, MatchResult, ArbitrageResult
  comparator.py          # Two-level matching orchestration + arbitrage detection
  cache.py               # SQLite persistence for match embeddings and scores
  main.py                # CLI interface (argparse)
  clients/
    btc_watcher.py       # BtcStreamManager — live 15-min BTC options streaming
    ws_pool.py           # RedundantWSPool — N parallel WS connections with dedup
    brti_tracker.py      # BRTI replication (6 exchange feeds, CF Benchmarks methodology)
    executor.py          # Trading execution (Kalshi RSA-PSS, PM py-clob-client)
    polymarket.py        # PM event/market fetch + normalization
    kalshi.py            # KS event/market fetch + normalization
    embeddings.py        # Gemini embedding API client with rate-limit handling
  matchers/
    protocol.py          # EventMatcher protocol (runtime_checkable)
    v1.py                # GeminiFuzzyMatcher (embeddings + rapidfuzz fallback)
    v2.py                # GeminiRichMatcher (rich embeddings + composite scoring + cache)
  scripts/
    pm_latency_test.py   # PM WebSocket latency testing
    pm_orderbook_monitor.py  # PM orderbook depth monitor
    strike_poll_test.py  # PM strike price polling test
  tests/
    test_pm_stream.py    # Standalone PM bid/ask stream test harness
  terminal/              # Electron + React frontend
    src/
      App.tsx            # Main app (command handling, WS callbacks, keyboard shortcuts)
      store.ts           # Zustand store (all shared state)
      types.ts           # TypeScript interfaces
      ws/ConnectionManager.ts  # 3 independent WS connections with auto-reconnect
      components/
        PanelGrid.tsx    # Dynamic CSS grid layout
        BtcPanel.tsx     # BTC 15-min charts + platform cards
        DetailPanel.tsx  # ARB/CMP/BTC detail views
        ResultsPanel.tsx # Center panel (ARB, CMP, HELP, CACHE, CATS, HIST, BTC views)
        EventsPanel.tsx  # PM/KS event lists
        StatusBar.tsx    # Top status bar (clock, WS status, cache stats)
        CommandBar.tsx   # Command input with history
        PositionsPanel.tsx  # Active positions display
        OrdersPanel.tsx  # Order tracking + fill history
      styles/bloomberg.css  # Bloomberg amber theme
    electron/
      main.ts            # Electron main process (spawns Python server)
      preload.ts         # Context-isolated bridge (quit only)
```

## Architecture Patterns

### WebSocket Architecture (3 separate frontend sockets)

The frontend uses a `ConnectionManager` (`terminal/src/ws/ConnectionManager.ts`) that manages 3 independent WebSocket connections, each with its own reconnect logic:

| Endpoint | Purpose | Traffic |
|----------|---------|---------|
| `/ws/cmd` | ARB, CMP commands (progress/done) | Request/response |
| `/ws/btc` | BTC price streaming + debug + ATE | Server push, ~2/sec |
| `/ws/trade` | Order confirm/execute/cancel + fills | Stateful, low freq |

- Each socket reconnects independently with 2s retry
- BTC socket auto-resubscribes on reconnect if streaming was active
- `BtcStreamManager` is module-level — supports multiple subscribers via broadcast
- Guards prevent duplicate connections (React StrictMode safe)

### WebSocket Protocol
- `/ws/cmd`: Client sends `{"type": "arb"|"compare", ...}` -> Server streams `{"type": "progress"}` then `{"type": "done", "data": [...]}`
- `/ws/btc`: Client sends `{"type": "btc", "action": "subscribe"}` -> Server pushes `{"type": "btc_update", ...}` continuously
  - Also: `btc_stopped`, `btc_debug_status`, `btc_debug_log`, `ate_status`, `ate_triggered`, `ate_done`, `ate_unwind`, `btc_refresh_status`
- `/ws/trade`: `btc_order` -> `btc_order_confirm` -> `btc_order_execute`/`btc_order_cancel` -> `btc_order_result`
  - Also: `ks_fill`, `ks_order_update`, `pm_fill`, `pm_order_update`

### RedundantWSPool (`clients/ws_pool.py`)

Reusable N-connection pool for high-availability WebSocket streaming:

```
Connection 0  --+
                +--> dedup ring (512, MD5) --> on_message(raw) callback
Connection 1  --+
```

- `WSPoolConfig` dataclass: url, pool_size, subscribe_msgs (lambda), ping_text, timeouts, dedup_key
- `RedundantWSPool`: start/stop, swap_subscriptions, send_all, is_live, health
- Per-connection reconnect loop with exponential backoff (0.5s base, 5s max)
- Staggered startup (conn_id * 0.5s) to avoid thundering herd
- Ring buffer dedup: `deque(maxlen=512)` + mirrored `set` for O(1) lookup
- Used by both PM market WS and KS data WS

### Polymarket WebSocket Subscription Formats

PM CLOB WebSocket has TWO different subscription message formats:

| Context | Format |
|---------|--------|
| Initial (on connect) | `{"assets_ids": [...], "type": "market", "custom_feature_enabled": true}` |
| Dynamic (mid-connection swap) | `{"assets_ids": [...], "operation": "subscribe", "custom_feature_enabled": true}` |
| Unsubscribe | `{"assets_ids": [...], "operation": "unsubscribe"}` |

**Critical**: Using the initial format for mid-connection swaps causes PM to stop sending book/price_change events. Always use `"operation": "subscribe"` for swaps.

`swap_subscriptions()` does NOT overwrite `subscribe_msgs` — the initial lambda reads `self._pm_token_ids` by reference, so reconnects naturally pick up new token IDs with the correct initial format.

### Zustand Store
- `useStore` holds all shared state; setters are stable refs
- Use `useStore.getState()` in WS callbacks to avoid stale closures
- `pendingCmdRef` tracks which WS command is in flight
- Per-socket status: `cmdWsStatus`, `btcWsStatus`, `tradeWsStatus`
- Center panel history with back/forward navigation (Alt+Arrow keys)
- Order tracking: activeOrders Map, fillHistory (max 50), recentOrders (max 50)
- BTC time series: sliding window of 2000 points, resets on contract roll

### Panel Layout
- Dynamic CSS grid in PanelGrid.tsx — columns adjust based on `showPm`/`showKs`/`showDetail`
- Panels closable via SHOW/HIDE/TOGGLE commands or X button
- Panels: PM (0), KS (1), Center/Results (2), Detail (3)
- `activePanel` determines keyboard focus; `Tab` cycles panels

### BTC Watcher (`clients/btc_watcher.py`)
- `BtcStreamManager` — async class that streams prices from both platforms
- **Kalshi**: RedundantWSPool with RSA-PSS auth, falls back to REST polling (3s interval)
- **Polymarket**: RedundantWSPool (pool_size=2) to CLOB WS, public (no auth needed for market data)
- **BRTI Tracker**: 6 exchange feeds (Coinbase, Kraken, Bitstamp, Gemini, Crypto.com, Bullish) replicating CF Benchmarks BRTI methodology — used as Kalshi settlement price source
- **RTDS**: Chainlink BTC/USD price feed via Polymarket RTDS WebSocket — used as PM settlement price source
- **PM User WS**: Authenticated channel for fill/order tracking (requires API creds)
- Auto-rolls to next 15-min window; `_rolling` flag marks transition state
- Subscription swap uses `"operation": "subscribe"` (dynamic format) on roll
- Old Kalshi data kept visible during roll (~18s Kalshi delay)
- Per-platform staleness detection: logs WARNING after 5s no data, INFO on recovery
- Per-platform uptime tracking: `_pm_live_accum` / `_pm_window_start` — reset AFTER roll completes
- PM strike price: fetched from `https://polymarket.com/api/crypto/crypto-price` — appears ~8s after window start, settles immediately (no drift)
- REST OB refresh loop every 5s corrects WS state drift (skipped during rolls)

### Two-Level Semantic Matching
- **V2 (default)**: `GeminiRichMatcher` — rich text embeddings + composite scoring (70% cosine, 30% structural)
- **V1 (fallback)**: `GeminiFuzzyMatcher` — basic embeddings + rapidfuzz token_sort_ratio fallback
- Events matched loosely (threshold 0.75), sub-markets matched strictly (threshold 0.82)
- Embedding cache: SQLite with SHA256 text_hash keys — invalidates on text change
- Match cache: SQLite persistence of event/market pair scores
- Kalshi date quirk: settlement dates are 1+ year past event; V2 uses slow decay (2000-day constant, 0.6 floor)

### Auto-Trade Executor (ATE)
- Triggers on BTC 15-min arb opportunities (profit > 6c, `ATE_MIN_PROFIT = 0.06`)
- Liquidity-aware: checks orderbook depth before executing
- Adaptive sizing: executes min(available, `ATE_MAX_COUNT=10`) contracts
- Unwind logic for one-leg failures (immediate market sell to close)
- Prevents execution <59s to settlement

### Trading (`clients/executor.py`)
- Kalshi: `POST /trade-api/v2/portfolio/orders` with RSA-PSS signed headers
- Polymarket: `py-clob-client` with `signature_type=2` (GNOSIS_SAFE) for Safe wallet accounts
  - L2 CLOB creds derived from private key, or set explicitly in .env
  - Optional builder config for gasless trading
- All orders go through Y/N confirmation in the terminal
- Order execution logged at INFO/WARNING/ERROR level in server console

## API Specifics

### Polymarket
- `https://gamma-api.polymarket.com/events` — public, no auth
- `outcomePrices` is a JSON-encoded string, must `json.loads()` it
- Use `bestAsk`/`bestBid` as primary price, `outcomePrices[0]` as fallback
- Sort by volume: `order=volume24hr&ascending=false`
- Strike price: `https://polymarket.com/api/crypto/crypto-price?symbol=BTC&eventStartTime=...&variant=fifteen&endDate=...`
- Strike appears ~8s after window start, no subsequent changes
- CLOB WS: `wss://ws-subscriptions-clob.polymarket.com/ws/market` — public, no auth for market data
- User WS: `wss://ws-subscriptions-clob.polymarket.com/ws/user` — requires API creds
- RTDS WS: `wss://ws-live-data.polymarket.com` — Chainlink price feed

### Kalshi
- `https://api.elections.kalshi.com/trade-api/v2/events` — public, optional auth
- `api.kalshi.com` and `trading-api.kalshi.com` do NOT resolve from this server
- Prices in cents (0-100), divide by 100 for probability
- Use `last_price` for yes_price, `no_bid` for no_price
- New contracts appear ~18s after window boundary
- WS auth: RSA-PSS signed headers per connection

## Environment Variables (`.env`)

```
GEMINI_API_KEY=...
KALSHI_API_KEY=...
KALSHI_PRIVATE_KEY_PATH=~/.ssh/kalshi_private_key.pem
POLYMARKET_PRIVATE_KEY=0x...          # MetaMask private key (controls proxy wallet)
POLYMARKET_WALLET_ADDRESS=0x...       # Safe/proxy funder address
POLYMARKET_API_KEY=...                # Optional — derived from private key if not set
POLYMARKET_API_SECRET=...             # Optional — derived from private key if not set
POLYMARKET_API_PASSPHRASE=...         # Optional — derived from private key if not set
POLYMARKET_BUILDER_KEY=...            # Optional — gasless trading
POLYMARKET_BUILDER_SECRET=...         # Optional
POLYMARKET_BUILDER_PASSPHRASE=...     # Optional
COINBASE_CDP_API_KEY=...              # Optional — for BRTI tracker Coinbase feed
COINBASE_CDP_API_SECRET=...           # Optional — falls back to public Coinbase WS
```

## Code Style

- Python: no type stubs, use `dict | None` not `Optional[dict]`
- TypeScript: interfaces in `types.ts`, Zustand store in `store.ts`
- CSS: Bloomberg amber theme variables defined in `bloomberg.css`
- Commands are UPPERCASE in the terminal (parsed case-insensitively)

## Testing

Test harness for PM bid/ask stream (reuses production code, no API keys):
```bash
python3 tests/test_pm_stream.py              # 2 connections, status every 10s
python3 tests/test_pm_stream.py --pool-size 1  # single connection baseline
python3 tests/test_pm_stream.py -v           # verbose: log every price update
```

Shows: live prices, uptime %, per-connection message counts, price flips, REST vs WS divergence, staleness, and handles rolls automatically.

Verify Python:
```bash
python3 -c "import py_compile; py_compile.compile('file.py', doraise=True)"
```

TypeScript:
```bash
cd terminal && npx tsc --noEmit
```

## Debugging BTC Stream

```
DBG ON              # enable debug logging to btc_debug.log
DBG                 # download the log file
DBG OFF             # disable
DBG CLEAR           # clear the log file
```

Server console always shows WARNING-level stale/reconnect logs (no DBG needed):
- `KS STALE: no Kalshi data for 5s` / `PM STALE: no Polymarket data for 5s`
- `KS RECOVERED` / `PM RECOVERED`
- `ROLL LOOP CRASHED` / `ROLL PARTIAL` / `ROLL FAILED`
- `ROLL DONE: pm_ok=True ks_ok=True total=XXXms`

## Caution: replace_all

When using Edit tool's `replace_all`, verify it doesn't replace content inside method definitions that should remain as-is. A past incident replaced timestamp assignments inside the `_mark_ks_recv`/`_mark_pm_recv` method bodies, causing infinite recursion.
