# Prediction Market Terminal

A Bloomberg-style desktop trading terminal for finding arbitrage opportunities across **Polymarket** and **Kalshi** prediction markets — with live BTC 15-min binary options streaming and trade execution.

![Terminal](https://img.shields.io/badge/stack-Electron%20%7C%20React%20%7C%20Python-ffb000?style=flat&labelColor=000000)

---

## What it does

- Fetches live events from Polymarket and Kalshi
- Semantically matches equivalent markets using **Gemini embeddings**
- Detects cross-platform arbitrage (e.g. buy Yes on PM + No on KS for < $1.00 combined)
- Ranks opportunities by annualized return
- Caches embedding scores in SQLite so repeat runs are fast
- **Live BTC 15-min binary options** — real-time WebSocket streaming from both platforms with auto-rolling between windows
- **Synthetic options** — shows combined cost, profit, strike gap, and max contracts for cross-platform trades
- **Trade execution** — place buy/sell orders on Kalshi and Polymarket directly from the terminal
- **Closable panels** — Bloomberg-style show/hide/toggle for all side panels

---

## Architecture

```
Electron Desktop (React + TypeScript)   ← Bloomberg amber UI, tiled panels
        ↕ REST + WebSocket
Python Analytics Service (port 8081)    ← FastAPI wrapper around existing logic
        ↓
Gemini Embeddings + SQLite Cache        ← semantic matching, arbitrage calc
```

**Planned (Phase 2+):** Go data service for concurrent API fetching and real-time price streaming. C++ execution engine for order signing and CLOB interaction.

---

## Prerequisites

- Python 3.9+
- Node.js 18+ and npm
- A **Gemini API key** (for semantic matching) — [get one here](https://aistudio.google.com/app/apikey)
- A **Kalshi API key + RSA private key** for live streaming and trading
- Optionally **Polymarket wallet keys** for trade execution (see [Polymarket setup](docs/polymarket-setup.md))

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/dk-webt/prediction-terminal.git
cd prediction-terminal
```

### 2. Python dependencies

```bash
python3 -m pip install -r requirements.txt
```

> On some systems use `python3 -m pip install -r requirements.txt --break-system-packages` if pip complains.

### 3. Environment variables

Create a `.env` file in the repo root:

```env
GEMINI_API_KEY=your_gemini_api_key_here

# Kalshi (required for BTC streaming + trading)
KALSHI_API_KEY=your_kalshi_api_key_here
KALSHI_PRIVATE_KEY_PATH=~/.ssh/kalshi_private_key.pem

# Polymarket execution (optional — see docs/polymarket-setup.md)
POLYMARKET_PRIVATE_KEY=0xYOUR_PRIVATE_KEY_HERE
POLYMARKET_WALLET_ADDRESS=0xYOUR_PROXY_WALLET_ADDRESS_HERE
```

### 4. Terminal (Electron) dependencies

```bash
cd terminal
npm install
cd ..
```

---

## Running locally

You need **two terminals** running simultaneously.

### Terminal 1 — Python analytics server

```bash
python3 api_server.py
```

The server starts on `http://localhost:8081`. You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8081
```

### Terminal 2 — Electron desktop app

```bash
cd terminal
npm run dev
```

The Electron window opens automatically with the Bloomberg-style UI.

---

## Terminal commands

Type commands into the `CMD >` bar at the bottom of the window.

### Market Data

| Command | Description |
|---------|-------------|
| `PM [N]` | Fetch N Polymarket events (default: 200) |
| `KS [N]` | Fetch N Kalshi events |
| `ARB [N]` | Run arbitrage scan across both platforms |
| `CMP [N]` | Run semantic bracket comparison |
| `BTC` | Start live BTC 15-min binary options streaming |
| `CATS` | Show event categories from both platforms |

### Trading

| Command | Description |
|---------|-------------|
| `BUY KS YES 10 0.50` | Buy 10 Yes contracts on Kalshi at $0.50 |
| `BUY PM UP 5 MKT` | Market buy 5 Up contracts on Polymarket |
| `SELL KS NO 10 0.55` | Sell 10 No contracts on Kalshi at $0.55 |
| `SELL PM DOWN 5` | Sell 5 Down contracts on Polymarket |
| `POS` | Show current positions on both platforms |
| `FUND KS 50` | Set Kalshi available cash to $50 |
| `FUND PM 60` | Set Polymarket available cash to $60 |
| `FUND PCT 0.6` | Use 60% of funds for contract calculations |
| `FUND` | Show current fund settings |

### Panel Management

| Command | Description |
|---------|-------------|
| `SHOW PM\|KS\|DETAIL` | Show a hidden panel |
| `HIDE PM\|KS\|DETAIL` | Hide a panel |
| `TOGGLE PM\|KS\|DETAIL` | Toggle panel visibility |

### Utilities

| Command | Description |
|---------|-------------|
| `CACHE` | Show cache statistics |
| `CLEAR` | Clear the semantic match cache |
| `LIMIT N` | Set default event limit |
| `DBG ON\|OFF` | Enable/disable BTC debug logging |
| `R` | Re-run last command |
| `?` / `HELP` | Show command reference |
| `Q` | Quit |

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `/` or `:` | Focus command bar |
| `Esc` | Unfocus command bar |
| `Tab` | Cycle panels (left -> center -> right) |
| `Up` `Down` | Navigate rows |

---

## BTC 15-Min Binary Options

The `BTC` command starts live streaming of BTC 15-minute binary option contracts from both Kalshi and Polymarket.

Features:
- **Real-time prices** via WebSocket (Kalshi + Polymarket CLOB)
- **Auto-rolling** — automatically transitions to the next 15-minute window
- **Synthetic options** — detail panel shows combined cost, profit, and max contracts for cross-platform trades
- **Strike gap analysis** — compares strike prices between platforms
- **Rolling state indicator** — shows transition status when Kalshi is slow to create new contracts (~18s typical)

---

## Trade Execution

Orders require confirmation before execution. After typing a BUY/SELL command, you'll see a summary and must type `Y` to confirm or `N` to cancel.

For Polymarket setup (wallet, keys, allowances), see **[docs/polymarket-setup.md](docs/polymarket-setup.md)**.

---

## CLI (without the desktop app)

The original Python CLI still works independently:

```bash
# List events
python3 main.py list --source polymarket --limit 20
python3 main.py list --source kalshi --limit 20

# Compare markets with semantic matching
python3 main.py compare --brackets --limit 200

# Find arbitrage opportunities
python3 main.py arb --limit 200 --min-profit 0.5 --max-days 90

# Cache management
python3 main.py cache --stats
python3 main.py cache --list-pairs
python3 main.py cache --clear
```

---

## Project structure

```
prediction-terminal/
├── api_server.py          FastAPI server (port 8081)
├── main.py                CLI entry point
├── comparator.py          Semantic matching + arbitrage logic
├── cache.py               SQLite embedding cache
├── models.py              Shared data models
├── config.py              Environment variable loading
├── clients/
│   ├── polymarket.py      Polymarket API client
│   ├── kalshi.py          Kalshi API client
│   ├── embeddings.py      Gemini embedding client
│   ├── btc_watcher.py     BTC 15-min streaming (WS + REST, auto-roll)
│   └── executor.py        Trade execution (Kalshi RSA-PSS + Polymarket CLOB)
├── docs/
│   └── polymarket-setup.md  Polymarket trading API setup guide
├── requirements.txt
└── terminal/              Electron desktop app
    ├── electron/
    │   ├── main.ts        Electron main process (spawns Python server)
    │   └── preload.ts     Context bridge
    ├── src/
    │   ├── App.tsx        Root component, WS lifecycle, command router
    │   ├── store.ts       Zustand state management
    │   ├── types.ts       TypeScript interfaces
    │   ├── styles/
    │   │   └── bloomberg.css   Amber-on-black theme
    │   └── components/
    │       ├── StatusBar.tsx   Top bar (clock, WS status, cache stats)
    │       ├── CommandBar.tsx  Bottom command input with history
    │       ├── PanelGrid.tsx   Dynamic panel layout (closable panels)
    │       ├── EventsPanel.tsx PM / KS event lists (left, closable)
    │       ├── ResultsPanel.tsx ARB / CMP / BTC / HELP / CACHE (center)
    │       ├── DetailPanel.tsx Selected row deep-dive (right, closable)
    │       └── BtcPanel.tsx   BTC 15-min live streaming display
    ├── package.json
    └── electron.vite.config.ts
```
