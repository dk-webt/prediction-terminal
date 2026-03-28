# CLAUDE.md — Instructions for Claude Code

## Project Overview

Prediction Market Terminal: Bloomberg-style Electron desktop app for cross-platform arbitrage detection and trade execution across Polymarket and Kalshi.

## Stack

- **Frontend**: Electron + React + TypeScript (electron-vite, Zustand store)
- **Backend**: Python FastAPI on port 8081 (REST + WebSocket)
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

### WebSocket Protocol
- Client sends `{"type": "<cmd>", ...}` to `/ws/status`
- Server responds with `{"type": "progress", "msg": "..."}` then `{"type": "done", "data": [...]}`
- BTC streaming: `{"type": "btc_update", ...}` pushed continuously
- Order flow: `btc_order` -> `btc_order_confirm` -> `btc_order_execute`/`btc_order_cancel` -> `btc_order_result`

### Zustand Store
- `useStore` holds all shared state; setters are stable refs
- Use `useStore.getState()` in WS callbacks to avoid stale closures
- `pendingCmdRef` tracks which WS command is in flight

### Panel Layout
- Dynamic CSS grid in PanelGrid.tsx — columns adjust based on `showPm`/`showKs`/`showDetail`
- Panels: PM (0), KS (1), Center/Results (2), Detail (3)
- `activePanel` determines keyboard focus; `Tab` cycles panels

### BTC Watcher (`clients/btc_watcher.py`)
- `BtcStreamManager` — async class that streams prices from both platforms
- Kalshi: WebSocket with RSA-PSS auth, falls back to REST polling
- Polymarket: WebSocket to CLOB
- Auto-rolls to next 15-min window; `_rolling` flag marks transition state
- Old Kalshi data kept visible during roll (~18s Kalshi delay)

### Trading (`clients/executor.py`)
- Kalshi: `POST /trade-api/v2/portfolio/orders` with RSA-PSS signed headers
- Polymarket: `py-clob-client` with `signature_type=1` (POLY_PROXY)
- All orders go through Y/N confirmation in the terminal

## API Specifics

### Polymarket
- `https://gamma-api.polymarket.com/events` — public, no auth
- `outcomePrices` is a JSON-encoded string, must `json.loads()` it
- Use `lastTradePrice` as primary price, `outcomePrices[0]` as fallback
- Sort by volume: `order=volume24hr&ascending=false`

### Kalshi
- `https://api.elections.kalshi.com/trade-api/v2/events` — public, optional auth
- `api.kalshi.com` and `trading-api.kalshi.com` do NOT resolve from this server
- Prices in cents (0-100), divide by 100 for probability
- Use `last_price` for yes_price, `no_bid` for no_price

## Environment Variables (`.env`)

```
GEMINI_API_KEY=...
KALSHI_API_KEY=...
KALSHI_PRIVATE_KEY_PATH=~/.ssh/kalshi_private_key.pem
POLYMARKET_PRIVATE_KEY=0x...
POLYMARKET_WALLET_ADDRESS=0x...
```

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
