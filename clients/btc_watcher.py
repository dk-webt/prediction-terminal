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
from datetime import datetime, timedelta, timezone
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
                salt_length=padding.PSS.DIGEST_LENGTH,
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
        timeout=5,
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
            timeout=5,
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
PM_CRYPTO_PRICE = "https://polymarket.com/api/crypto/crypto-price"
PM_USER_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/user"
PM_RTDS_URL = "wss://ws-live-data.polymarket.com"


def fetch_pm_strike_price(event_start_time: str, end_time: str) -> float | None:
    """
    Fetch the actual Chainlink BTC/USD opening price (priceToBeat) from
    Polymarket's internal crypto-price API.
    """
    try:
        resp = requests.get(
            PM_CRYPTO_PRICE,
            params={
                "symbol": "BTC",
                "eventStartTime": event_start_time,
                "variant": "fifteen",
                "endDate": end_time,
            },
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        price = data.get("openPrice")
        if price is not None and isinstance(price, (int, float)):
            return float(price)
    except Exception as e:
        log.debug("Failed to fetch PM strike price: %s", e)
    return None


def _current_15m_slug() -> str:
    """
    Compute the slug for the Polymarket BTC 15-min market covering the current window.
    """
    now = datetime.now(timezone.utc)
    minute = (now.minute // 15) * 15
    window_start = now.replace(minute=minute, second=0, microsecond=0)
    window_end = window_start + timedelta(minutes=15)
    ts = int(calendar.timegm(window_start.timetuple()))
    slug = f"btc-updown-15m-{ts}"
    log.debug("_current_15m_slug: now=%s window=%s–%s slug=%s",
              now.strftime("%H:%M:%S"), window_start.strftime("%H:%M:%S"),
              window_end.strftime("%H:%M:%S"), slug)
    return slug


def _try_find_pm_btc_15m():
    """Try multiple strategies to find the current active PM BTC 15-min event."""
    slug = _current_15m_slug()
    resp = requests.get(
        f"{PM_GAMMA}/events",
        params={"slug": slug},
        timeout=5,
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
        timeout=5,
    )
    resp.raise_for_status()
    for e in resp.json():
        s = e.get("slug", "")
        if s.startswith("btc-updown-15m-"):
            return e

    return None


def _fetch_ob(token_id: str) -> tuple[float, float]:
    """Fetch best bid/ask for a single PM CLOB token. Returns (best_bid, best_ask)."""
    try:
        resp = requests.get(
            f"{PM_CLOB}/book", params={"token_id": token_id}, timeout=5,
        )
        resp.raise_for_status()
        ob = resp.json()
        best_bid = max((float(b["price"]) for b in ob.get("bids", [])), default=0.0)
        best_ask = min((float(a["price"]) for a in ob.get("asks", [])), default=0.0)
        return best_bid, best_ask
    except Exception:
        return 0.0, 0.0


def fetch_polymarket_btc_15m() -> dict | None:
    """Fetch the currently active Polymarket BTC 15-min market."""
    from concurrent.futures import ThreadPoolExecutor

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

    event_start = market.get("eventStartTime", "")
    end_date = market.get("endDate", "")

    # Fetch orderbooks + strike price in parallel
    up_bid, up_ask, down_bid, down_ask, strike = 0.0, 0.0, 0.0, 0.0, None

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {}
        if len(tokens) >= 1:
            futures["up"] = pool.submit(_fetch_ob, tokens[0])
        if len(tokens) >= 2:
            futures["down"] = pool.submit(_fetch_ob, tokens[1])
        if event_start:
            futures["strike"] = pool.submit(fetch_pm_strike_price, event_start, end_date)

        if "up" in futures:
            up_bid, up_ask = futures["up"].result()
        if "down" in futures:
            down_bid, down_ask = futures["down"].result()
        if "strike" in futures:
            strike = futures["strike"].result()

    if up_ask == 0.0:
        up_ask = _safe_float(market.get("bestAsk"))
    if up_bid == 0.0:
        up_bid = _safe_float(market.get("bestBid"))
    if down_ask == 0.0 and up_bid > 0:
        down_ask = round(1.0 - up_bid, 4)
    if down_bid == 0.0 and up_ask > 0:
        down_bid = round(1.0 - up_ask, 4)

    return {
        "platform": "polymarket",
        "slug": slug,
        "title": title,
        "floor_strike": strike,
        "event_start_time": event_start,
        "end_time": end_date,
        "up_ask": up_ask,
        "up_bid": up_bid,
        "down_ask": down_ask,
        "down_bid": down_bid,
        "fee_schedule": market.get("feeSchedule"),
        "description": (market.get("description") or "")[:300],
        "resolution_source": market.get("resolutionSource", ""),
        "url": f"https://polymarket.com/event/{slug}",
        "token_ids": tokens[:2] if len(tokens) >= 2 else [],
        "condition_id": market.get("conditionId", ""),
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

    PM_PING_INTERVAL = 8         # seconds between PM WS PING heartbeats
    PM_INACTIVITY_TIMEOUT = 120  # seconds before force-reconnect
    MIN_PUSH_INTERVAL = 0.5      # seconds — throttle pushes to frontend
    ROLL_RETRY_INTERVAL = 0.5    # seconds between retries when new contract not ready
    ROLL_MAX_RETRIES = 60        # max retries (~30s max wait for slow platforms)
    STALE_THRESHOLD = 10         # seconds — log warning when platform data goes stale

    def __init__(self, on_update, on_ks_fill=None, on_ks_order=None,
                 on_pm_fill=None, on_pm_order=None):
        self._on_update = on_update
        self._on_ks_fill = on_ks_fill        # async callback for Kalshi fill events
        self._on_ks_order = on_ks_order      # async callback for Kalshi order updates
        self._on_pm_fill = on_pm_fill        # async callback for PM trade events
        self._on_pm_order = on_pm_order      # async callback for PM order events
        self._running = False
        self._tasks: list[asyncio.Task] = []
        self._last_push_time: float = 0.0

        # Latest data from each platform
        self._kalshi_data: dict | None = None
        self._kalshi_ticker: str = ""  # current market ticker for KS WS subscription
        self._pm_data: dict | None = None
        self._pm_token_ids: list[str] = []
        self._pm_condition_id: str = ""  # condition ID for PM user channel
        self._current_slug: str = ""
        self._rolling = False  # True while window roll is in progress
        self._ks_last_update: str = ""   # ISO timestamp of last Kalshi data
        self._pm_last_update: str = ""   # ISO timestamp of last Polymarket data
        self._ks_last_recv: float = 0.0  # monotonic time of last KS data
        self._pm_last_recv: float = 0.0  # monotonic time of last PM data
        self._ks_stale_logged = False    # avoid spamming stale warnings
        self._pm_stale_logged = False

        # Kalshi WS state
        self._ks_ws = None               # active WS connection reference
        self._ks_sids: dict[str, int] = {}  # channel_name -> subscription ID
        self._ks_cmd_id: int = 0         # incrementing command ID for KS WS

        # Polymarket WS state
        self._pm_ws = None               # active market channel WS reference

        # RTDS live BTC price feeds (Chainlink + Binance)
        self._chainlink_price: float | None = None
        self._binance_price: float | None = None

        # Signals WS loops to reconnect with new tokens/tickers
        self._pm_reconnect = asyncio.Event()
        self._pm_user_reconnect = asyncio.Event()  # separate for user channel
        self._ks_reconnect = asyncio.Event()

    async def start(self):
        """Start streaming. Performs initial REST fetch, then opens live channels."""
        self._running = True

        # Initial REST fetch to get contract details + identifiers
        t0 = time.monotonic()
        initial = await asyncio.to_thread(fetch_btc_snapshot)
        t1 = time.monotonic()
        self._kalshi_data = initial["kalshi"]
        self._pm_data = initial["polymarket"]

        if self._kalshi_data and not self._kalshi_data.get("error"):
            self._kalshi_ticker = self._kalshi_data.get("ticker", "")
            self._mark_ks_recv()

        if self._pm_data and not self._pm_data.get("error"):
            self._pm_token_ids = self._pm_data.get("token_ids", [])
            self._pm_condition_id = self._pm_data.get("condition_id", "")
            self._current_slug = self._pm_data.get("slug", "")
            self._mark_pm_recv()

        log.info("INIT: snapshot in %.0fms | slug=%s ks_ticker=%s pm_tokens=%d pm_strike=%s",
                 (t1 - t0) * 1000, self._current_slug, self._kalshi_ticker,
                 len(self._pm_token_ids),
                 self._pm_data.get("floor_strike") if self._pm_data else None)

        # Push initial snapshot immediately
        await self._push_update(force=True)

        # Start background tasks
        if kalshi_ws_available():
            log.info("Kalshi WebSocket auth configured — using live streaming")
            self._tasks.append(asyncio.create_task(self._kalshi_ws_loop()))
        else:
            log.warning("Kalshi RSA keys not configured — no Kalshi streaming available")

        self._tasks.append(asyncio.create_task(self._pm_ws_loop()))
        self._tasks.append(asyncio.create_task(self._rtds_ws_loop()))
        self._tasks.append(asyncio.create_task(self._window_roll_loop()))

        # Start PM user channel for fill/order tracking if creds configured
        from config import POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE
        if POLYMARKET_API_KEY and POLYMARKET_API_SECRET and POLYMARKET_API_PASSPHRASE:
            log.info("PM User WS auth configured — starting fill/order tracking")
            self._tasks.append(asyncio.create_task(self._pm_user_ws_loop()))
        else:
            log.warning("PM API creds not configured — no PM fill/order tracking")

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

    def _next_ks_cmd_id(self) -> int:
        """Get next incrementing command ID for Kalshi WS."""
        self._ks_cmd_id += 1
        return self._ks_cmd_id

    def _mark_ks_recv(self):
        """Mark Kalshi data received — resets stale flag."""
        self._ks_last_recv = time.monotonic()
        self._ks_last_update = datetime.now(timezone.utc).isoformat()
        if self._ks_stale_logged:
            log.info("KS RECOVERED: data flowing again after stale period")
            self._ks_stale_logged = False

    def _mark_pm_recv(self):
        """Mark Polymarket data received — resets stale flag."""
        self._pm_last_recv = time.monotonic()
        self._pm_last_update = datetime.now(timezone.utc).isoformat()
        if self._pm_stale_logged:
            log.info("PM RECOVERED: data flowing again after stale period")
            self._pm_stale_logged = False

    def _check_staleness(self):
        """Log warnings when platform data goes stale. Skipped during rolls."""
        if self._rolling:
            return
        now = time.monotonic()
        if self._ks_last_recv > 0:
            ks_age = now - self._ks_last_recv
            if ks_age > self.STALE_THRESHOLD and not self._ks_stale_logged:
                log.warning("KS STALE: no Kalshi data for %.0fs (ticker=%s)",
                            ks_age, self._kalshi_ticker)
                self._ks_stale_logged = True
        if self._pm_last_recv > 0:
            pm_age = now - self._pm_last_recv
            if pm_age > self.STALE_THRESHOLD and not self._pm_stale_logged:
                log.warning("PM STALE: no Polymarket data for %.0fs (slug=%s)",
                            pm_age, self._current_slug)
                self._pm_stale_logged = True

    async def _push_update(self, force: bool = False):
        """Build merged snapshot and send to callback (throttled)."""
        now = asyncio.get_event_loop().time()
        if not force and (now - self._last_push_time) < self.MIN_PUSH_INTERVAL:
            return
        self._last_push_time = now

        self._check_staleness()

        snapshot = {
            "kalshi": self._kalshi_data,
            "polymarket": self._pm_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "streaming": True,
            "kalshi_mode": "websocket",
            "rolling": self._rolling,
            "kalshi_last_update": self._ks_last_update,
            "polymarket_last_update": self._pm_last_update,
            "btc_chainlink": self._chainlink_price,
            "btc_binance": self._binance_price,
            "btc_price_gap": (
                self._chainlink_price - self._binance_price
                if self._chainlink_price is not None and self._binance_price is not None
                else None
            ),
        }
        try:
            await self._on_update(snapshot)
        except Exception as e:
            log.warning("BTC stream callback error: %s", e)

    # ── Kalshi: WebSocket streaming ───────────────────────────────────────────

    async def _kalshi_ws_loop(self):
        """
        Connect to Kalshi WebSocket with RSA-PSS auth and stream
        ticker updates + fill/order notifications.
        Reconnects automatically on failure. Uses update_subscription
        for rolls instead of full reconnect.
        """
        import websockets

        while self._running:
            auth_headers = _kalshi_ws_auth_headers()
            if not auth_headers:
                log.error("Kalshi WS auth failed — check KALSHI_API_KEY and KALSHI_PRIVATE_KEY_PATH")
                await asyncio.sleep(30)
                continue

            try:
                self._ks_cmd_id = 0
                self._ks_sids.clear()
                connected_ticker = self._kalshi_ticker
                log.info("Connecting to Kalshi WS for %s", connected_ticker)

                self._ks_reconnect.clear()

                async with websockets.connect(
                    KALSHI_WS_URL,
                    additional_headers=auth_headers,
                    ping_interval=None,  # Kalshi sends pings every 10s; library auto-responds
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    self._ks_ws = ws

                    # Subscribe to market data channels
                    if connected_ticker:
                        await ws.send(json.dumps({
                            "id": self._next_ks_cmd_id(),
                            "cmd": "subscribe",
                            "params": {
                                "channels": ["ticker", "orderbook_delta"],
                                "market_ticker": connected_ticker,
                            },
                        }))
                        log.info("Subscribed to Kalshi WS data: %s", connected_ticker)

                    # Subscribe to private channels (fill + order tracking)
                    # No market_ticker — receive for all markets
                    await ws.send(json.dumps({
                        "id": self._next_ks_cmd_id(),
                        "cmd": "subscribe",
                        "params": {"channels": ["fill"]},
                    }))
                    await ws.send(json.dumps({
                        "id": self._next_ks_cmd_id(),
                        "cmd": "subscribe",
                        "params": {"channels": ["user_orders"]},
                    }))
                    log.info("Subscribed to Kalshi WS fill + user_orders")

                    recv_task = asyncio.create_task(self._kalshi_recv_loop(ws))
                    roll_task = asyncio.create_task(self._ks_reconnect.wait())

                    done, pending = await asyncio.wait(
                        [recv_task, roll_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if roll_task in done:
                        log.info("Kalshi WS reconnecting for new window ticker")
                    else:
                        for t in done:
                            t.result()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("Kalshi WS error, reconnecting in 3s: %s", e)
                await asyncio.sleep(3)
            finally:
                self._ks_ws = None

    async def _kalshi_recv_loop(self, ws):
        """Receive and process Kalshi WS messages."""
        import websockets

        consecutive_timeouts = 0
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                consecutive_timeouts = 0
            except websockets.exceptions.ConnectionClosed:
                log.info("KS WS connection closed")
                return
            except asyncio.TimeoutError:
                consecutive_timeouts += 1
                log.warning("KS WS idle: no data for %ds (ticker=%s)",
                            consecutive_timeouts * 60, self._kalshi_ticker)
                if consecutive_timeouts >= 3:
                    log.warning("KS WS idle too long (%ds), forcing reconnect", consecutive_timeouts * 60)
                    return  # exits recv loop → outer loop reconnects
                continue

            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            msg_type = msg.get("type", "")
            data = msg.get("msg", {})

            if msg_type == "subscribed":
                # Track SIDs from subscribe responses
                channel = data.get("channel", "")
                sid = data.get("sid")
                if channel and sid is not None:
                    self._ks_sids[channel] = sid
                    log.info("KS WS subscribed: channel=%s sid=%d", channel, sid)

            elif msg_type == "ticker":
                self._apply_kalshi_ticker(data)
                self._mark_ks_recv()
                await self._push_update()

            elif msg_type == "orderbook_snapshot":
                self._apply_kalshi_orderbook_snapshot(data)
                self._mark_ks_recv()
                await self._push_update()

            elif msg_type == "orderbook_delta":
                self._apply_kalshi_orderbook_delta(data)
                self._mark_ks_recv()
                await self._push_update()

            elif msg_type == "fill":
                log.info("KS FILL: %s", data)
                if self._on_ks_fill:
                    try:
                        await self._on_ks_fill(data)
                    except Exception as e:
                        log.warning("KS fill callback error: %s", e)

            elif msg_type == "user_order":
                log.info("KS ORDER: %s %s %s", data.get("ticker", ""),
                         data.get("status", ""), data.get("side", ""))
                if self._on_ks_order:
                    try:
                        await self._on_ks_order(data)
                    except Exception as e:
                        log.warning("KS order callback error: %s", e)

            elif msg_type == "ok":
                # Response to update_subscription / list_subscriptions
                log.debug("KS WS ok: id=%s msg=%s", msg.get("id"), data)

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

    # ── Kalshi: dynamic subscription updates ───────────────────────────────

    async def _kalshi_update_subscription(self, old_ticker: str, new_ticker: str) -> bool:
        """
        Swap Kalshi market subscription in-place using update_subscription.
        Adds new ticker then removes old ticker from existing SIDs.
        Returns True on success, False if fallback reconnect needed.
        """
        ws = self._ks_ws
        if not ws:
            log.warning("KS update_subscription: no active WS connection")
            return False

        # Get SIDs for data channels (ticker + orderbook_delta)
        data_sids = []
        for ch in ("ticker", "orderbook_delta"):
            sid = self._ks_sids.get(ch)
            if sid is not None:
                data_sids.append(sid)

        if not data_sids:
            log.warning("KS update_subscription: no SIDs tracked, falling back to reconnect")
            return False

        try:
            # Add new ticker to existing subscriptions
            await ws.send(json.dumps({
                "id": self._next_ks_cmd_id(),
                "cmd": "update_subscription",
                "params": {
                    "sids": data_sids,
                    "market_tickers": [new_ticker],
                    "action": "add_markets",
                    "send_initial_snapshot": True,
                },
            }))
            log.info("KS update_subscription: added %s to sids=%s", new_ticker, data_sids)

            # Remove old ticker
            if old_ticker:
                await ws.send(json.dumps({
                    "id": self._next_ks_cmd_id(),
                    "cmd": "update_subscription",
                    "params": {
                        "sids": data_sids,
                        "market_tickers": [old_ticker],
                        "action": "delete_markets",
                    },
                }))
                log.info("KS update_subscription: removed %s", old_ticker)

            return True
        except Exception as e:
            log.warning("KS update_subscription failed: %s", e)
            return False

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
                # Snapshot token IDs at connect time to detect rolls
                connected_tokens = list(self._pm_token_ids)
                log.info("Connecting to Polymarket WS for tokens: %s",
                         [t[:20] + "..." for t in connected_tokens])

                self._pm_reconnect.clear()

                async with websockets.connect(
                    PM_WS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    self._pm_ws = ws
                    sub_msg = json.dumps({
                        "assets_ids": connected_tokens,
                        "type": "market",
                        "custom_feature_enabled": True,
                    })
                    await ws.send(sub_msg)
                    log.info("Subscribed to PM WS")

                    recv_task = asyncio.create_task(self._pm_recv_loop(ws))
                    ping_task = asyncio.create_task(self._pm_ping_loop(ws))
                    roll_task = asyncio.create_task(self._pm_reconnect.wait())

                    done, pending = await asyncio.wait(
                        [recv_task, ping_task, roll_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if roll_task in done:
                        log.info("PM WS reconnecting for new window tokens")
                    else:
                        for t in done:
                            t.result()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("PM WS error, reconnecting in 3s: %s", e)
                await asyncio.sleep(3)
            finally:
                self._pm_ws = None

    async def _pm_recv_loop(self, ws):
        """Receive messages from PM WS and update prices."""
        import websockets
        while self._running:
            try:
                raw = await asyncio.wait_for(
                    ws.recv(), timeout=self.PM_INACTIVITY_TIMEOUT
                )
            except asyncio.TimeoutError:
                log.warning("PM WS inactivity timeout, forcing reconnect")
                return
            except websockets.exceptions.ConnectionClosed:
                log.info("PM WS connection closed cleanly")
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

                elif event_type == "best_bid_ask":
                    asset_id = msg.get("asset_id", "")
                    best_bid = _safe_float(msg.get("best_bid"))
                    best_ask = _safe_float(msg.get("best_ask"))
                    updated |= self._apply_pm_price(asset_id, best_bid, best_ask)

                elif event_type == "new_market":
                    log.info("PM new_market: slug=%s id=%s", msg.get("slug", ""), msg.get("id", ""))

                elif event_type == "market_resolved":
                    log.info("PM market_resolved: id=%s winner=%s",
                             msg.get("id", ""), msg.get("winning_outcome", ""))

                elif event_type == "last_trade_price":
                    pass  # informational, price already tracked via best_bid_ask

            if updated:
                self._mark_pm_recv()
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

    # ── RTDS: live BTC price feeds (Chainlink + Binance) ────────────────────

    RTDS_PING_INTERVAL = 5  # seconds — stricter than market channel

    async def _rtds_ws_loop(self):
        """
        Connect to Polymarket RTDS for live BTC spot prices.
        Subscribes to both Chainlink (PM's oracle) and Binance feeds.
        """
        import websockets

        while self._running:
            try:
                log.info("Connecting to RTDS for live BTC price feeds")
                async with websockets.connect(
                    PM_RTDS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    # Subscribe to both feeds
                    await ws.send(json.dumps({
                        "action": "subscribe",
                        "subscriptions": [
                            {"topic": "crypto_prices_chainlink", "type": "price", "filters": "btc/usd"},
                            {"topic": "crypto_prices", "type": "price", "filters": "btcusdt"},
                        ],
                    }))
                    log.info("RTDS subscribed: chainlink btc/usd + binance btcusdt")

                    recv_task = asyncio.create_task(self._rtds_recv_loop(ws))
                    ping_task = asyncio.create_task(self._rtds_ping_loop(ws))

                    done, pending = await asyncio.wait(
                        [recv_task, ping_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    for t in done:
                        try:
                            t.result()
                        except Exception:
                            pass

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("RTDS WS error, reconnecting in 3s: %s", e)
                await asyncio.sleep(3)

    async def _rtds_recv_loop(self, ws):
        """Receive live BTC price updates from RTDS."""
        import websockets
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                log.warning("RTDS inactivity timeout (30s), reconnecting")
                return
            except websockets.exceptions.ConnectionClosed:
                log.info("RTDS connection closed")
                return

            if raw == "PONG":
                continue

            try:
                msg = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            topic = msg.get("topic", "")
            payload = msg.get("payload")
            if not payload:
                continue

            # Extract price from payload
            price = None
            if isinstance(payload, dict):
                price = payload.get("price") or payload.get("p")
            elif isinstance(payload, (int, float)):
                price = payload

            if price is None:
                continue

            try:
                price = float(price)
            except (TypeError, ValueError):
                continue

            updated = False
            if topic == "crypto_prices_chainlink":
                if price != self._chainlink_price:
                    self._chainlink_price = price
                    updated = True
            elif topic == "crypto_prices":
                if price != self._binance_price:
                    self._binance_price = price
                    updated = True

            if updated:
                await self._push_update()

    async def _rtds_ping_loop(self, ws):
        """Send PING to RTDS every 5 seconds."""
        while self._running:
            try:
                await ws.send("PING")
            except Exception:
                return
            await asyncio.sleep(self.RTDS_PING_INTERVAL)

    # ── Polymarket: dynamic subscription updates ────────────────────────────

    async def _pm_swap_tokens(self, old_tokens: list[str], new_tokens: list[str]) -> bool:
        """
        Swap PM market channel subscription in-place.
        Subscribe to new tokens, unsubscribe old ones. No server acknowledgment.
        Returns True on success, False if fallback reconnect needed.
        """
        ws = self._pm_ws
        if not ws:
            log.warning("PM swap_tokens: no active WS connection")
            return False

        try:
            if new_tokens:
                await ws.send(json.dumps({"operation": "subscribe", "assets_ids": new_tokens}))
                log.info("PM WS dynamic subscribe: %s", [t[:20] + "..." for t in new_tokens])
            if old_tokens:
                await ws.send(json.dumps({"operation": "unsubscribe", "assets_ids": old_tokens}))
                log.info("PM WS dynamic unsubscribe: %s", [t[:20] + "..." for t in old_tokens])
            return True
        except Exception as e:
            log.warning("PM WS swap_tokens failed: %s", e)
            return False

    # ── Polymarket: user channel (fill/order tracking) ────────────────────────

    async def _pm_user_ws_loop(self):
        """
        Connect to PM user channel WS for fill/order notifications.
        Requires POLYMARKET_API_KEY/SECRET/PASSPHRASE.
        """
        import websockets
        from config import POLYMARKET_API_KEY, POLYMARKET_API_SECRET, POLYMARKET_API_PASSPHRASE

        while self._running:
            if not self._pm_condition_id:
                await asyncio.sleep(5)
                continue

            try:
                self._pm_user_reconnect.clear()
                log.info("Connecting to PM User WS for condition=%s", self._pm_condition_id[:20])

                async with websockets.connect(
                    PM_USER_WS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    close_timeout=5,
                ) as ws:
                    sub_msg = json.dumps({
                        "auth": {
                            "apiKey": POLYMARKET_API_KEY,
                            "secret": POLYMARKET_API_SECRET,
                            "passphrase": POLYMARKET_API_PASSPHRASE,
                        },
                        "type": "user",
                        "markets": [self._pm_condition_id],
                    })
                    await ws.send(sub_msg)
                    log.info("PM User WS: subscribed")

                    recv_task = asyncio.create_task(self._pm_user_recv_loop(ws))
                    ping_task = asyncio.create_task(self._pm_user_ping_loop(ws))
                    roll_task = asyncio.create_task(self._pm_user_reconnect.wait())

                    done, pending = await asyncio.wait(
                        [recv_task, ping_task, roll_task],
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in pending:
                        t.cancel()
                    if roll_task in done:
                        log.info("PM User WS reconnecting for new condition ID")

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("PM User WS error, reconnecting in 3s: %s", e)
                await asyncio.sleep(3)

    async def _pm_user_recv_loop(self, ws):
        """Receive fill/order events from PM user channel."""
        import websockets
        while self._running:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
            except asyncio.TimeoutError:
                log.warning("PM User WS inactivity timeout")
                return
            except websockets.exceptions.ConnectionClosed:
                log.info("PM User WS connection closed")
                return

            if raw == "PONG":
                continue

            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue

            messages = parsed if isinstance(parsed, list) else [parsed]
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                event_type = msg.get("event_type", "")

                if event_type == "trade":
                    log.info("PM TRADE: status=%s side=%s size=%s price=%s",
                             msg.get("status"), msg.get("side"),
                             msg.get("size"), msg.get("price"))
                    if self._on_pm_fill:
                        try:
                            await self._on_pm_fill(msg)
                        except Exception as e:
                            log.warning("PM fill callback error: %s", e)

                elif event_type == "order":
                    log.info("PM ORDER: type=%s matched=%s/%s",
                             msg.get("type"), msg.get("size_matched"),
                             msg.get("original_size"))
                    if self._on_pm_order:
                        try:
                            await self._on_pm_order(msg)
                        except Exception as e:
                            log.warning("PM order callback error: %s", e)

    async def _pm_user_ping_loop(self, ws):
        """Send PING to PM user channel every 10 seconds."""
        while self._running:
            try:
                await ws.send("PING")
            except Exception:
                return
            await asyncio.sleep(10)

    # ── Window roll ───────────────────────────────────────────────────────────

    async def _poll_pm_strike(self):
        """Poll for the PM Chainlink strike price until it becomes available."""
        STRIKE_POLL_INTERVAL = 3  # seconds
        STRIKE_MAX_ATTEMPTS = 20  # ~60 seconds max

        event_start = self._pm_data.get("event_start_time", "")
        end_time = self._pm_data.get("end_time", "")
        if not event_start:
            return

        for attempt in range(1, STRIKE_MAX_ATTEMPTS + 1):
            if not self._running:
                return
            await asyncio.sleep(STRIKE_POLL_INTERVAL)

            try:
                strike = await asyncio.to_thread(
                    fetch_pm_strike_price, event_start, end_time
                )
                if strike is not None:
                    self._pm_data["floor_strike"] = strike
                    log.info("PM strike price available: $%.2f (attempt %d)", strike, attempt)
                    await self._push_update(force=True)
                    return
            except Exception as e:
                log.debug("PM strike poll error: %s", e)

        log.warning("PM strike price not available after %d attempts", STRIKE_MAX_ATTEMPTS)

    def _seconds_until_next_window(self) -> float:
        """Calculate seconds until the next 15-min window boundary."""
        now = datetime.now(timezone.utc)
        minute = (now.minute // 15) * 15
        window_start = now.replace(minute=minute, second=0, microsecond=0)
        next_window = window_start + timedelta(minutes=15)
        secs = max(0, (next_window - now).total_seconds())
        log.debug("_seconds_until_next_window: now=%s cur_window=%s next=%s wait=%.1fs",
                  now.strftime("%H:%M:%S.%f")[:12],
                  window_start.strftime("%H:%M:%S"),
                  next_window.strftime("%H:%M:%S"), secs)
        return secs

    async def _window_roll_loop(self):
        """
        Sleep until the current 15-min window ends, then fetch new contracts
        from both platforms in parallel. Retries with tight interval if a
        contract isn't available yet.
        """
        while self._running:
          try:
            wait = self._seconds_until_next_window()
            now = datetime.now(timezone.utc)
            target = now + timedelta(seconds=wait)
            log.info("ROLL TIMER: sleeping %.1fs until %s (now=%s, cur_slug=%s)",
                     wait, target.strftime("%H:%M:%S.%f")[:12],
                     now.strftime("%H:%M:%S.%f")[:12], self._current_slug)
            # Sleep until boundary + 100ms buffer to avoid computing slug
            # a few ms before the boundary (negative drift race condition)
            await asyncio.sleep(wait + 0.1)

            if not self._running:
                break

            wake_time = datetime.now(timezone.utc)
            log.info("ROLL WAKE: woke at %s (target was %s, drift=%.3fs)",
                     wake_time.strftime("%H:%M:%S.%f")[:12],
                     target.strftime("%H:%M:%S.%f")[:12],
                     (wake_time - target).total_seconds())

            new_slug = _current_15m_slug()
            log.info("ROLL START: %s -> %s", self._current_slug, new_slug)
            self._current_slug = new_slug

            # Save old tickers for comparison, clear PM data (slug-validated)
            # Keep Kalshi data visible until new contract arrives since
            # Kalshi can be slow to transition between windows
            old_ks_ticker = self._kalshi_ticker
            old_pm_tokens = list(self._pm_token_ids)
            self._pm_data = None
            self._pm_token_ids = []
            self._rolling = True

            pm_ok = False
            ks_ok = False
            roll_start = time.monotonic()

            for attempt in range(1, self.ROLL_MAX_RETRIES + 1):
                if not self._running:
                    break

                attempt_start = time.monotonic()
                log.debug("ROLL attempt %d/%d (pm_ok=%s ks_ok=%s)",
                          attempt, self.ROLL_MAX_RETRIES, pm_ok, ks_ok)

                # Fetch both platforms in parallel
                coros = []
                if not pm_ok:
                    coros.append(("pm", asyncio.to_thread(fetch_polymarket_btc_15m)))
                if not ks_ok:
                    coros.append(("ks", asyncio.to_thread(fetch_kalshi_btc_15m)))

                results = await asyncio.gather(
                    *(c for _, c in coros), return_exceptions=True
                )
                fetch_elapsed = time.monotonic() - attempt_start

                for (label, _), result in zip(coros, results):
                    if isinstance(result, Exception):
                        log.warning("ROLL %s exception (attempt %d, %.0fms): %s",
                                    label, attempt, fetch_elapsed * 1000, result)
                        continue

                    if label == "pm":
                        got_slug = result.get("slug", "") if result else ""
                        log.debug("ROLL PM response: slug=%s (want=%s) error=%s strike=%s tokens=%d",
                                  got_slug, new_slug, result.get("error") if result else "null",
                                  result.get("floor_strike") if result else "null",
                                  len(result.get("token_ids", [])) if result else 0)
                        if result and not result.get("error") and got_slug == new_slug:
                            self._pm_data = result
                            self._pm_token_ids = result.get("token_ids", [])
                            self._pm_condition_id = result.get("condition_id", "")
                            pm_ok = True
                            self._mark_pm_recv()
                            log.info("ROLL PM ready (attempt %d, %.0fms)", attempt, fetch_elapsed * 1000)
                    elif label == "ks":
                        got_ticker = result.get("ticker", "") if result else ""
                        log.debug("ROLL KS response: ticker=%s (old=%s) error=%s strike=%s close=%s",
                                  got_ticker, old_ks_ticker,
                                  result.get("error") if result else "null",
                                  result.get("floor_strike") if result else "null",
                                  result.get("close_time", "") if result else "null")
                        if result and not result.get("error") and got_ticker and got_ticker != old_ks_ticker:
                            self._kalshi_data = result
                            self._kalshi_ticker = got_ticker
                            ks_ok = True
                            self._mark_ks_recv()
                            log.info("ROLL KS ready (attempt %d, %.0fms, ticker=%s)",
                                     attempt, fetch_elapsed * 1000, got_ticker)

                if pm_ok and ks_ok:
                    break

                log.debug("ROLL attempt %d incomplete, retrying in %.1fs", attempt, self.ROLL_RETRY_INTERVAL)
                await self._push_update(force=True)
                await asyncio.sleep(self.ROLL_RETRY_INTERVAL)

            self._rolling = False
            roll_elapsed = time.monotonic() - roll_start

            if not pm_ok and not ks_ok:
                log.warning("ROLL FAILED: neither platform returned new contract after %d attempts (%.0fms)",
                            self.ROLL_MAX_RETRIES, roll_elapsed)
            elif not pm_ok:
                log.warning("ROLL PARTIAL: PM failed to return new contract (KS ok) after %.0fms", roll_elapsed)
            elif not ks_ok:
                log.warning("ROLL PARTIAL: KS failed to return new contract (PM ok) after %.0fms", roll_elapsed)
            else:
                log.info("ROLL DONE: pm_ok=%s ks_ok=%s total=%.0fms", pm_ok, ks_ok, roll_elapsed * 1000)

            # Update WS subscriptions in-place, fall back to reconnect
            if pm_ok:
                # Try dynamic swap on market channel
                swapped = await self._pm_swap_tokens(old_pm_tokens, self._pm_token_ids)
                if not swapped:
                    log.info("PM WS swap failed, falling back to reconnect")
                    self._pm_reconnect.set()
                # Always reconnect user channel (condition_id changed)
                self._pm_user_reconnect.set()
            if ks_ok:
                # Try update_subscription on existing WS (no reconnect needed)
                swapped = await self._kalshi_update_subscription(old_ks_ticker, self._kalshi_ticker)
                if not swapped:
                    # Fallback: signal full reconnect
                    log.info("KS WS update_subscription failed, falling back to reconnect")
                    self._ks_reconnect.set()

            await self._push_update(force=True)

            # Strike price may not be available immediately at window open.
            # Retry fetching it until the Chainlink opening price is recorded.
            if pm_ok and not self._pm_data.get("floor_strike"):
                log.info("STRIKE POLL: floor_strike missing, starting poll")
                await self._poll_pm_strike()
            elif pm_ok:
                log.info("STRIKE OK: $%.2f", self._pm_data.get("floor_strike", 0))

          except asyncio.CancelledError:
            raise
          except Exception as exc:
            log.error("ROLL LOOP CRASHED: %s — will retry next window", exc, exc_info=True)
            self._rolling = False
            # Don't break — sleep until next window and try again
