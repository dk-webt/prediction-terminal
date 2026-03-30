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
    """Fetch positions from both platforms."""
    from clients.executor import get_kalshi_positions, get_polymarket_positions
    try:
        ks = await asyncio.to_thread(get_kalshi_positions)
        pm = await asyncio.to_thread(get_polymarket_positions)
        return {"kalshi": ks, "polymarket": pm}
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
            await _btc_stop()


# ── /ws/trade — Order confirmation + execution ───────────────────────────────

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
                count = int(data.get("count", 0))
                price = data.get("price")
                order_type = data.get("order_type", "limit")
                ticker = data.get("ticker", "")
                token_id = data.get("token_id", "")

                plat_label = "KS" if platform == "kalshi" else "PM"
                price_str = f" @ ${price:.2f}" if price else " MKT"
                total = f" (${count * price:.2f} total)" if price else ""
                summary = f"{action.upper()} {count} {plat_label} {side.upper()}{price_str}{total}"

                pending_orders[order_id] = {
                    "platform": platform, "action": action, "side": side,
                    "count": count, "price": price, "order_type": order_type,
                    "ticker": ticker, "token_id": token_id,
                }
                await websocket.send_json({
                    "type": "btc_order_confirm",
                    "order_id": order_id,
                    "summary": summary,
                })

            elif cmd == "btc_order_execute":
                order_id = data.get("order_id", "")
                order = pending_orders.pop(order_id, None)
                if not order:
                    log.warning("ORDER %s: not found or expired", order_id)
                    await websocket.send_json({"type": "btc_order_result", "success": False, "error": "Order expired or not found"})
                else:
                    log.info("ORDER %s: executing %s %s %s x%d @ %s",
                             order_id, order["platform"], order["action"],
                             order["side"], order["count"], order.get("price", "MKT"))
                    try:
                        if order["platform"] == "kalshi":
                            from clients.executor import place_kalshi_order
                            # For market orders, compute a price cap from live data
                            # (best ask + 5c buffer) to avoid filling at extreme prices
                            ks_price = order["price"]
                            if order["order_type"] == "market" and ks_price is None and _btc_stream:
                                ks_data = _btc_stream._kalshi_data
                                if ks_data and not ks_data.get("error"):
                                    side = order["side"]
                                    if side == "yes":
                                        best_ask = ks_data.get("yes_ask", 0)
                                    else:
                                        best_ask = ks_data.get("no_ask", 0)
                                    if best_ask > 0:
                                        ks_price = round(best_ask + 0.02, 2)
                                        log.info("ORDER %s: market cap from live data: best_ask=%.2f cap=%.2f",
                                                 order_id, best_ask, ks_price)
                            result = await asyncio.to_thread(
                                place_kalshi_order,
                                order["ticker"], order["action"], order["side"],
                                order["count"], ks_price, order["order_type"],
                            )
                        else:
                            from clients.executor import place_polymarket_order
                            pm_side = "BUY" if order["action"] == "buy" else "SELL"
                            # For PM market orders, compute price cap from live data
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
                                        pm_price = round(best_ask + 0.02, 2)
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
                            # Start PM heartbeat for GTC limit orders
                            if order["platform"] == "polymarket" and order["order_type"] == "limit":
                                resp_data = result.get("data", {})
                                pm_oid = resp_data.get("orderID", resp_data.get("id", ""))
                                if pm_oid:
                                    _start_pm_heartbeat(pm_oid)
                        else:
                            log.warning("ORDER %s: failed — %s", order_id, result.get("error", "unknown"))
                        await websocket.send_json({"type": "btc_order_result", **result})
                    except Exception as exc:
                        log.error("ORDER %s: exception — %s", order_id, exc, exc_info=True)
                        await websocket.send_json({"type": "btc_order_result", "success": False, "error": str(exc)})

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
    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
    uvicorn.run(app, host="127.0.0.1", port=8081, log_level="info")
