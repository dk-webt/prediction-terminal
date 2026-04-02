"""
BRTI Tracker — Replicates the CME CF Bitcoin Real Time Index (BRTI).

Connects to 5 constituent exchange L2 order book WebSocket feeds:
  Coinbase, Kraken, Bitstamp, Gemini, Crypto.com

Computes the BRTI once per second using the official CF Benchmarks methodology:
  1. Maintain local L2 order books per exchange
  2. Erroneous data detection (exclude if mid deviates >5% from median)
  3. Staleness exclusion (>30s old data dropped)
  4. Dynamic order size cap (winsorized mean + 5σ)
  5. Consolidated order book with size caps
  6. Mid-price volume curve at spacing s=1 BTC
  7. Utilized depth v_T where midSV ≤ 0.5% (D parameter)
  8. Exponential weighting: λ = 1/(0.3 * v_T)
  9. BRTI = Σ midPV(v) * (1/NF) * λ * e^(-λv)

Reference: https://docs.cfbenchmarks.com/CME%20CF%20Real%20Time%20Indices%20Methodology.pdf
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import websockets

log = logging.getLogger(__name__)

# ── BRTI Parameters (from CF Benchmarks methodology §6) ─────────────────────

SPACING = 1              # s = 1 BTC for Bitcoin RTI
DEVIATION_FROM_MID = 0.005  # D = 0.5%
ERRONEOUS_DATA_PCT = 0.05   # 5% — exclude exchange if mid deviates this much
ERRONEOUS_REENTRY_PCT = 0.025  # 50% of 5% = 2.5% to re-enter
STALENESS_THRESHOLD = 30.0     # seconds — drop exchange if book older than this
WINSORIZE_TRIM_PCT = 0.01     # k = floor(0.01 * n_T)
SIZE_CAP_SIGMA_MULT = 5       # C_T = winsorized_mean + 5σ

# ── Exchange Definitions ─────────────────────────────────────────────────────

EXCHANGES = ["coinbase", "kraken", "bitstamp", "gemini", "crypto_com"]


@dataclass
class OrderBook:
    """Local L2 order book mirror for one exchange."""
    bids: dict[float, float] = field(default_factory=dict)  # price -> size
    asks: dict[float, float] = field(default_factory=dict)  # price -> size
    last_update: float = 0.0

    def mid_price(self) -> float | None:
        if not self.bids or not self.asks:
            return None
        return (max(self.bids) + min(self.asks)) / 2.0

    def is_stale(self, now: float) -> bool:
        return (now - self.last_update) > STALENESS_THRESHOLD

    def is_crossed(self) -> bool:
        if not self.bids or not self.asks:
            return False
        return max(self.bids) >= min(self.asks)


class BRTITracker:
    """
    Replicates the CME CF Bitcoin Real Time Index.

    Usage:
        tracker = BRTITracker(
            coinbase_api_key=...,      # CDP key for Advanced Trade WS (stable)
            coinbase_api_secret=...,   # EC PEM private key from CDP
        )
        await tracker.start()  # connects to all exchanges, starts computing

    If no Coinbase CDP key is provided, falls back to the public Exchange WS.

    The computed BRTI value is available via:
        tracker.brti_value   — latest computed value (float or None)
        tracker.brti_time    — timestamp of last computation
        tracker.on_update    — callback(brti_value, timestamp) called each second
    """

    def __init__(
        self,
        coinbase_api_key: str | None = None,
        coinbase_api_secret: str | None = None,
        on_update=None,
    ):
        self.coinbase_api_key = coinbase_api_key
        self.coinbase_api_secret = coinbase_api_secret
        self.on_update = on_update

        # Per-exchange order books
        self.books: dict[str, OrderBook] = {ex: OrderBook() for ex in EXCHANGES}

        # Erroneous data tracking: exchanges currently flagged
        self._flagged: set[str] = set()

        # Output
        self.brti_value: float | None = None
        self.brti_time: float = 0.0
        self.settlement_buffer: list[float] = []  # rolling 60s of BRTI values

        # Control
        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ── Public API ───────────────────────────────────────────────────────────

    async def start(self):
        """Start all exchange feeds and the computation loop."""
        self._running = True
        self._tasks = [
            asyncio.create_task(self._feed_coinbase(), name="feed_coinbase"),
            asyncio.create_task(self._feed_kraken(), name="feed_kraken"),
            asyncio.create_task(self._feed_bitstamp(), name="feed_bitstamp"),
            asyncio.create_task(self._feed_gemini(), name="feed_gemini"),
            asyncio.create_task(self._feed_crypto_com(), name="feed_crypto_com"),
            asyncio.create_task(self._compute_loop(), name="brti_compute"),
        ]
        log.info("BRTI tracker started — 5 exchange feeds + compute loop")

    async def stop(self):
        """Stop all feeds and computation."""
        self._running = False
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        log.info("BRTI tracker stopped")

    def get_settlement_price(self) -> float | None:
        """Average of last 60 BRTI values (Kalshi settlement methodology)."""
        if len(self.settlement_buffer) < 60:
            return None
        return float(np.mean(self.settlement_buffer[-60:]))

    def get_status(self) -> dict:
        """Return status of all exchange feeds."""
        now = time.time()
        status = {}
        for ex in EXCHANGES:
            book = self.books[ex]
            mid = book.mid_price()
            status[ex] = {
                "connected": book.last_update > 0,
                "mid_price": round(mid, 2) if mid else None,
                "age_s": round(now - book.last_update, 1) if book.last_update > 0 else None,
                "stale": book.is_stale(now),
                "flagged": ex in self._flagged,
                "bid_levels": len(book.bids),
                "ask_levels": len(book.asks),
            }
        status["brti"] = self.brti_value
        status["brti_time"] = self.brti_time
        status["active_exchanges"] = sum(
            1 for ex in EXCHANGES
            if not self.books[ex].is_stale(now) and ex not in self._flagged
        )
        return status

    # ── BRTI Computation (runs once per second) ──────────────────────────────

    async def _compute_loop(self):
        """Compute BRTI once per second."""
        while self._running:
            try:
                t0 = time.time()
                value = self._compute_brti()
                if value is not None:
                    self.brti_value = value
                    self.brti_time = t0
                    self.settlement_buffer.append(value)
                    # Keep only last 120 values (2 min buffer, need 60 for settlement)
                    if len(self.settlement_buffer) > 120:
                        self.settlement_buffer = self.settlement_buffer[-120:]
                    if self.on_update:
                        try:
                            self.on_update(value, t0)
                        except Exception:
                            log.exception("on_update callback error")
                elapsed = time.time() - t0
                await asyncio.sleep(max(0, 1.0 - elapsed))
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("BRTI compute error")
                await asyncio.sleep(1.0)

    def _compute_brti(self) -> float | None:
        """Full BRTI calculation per CF Benchmarks methodology."""
        now = time.time()

        # Step 1: Collect non-stale, non-erroneous exchange books
        valid_exchanges = self._get_valid_exchanges(now)
        if len(valid_exchanges) < 1:
            log.warning("BRTI: no valid exchanges available")
            return None

        # Step 2: Run erroneous data detection
        valid_exchanges = self._filter_erroneous(valid_exchanges)
        if len(valid_exchanges) < 1:
            log.warning("BRTI: all exchanges flagged as erroneous")
            return None

        # Step 3: Compute dynamic order size cap
        cap = self._compute_size_cap(valid_exchanges)

        # Step 4: Build consolidated order book with size caps
        cons_bids, cons_asks = self._build_consolidated_book(valid_exchanges, cap)
        if not cons_bids or not cons_asks:
            log.warning("BRTI: empty consolidated book")
            return None

        # Step 5: Compute price-volume curves and BRTI
        return self._compute_index(cons_bids, cons_asks)

    def _get_valid_exchanges(self, now: float) -> list[str]:
        """Return exchanges that are not stale and have valid books."""
        valid = []
        for ex in EXCHANGES:
            book = self.books[ex]
            if book.last_update == 0:
                continue
            if book.is_stale(now):
                log.debug("BRTI: %s stale (%.1fs)", ex, now - book.last_update)
                continue
            if not book.bids or not book.asks:
                continue
            if book.is_crossed():
                log.debug("BRTI: %s book is crossed", ex)
                continue
            valid.append(ex)
        return valid

    def _filter_erroneous(self, exchanges: list[str]) -> list[str]:
        """
        §5.3 Potentially Erroneous Data detection.
        Exclude exchanges whose mid-price deviates >5% from the median.
        """
        if len(exchanges) <= 1:
            return exchanges

        mids = {}
        for ex in exchanges:
            mid = self.books[ex].mid_price()
            if mid is not None and mid > 0:
                mids[ex] = mid

        if len(mids) <= 1:
            return list(mids.keys())

        median_mid = float(np.median(list(mids.values())))
        valid = []

        for ex, mid in mids.items():
            deviation = abs(mid - median_mid) / median_mid

            if ex in self._flagged:
                # Re-entry: must come back within 50% of threshold
                if deviation < ERRONEOUS_REENTRY_PCT:
                    self._flagged.discard(ex)
                    log.info("BRTI: %s re-entered (deviation %.2f%%)", ex, deviation * 100)
                    valid.append(ex)
                else:
                    log.debug("BRTI: %s still flagged (deviation %.2f%%)", ex, deviation * 100)
            else:
                if deviation > ERRONEOUS_DATA_PCT:
                    self._flagged.add(ex)
                    log.warning(
                        "BRTI: %s flagged erroneous (mid=%.2f, median=%.2f, dev=%.2f%%)",
                        ex, mid, median_mid, deviation * 100,
                    )
                else:
                    valid.append(ex)

        return valid

    def _compute_size_cap(self, exchanges: list[str]) -> float:
        """
        §4.1.3 Dynamic Order Size Cap.
        C_T = winsorized_mean + 5 * winsorized_std
        """
        # Collect all order sizes from valid exchanges
        sizes = []
        for ex in exchanges:
            book = self.books[ex]
            # Ask sizes within 5% of best ask
            if book.asks:
                best_ask = min(book.asks)
                threshold = best_ask * 1.05
                ac = [(p, s) for p, s in book.asks.items() if p <= threshold]
                sizes.extend(s for _, s in ac[:50])
            # Bid sizes within 5% of best bid
            if book.bids:
                best_bid = max(book.bids)
                threshold = best_bid * 0.95
                bc = [(p, s) for p, s in book.bids.items() if p >= threshold]
                sizes.extend(s for _, s in bc[:50])

        if len(sizes) < 3:
            return float("inf")  # No cap if too few data points

        sizes_arr = np.array(sorted(sizes))
        n = len(sizes_arr)
        k = int(0.01 * n)  # Eq. 4d

        # Trimmed mean (Eq. 4e)
        if k > 0 and n > 2 * k:
            trimmed = sizes_arr[k:n - k]
        else:
            trimmed = sizes_arr
        trimmed_mean = float(np.mean(trimmed))

        # Winsorized sample (Eq. 4f)
        winsorized = sizes_arr.copy()
        if k > 0:
            winsorized[:k] = sizes_arr[k]
            winsorized[n - k:] = sizes_arr[n - k - 1]
        winsorized_mean = float(np.mean(winsorized))
        winsorized_std = float(np.std(winsorized, ddof=1)) if n > 1 else 0.0

        # Eq. 5: C_T = s' + 5σ
        cap = winsorized_mean + SIZE_CAP_SIGMA_MULT * winsorized_std
        return max(cap, 0.001)  # Floor to avoid zero cap

    def _build_consolidated_book(
        self, exchanges: list[str], cap: float
    ) -> tuple[dict[float, float], dict[float, float]]:
        """
        Merge order books from all valid exchanges.
        Cap individual order sizes at C_T.
        """
        cons_bids: dict[float, float] = defaultdict(float)
        cons_asks: dict[float, float] = defaultdict(float)

        for ex in exchanges:
            book = self.books[ex]
            for price, size in book.bids.items():
                cons_bids[price] += min(size, cap)
            for price, size in book.asks.items():
                cons_asks[price] += min(size, cap)

        return dict(cons_bids), dict(cons_asks)

    def _compute_index(
        self, cons_bids: dict[float, float], cons_asks: dict[float, float]
    ) -> float | None:
        """
        Compute the BRTI value from the consolidated order book.

        Implements Eq. 1a-1f, Eq. 2, Eq. 3 from the methodology.
        """
        # Sort asks ascending by price, bids descending by price
        ask_levels = sorted(cons_asks.items(), key=lambda x: x[0])  # ascending
        bid_levels = sorted(cons_bids.items(), key=lambda x: x[0], reverse=True)  # descending

        if not ask_levels or not bid_levels:
            return None

        # Build cumulative volume -> price curves
        # askPV(v): price to buy v BTC (walking up the ask book)
        ask_cum = []  # [(cumulative_volume, price)]
        cum_vol = 0.0
        for price, size in ask_levels:
            cum_vol += size
            ask_cum.append((cum_vol, price))

        # bidPV(v): price to sell v BTC (walking down the bid book)
        bid_cum = []  # [(cumulative_volume, price)]
        cum_vol = 0.0
        for price, size in bid_levels:
            cum_vol += size
            bid_cum.append((cum_vol, price))

        max_vol = min(ask_cum[-1][0], bid_cum[-1][0]) if ask_cum and bid_cum else 0
        if max_vol < SPACING:
            # Not enough depth for even one spacing unit
            # Fall back to simple mid-price
            best_ask = ask_levels[0][0]
            best_bid = bid_levels[0][0]
            return (best_ask + best_bid) / 2.0

        def interp_price(cum_list: list[tuple[float, float]], volume: float) -> float:
            """Get the marginal price at a given cumulative volume."""
            for cum_v, price in cum_list:
                if cum_v >= volume:
                    return price
            return cum_list[-1][1]

        # Compute midPV and midSV at each spacing step (Eq. 1a-1f)
        # v goes from SPACING, 2*SPACING, ... up to max_vol
        steps = []
        v = SPACING
        while v <= max_vol:
            ask_pv = interp_price(ask_cum, v)
            bid_pv = interp_price(bid_cum, v)
            mid_pv = (ask_pv + bid_pv) / 2.0
            mid_sv = (ask_pv / mid_pv) - 1.0 if mid_pv > 0 else float("inf")
            steps.append((v, mid_pv, mid_sv))
            v += SPACING

        if not steps:
            return None

        # Eq. 2: Utilized depth v_T = max(v_i) where midSV(v_i) <= D
        utilized_depth = SPACING  # minimum
        for v, mid_pv, mid_sv in steps:
            if mid_sv <= DEVIATION_FROM_MID:
                utilized_depth = v
            else:
                break  # Once spread exceeds D, stop

        # Lambda = 1 / (0.3 * v_T) — §6.1
        lam = 1.0 / (0.3 * utilized_depth)

        # Normalization factor NF: sum of λ * e^(-λv) for v in {s, 2s, ..., v_T}
        volumes_in_range = [v for v, _, _ in steps if v <= utilized_depth]
        if not volumes_in_range:
            volumes_in_range = [SPACING]

        weights = np.array([lam * np.exp(-lam * v) for v in volumes_in_range])
        nf = float(np.sum(weights))

        if nf <= 0:
            return None

        # Eq. 3: BRTI = Σ midPV(v) * (1/NF) * λ * e^(-λv)
        brti = 0.0
        for v in volumes_in_range:
            mid_pv = None
            for sv, mp, _ in steps:
                if sv == v:
                    mid_pv = mp
                    break
            if mid_pv is None:
                continue
            w = lam * np.exp(-lam * v) / nf
            brti += mid_pv * w

        return round(brti, 2)

    # ── Exchange Feed: Coinbase ──────────────────────────────────────────────

    async def _feed_coinbase(self):
        """
        Coinbase Advanced Trade WebSocket L2 feed.
        wss://advanced-trade-ws.coinbase.com — requires CDP API key + JWT auth.
        """
        if not self.coinbase_api_key or not self.coinbase_api_secret:
            log.error(
                "Coinbase CDP key not configured — set COINBASE_CDP_API_KEY and "
                "COINBASE_CDP_API_SECRET in .env. Coinbase feed disabled."
            )
            return

        # Validate the key can generate a JWT before entering the reconnect loop
        test_jwt = self._coinbase_jwt()
        if not test_jwt:
            log.error("Coinbase JWT generation failed — check your CDP key format. Coinbase feed disabled.")
            return

        url = "wss://advanced-trade-ws.coinbase.com"

        while self._running:
            try:
                async with websockets.connect(
                    url, ping_interval=20, max_size=2**24,  # 16MB — L2 snapshots can be large
                ) as ws:
                    jwt_token = self._coinbase_jwt()
                    sub = {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channel": "level2",
                        "jwt": jwt_token,
                    }
                    await ws.send(json.dumps(sub))
                    log.info("Coinbase L2 connected (authenticated)")

                    # JWT expires in 120s — refresh subscription before that
                    last_jwt = time.time()

                    async for raw in ws:
                        if not self._running:
                            break

                        # Re-subscribe with fresh JWT every 90s
                        if time.time() - last_jwt > 90:
                            jwt_token = self._coinbase_jwt()
                            if jwt_token:
                                resub = {
                                    "type": "subscribe",
                                    "product_ids": ["BTC-USD"],
                                    "channel": "level2",
                                    "jwt": jwt_token,
                                }
                                await ws.send(json.dumps(resub))
                                last_jwt = time.time()
                                log.debug("Coinbase JWT refreshed")

                        try:
                            msg = json.loads(raw)
                            self._handle_coinbase_msg(msg)
                        except Exception:
                            log.exception("Coinbase parse error")
            except asyncio.CancelledError:
                break
            except websockets.ConnectionClosedError as e:
                log.warning("Coinbase WS closed: code=%s reason=%s — reconnecting in 2s", e.code, e.reason)
                await asyncio.sleep(2)
            except websockets.ConnectionClosedOK as e:
                log.warning("Coinbase WS closed OK: code=%s reason=%s — reconnecting in 2s", e.code, e.reason)
                await asyncio.sleep(2)
            except Exception as e:
                log.warning("Coinbase WS error: %s — reconnecting in 2s", e)
                await asyncio.sleep(2)

    def _coinbase_jwt(self) -> str | None:
        """
        Generate a JWT for Coinbase CDP auth using cryptography lib.
        Handles both EC (ES256) and ED25519 (EdDSA) key formats from CDP.
        """
        try:
            import base64 as b64
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.backends import default_backend

            now = int(time.time())

            def _b64url(data: bytes) -> str:
                return b64.urlsafe_b64encode(data).rstrip(b"=").decode()

            # Normalize the PEM key — .env may have literal \n or be on one line
            secret = self.coinbase_api_secret
            if "\\n" in secret:
                secret = secret.replace("\\n", "\n")

            # Try loading as-is first (full PEM)
            key_bytes = secret.encode()
            private_key = None

            # Try PEM private key (EC or ED25519)
            for loader in [serialization.load_pem_private_key, serialization.load_der_private_key]:
                try:
                    private_key = loader(key_bytes, password=None, backend=default_backend())
                    break
                except (ValueError, TypeError):
                    continue

            if private_key is None:
                # Maybe it's just the base64 body without PEM headers — try EC then ED25519
                raw = secret.replace("\n", "").replace(" ", "")
                for header in [
                    "-----BEGIN EC PRIVATE KEY-----",
                    "-----BEGIN PRIVATE KEY-----",
                ]:
                    footer = header.replace("BEGIN", "END")
                    pem = f"{header}\n{raw}\n{footer}"
                    try:
                        private_key = serialization.load_pem_private_key(
                            pem.encode(), password=None, backend=default_backend()
                        )
                        break
                    except (ValueError, TypeError):
                        continue

            if private_key is None:
                log.error("Could not parse Coinbase CDP private key — check format in .env")
                return None

            # Detect key type and choose algorithm
            from cryptography.hazmat.primitives.asymmetric import ec, ed25519

            if isinstance(private_key, ec.EllipticCurvePrivateKey):
                alg = "ES256"
                header = {"alg": alg, "typ": "JWT", "kid": self.coinbase_api_key, "nonce": hex(now)}
                payload = {
                    "sub": self.coinbase_api_key,
                    "iss": "cdp",
                    "aud": ["public_websocket_api"],
                    "nbf": now,
                    "exp": now + 120,
                }
                header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
                payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
                signing_input = f"{header_b64}.{payload_b64}".encode()

                from cryptography.hazmat.primitives import hashes
                from cryptography.hazmat.primitives.asymmetric import utils as asym_utils
                der_sig = private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
                r, s = asym_utils.decode_dss_signature(der_sig)
                raw_sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
                sig_b64 = _b64url(raw_sig)

            elif isinstance(private_key, ed25519.Ed25519PrivateKey):
                alg = "EdDSA"
                header = {"alg": alg, "typ": "JWT", "kid": self.coinbase_api_key, "nonce": hex(now)}
                payload = {
                    "sub": self.coinbase_api_key,
                    "iss": "cdp",
                    "aud": ["public_websocket_api"],
                    "nbf": now,
                    "exp": now + 120,
                }
                header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
                payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode())
                signing_input = f"{header_b64}.{payload_b64}".encode()

                raw_sig = private_key.sign(signing_input)
                sig_b64 = _b64url(raw_sig)
            else:
                log.error("Unsupported Coinbase CDP key type: %s", type(private_key).__name__)
                return None

            return f"{header_b64}.{payload_b64}.{sig_b64}"

        except Exception:
            log.exception("Coinbase JWT generation failed")
            return None

    def _handle_coinbase_msg(self, msg: dict):
        """Process Coinbase Advanced Trade level2 snapshot/update messages."""
        channel = msg.get("channel")
        if channel != "l2_data":
            return

        events = msg.get("events", [])
        book = self.books["coinbase"]

        for event in events:
            etype = event.get("type")
            updates = event.get("updates", [])

            if etype == "snapshot":
                book.bids.clear()
                book.asks.clear()

            for u in updates:
                side = u.get("side", "").lower()
                price = float(u.get("price_level", 0))
                size = float(u.get("new_quantity", 0))
                if price <= 0:
                    continue

                target = book.bids if side == "bid" else book.asks
                if size == 0:
                    target.pop(price, None)
                else:
                    target[price] = size

        book.last_update = time.time()

    # ── Exchange Feed: Kraken ────────────────────────────────────────────────

    async def _feed_kraken(self):
        """
        Kraken WebSocket v2 L2 book feed.
        wss://ws.kraken.com/v2 — public, no auth.
        """
        url = "wss://ws.kraken.com/v2"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    sub = {
                        "method": "subscribe",
                        "params": {
                            "channel": "book",
                            "symbol": ["BTC/USD"],
                            "depth": 100,
                        },
                    }
                    await ws.send(json.dumps(sub))
                    log.info("Kraken L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            self._handle_kraken_msg(msg)
                        except Exception:
                            log.exception("Kraken parse error")
            except asyncio.CancelledError:
                break
            except Exception:
                log.warning("Kraken WS disconnected, reconnecting in 2s")
                await asyncio.sleep(2)

    def _handle_kraken_msg(self, msg: dict):
        """Process Kraken book snapshot/update messages."""
        channel = msg.get("channel")
        if channel != "book":
            return

        data = msg.get("data", [])
        if not data:
            return

        book = self.books["kraken"]
        mtype = msg.get("type", "")

        for entry in data:
            if mtype == "snapshot":
                book.bids.clear()
                book.asks.clear()
                for b in entry.get("bids", []):
                    price = float(b.get("price", 0))
                    qty = float(b.get("qty", 0))
                    if price > 0:
                        book.bids[price] = qty
                for a in entry.get("asks", []):
                    price = float(a.get("price", 0))
                    qty = float(a.get("qty", 0))
                    if price > 0:
                        book.asks[price] = qty
            elif mtype == "update":
                for b in entry.get("bids", []):
                    price = float(b.get("price", 0))
                    qty = float(b.get("qty", 0))
                    if price <= 0:
                        continue
                    if qty == 0:
                        book.bids.pop(price, None)
                    else:
                        book.bids[price] = qty
                for a in entry.get("asks", []):
                    price = float(a.get("price", 0))
                    qty = float(a.get("qty", 0))
                    if price <= 0:
                        continue
                    if qty == 0:
                        book.asks.pop(price, None)
                    else:
                        book.asks[price] = qty

        book.last_update = time.time()

    # ── Exchange Feed: Bitstamp ──────────────────────────────────────────────

    async def _feed_bitstamp(self):
        """
        Bitstamp WebSocket order book feed.
        wss://ws.bitstamp.net — public, no auth.
        """
        url = "wss://ws.bitstamp.net"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    sub = {
                        "event": "bts:subscribe",
                        "data": {"channel": "order_book_btcusd"},
                    }
                    await ws.send(json.dumps(sub))
                    log.info("Bitstamp L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            self._handle_bitstamp_msg(msg)
                        except Exception:
                            log.exception("Bitstamp parse error")
            except asyncio.CancelledError:
                break
            except Exception:
                log.warning("Bitstamp WS disconnected, reconnecting in 2s")
                await asyncio.sleep(2)

    def _handle_bitstamp_msg(self, msg: dict):
        """
        Process Bitstamp order book messages.
        Bitstamp sends full snapshots on the order_book channel (not diffs).
        """
        event = msg.get("event")
        if event != "data":
            return
        channel = msg.get("channel", "")
        if "order_book" not in channel:
            return

        data = msg.get("data")
        if not data:
            return

        # Parse data if it's a string
        if isinstance(data, str):
            data = json.loads(data)

        book = self.books["bitstamp"]
        book.bids.clear()
        book.asks.clear()

        for b in data.get("bids", []):
            price = float(b[0])
            size = float(b[1])
            if price > 0 and size > 0:
                book.bids[price] = size

        for a in data.get("asks", []):
            price = float(a[0])
            size = float(a[1])
            if price > 0 and size > 0:
                book.asks[price] = size

        book.last_update = time.time()

    # ── Exchange Feed: Gemini ────────────────────────────────────────────────

    async def _feed_gemini(self):
        """
        Gemini WebSocket v2 market data L2 feed.
        wss://api.gemini.com/v2/marketdata — public, no auth.
        """
        url = "wss://api.gemini.com/v2/marketdata"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    sub = {
                        "type": "subscribe",
                        "subscriptions": [
                            {
                                "name": "l2",
                                "symbols": ["BTCUSD"],
                            }
                        ],
                    }
                    await ws.send(json.dumps(sub))
                    log.info("Gemini L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            self._handle_gemini_msg(msg)
                        except Exception:
                            log.exception("Gemini parse error")
            except asyncio.CancelledError:
                break
            except Exception:
                log.warning("Gemini WS disconnected, reconnecting in 2s")
                await asyncio.sleep(2)

    def _handle_gemini_msg(self, msg: dict):
        """Process Gemini L2 snapshot/update messages."""
        mtype = msg.get("type")
        if mtype not in ("l2_updates",):
            return

        book = self.books["gemini"]
        changes = msg.get("changes", [])

        for change in changes:
            # Each change is [side, price, quantity]
            if len(change) < 3:
                continue
            side = change[0].lower()  # "buy" or "sell"
            price = float(change[1])
            size = float(change[2])
            if price <= 0:
                continue

            if side == "buy":
                if size == 0:
                    book.bids.pop(price, None)
                else:
                    book.bids[price] = size
            elif side == "sell":
                if size == 0:
                    book.asks.pop(price, None)
                else:
                    book.asks[price] = size

        book.last_update = time.time()

    # ── Exchange Feed: Crypto.com ────────────────────────────────────────────

    async def _feed_crypto_com(self):
        """
        Crypto.com Exchange WebSocket market data feed.
        wss://stream.crypto.com/exchange/v1/market — public, no auth.
        """
        url = "wss://stream.crypto.com/exchange/v1/market"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    sub = {
                        "id": 1,
                        "method": "subscribe",
                        "params": {
                            "channels": ["book.BTC_USD.50"],
                        },
                    }
                    await ws.send(json.dumps(sub))
                    log.info("Crypto.com L2 connected")

                    async for raw in ws:
                        if not self._running:
                            break
                        try:
                            msg = json.loads(raw)
                            self._handle_crypto_com_msg(msg)
                        except Exception:
                            log.exception("Crypto.com parse error")
            except asyncio.CancelledError:
                break
            except Exception:
                log.warning("Crypto.com WS disconnected, reconnecting in 2s")
                await asyncio.sleep(2)

    def _handle_crypto_com_msg(self, msg: dict):
        """Process Crypto.com book snapshot/update messages."""
        result = msg.get("result")
        if not result:
            return

        channel = result.get("channel", "")
        if "book" not in channel:
            return

        data = result.get("data", [])
        if not data:
            return

        book = self.books["crypto_com"]

        for entry in data:
            # Crypto.com sends full snapshots on the book channel
            bids_data = entry.get("bids", [])
            asks_data = entry.get("asks", [])

            if bids_data:
                book.bids.clear()
                for b in bids_data:
                    # [price, quantity, number_of_orders]
                    price = float(b[0])
                    size = float(b[1])
                    if price > 0 and size > 0:
                        book.bids[price] = size

            if asks_data:
                book.asks.clear()
                for a in asks_data:
                    price = float(a[0])
                    size = float(a[1])
                    if price > 0 and size > 0:
                        book.asks[price] = size

        book.last_update = time.time()


# ── Standalone runner ────────────────────────────────────────────────────────

async def _main():
    """Run the BRTI tracker standalone for testing."""
    import sys
    sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
    from config import COINBASE_CDP_API_KEY, COINBASE_CDP_API_SECRET

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    def on_update(value, ts):
        from datetime import datetime, timezone
        t = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M:%S")
        settlement = tracker.get_settlement_price()
        settle_str = f"  settle={settlement:.2f}" if settlement else ""
        status = tracker.get_status()
        active = status["active_exchanges"]
        print(f"[{t}] BRTI: ${value:,.2f}  ({active}/5 exchanges){settle_str}")

    tracker = BRTITracker(
        coinbase_api_key=COINBASE_CDP_API_KEY,
        coinbase_api_secret=COINBASE_CDP_API_SECRET,
        on_update=on_update,
    )

    await tracker.start()

    # Print status every 10 seconds
    try:
        while True:
            await asyncio.sleep(10)
            status = tracker.get_status()
            parts = []
            for ex in EXCHANGES:
                s = status[ex]
                mid = f"${s['mid_price']:,.2f}" if s["mid_price"] else "---"
                flag = " FLAGGED" if s["flagged"] else ""
                stale = " STALE" if s["stale"] else ""
                parts.append(f"  {ex:12s}: {mid}{flag}{stale} ({s['bid_levels']}b/{s['ask_levels']}a)")
            print("─── Exchange Status ───")
            print("\n".join(parts))
            print(f"  BRTI: ${status['brti']:,.2f}" if status["brti"] else "  BRTI: computing...")
            print()
    except KeyboardInterrupt:
        pass
    finally:
        await tracker.stop()


if __name__ == "__main__":
    asyncio.run(_main())
