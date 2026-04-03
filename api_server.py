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
import logging
import sys
import os
from typing import Any, Callable

log = logging.getLogger(__name__)

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


# ── Date filter helper ─────────────────────────────────────────────────────────


from datetime import date as _date


def _filter_by_days(events: list, max_days: int | None) -> list:
    """Filter NormalizedEvent list to those expiring within max_days from today."""
    if max_days is None:
        return events
    today = _date.today()
    result = []
    for e in events:
        if not e.end_date:
            continue
        try:
            end = _date.fromisoformat(e.end_date[:10])
            if (end - today).days <= max_days:
                result.append(e)
        except ValueError:
            pass
    return result


# ── Sync runners (executed in thread pool) ────────────────────────────────────

Progress = Callable[[str], None]


def _run_arb(limit: int, progress: Progress, **kwargs) -> dict:
    category = kwargs.pop("category", None)
    progress("Fetching Polymarket events…")
    from clients.polymarket import fetch_events as pm_fetch
    pm_events = pm_fetch(limit=limit, category=category)

    progress(f"Got {len(pm_events)} PM events. Fetching Kalshi…")
    from clients.kalshi import fetch_events as ks_fetch
    ks_events = ks_fetch(limit=limit, category=category)

    progress(f"Got {len(ks_events)} KS events. Running semantic matching…")
    from comparator import find_market_matches, find_arbitrage
    pairs = find_market_matches(
        pm_events, ks_events,
        event_min_score=kwargs.get("event_min_score", 0.75),
        market_min_score=kwargs.get("market_min_score", 0.82),
        refresh_cache=kwargs.get("refresh_cache", False),
        max_days=kwargs.get("max_days"),
    )
    n_brackets = sum(len(m) for _, m in pairs)
    progress(f"Matched {len(pairs)} event pairs, {n_brackets} brackets. Computing arbitrage…")
    results = find_arbitrage(
        pairs,
        min_profit=kwargs.get("min_profit", 0.0),
        max_days=kwargs.get("max_days"),
    )
    progress(f"Found {len(results)} arbitrage opportunities.")
    return {"results": results, "pm_events": pm_events, "ks_events": ks_events}


def _run_compare(limit: int, progress: Progress, **kwargs) -> dict:
    category = kwargs.pop("category", None)
    max_days = kwargs.get("max_days")
    progress("Fetching Polymarket events…")
    from clients.polymarket import fetch_events as pm_fetch
    pm_events = pm_fetch(limit=limit, category=category)

    progress(f"Got {len(pm_events)} PM events. Fetching Kalshi…")
    from clients.kalshi import fetch_events as ks_fetch
    ks_events = ks_fetch(limit=limit, category=category)

    pm_events = _filter_by_days(pm_events, max_days)
    ks_events = _filter_by_days(ks_events, max_days)

    progress(f"Got {len(ks_events)} KS events. Running semantic matching…")
    from comparator import find_market_matches
    pairs = find_market_matches(
        pm_events, ks_events,
        event_min_score=kwargs.get("event_min_score", 0.75),
        market_min_score=kwargs.get("market_min_score", 0.82),
        refresh_cache=kwargs.get("refresh_cache", False),
        max_days=max_days,
    )
    n_brackets = sum(len(m) for _, m in pairs)
    progress(f"Matched {len(pairs)} event pairs, {n_brackets} brackets.")
    return {"pairs": pairs, "pm_events": pm_events, "ks_events": ks_events}


# ── WebSocket streaming helper ─────────────────────────────────────────────────


def _transform_arb_done(raw: dict) -> dict:
    """Build the 'done' payload for an ARB run, including raw event lists."""
    return {
        "data": _serialize(raw["results"]),
        "pm_events": _serialize(raw["pm_events"]),
        "ks_events": _serialize(raw["ks_events"]),
    }


def _transform_cmp_done(raw: dict) -> dict:
    """Build the 'done' payload for a CMP run, including raw event lists."""
    return {
        "data": _serialize_compare(raw["pairs"]),
        "pm_events": _serialize(raw["pm_events"]),
        "ks_events": _serialize(raw["ks_events"]),
    }


async def _stream_ws(websocket: WebSocket, sync_fn, *args, **kwargs):
    """
    Run sync_fn(*args, progress=cb, **kwargs) in a thread pool.
    Stream progress messages to websocket until done, then send result.

    Optional kwargs (popped before forwarding to sync_fn):
      serialize_fn=fn   — custom serializer for the raw result
      transform_done=fn — takes raw result, returns dict merged into 'done' message
                          (overrides serialize_fn when provided)
    """
    q: sync_queue.Queue = sync_queue.Queue()
    result_holder: list = []
    error_holder: list = []
    serialize_fn = kwargs.pop("serialize_fn", None)
    transform_done = kwargs.pop("transform_done", None)

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
        raw = result_holder[0] if result_holder else {}
        if transform_done:
            payload = {"type": "done", **transform_done(raw)}
        else:
            data = serialize_fn(raw) if serialize_fn else _serialize(raw)
            payload = {"type": "done", "data": data}
        await websocket.send_json(payload)


# ── REST endpoints ─────────────────────────────────────────────────────────────


@app.get("/health")
async def health():
    return {"status": "ok", "service": "prediction-terminal-api"}


@app.get("/events/polymarket")
async def get_polymarket_events(limit: int = 200, category: str | None = None, max_days: int | None = None):
    def fetch():
        from clients.polymarket import fetch_events
        events = fetch_events(limit=limit, category=category)
        return _filter_by_days(events, max_days)
    try:
        events = await asyncio.to_thread(fetch)
        return _serialize(events)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/events/kalshi")
async def get_kalshi_events(limit: int = 200, category: str | None = None, max_days: int | None = None):
    def fetch():
        from clients.kalshi import fetch_events
        events = fetch_events(limit=limit, category=category)
        return _filter_by_days(events, max_days)
    try:
        events = await asyncio.to_thread(fetch)
        return _serialize(events)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/categories")
async def get_categories():
    """Return unique normalized categories available on each platform."""
    def fetch():
        from clients.polymarket import fetch_events as pm_fetch
        from clients.kalshi import fetch_events as ks_fetch
        from comparator import normalize_category
        pm_events = pm_fetch(limit=200)
        ks_events = ks_fetch(limit=200)
        pm_cats = sorted({normalize_category(e.category) for e in pm_events if e.category})
        ks_cats = sorted({normalize_category(e.category) for e in ks_events if e.category})
        return {"polymarket": pm_cats, "kalshi": ks_cats}
    try:
        return await asyncio.to_thread(fetch)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/btc/snapshot")
async def get_btc_snapshot():
    """Fetch current BTC 15-min binary option contracts from both platforms."""
    def fetch():
        from clients.btc_watcher import fetch_btc_snapshot
        return fetch_btc_snapshot()
    try:
        return await asyncio.to_thread(fetch)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/btc/order")
async def place_btc_order(body: dict):
    """Place a buy/sell order on Kalshi or Polymarket."""
    platform = body.get("platform", "")
    action = body.get("action", "")
    side = body.get("side", "")
    count = body.get("count", 0)
    price = body.get("price")
    order_type = body.get("order_type", "limit")

    try:
        if platform == "kalshi":
            from clients.executor import place_kalshi_order
            ticker = body.get("ticker", "")
            if not ticker:
                raise HTTPException(status_code=400, detail="Missing ticker for Kalshi order")
            result = await asyncio.to_thread(
                place_kalshi_order, ticker, action, side, count, price, order_type
            )
        elif platform == "polymarket":
            from clients.executor import place_polymarket_order
            token_id = body.get("token_id", "")
            if not token_id:
                raise HTTPException(status_code=400, detail="Missing token_id for Polymarket order")
            pm_side = "BUY" if action == "buy" else "SELL"
            result = await asyncio.to_thread(
                place_polymarket_order, token_id, pm_side, count, price, order_type
            )
        else:
            raise HTTPException(status_code=400, detail=f"Unknown platform: {platform}")

        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/btc/positions")
async def get_btc_positions():
    """Fetch positions from both platforms in parallel."""
    from clients.executor import get_kalshi_positions, get_polymarket_positions
    try:
        ks, pm = await asyncio.gather(
            asyncio.to_thread(get_kalshi_positions),
            asyncio.to_thread(get_polymarket_positions),
        )
        return {"kalshi": ks, "polymarket": pm}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/pm/setup")
async def pm_setup():
    """One-time Polymarket setup: set USDC.e + conditional token allowances."""
    from clients.executor import set_pm_allowances
    try:
        result = await asyncio.to_thread(set_pm_allowances)
        return result
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


# ── WebSocket endpoints ────────────────────────────────────────────────────────

# Module-level BTC stream — shared across /ws/btc connections
_btc_stream = None            # BtcStreamManager instance
_btc_subscribers: set = set() # set of WebSocket objects listening for updates
_trade_subscribers: set = set()  # set of /ws/trade WebSocket objects

# Active order tracking — correlate REST placement with WS fill/order events
# Keyed by both server order_id and client_order_id for lookup flexibility
_active_orders: dict[str, dict] = {}  # order_id -> order info

# ── Auto Trade Executor (ATE) ────────────────────────────────────────────────
_ate_enabled = False
_ate_executing = False  # guard against concurrent execution
ATE_MIN_PROFIT = 0.06  # minimum profit per contract to trigger
ATE_MAX_COUNT = 10      # maximum contracts per leg (upper bound)
ATE_MIN_COUNT = 1       # minimum contracts to bother executing
ATE_ORDER_COUNT = ATE_MAX_COUNT  # backward compat alias


def _check_depth(levels: list[tuple[float, float]], count: int, price_cap: float) -> tuple[bool, int]:
    """Walk orderbook levels to check if `count` contracts available within price_cap.
    levels: [(price, size), ...] sorted ascending by price.
    Returns (sufficient, available_count).
    """
    available = 0
    for price, size in levels:
        if price > price_cap:
            break
        available += size
        if available >= count:
            return True, int(min(available, count))
    return False, int(available)


async def _btc_broadcast(snapshot: dict):
    """Push BTC update to all connected /ws/btc subscribers."""
    msg = {"type": "btc_update", **snapshot}
    dead = []
    for ws in _btc_subscribers:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _btc_subscribers.discard(ws)

    # Check ATE trigger on every broadcast
    if _ate_enabled:
        await _ate_check(snapshot)


async def _trade_broadcast(msg_type: str, data: dict):
    """Push fill/order events to all connected /ws/trade subscribers."""
    msg = {"type": msg_type, "data": data}
    dead = []
    for ws in _trade_subscribers:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _trade_subscribers.discard(ws)


async def _ate_unwind(trade_ws, platform: str, side: str, ticker: str, token_id: str,
                      buy_result: dict, requested_count: int):
    """Unwind a successful leg after the other leg failed. Sells the position at market."""
    import uuid as _uuid

    try:
        if platform == "kalshi":
            # Parse fill count from KS result — may be partial
            order_data = buy_result.get("data", {})
            if isinstance(order_data, dict):
                order_info = order_data.get("order", order_data)
                fill_count = int(float(order_info.get("fill_count_fp", 0)))
                ks_order_id = order_info.get("order_id", "")
                status = order_info.get("status", "")

                # Cancel resting portion if order is still resting
                if status == "resting" and ks_order_id:
                    log.warning("ATE UNWIND: canceling resting KS order %s", ks_order_id)
                    from clients.executor import cancel_kalshi_order
                    await asyncio.to_thread(cancel_kalshi_order, ks_order_id)
            else:
                fill_count = requested_count

            if fill_count <= 0:
                log.info("ATE UNWIND: KS fill_count=0, nothing to unwind")
                return

            # Sell the filled contracts
            unwind_id = f"unwind-{str(_uuid.uuid4())[:6]}"
            unwind_pending = {unwind_id: {
                "platform": "kalshi", "action": "sell", "side": side,
                "count": fill_count, "price": None, "order_type": "market",
                "ticker": ticker, "token_id": "",
            }}
            log.warning("ATE UNWIND: selling %d KS %s MKT", fill_count, side.upper())
            result = await _execute_order(trade_ws, unwind_id, unwind_pending)

        else:  # polymarket
            # PM FOK = all or nothing, so fill_count = requested_count
            unwind_id = f"unwind-{str(_uuid.uuid4())[:6]}"
            unwind_pending = {unwind_id: {
                "platform": "polymarket", "action": "sell", "side": side,
                "count": requested_count, "price": None, "order_type": "market",
                "ticker": "", "token_id": token_id,
            }}
            log.warning("ATE UNWIND: selling %d PM %s MKT", requested_count, side.upper())
            result = await _execute_order(trade_ws, unwind_id, unwind_pending)

        unwind_ok = result and result.get("success")
        if unwind_ok:
            log.warning("ATE UNWIND: success — position closed")
        else:
            log.error("ATE UNWIND: FAILED — %s — MANUAL INTERVENTION NEEDED",
                      result.get("error") if result else "no result")

        # Notify frontend
        for ws in list(_trade_subscribers):
            try:
                await ws.send_json({
                    "type": "ate_unwind",
                    "platform": platform,
                    "success": unwind_ok,
                    "count": fill_count if platform == "kalshi" else requested_count,
                    "error": result.get("error") if result and not unwind_ok else None,
                })
            except Exception:
                pass

    except Exception:
        log.exception("ATE UNWIND: exception during unwind — MANUAL INTERVENTION NEEDED")


async def _ate_check(snapshot: dict):
    """Check if an arb opportunity meets ATE threshold and execute if so."""
    global _ate_enabled, _ate_executing

    if _ate_executing:
        return

    ks = snapshot.get("kalshi")
    pm = snapshot.get("polymarket")
    if not ks or not pm:
        return

    # Staleness guard — don't trade on old data (uses btc_watcher's staleness flags)
    if snapshot.get("ks_stale") or snapshot.get("pm_stale"):
        return

    # Skip during window rolls (contracts are changing)
    if snapshot.get("rolling"):
        return

    # Skip if less than 59 seconds remaining — too close to settlement
    close_time = ks.get("close_time", "")
    if close_time:
        try:
            from datetime import datetime, timezone
            remaining = (datetime.fromisoformat(close_time) - datetime.now(timezone.utc)).total_seconds()
            if remaining < 59:
                return
        except (ValueError, TypeError):
            pass

    # Get ask prices (cost to enter each leg)
    ks_yes_ask = ks.get("yes_ask", 0) or 0
    ks_no_ask = ks.get("no_ask", 0) or 0
    pm_down_ask = pm.get("down_ask", 0) or 0
    pm_up_ask = pm.get("up_ask", 0) or 0

    # Combo A: buy KS YES + buy PM DOWN → settles to $1.00
    cost_a = ks_yes_ask + pm_down_ask
    profit_a = 1.0 - cost_a if cost_a > 0 else -999

    # Combo B: buy KS NO + buy PM UP → settles to $1.00
    cost_b = ks_no_ask + pm_up_ask
    profit_b = 1.0 - cost_b if cost_b > 0 else -999

    # Pick the better combo if either meets threshold
    chosen = None
    if profit_a >= ATE_MIN_PROFIT and profit_a >= profit_b:
        chosen = "A"
    elif profit_b >= ATE_MIN_PROFIT:
        chosen = "B"

    if not chosen:
        return

    profit = profit_a if chosen == "A" else profit_b
    cost = cost_a if chosen == "A" else cost_b

    if chosen == "A":
        ks_side, pm_side = "yes", "down"
        label = "KS YES + PM DOWN"
    else:
        ks_side, pm_side = "no", "up"
        label = "KS NO + PM UP"

    # Pre-execution liquidity check — verify depth on both sides
    ks_ticker = ks.get("ticker", "")
    pm_token_ids = pm.get("token_ids", [])
    if pm_side == "up":
        pm_token_id = pm_token_ids[0] if len(pm_token_ids) > 0 else ""
    else:
        pm_token_id = pm_token_ids[1] if len(pm_token_ids) > 1 else ""

    try:
        from clients.executor import fetch_kalshi_orderbook, fetch_polymarket_orderbook
        ks_ob, pm_ob = await asyncio.gather(
            asyncio.to_thread(fetch_kalshi_orderbook, ks_ticker),
            asyncio.to_thread(fetch_polymarket_orderbook, pm_token_id),
        )

        # KS: yes_ask levels = inverted no_dollars bids; no_ask levels = inverted yes_dollars bids
        if ks_side == "yes":
            ks_raw = ks_ob.get("no_dollars", [])
            ks_ask_levels = [(round(1.0 - float(p), 4), float(s)) for p, s in ks_raw if float(s) > 0]
            ks_ask_levels.sort()  # ascending by price
        else:
            ks_raw = ks_ob.get("yes_dollars", [])
            ks_ask_levels = [(round(1.0 - float(p), 4), float(s)) for p, s in ks_raw if float(s) > 0]
            ks_ask_levels.sort()

        pm_ask_levels = [(float(a["price"]), float(a["size"])) for a in pm_ob.get("asks", [])]
        pm_ask_levels.sort()

        ks_cap = (ks_yes_ask if ks_side == "yes" else ks_no_ask) + 0.02
        pm_cap = (pm_down_ask if pm_side == "down" else pm_up_ask) + 0.02

        ks_depth_ok, ks_avail = _check_depth(ks_ask_levels, ATE_ORDER_COUNT, ks_cap)
        pm_depth_ok, pm_avail = _check_depth(pm_ask_levels, ATE_ORDER_COUNT, pm_cap)

        log.info("ATE liquidity: KS %s %d/%d @ cap %.2f | PM %s %d/%d @ cap %.2f",
                 "OK" if ks_depth_ok else "THIN", ks_avail, ATE_ORDER_COUNT, ks_cap,
                 "OK" if pm_depth_ok else "THIN", pm_avail, ATE_ORDER_COUNT, pm_cap)

        if not ks_depth_ok or not pm_depth_ok:
            # Phase 4: adaptive sizing — use min available
            actual_count = min(ks_avail, pm_avail, ATE_ORDER_COUNT)
            if actual_count < ATE_MIN_COUNT:
                log.warning("ATE: insufficient liquidity — KS: %d, PM: %d, need >= %d — skipping",
                            ks_avail, pm_avail, ATE_MIN_COUNT)
                return
            log.info("ATE: adapting size from %d to %d based on available depth",
                     ATE_ORDER_COUNT, actual_count)
        else:
            actual_count = ATE_ORDER_COUNT
    except Exception as e:
        log.warning("ATE: liquidity check failed: %s — proceeding with default size", e)
        actual_count = ATE_ORDER_COUNT

    # Trigger! Disable ATE and execute
    _ate_executing = True
    _ate_enabled = False

    log.warning(
        "ATE TRIGGERED: %s | cost=%.3f profit=%.3f (%.1f%%) | %d contracts",
        label, cost, profit, profit * 100, actual_count,
    )

    # Notify all trade subscribers
    ate_msg = {
        "type": "ate_triggered",
        "combo": label,
        "cost": round(cost, 4),
        "profit": round(profit, 4),
        "count": actual_count,
    }
    for ws in list(_trade_subscribers):
        try:
            await ws.send_json(ate_msg)
        except Exception:
            pass

    # Execute both legs — use the first available trade subscriber for order flow
    trade_ws = next(iter(_trade_subscribers), None)
    if not trade_ws:
        log.error("ATE: no trade WebSocket available for execution")
        _ate_executing = False
        return

    ate_status = "error"
    try:
        import uuid as _uuid

        # Build both order dicts with adaptive count
        ks_order_id = f"ate-{str(_uuid.uuid4())[:6]}"
        ks_pending = {ks_order_id: {
            "platform": "kalshi", "action": "buy", "side": ks_side,
            "count": actual_count, "price": None, "order_type": "market",
            "ticker": ks_ticker, "token_id": "",
        }}
        pm_order_id = f"ate-{str(_uuid.uuid4())[:6]}"
        pm_pending = {pm_order_id: {
            "platform": "polymarket", "action": "buy", "side": pm_side,
            "count": actual_count, "price": None, "order_type": "market",
            "ticker": "", "token_id": pm_token_id,
        }}

        # Execute both legs in parallel
        log.info("ATE: executing PARALLEL — BUY %d KS %s + BUY %d PM %s MKT",
                 actual_count, ks_side.upper(), actual_count, pm_side.upper())
        results = await asyncio.gather(
            _execute_order(trade_ws, ks_order_id, ks_pending),
            _execute_order(trade_ws, pm_order_id, pm_pending),
            return_exceptions=True,
        )
        ks_result = results[0] if not isinstance(results[0], Exception) else {"success": False, "error": str(results[0])}
        pm_result = results[1] if not isinstance(results[1], Exception) else {"success": False, "error": str(results[1])}

        # Check per-leg results
        ks_ok = isinstance(ks_result, dict) and ks_result.get("success")
        pm_ok = isinstance(pm_result, dict) and pm_result.get("success")

        if ks_ok and pm_ok:
            log.warning("ATE: both legs executed successfully — auto-disabled")
            ate_status = "success"
        elif ks_ok and not pm_ok:
            log.error("ATE: KS succeeded but PM FAILED: %s — attempting unwind",
                      pm_result.get("error") if isinstance(pm_result, dict) else pm_result)
            ate_status = "partial_ks"
            await _ate_unwind(trade_ws, "kalshi", ks_side, ks_ticker, "", ks_result, actual_count)
        elif pm_ok and not ks_ok:
            log.error("ATE: PM succeeded but KS FAILED: %s — attempting unwind",
                      ks_result.get("error") if isinstance(ks_result, dict) else ks_result)
            ate_status = "partial_pm"
            await _ate_unwind(trade_ws, "polymarket", pm_side, "", pm_token_id, pm_result, actual_count)
        else:
            log.error("ATE: BOTH legs failed — KS: %s | PM: %s",
                      ks_result.get("error") if isinstance(ks_result, dict) else ks_result,
                      pm_result.get("error") if isinstance(pm_result, dict) else pm_result)
            ate_status = "failed"

    except Exception:
        log.exception("ATE: execution error")
        ate_status = "error"
    finally:
        _ate_executing = False

    # Notify completion with status
    for ws in list(_trade_subscribers):
        try:
            await ws.send_json({"type": "ate_done", "combo": label, "status": ate_status})
        except Exception:
            pass


def _track_order(platform: str, order_info: dict, rest_response: dict):
    """Store an order for correlation with WS fill/order events."""
    # Kalshi REST response has order.order_id; PM has different structure
    resp_data = rest_response.get("data", {})
    if platform == "kalshi":
        order_data = resp_data.get("order", resp_data)
        server_order_id = order_data.get("order_id", "")
        client_order_id = rest_response.get("client_order_id", "")
        entry = {
            "platform": platform,
            "server_order_id": server_order_id,
            "client_order_id": client_order_id,
            "ticker": order_info.get("ticker", ""),
            "action": order_info.get("action", ""),
            "side": order_info.get("side", ""),
            "count": order_info.get("count", 0),
            "price": order_info.get("price"),
            "status": "submitted",
        }
        if server_order_id:
            _active_orders[server_order_id] = entry
            log.info("TRACK ORDER: %s (server=%s client=%s)",
                     platform, server_order_id, client_order_id)
        if client_order_id and client_order_id != server_order_id:
            _active_orders[client_order_id] = entry
    elif platform == "polymarket":
        # PM REST response: {"orderID": "...", ...} or {"id": "...", ...}
        order_id = resp_data.get("orderID", resp_data.get("id", ""))
        entry = {
            "platform": platform,
            "server_order_id": order_id,
            "token_id": order_info.get("token_id", ""),
            "action": order_info.get("action", ""),
            "side": order_info.get("side", ""),
            "count": order_info.get("count", 0),
            "price": order_info.get("price"),
            "status": "submitted",
        }
        if order_id:
            _active_orders[order_id] = entry
            log.info("TRACK ORDER: %s (order_id=%s)", platform, order_id)


# ── PM heartbeat for GTC orders ──────────────────────────────────────────────

_pm_heartbeat_task: "asyncio.Task | None" = None
_pm_heartbeat_id: str | None = None
_pm_resting_orders: set = set()


async def _pm_heartbeat_loop():
    """Send PM heartbeat every 5s while GTC orders are resting."""
    global _pm_heartbeat_id
    from clients.executor import _get_pm_client
    client = _get_pm_client()
    if not client:
        log.warning("PM heartbeat: no client available")
        return
    log.info("PM heartbeat loop started")
    while _pm_resting_orders:
        try:
            resp = await asyncio.to_thread(client.post_heartbeat, _pm_heartbeat_id)
            if isinstance(resp, dict):
                _pm_heartbeat_id = resp.get("heartbeat_id", _pm_heartbeat_id)
        except Exception as e:
            log.warning("PM heartbeat failed: %s", e)
        await asyncio.sleep(5)
    log.info("PM heartbeat loop stopped (no resting orders)")


def _start_pm_heartbeat(order_id: str):
    """Start PM heartbeat loop when a GTC order is placed."""
    global _pm_heartbeat_task
    _pm_resting_orders.add(order_id)
    if _pm_heartbeat_task is None or _pm_heartbeat_task.done():
        _pm_heartbeat_task = asyncio.create_task(_pm_heartbeat_loop())


def _stop_pm_heartbeat(order_id: str):
    """Stop tracking a PM order. Loop exits when no resting orders remain."""
    _pm_resting_orders.discard(order_id)


async def _on_ks_fill(data: dict):
    """Callback for Kalshi fill events — enrich and broadcast to /ws/trade."""
    order_id = data.get("order_id", "")
    tracked = _active_orders.get(order_id)
    if tracked:
        tracked["status"] = "filled"
        data["_tracked"] = True
        data["_order"] = tracked
        log.info("KS FILL matched: order=%s %s %s x%s @ %s",
                 order_id, tracked["action"], tracked["side"],
                 data.get("count_fp", "?"), data.get("yes_price_dollars", "?"))
    await _trade_broadcast("ks_fill", data)


async def _on_pm_fill(data: dict):
    """Callback for PM trade events (MATCHED/MINED/CONFIRMED/FAILED)."""
    status = data.get("status", "")
    if status == "CONFIRMED":
        # Look up by asset_id or market (condition_id)
        # PM doesn't give a simple order_id in trade events like Kalshi
        data["_pm_confirmed"] = True
    await _trade_broadcast("pm_fill", data)


async def _on_pm_order(data: dict):
    """Callback for PM order events (PLACEMENT/UPDATE/CANCELLATION)."""
    order_id = data.get("id", "")
    event_type = data.get("type", "")
    tracked = _active_orders.get(order_id)
    if tracked:
        data["_tracked"] = True
        data["_order"] = tracked
        if event_type == "CANCELLATION":
            tracked["status"] = "canceled"
            _active_orders.pop(order_id, None)
            _stop_pm_heartbeat(order_id)
        elif event_type == "UPDATE":
            size_matched = float(data.get("size_matched", 0))
            original_size = float(data.get("original_size", 0))
            if original_size > 0 and size_matched >= original_size:
                tracked["status"] = "filled"
                _active_orders.pop(order_id, None)
                _stop_pm_heartbeat(order_id)
            else:
                tracked["status"] = "partial"
    await _trade_broadcast("pm_order_update", data)


async def _on_ks_order(data: dict):
    """Callback for Kalshi order updates — enrich and broadcast to /ws/trade."""
    order_id = data.get("order_id", "")
    status = data.get("status", "")
    tracked = _active_orders.get(order_id)
    if tracked:
        tracked["status"] = status
        data["_tracked"] = True
        data["_order"] = tracked
        log.info("KS ORDER UPDATE matched: order=%s status=%s", order_id, status)
        # Clean up terminal states
        if status in ("canceled", "executed"):
            _active_orders.pop(order_id, None)
            # Also remove by client_order_id if present
            client_id = tracked.get("client_order_id", "")
            if client_id:
                _active_orders.pop(client_id, None)
    await _trade_broadcast("ks_order_update", data)


async def _btc_ensure_started():
    """Start the BTC stream if not already running."""
    global _btc_stream
    if _btc_stream is not None:
        return
    from clients.btc_watcher import BtcStreamManager
    _btc_stream = BtcStreamManager(
        on_update=_btc_broadcast,
        on_ks_fill=_on_ks_fill,
        on_ks_order=_on_ks_order,
        on_pm_fill=_on_pm_fill,
        on_pm_order=_on_pm_order,
    )
    await _btc_stream.start()
    log.info("BTC stream started (module-level)")


async def _btc_stop():
    """Stop the BTC stream."""
    global _btc_stream
    if _btc_stream is not None:
        await _btc_stream.stop()
        _btc_stream = None
        log.info("BTC stream stopped")


# ── /ws/cmd — Commands: ARB, CMP, CACHE ──────────────────────────────────────

@app.websocket("/ws/cmd")
async def websocket_cmd(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            cmd = data.get("type", "")
            limit = int(data.get("limit", 200))
            category = data.get("category") or None

            if cmd == "arb":
                await _stream_ws(
                    websocket, _run_arb, limit,
                    transform_done=_transform_arb_done,
                    category=category,
                    event_min_score=float(data.get("event_min_score", 0.75)),
                    market_min_score=float(data.get("market_min_score", 0.82)),
                    min_profit=float(data.get("min_profit", 0.0)),
                    max_days=data.get("max_days"),
                    refresh_cache=bool(data.get("refresh_cache", False)),
                )
            elif cmd == "compare":
                await _stream_ws(
                    websocket, _run_compare, limit,
                    transform_done=_transform_cmp_done,
                    category=category,
                    event_min_score=float(data.get("event_min_score", 0.75)),
                    market_min_score=float(data.get("market_min_score", 0.82)),
                    max_days=data.get("max_days"),
                    refresh_cache=bool(data.get("refresh_cache", False)),
                )
            else:
                await websocket.send_json({"type": "error", "msg": f"Unknown cmd: {cmd}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "msg": str(exc)})
        except Exception:
            pass


# ── /ws/btc — BTC price streaming + debug ─────────────────────────────────────

@app.websocket("/ws/btc")
async def websocket_btc(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_json()
            cmd = data.get("type", "")

            if cmd == "btc":
                action = data.get("action", "subscribe")
                if action == "subscribe":
                    _btc_subscribers.add(websocket)
                    try:
                        await _btc_ensure_started()
                    except Exception as exc:
                        log.error("BTC stream start failed: %s", exc, exc_info=True)
                        await websocket.send_json({"type": "error", "msg": f"BTC start failed: {exc}"})
                        _btc_subscribers.discard(websocket)
                        continue
                    log.info("BTC subscriber added (total: %d)", len(_btc_subscribers))
                elif action == "unsubscribe":
                    _btc_subscribers.discard(websocket)
                    await websocket.send_json({"type": "btc_stopped"})
                    # Stop stream if no more subscribers
                    if not _btc_subscribers and _btc_stream is not None:
                        await _btc_stop()

            elif cmd == "btc_ate":
                global _ate_enabled
                action = data.get("action", "")
                if action == "on":
                    _ate_enabled = True
                    log.warning("ATE: enabled — monitoring for >= $%.2f profit", ATE_MIN_PROFIT)
                    await websocket.send_json({
                        "type": "ate_status", "enabled": True,
                        "min_profit": ATE_MIN_PROFIT, "count": ATE_ORDER_COUNT,
                    })
                elif action == "off":
                    _ate_enabled = False
                    log.info("ATE: disabled")
                    await websocket.send_json({"type": "ate_status", "enabled": False})
                elif action == "status":
                    await websocket.send_json({
                        "type": "ate_status", "enabled": _ate_enabled,
                        "min_profit": ATE_MIN_PROFIT, "count": ATE_ORDER_COUNT,
                    })

            elif cmd == "btc_refresh":
                if _btc_stream:
                    hard = data.get("hard", False)
                    await websocket.send_json({"type": "btc_refresh_status", "status": "refreshing"})
                    await _btc_stream.force_refresh(hard=hard)
                    await websocket.send_json({"type": "btc_refresh_status", "status": "done"})
                else:
                    await websocket.send_json({"type": "error", "msg": "BTC stream not running"})

            elif cmd == "btc_debug":
                action = data.get("action", "get")
                if action == "on":
                    _set_btc_debug(True)
                    await websocket.send_json({"type": "btc_debug_status", "enabled": True})
                elif action == "off":
                    _set_btc_debug(False)
                    await websocket.send_json({"type": "btc_debug_status", "enabled": False})
                elif action == "get":
                    try:
                        with open(BTC_DEBUG_LOG, "r") as f:
                            log_text = f.read()
                    except FileNotFoundError:
                        log_text = "(no log file yet — enable with DBG ON)"
                    await websocket.send_json({"type": "btc_debug_log", "log": log_text})
                elif action == "clear":
                    open(BTC_DEBUG_LOG, "w").close()
                    await websocket.send_json({"type": "btc_debug_log", "log": "(cleared)"})
            else:
                await websocket.send_json({"type": "error", "msg": f"Unknown btc cmd: {cmd}"})

    except WebSocketDisconnect:
        log.info("/ws/btc: client disconnected")
    except Exception as exc:
        log.error("/ws/btc: handler error: %s", exc, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "msg": str(exc)})
        except Exception:
            pass
    finally:
        _btc_subscribers.discard(websocket)
        if not _btc_subscribers and _btc_stream is not None:
            # Delay before stopping — allows StrictMode remount or brief disconnects
            await asyncio.sleep(2)
            if not _btc_subscribers and _btc_stream is not None:
                await _btc_stop()


# ── /ws/trade — Order confirmation + execution ───────────────────────────────


async def _execute_order(websocket, order_id: str, pending_orders: dict):
    """Execute a pending order (shared by confirm flow and auto-execute)."""
    order = pending_orders.pop(order_id, None)
    if not order:
        log.warning("ORDER %s: not found or expired", order_id)
        await websocket.send_json({"type": "btc_order_result", "success": False, "error": "Order expired or not found"})
        return
    log.info("ORDER %s: executing %s %s %s x%s @ %s",
             order_id, order["platform"], order["action"],
             order["side"], order["count"], order.get("price", "MKT"))
    try:
        if order["platform"] == "kalshi":
            from clients.executor import place_kalshi_order
            ks_price = order["price"]
            if order["order_type"] == "market" and ks_price is None and _btc_stream:
                ks_data = _btc_stream._kalshi_data
                if ks_data and not ks_data.get("error"):
                    side = order["side"]
                    if side == "yes":
                        best_ask = ks_data.get("yes_ask", 0)
                    else:
                        best_ask = ks_data.get("no_ask", 0)
                    log.info("ORDER %s: KS live data: side=%s yes_ask=%s no_ask=%s yes_bid=%s no_bid=%s",
                             order_id, side,
                             ks_data.get("yes_ask"), ks_data.get("no_ask"),
                             ks_data.get("yes_bid"), ks_data.get("no_bid"))
                    if best_ask > 0:
                        ks_price = min(round(best_ask + 0.02, 2), 0.99)
                        log.info("ORDER %s: market cap: best_ask=%.2f cap=%.2f",
                                 order_id, best_ask, ks_price)
                    else:
                        last = ks_data.get("last_price", 0)
                        if last > 0:
                            ks_price = round(last + 0.05, 2)
                            log.info("ORDER %s: market cap from last_price: %.2f cap=%.2f",
                                     order_id, last, ks_price)
                else:
                    log.warning("ORDER %s: _btc_stream exists but KS data missing/error", order_id)
            result = await asyncio.to_thread(
                place_kalshi_order,
                order["ticker"], order["action"], order["side"],
                int(order["count"]), ks_price, order["order_type"],
            )
        else:
            from clients.executor import place_polymarket_order
            pm_side = "BUY" if order["action"] == "buy" else "SELL"
            pm_price = order["price"]
            if order["order_type"] == "market" and pm_price is None and _btc_stream:
                pm_data = _btc_stream._pm_data
                if pm_data and not pm_data.get("error"):
                    pm_order_side = order["side"]
                    if pm_order_side in ("up", "yes"):
                        best_ask = pm_data.get("up_ask", 0)
                    else:
                        best_ask = pm_data.get("down_ask", 0)
                    if best_ask > 0:
                        pm_price = min(round(best_ask + 0.02, 2), 0.99)
                        log.info("ORDER %s: PM market cap: best_ask=%.2f cap=%.2f",
                                 order_id, best_ask, pm_price)
            result = await asyncio.to_thread(
                place_polymarket_order,
                order["token_id"], pm_side,
                order["count"], pm_price, order["order_type"],
            )
        if result.get("success"):
            log.info("ORDER %s: success — %s", order_id, result.get("data", ""))
            _track_order(order["platform"], order, result)
            if order["platform"] == "polymarket" and order["order_type"] == "limit":
                resp_data = result.get("data", {})
                pm_oid = resp_data.get("orderID", resp_data.get("id", ""))
                if pm_oid:
                    _start_pm_heartbeat(pm_oid)
        else:
            log.warning("ORDER %s: failed — %s", order_id, result.get("error", "unknown"))
        await websocket.send_json({"type": "btc_order_result", **result})
        return result
    except Exception as exc:
        log.error("ORDER %s: exception — %s", order_id, exc, exc_info=True)
        result = {"success": False, "error": str(exc)}
        await websocket.send_json({"type": "btc_order_result", **result})
        return result

@app.websocket("/ws/trade")
async def websocket_trade(websocket: WebSocket):
    await websocket.accept()
    _trade_subscribers.add(websocket)
    pending_orders: dict = {}
    try:
        while True:
            data = await websocket.receive_json()
            cmd = data.get("type", "")

            if cmd == "btc_order":
                import uuid as _uuid
                order_id = str(_uuid.uuid4())[:8]
                platform = data.get("platform", "")
                action = data.get("action", "")
                side = data.get("side", "")
                count = float(data.get("count", 0))
                price = data.get("price")
                order_type = data.get("order_type", "limit")
                ticker = data.get("ticker", "")
                token_id = data.get("token_id", "")
                auto_execute = data.get("auto_execute", False)

                plat_label = "KS" if platform == "kalshi" else "PM"
                price_str = f" @ ${price:.2f}" if price else " MKT"
                total = f" (${count * price:.2f} total)" if price else ""
                summary = f"{action.upper()} {count} {plat_label} {side.upper()}{price_str}{total}"

                pending_orders[order_id] = {
                    "platform": platform, "action": action, "side": side,
                    "count": count, "price": price, "order_type": order_type,
                    "ticker": ticker, "token_id": token_id,
                }

                if auto_execute:
                    # Piped commands: skip Y/N confirmation, execute immediately
                    log.info("ORDER %s: auto-executing (piped) %s", order_id, summary)
                    await _execute_order(websocket, order_id, pending_orders)
                else:
                    await websocket.send_json({
                        "type": "btc_order_confirm",
                        "order_id": order_id,
                        "summary": summary,
                    })

            elif cmd == "btc_order_execute":
                order_id = data.get("order_id", "")
                await _execute_order(websocket, order_id, pending_orders)

            elif cmd == "btc_order_cancel":
                order_id = data.get("order_id", "")
                pending_orders.pop(order_id, None)
                await websocket.send_json({"type": "btc_order_cancelled"})

            else:
                await websocket.send_json({"type": "error", "msg": f"Unknown trade cmd: {cmd}"})

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        try:
            await websocket.send_json({"type": "error", "msg": str(exc)})
        except Exception:
            pass
    finally:
        _trade_subscribers.discard(websocket)


# ── Entry point ────────────────────────────────────────────────────────────────


BTC_DEBUG_LOG = os.path.join(os.path.dirname(__file__), "btc_debug.log")
_btc_debug_handler: "logging.FileHandler | None" = None


def _set_btc_debug(enabled: bool):
    """Enable or disable BTC watcher debug logging to file."""
    import logging
    global _btc_debug_handler

    btc_log = logging.getLogger("clients.btc_watcher")

    if enabled and _btc_debug_handler is None:
        fh = logging.FileHandler(BTC_DEBUG_LOG, mode="a")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        btc_log.setLevel(logging.DEBUG)
        btc_log.addHandler(fh)
        _btc_debug_handler = fh
        btc_log.info("=== DEBUG LOGGING ENABLED ===")
    elif not enabled and _btc_debug_handler is not None:
        btc_log.info("=== DEBUG LOGGING DISABLED ===")
        btc_log.removeHandler(_btc_debug_handler)
        _btc_debug_handler.close()
        _btc_debug_handler = None
        btc_log.setLevel(logging.WARNING)


@app.get("/btc/debug-log")
async def get_btc_debug_log():
    """Return the BTC watcher debug log contents."""
    try:
        with open(BTC_DEBUG_LOG, "r") as f:
            return {"log": f.read()}
    except FileNotFoundError:
        return {"log": "(no log file yet — enable with DBG ON)"}


@app.delete("/btc/debug-log")
async def clear_btc_debug_log():
    """Clear the BTC watcher debug log."""
    try:
        open(BTC_DEBUG_LOG, "w").close()
        return {"status": "cleared"}
    except Exception as e:
        return {"status": "error", "msg": str(e)}


if __name__ == "__main__":
    # Ensure all app loggers output to console
    logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s: %(message)s", datefmt="%H:%M:%S")
    uvicorn.run(app, host="127.0.0.1", port=8081, log_level="info")
