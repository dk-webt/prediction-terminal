#!/usr/bin/env python3
"""
FastAPI wrapper around prediction market analytics.
Serves REST + WebSocket on port 8081.

Endpoints:
  GET  /health
  GET  /events/polymarket?limit=N
  GET  /events/kalshi?limit=N
  GET  /cache/stats
  DELETE /cache
  WS   /ws/status  ← streaming progress for ARB / compare

WebSocket protocol (ARB, compare):
  Client sends: {"type": "arb"|"compare", "limit": N, ...options}
  Server sends: {"type": "progress", "msg": "..."} (multiple)
               {"type": "done",     "data": [...]}
            or {"type": "error",    "msg": "..."}
"""

import asyncio
import dataclasses
import queue as sync_queue
import sys
import os
from typing import Any, Callable

# Ensure sibling modules (clients/, comparator.py, etc.) are importable
sys.path.insert(0, os.path.dirname(__file__))

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

app = FastAPI(title="Prediction Market Terminal API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Serialization ──────────────────────────────────────────────────────────────


def _serialize(obj: Any) -> Any:
    """Recursively convert dataclasses / lists / dicts to JSON-safe types."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _serialize(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, list):
        return [_serialize(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, tuple):
        return [_serialize(i) for i in obj]
    return obj


def _serialize_compare(pairs: list) -> list:
    """Serialize list[tuple[MatchResult, list[MarketMatchResult]]] → JSON."""
    return [
        {"event_match": _serialize(em), "market_matches": _serialize(mm)}
        for em, mm in pairs
    ]


# ── Sync runners (executed in thread pool) ────────────────────────────────────

Progress = Callable[[str], None]


def _run_arb(limit: int, progress: Progress, **kwargs) -> list:
    progress("Fetching Polymarket events…")
    from clients.polymarket import fetch_events as pm_fetch
    pm_events = pm_fetch(limit=limit)

    progress(f"Got {len(pm_events)} PM events. Fetching Kalshi…")
    from clients.kalshi import fetch_events as ks_fetch
    ks_events = ks_fetch(limit=limit)

    progress(f"Got {len(ks_events)} KS events. Running semantic matching…")
    from comparator import find_market_matches, find_arbitrage
    pairs = find_market_matches(
        pm_events, ks_events,
        event_min_score=kwargs.get("event_min_score", 0.75),
        market_min_score=kwargs.get("market_min_score", 0.82),
        refresh_cache=kwargs.get("refresh_cache", False),
    )
    n_brackets = sum(len(m) for _, m in pairs)
    progress(f"Matched {len(pairs)} event pairs, {n_brackets} brackets. Computing arbitrage…")
    results = find_arbitrage(
        pairs,
        min_profit=kwargs.get("min_profit", 0.0),
        max_days=kwargs.get("max_days"),
    )
    progress(f"Found {len(results)} arbitrage opportunities.")
    return results


def _run_compare(limit: int, progress: Progress, **kwargs) -> list:
    progress("Fetching Polymarket events…")
    from clients.polymarket import fetch_events as pm_fetch
    pm_events = pm_fetch(limit=limit)

    progress(f"Got {len(pm_events)} PM events. Fetching Kalshi…")
    from clients.kalshi import fetch_events as ks_fetch
    ks_events = ks_fetch(limit=limit)

    progress(f"Got {len(ks_events)} KS events. Running semantic matching…")
    from comparator import find_market_matches
    pairs = find_market_matches(
        pm_events, ks_events,
        event_min_score=kwargs.get("event_min_score", 0.75),
        market_min_score=kwargs.get("market_min_score", 0.82),
        refresh_cache=kwargs.get("refresh_cache", False),
    )
    n_brackets = sum(len(m) for _, m in pairs)
    progress(f"Matched {len(pairs)} event pairs, {n_brackets} brackets.")
    return pairs


# ── WebSocket streaming helper ─────────────────────────────────────────────────


async def _stream_ws(websocket: WebSocket, sync_fn, *args, **kwargs):
    """
    Run sync_fn(*args, progress=cb, **kwargs) in a thread pool.
    Stream progress messages to websocket until done, then send result.
    Pass serialize_fn=fn in kwargs to use a custom serializer.
    """
    q: sync_queue.Queue = sync_queue.Queue()
    result_holder: list = []
    error_holder: list = []
    serialize_fn = kwargs.pop("serialize_fn", None)

    def progress_cb(msg: str) -> None:
        q.put(("progress", msg))

    def run_sync() -> None:
        try:
            result = sync_fn(*args, progress=progress_cb, **kwargs)
            result_holder.append(result)
        except Exception as exc:
            error_holder.append(str(exc))
        finally:
            q.put(("__done__", None))

    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(None, run_sync)

    while True:
        try:
            msg_type, msg_data = q.get_nowait()
            if msg_type == "__done__":
                break
            await websocket.send_json({"type": msg_type, "msg": msg_data})
        except sync_queue.Empty:
            await asyncio.sleep(0.05)

    await future  # propagate any unhandled exceptions

    if error_holder:
        await websocket.send_json({"type": "error", "msg": error_holder[0]})
    else:
        raw = result_holder[0] if result_holder else []
        data = serialize_fn(raw) if serialize_fn else _serialize(raw)
        await websocket.send_json({"type": "done", "data": data})


# ── REST endpoints ─────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "prediction-terminal-api"}


@app.get("/events/polymarket")
async def get_polymarket_events(limit: int = 200):
    def fetch():
        from clients.polymarket import fetch_events
        return fetch_events(limit=limit)
    try:
        events = await asyncio.to_thread(fetch)
        return _serialize(events)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/events/kalshi")
async def get_kalshi_events(limit: int = 200):
    def fetch():
        from clients.kalshi import fetch_events
        return fetch_events(limit=limit)
    try:
        events = await asyncio.to_thread(fetch)
        return _serialize(events)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/cache/stats")
async def get_cache_stats():
    from cache import cache_stats
    return cache_stats()


@app.delete("/cache", status_code=204)
async def clear_cache_endpoint():
    from cache import clear_cache
    clear_cache()


# ── WebSocket endpoint ─────────────────────────────────────────────────────────


@app.websocket("/ws/status")
async def websocket_status(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            cmd = data.get("type", "")
            limit = int(data.get("limit", 200))

            if cmd == "arb":
                await _stream_ws(
                    websocket, _run_arb, limit,
                    event_min_score=float(data.get("event_min_score", 0.75)),
                    market_min_score=float(data.get("market_min_score", 0.82)),
                    min_profit=float(data.get("min_profit", 0.0)),
                    max_days=data.get("max_days"),
                    refresh_cache=bool(data.get("refresh_cache", False)),
                )
            elif cmd == "compare":
                await _stream_ws(
                    websocket, _run_compare, limit,
                    serialize_fn=_serialize_compare,
                    event_min_score=float(data.get("event_min_score", 0.75)),
                    market_min_score=float(data.get("market_min_score", 0.82)),
                    refresh_cache=bool(data.get("refresh_cache", False)),
                )
            else:
                await websocket.send_json({"type": "error", "msg": f"Unknown WS command: {cmd}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "msg": str(exc)})
        except Exception:
            pass


# ── Entry point ────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8081, log_level="info")
