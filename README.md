# Prediction Market Terminal

A Bloomberg-style desktop trading terminal for finding arbitrage opportunities across **Polymarket** and **Kalshi** prediction markets.

![Terminal](https://img.shields.io/badge/stack-Electron%20%7C%20React%20%7C%20Python-ffb000?style=flat&labelColor=000000)

---

## What it does

- Fetches live events from Polymarket and Kalshi
- Semantically matches equivalent markets using **Gemini embeddings**
- Detects cross-platform arbitrage (e.g. buy Yes on PM + No on KS for < $1.00 combined)
- Ranks opportunities by annualized return
- Caches embedding scores in SQLite so repeat runs are fast

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
- Optionally a **Kalshi API key** for higher rate limits

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/dk-webt/prediction-terminal.git
cd prediction-terminal
```

### 2. Python dependencies

```bash
pip install -r requirements.txt
```

> On some systems use `pip3` or `python3 -m pip` if `pip` is not found.

### 3. Environment variables

Create a `.env` file in the repo root:

```env
GEMINI_API_KEY=your_gemini_api_key_here
KALSHI_API_KEY=your_kalshi_api_key_here   # optional
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
cd prediction-terminal
python3 api_server.py
```

The server starts on `http://localhost:8081`. You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8081
```

### Terminal 2 — Electron desktop app

```bash
cd prediction-terminal/terminal
npm run dev
```

The Electron window opens automatically with the Bloomberg-style UI.

---

## Terminal commands

Type commands into the `CMD ▶` bar at the bottom of the window.

| Command | Description |
|---------|-------------|
| `PM [N]` | Fetch N Polymarket events (default: 200) |
| `KS [N]` | Fetch N Kalshi events |
| `ARB [N]` | Run arbitrage scan across both platforms |
| `CMP [N]` | Run semantic bracket comparison |
| `CACHE` | Show cache statistics |
| `CLEAR` | Clear the semantic match cache |
| `LIMIT N` | Set default event limit |
| `R` | Re-run last command |
| `?` / `HELP` | Show command reference |
| `Q` | Quit |

### Keyboard shortcuts

| Key | Action |
|-----|--------|
| `/` or `:` | Focus command bar |
| `Esc` | Unfocus command bar |
| `Tab` | Cycle panels (left → center → right) |
| `↑` `↓` | Navigate rows |

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
│   └── embeddings.py      Gemini embedding client
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
    │       ├── PanelGrid.tsx   3-panel layout manager
    │       ├── EventsPanel.tsx PM / KS event lists (left)
    │       ├── ResultsPanel.tsx ARB / CMP / HELP / CACHE (center)
    │       └── DetailPanel.tsx Selected row deep-dive (right)
    ├── package.json
    └── electron.vite.config.ts
```
