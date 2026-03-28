# CLAUDE.md — Instructions for Claude Code

## Project Overview

Prediction Market Terminal: Bloomberg-style Electron desktop app for cross-platform arbitrage detection and trade execution across Polymarket and Kalshi. Includes live BTC 15-min binary options streaming with real-time WebSocket price feeds.

## Stack

- **Frontend**: Electron + React + TypeScript (electron-vite, Zustand store)
- **Backend**: Python FastAPI on port 8081 (REST + 3 WebSockets)
- **Matching**: Gemini embeddings for semantic market matching
- **Trading**: Kalshi RSA-PSS auth, Polymarket py-clob-client (proxy wallet, signature_type=1)

## Key Commands

```bash
# Python server
cd /home/dastiger/prediciton && python3 api_server.py

# Electron dev
cd /home/dastiger/prediciton/terminal && npm run dev

# Install deps
python3 -m pip install -r requirements.txt --break-system-packages
cd terminal && npm install
```

## Architecture Patterns

### WebSocket Architecture (3 separate sockets)

The frontend uses a `ConnectionManager` (`terminal/src/ws/ConnectionManager.ts`) that manages 3 independent WebSocket connections, each with its own reconnect logic:

| Endpoint | Purpose | Traffic |
|----------|---------|---------|
| `/ws/cmd` | ARB, CMP commands (progress/done) | Request/response |
| `/ws/btc` | BTC price streaming + debug | Server push, ~2/sec |
| `/ws/trade` | Order confirm/execute/cancel | Stateful, low freq |

- Each socket reconnects independently with 2s retry
- BTC socket auto-resubscribes on reconnect if streaming was active
- `BtcStreamManager` is module-level — supports multiple subscribers via broadcast
- Guards prevent duplicate connections (React StrictMode safe)

### WebSocket Protocol
- `/ws/cmd`: Client sends `{"type": "arb"|"compare", ...}` → Server streams `{"type": "progress"}` then `{"type": "done", "data": [...]}`
- `/ws/btc`: Client sends `{"type": "btc", "action": "subscribe"}` → Server pushes `{"type": "btc_update", ...}` continuously
- `/ws/trade`: `btc_order` → `btc_order_confirm` → `btc_order_execute`/`btc_order_cancel` → `btc_order_result`

### Zustand Store
- `useStore` holds all shared state; setters are stable refs
- Use `useStore.getState()` in WS callbacks to avoid stale closures
- `pendingCmdRef` tracks which WS command is in flight
- Per-socket status: `cmdWsStatus`, `btcWsStatus`, `tradeWsStatus`

### Panel Layout
- Dynamic CSS grid in PanelGrid.tsx — columns adjust based on `showPm`/`showKs`/`showDetail`
- Panels closable via SHOW/HIDE/TOGGLE commands or X button
- Panels: PM (0), KS (1), Center/Results (2), Detail (3)
- `activePanel` determines keyboard focus; `Tab` cycles panels

### BTC Watcher (`clients/btc_watcher.py`)
- `BtcStreamManager` — async class that streams prices from both platforms
- Kalshi: WebSocket with RSA-PSS auth, falls back to REST polling (3s interval)
- Polymarket: WebSocket to CLOB with app-level PING every 8s
- Auto-rolls to next 15-min window; `_rolling` flag marks transition state
- Old Kalshi data kept visible during roll (~18s Kalshi delay)
- Per-platform staleness detection: logs WARNING after 10s no data, INFO on recovery
- KS WS forces reconnect after 180s idle
- Roll loop wrapped in try/except — crashes are logged and recovered next window
- PM strike price: fetched from `https://polymarket.com/api/crypto/crypto-price` — appears ~8s after window start, settles immediately (no drift)

### Trading (`clients/executor.py`)
- Kalshi: `POST /trade-api/v2/portfolio/orders` with RSA-PSS signed headers
- Polymarket: `py-clob-client` with `signature_type=1` (POLY_PROXY), derives API creds from private key
- All orders go through Y/N confirmation in the terminal
- Order execution logged at INFO/WARNING/ERROR level in server console

## API Specifics

### Polymarket
- `https://gamma-api.polymarket.com/events` — public, no auth
- `outcomePrices` is a JSON-encoded string, must `json.loads()` it
- Use `lastTradePrice` as primary price, `outcomePrices[0]` as fallback
- Sort by volume: `order=volume24hr&ascending=false`
- Strike price: `https://polymarket.com/api/crypto/crypto-price?symbol=BTC&eventStartTime=...&variant=fifteen&endDate=...`
- Strike appears ~8s after window start, no subsequent changes

### Kalshi
- `https://api.elections.kalshi.com/trade-api/v2/events` — public, optional auth
- `api.kalshi.com` and `trading-api.kalshi.com` do NOT resolve from this server
- Prices in cents (0-100), divide by 100 for probability
- Use `last_price` for yes_price, `no_bid` for no_price
- New contracts appear ~18s after window boundary

## Environment Variables (`.env`)

```
GEMINI_API_KEY=...
KALSHI_API_KEY=...
KALSHI_PRIVATE_KEY_PATH=~/.ssh/kalshi_private_key.pem
POLYMARKET_PRIVATE_KEY=0x...          # MetaMask private key (controls proxy wallet)
POLYMARKET_WALLET_ADDRESS=0x...       # Signer/relayer address from PM API settings
```

Note: Polymarket API key/secret/passphrase are NOT needed in .env — they are derived automatically from the private key via `py-clob-client`.

## Code Style

- Python: no type stubs, use `dict | None` not `Optional[dict]`
- TypeScript: interfaces in `types.ts`, Zustand store in `store.ts`
- CSS: Bloomberg amber theme variables defined in `bloomberg.css`
- Commands are UPPERCASE in the terminal (parsed case-insensitively)

## Testing

No test suite currently. Verify Python with:
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
- `KS STALE: no Kalshi data for 10s` / `PM STALE: no Polymarket data for 10s`
- `KS RECOVERED` / `PM RECOVERED`
- `KS WS idle too long (180s), forcing reconnect`
- `ROLL LOOP CRASHED` / `ROLL PARTIAL` / `ROLL FAILED`

## Caution: replace_all

When using Edit tool's `replace_all`, verify it doesn't replace content inside method definitions that should remain as-is. A past incident replaced timestamp assignments inside the `_mark_ks_recv`/`_mark_pm_recv` method bodies, causing infinite recursion.
