"""
BTC 15-minute binary options watcher.

Fetches the current active BTC 15-min "Up or Down" contract from both
Kalshi (KXBTC15M series) and Polymarket, including orderbook bid/ask.

Streaming: BtcStreamManager connects to both platforms via WebSocket:
  - Polymarket: CLOB WebSocket (public, no auth)
  - Kalshi: WebSocket with RSA-PSS auth (requires KALSHI_API_KEY + KALSHI_PRIVATE_KEY_PATH)
    Falls back to REST polling if RSA keys are not configured.
"""

import asyncio
import base64
import calendar
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from config import KALSHI_API_KEY, KALSHI_PRIVATE_KEY_PATH

log = logging.getLogger(__name__)

# ── Kalshi ────────────────────────────────────────────────────────────────────

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_WS_URL = "wss://api.elections.kalshi.com/trade-api/ws/v2"


def _kalshi_headers() -> dict:
    headers = {"Accept": "application/json"}
    if KALSHI_API_KEY:
        headers["Authorization"] = f"Bearer {KALSHI_API_KEY}"
    return headers


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _load_kalshi_private_key():
    """Load the RSA private key from the path in KALSHI_PRIVATE_KEY_PATH."""
    if not KALSHI_PRIVATE_KEY_PATH:
        return None
    path = Path(KALSHI_PRIVATE_KEY_PATH).expanduser()
    if not path.exists():
        log.warning("KALSHI_PRIVATE_KEY_PATH=%s does not exist", path)
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        key_data = path.read_bytes()
        return load_pem_private_key(key_data, password=None)
    except Exception as e:
        log.warning("Failed to load Kalshi RSA private key: %s", e)
        return None


def _kalshi_ws_auth_headers() -> dict | None:
    """
    Build Kalshi WebSocket authentication headers using RSA-PSS signing.

    Returns dict with KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE,
    KALSHI-ACCESS-TIMESTAMP or None if keys not configured.
    """
    if not KALSHI_API_KEY or not KALSHI_PRIVATE_KEY_PATH:
        return None

    private_key = _load_kalshi_private_key()
    if not private_key:
        return None

    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp_ms = str(int(time.time() * 1000))
        # Message to sign: timestamp_ms + method + path
        message = timestamp_ms + "GET" + "/trade-api/ws/v2"

        signature = private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.MAX_LENGTH,
            ),
            hashes.SHA256(),
        )

        return {
            "KALSHI-ACCESS-KEY": KALSHI_API_KEY,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
        }
    except Exception as e:
        log.warning("Failed to sign Kalshi WS auth: %s", e)
        return None


def kalshi_ws_available() -> bool:
    """Check if Kalshi WebSocket credentials are configured."""
    return bool(KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH)


def fetch_kalshi_btc_15m() -> dict | None:
    """
    Fetch the currently active Kalshi BTC 15-min market via REST.

    Returns dict with:
      ticker, title, floor_strike, open_time, close_time,
      yes_ask, yes_bid, no_ask, no_bid, last_price,
      volume, open_interest
    or None if no active market found.
    """
    resp = requests.get(
        f"{KALSHI_BASE}/markets",
        headers=_kalshi_headers(),
        params={"series_ticker": "KXBTC15M", "status": "open", "limit": 5},
        timeout=10,
    )
    resp.raise_for_status()
    markets = resp.json().get("markets", [])

    # Find the active market (status == "active")
    active = None
    for m in markets:
        if m.get("status") == "active":
            active = m
            break

    if not active:
        active = markets[0] if markets else None

    if not active:
        return None

    ticker = active["ticker"]

    # Fetch orderbook for richer bid/ask data
    yes_bid = _safe_float(active.get("yes_bid_dollars"))
    yes_ask = _safe_float(active.get("yes_ask_dollars"))
    no_bid = _safe_float(active.get("no_bid_dollars"))
    no_ask = _safe_float(active.get("no_ask_dollars"))

    try:
        ob_resp = requests.get(
            f"{KALSHI_BASE}/markets/{ticker}/orderbook",
            headers=_kalshi_headers(),
            timeout=10,
        )
        ob_resp.raise_for_status()
        ob = ob_resp.json().get("orderbook_fp", {})
        yes_levels = ob.get("yes_dollars", [])
        no_levels = ob.get("no_dollars", [])

        if yes_levels:
            yes_bid = _safe_float(yes_levels[-1][0])
            no_ask = round(1.0 - yes_bid, 4)
        if no_levels:
            no_bid = _safe_float(no_levels[-1][0])
            yes_ask = round(1.0 - no_bid, 4)
    except Exception:
        pass

    return {
        "platform": "kalshi",
        "ticker": ticker,
        "title": active.get("title", ""),
        "floor_strike": _safe_float(active.get("floor_strike")),
        "open_time": active.get("open_time", ""),
        "close_time": active.get("close_time", ""),
        "yes_ask": yes_ask,
        "yes_bid": yes_bid,
        "no_ask": no_ask,
        "no_bid": no_bid,
        "last_price": _safe_float(active.get("last_price_dollars")),
        "volume": _safe_float(active.get("volume_fp")),
        "open_interest": _safe_float(active.get("open_interest_fp")),
        "rules": active.get("rules_primary", ""),
        "url": f"https://kalshi.com/markets/kxbtc15m",
    }


# ── Polymarket ────────────────────────────────────────────────────────────────

PM_GAMMA = "https://gamma-api.polymarket.com"
PM_CLOB = "https://clob.polymarket.com"
PM_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


def _current_15m_slug() -> str:
    """
    Compute the slug for the Polymarket BTC 15-min market covering the current window.
    """
    now = datetime.now(timezone.utc)
    minute = (now.minute // 15) * 15
    window_start = now.replace(minute=minute, second=0, microsecond=0)
    ts = int(calendar.timegm(window_start.timetuple()))
    return f"btc-updown-15m-{ts}"


def _try_find_pm_btc_15m():
    """Try multiple strategies to find the current active PM BTC 15-min event."""
    slug = _current_15m_slug()
    resp = requests.get(
        f"{PM_GAMMA}/events",
        params={"slug": slug},
        timeout=10,
    )
    resp.raise_for_status()
    events = resp.json()
    if events:
        return events[0]

    resp = requests.get(
        f"{PM_GAMMA}/events",
        params={
            "active": "true",
            "closed": "false",
            "limit": 50,
            "order": "startDate",
            "ascending": "false",
        },
        timeout=10,
    )
    resp.raise_for_status()
    for e in resp.json():
        s = e.get("slug", "")
        if s.startswith("btc-updown-15m-"):
            return e

    return None


def fetch_polymarket_btc_15m() -> dict | None:
    """Fetch the currently active Polymarket BTC 15-min market."""
    event = _try_find_pm_btc_15m()
    if not event:
        return None

    market = event["markets"][0]
    slug = event.get("slug", "")
    title = event.get("title", "")

    raw_tokens = market.get("clobTokenIds", "[]")
    if isinstance(raw_tokens, str):
        try:
            tokens = json.loads(raw_tokens)
        except (json.JSONDecodeError, ValueError):
            tokens = []
    else:
        tokens = raw_tokens

    up_bid = 0.0
    up_ask = 0.0
    down_bid = 0.0
    down_ask = 0.0

    for i, token_id in enumerate(tokens[:2]):
        try:
            ob_resp = requests.get(
                f"{PM_CLOB}/book",
                params={"token_id": token_id},
                timeout=10,
            )
            ob_resp.raise_for_status()
            ob = ob_resp.json()
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            best_bid = max((float(b["price"]) for b in bids), default=0.0)
            best_ask = min((float(a["price"]) for a in asks), default=0.0)

            if i == 0:
                up_bid = best_bid
                up_ask = best_ask
            else:
                down_bid = best_bid
                down_ask = best_ask
        except Exception:
            pass

    if up_ask == 0.0:
        up_ask = _safe_float(market.get("bestAsk"))
    if up_bid == 0.0:
        up_bid = _safe_float(market.get("bestBid"))
    if down_ask == 0.0 and up_bid > 0:
        down_ask = round(1.0 - up_bid, 4)
    if down_bid == 0.0 and up_ask > 0:
        down_bid = round(1.0 - up_ask, 4)

    fee_schedule = market.get("feeSchedule")

    return {
        "platform": "polymarket",
        "slug": slug,
        "title": title,
        "event_start_time": market.get("eventStartTime", ""),
        "end_time": market.get("endDate", ""),
        "up_ask": up_ask,
        "up_bid": up_bid,
        "down_ask": down_ask,
        "down_bid": down_bid,
        "fee_schedule": fee_schedule,
        "description": (market.get("description") or "")[:300],
        "resolution_source": market.get("resolutionSource", ""),
        "url": f"https://polymarket.com/event/{slug}",
        "token_ids": tokens[:2] if len(tokens) >= 2 else [],
    }


# ── Combined snapshot (REST, one-shot) ────────────────────────────────────────


def fetch_btc_snapshot() -> dict:
    """Fetch BTC 15-min binary option data from both platforms via REST."""
    kalshi = None
    polymarket = None

    try:
        kalshi = fetch_kalshi_btc_15m()
    except Exception as e:
        kalshi = {"error": str(e)}

    try:
        polymarket = fetch_polymarket_btc_15m()
    except Exception as e:
        polymarket = {"error": str(e)}

    return {
        "kalshi": kalshi,
        "polymarket": polymarket,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


# ── Live streaming manager ────────────────────────────────────────────────────


class BtcStreamManager:
    """
    Manages live price streaming for BTC 15-min binary options.

    - Kalshi: WebSocket with RSA-PSS auth (ticker channel).
      Falls back to REST polling if KALSHI_API_KEY + KALSHI_PRIVATE_KEY_PATH
      are not both set.
    - Polymarket: WebSocket to CLOB (public, no auth).

    Usage:
        manager = BtcStreamManager(on_update=async_callback)
        await manager.start()
        ...
        await manager.stop()
    """

    KALSHI_POLL_INTERVAL = 3     # seconds — REST fallback polling rate
    PM_PING_INTERVAL = 8         # seconds between PM WS PING heartbeats
    PM_INACTIVITY_TIMEOUT = 120  # seconds before force-reconnect
    WINDOW_CHECK_INTERVAL = 30   # seconds between window-roll checks
    MIN_PUSH_INTERVAL = 0.5      # seconds — throttle pushes to frontend

    def __init__(self, on_update):
        self._on_update = on_update
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._last_push_time: float = 0.0

        # Latest data from each platform
        self._kalshi_data: dict | None = None
        self._kalshi_ticker: str = ""  # current market ticker for KS WS subscription
        self._pm_data: dict | None = None
        self._pm_token_ids: list[str] = []
        self._current_slug: str = ""

    async def start(self):
        """Start streaming. Performs initial REST fetch, then opens live channels."""
        self._running = True

        # Initial REST fetch to get contract details + identifiers
        initial = await asyncio.to_thread(fetch_btc_snapshot)
        self._kalshi_data = initial["kalshi"]
        self._pm_data = initial["polymarket"]

        if self._kalshi_data and not self._kalshi_data.get("error"):
            self._kalshi_ticker = self._kalshi_data.get("ticker", "")

        if self._pm_data and not self._pm_data.get("error"):
            self._pm_token_ids = self._pm_data.get("token_ids", [])
            self._current_slug = self._pm_data.get("slug", "")

        # Push initial snapshot immediately
        await self._push_update(force=True)

        # Start background tasks — use WS for Kalshi if keys configured, else REST
        if kalshi_ws_available():
            log.info("Kalshi WebSocket auth configured — using live streaming")
            self._tasks.append(asyncio.create_task(self._kalshi_ws_loop()))
        else:
            log.info("Kalshi RSA keys not configured — falling back to REST polling")
            self._tasks.append(asyncio.create_task(self._kalshi_poll_loop()))

        self._tasks.append(asyncio.create_task(self._pm_ws_loop()))
        self._tasks.append(asyncio.create_task(self._window_roll_loop()))

    async def stop(self):
        """Stop all streaming tasks."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

    async def _push_update(self, force: bool = False):
        """Build merged snapshot and send to callback (throttled)."""
        now = asyncio.get_event_loop().time()
        if not force and (now - self._last_push_time) < self.MIN_PUSH_INTERVAL:
            return
        self._last_push_time = now

        snapshot = {
            "kalshi": self._kalshi_data,
            "polymarket": self._pm_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "streaming": True,
            "kalshi_mode": "websocket" if kalshi_ws_available() else "polling",
        }
        try:
            await self._on_update(snapshot)
        except Exception as e:
            log.warning("BTC stream callback error: %s", e)

    # ── Kalshi: WebSocket streaming ───────────────────────────────────────────

    async def _kalshi_ws_loop(self):
        """
        Connect to Kalshi WebSocket with RSA-PSS auth and stream
        ticker updates for the active BTC 15-min market.
        Reconnects automatically on failure.
        """
        import websockets

        while self._running:
            auth_headers = _kalshi_ws_auth_headers()
            if not auth_headers:
                log.warning("Kalshi WS auth failed, falling back to REST poll")
                await self._kalshi_poll_loop()
                return

            try:
                log.info("Connecting to Kalshi WS for %s", self._kalshi_ticker)

                async with websockets.connect(
                    KALSHI_WS_URL,
                    additional_headers=auth_headers,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    # Subscribe to ticker + orderbook_delta for the active market
                    if self._kalshi_ticker:
                        sub_msg = json.dumps({
                            "id": 1,
                            "cmd": "subscribe",
                            "params": {
                                "channels": ["ticker", "orderbook_delta"],
                                "market_ticker": self._kalshi_ticker,
                            },
                        })
                        await ws.send(sub_msg)
                        log.info("Subscribed to Kalshi WS ticker: %s", self._kalshi_ticker)

                    await self._kalshi_recv_loop(ws)

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Kalshi WS error, reconnecting in 3s: %s", e)
                await asyncio.sleep(3)

    async def _kalshi_recv_loop(self, ws):
        """Receive and process Kalshi WS messages."""
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
            except asyncio.TimeoutError:
                # No data in 60s — send a ping to check connection
                continue

            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            msg_type = msg.get("type", "")
            data = msg.get("msg", {})

            if msg_type == "ticker":
                self._apply_kalshi_ticker(data)
                await self._push_update()

            elif msg_type == "orderbook_snapshot":
                self._apply_kalshi_orderbook_snapshot(data)
                await self._push_update()

            elif msg_type == "orderbook_delta":
                # Delta updates are incremental — for simplicity, just update
                # bid/ask from the delta's price+side info
                self._apply_kalshi_orderbook_delta(data)
                await self._push_update()

            elif msg_type == "error":
                log.warning("Kalshi WS error msg: %s", data)

    def _apply_kalshi_ticker(self, data: dict):
        """Apply a Kalshi ticker update to our stored data."""
        if not self._kalshi_data or self._kalshi_data.get("error"):
            return

        # Ticker gives us direct yes_bid, yes_ask, price, volume, OI
        yes_bid = _safe_float(data.get("yes_bid_dollars"))
        yes_ask = _safe_float(data.get("yes_ask_dollars"))
        price = _safe_float(data.get("price_dollars"))
        volume = _safe_float(data.get("volume_fp"))
        oi = _safe_float(data.get("open_interest_fp"))

        if yes_bid:
            self._kalshi_data["yes_bid"] = yes_bid
            self._kalshi_data["no_ask"] = round(1.0 - yes_bid, 4)
        if yes_ask:
            self._kalshi_data["yes_ask"] = yes_ask
            self._kalshi_data["no_bid"] = round(1.0 - yes_ask, 4)
        if price:
            self._kalshi_data["last_price"] = price
        if volume:
            self._kalshi_data["volume"] = volume
        if oi:
            self._kalshi_data["open_interest"] = oi

    def _apply_kalshi_orderbook_snapshot(self, data: dict):
        """Apply a Kalshi orderbook snapshot."""
        if not self._kalshi_data or self._kalshi_data.get("error"):
            return

        yes_levels = data.get("yes_dollars_fp", [])
        no_levels = data.get("no_dollars_fp", [])

        if yes_levels:
            yes_bid = _safe_float(yes_levels[-1][0])
            self._kalshi_data["yes_bid"] = yes_bid
            self._kalshi_data["no_ask"] = round(1.0 - yes_bid, 4)
        if no_levels:
            no_bid = _safe_float(no_levels[-1][0])
            self._kalshi_data["no_bid"] = no_bid
            self._kalshi_data["yes_ask"] = round(1.0 - no_bid, 4)

    def _apply_kalshi_orderbook_delta(self, data: dict):
        """Apply a Kalshi orderbook delta (incremental update)."""
        if not self._kalshi_data or self._kalshi_data.get("error"):
            return

        price = _safe_float(data.get("price_dollars"))
        side = data.get("side", "")
        delta = _safe_float(data.get("delta_fp"))

        if not price or not side:
            return

        # A delta with positive size means new/increased level,
        # negative means removed/decreased. For best bid/ask we
        # just track the price level if it's better than current.
        if side == "yes":
            if delta > 0 and price >= self._kalshi_data.get("yes_bid", 0):
                self._kalshi_data["yes_bid"] = price
                self._kalshi_data["no_ask"] = round(1.0 - price, 4)
        elif side == "no":
            if delta > 0 and price >= self._kalshi_data.get("no_bid", 0):
                self._kalshi_data["no_bid"] = price
                self._kalshi_data["yes_ask"] = round(1.0 - price, 4)

    # ── Kalshi: REST polling fallback ─────────────────────────────────────────

    async def _kalshi_poll_loop(self):
        """Poll Kalshi REST API — used when WS auth keys not configured."""
        while self._running:
            try:
                data = await asyncio.to_thread(fetch_kalshi_btc_15m)
                if data:
                    self._kalshi_data = data
                    self._kalshi_ticker = data.get("ticker", "")
                    await self._push_update()
            except Exception as e:
                log.debug("Kalshi poll error: %s", e)
            await asyncio.sleep(self.KALSHI_POLL_INTERVAL)

    # ── Polymarket: WebSocket streaming ───────────────────────────────────────

    async def _pm_ws_loop(self):
        """
        Connect to Polymarket CLOB WebSocket and stream live price updates.
        Reconnects automatically on failure.
        """
        import websockets

        while self._running:
            if not self._pm_token_ids:
                await asyncio.sleep(5)
                continue

            try:
                log.info("Connecting to Polymarket WS for tokens: %s",
                         [t[:20] + "..." for t in self._pm_token_ids])

                async with websockets.connect(
                    PM_WS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    sub_msg = json.dumps({
                        "assets_ids": self._pm_token_ids,
                        "type": "market",
                    })
                    await ws.send(sub_msg)
                    log.info("Subscribed to PM WS")

                    recv_task = asyncio.create_task(self._pm_recv_loop(ws))
                    ping_task = asyncio.create_task(self._pm_ping_loop(ws))

                    done, pending = await asyncio.wait(
                        [recv_task, ping_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    for t in done:
                        t.result()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("PM WS error, reconnecting in 3s: %s", e)
                await asyncio.sleep(3)

    async def _pm_recv_loop(self, ws):
        """Receive messages from PM WS and update prices."""
        while self._running:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=self.PM_INACTIVITY_TIMEOUT
                )
            except asyncio.TimeoutError:
                log.warning("PM WS inactivity timeout, forcing reconnect")
                return

            if raw == "PONG":
                continue

            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            messages = parsed if isinstance(parsed, list) else [parsed]
            updated = False

            for msg in messages:
                if not isinstance(msg, dict):
                    continue

                event_type = msg.get("event_type", "")

                if event_type == "price_change":
                    for pc in msg.get("price_changes", []):
                        asset_id = pc.get("asset_id", "")
                        best_bid = _safe_float(pc.get("best_bid"))
                        best_ask = _safe_float(pc.get("best_ask"))
                        updated |= self._apply_pm_price(asset_id, best_bid, best_ask)

                elif event_type == "book" or (not event_type and "bids" in msg):
                    asset_id = msg.get("asset_id", "")
                    bids = msg.get("bids", [])
                    asks = msg.get("asks", [])
                    best_bid = max((float(b["price"]) for b in bids), default=0.0)
                    best_ask = min((float(a["price"]) for a in asks), default=0.0)
                    updated |= self._apply_pm_price(asset_id, best_bid, best_ask)

            if updated:
                await self._push_update()

    def _apply_pm_price(self, asset_id: str, best_bid: float, best_ask: float) -> bool:
        """Apply a PM price update. Returns True if data actually changed."""
        if not self._pm_data or self._pm_data.get("error"):
            return False

        tokens = self._pm_token_ids
        changed = False

        if len(tokens) >= 1 and asset_id == tokens[0]:
            if best_bid and best_bid != self._pm_data.get("up_bid"):
                self._pm_data["up_bid"] = best_bid
                changed = True
            if best_ask and best_ask != self._pm_data.get("up_ask"):
                self._pm_data["up_ask"] = best_ask
                changed = True
        elif len(tokens) >= 2 and asset_id == tokens[1]:
            if best_bid and best_bid != self._pm_data.get("down_bid"):
                self._pm_data["down_bid"] = best_bid
                changed = True
            if best_ask and best_ask != self._pm_data.get("down_ask"):
                self._pm_data["down_ask"] = best_ask
                changed = True

        return changed

    async def _pm_ping_loop(self, ws):
        """Send application-level PING to PM WS every 8 seconds."""
        while self._running:
            try:
                await ws.send("PING")
            except Exception:
                return
            await asyncio.sleep(self.PM_PING_INTERVAL)

    # ── Window roll ───────────────────────────────────────────────────────────

    async def _window_roll_loop(self):
        """
        Check if the 15-min window has changed. If so, re-fetch contract
        details and re-subscribe WebSockets to new markets/tokens.
        """
        while self._running:
            await asyncio.sleep(self.WINDOW_CHECK_INTERVAL)
            new_slug = _current_15m_slug()
            if new_slug != self._current_slug:
                log.info("Window rolled: %s -> %s", self._current_slug, new_slug)
                self._current_slug = new_slug

                # Re-fetch both platforms for new window
                try:
                    pm = await asyncio.to_thread(fetch_polymarket_btc_15m)
                    if pm and not pm.get("error"):
                        self._pm_data = pm
                        self._pm_token_ids = pm.get("token_ids", [])
                except Exception as e:
                    log.debug("Window roll PM fetch error: %s", e)

                try:
                    ks = await asyncio.to_thread(fetch_kalshi_btc_15m)
                    if ks:
                        self._kalshi_data = ks
                        self._kalshi_ticker = ks.get("ticker", "")
                except Exception as e:
                    log.debug("Window roll KS fetch error: %s", e)

                await self._push_update(force=True)
