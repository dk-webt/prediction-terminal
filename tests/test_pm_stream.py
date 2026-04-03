#!/usr/bin/env python3
"""
PM Bid/Ask Stream Test Harness
==============================
Standalone test that reuses the actual RedundantWSPool and PM data code
from the production terminal. No API keys needed.

Shows live prices, uptime tracking, and per-source logging to diagnose
issues like price alternation and post-roll uptime drops.

Usage:
    python3 test_pm_stream.py              # normal
    python3 test_pm_stream.py --pool-size 1  # single connection (baseline)
    python3 test_pm_stream.py -v           # verbose (show every price update)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timezone, timedelta
import calendar

# Ensure project root is on sys.path so `clients.*` imports work
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Reuse production code
from clients.ws_pool import RedundantWSPool, WSPoolConfig
from clients.btc_watcher import (
    PM_WS_URL,
    fetch_polymarket_btc_15m,
    _current_15m_slug,
    _try_find_pm_btc_15m,
    _fetch_ob,
)

log = logging.getLogger("pm_test")


class PMStreamTester:
    """Minimal PM stream tester reusing production pool + uptime logic."""

    STALE_THRESHOLD = 5.0
    PING_INTERVAL = 8
    RECV_TIMEOUT = 20
    RECONNECT_BASE = 0.5
    RECONNECT_MAX = 5
    OB_REFRESH_INTERVAL = 5

    def __init__(self, pool_size: int = 2, verbose: bool = False):
        self._pool_size = pool_size
        self._verbose = verbose
        self._pool: RedundantWSPool | None = None
        self._running = False

        # PM state
        self._pm_data: dict | None = None
        self._pm_token_ids: list[str] = []
        self._current_slug: str = ""

        # Uptime tracking (mirrors btc_watcher.py exactly)
        self._pm_window_start: float = 0.0
        self._pm_live_accum: float = 0.0
        self._pm_last_live_mark: float = 0.0
        self._pm_is_stale: bool = True
        self._pm_last_recv: float = 0.0
        self._pm_stale_logged = False

        # Stats
        self._msg_count = 0
        self._price_updates = 0
        self._last_up_ask: float | None = None
        self._last_down_ask: float | None = None
        self._price_flips = 0
        self._sources: dict[str, int] = {}  # event_type -> count

    # ── Uptime tracking (copied from btc_watcher.py) ──────────────────────────

    def _mark_pm_recv(self):
        now = time.monotonic()
        if not self._pm_is_stale and self._pm_last_live_mark > 0:
            self._pm_live_accum += now - self._pm_last_live_mark
        self._pm_last_live_mark = now
        was_stale = self._pm_is_stale
        self._pm_is_stale = False
        self._pm_last_recv = now
        if was_stale and self._pm_window_start > 0:
            log.info("RECOVERED: data flowing again")
        self._pm_stale_logged = False

    def _check_staleness(self):
        if self._pm_last_recv <= 0:
            return
        now = time.monotonic()
        age = now - self._pm_last_recv
        if age > self.STALE_THRESHOLD and not self._pm_stale_logged:
            log.warning("STALE: no PM data for %.1fs", age)
            self._pm_stale_logged = True
            if not self._pm_is_stale and self._pm_last_live_mark > 0:
                self._pm_live_accum += self._pm_last_recv - self._pm_last_live_mark
            self._pm_is_stale = True

    def _get_uptime_pct(self) -> float | None:
        if self._pm_window_start <= 0:
            return None
        now = time.monotonic()
        elapsed = now - self._pm_window_start
        if elapsed <= 0:
            return None
        live = self._pm_live_accum
        if not self._pm_is_stale and self._pm_last_live_mark > 0:
            live += now - self._pm_last_live_mark
        return min(100.0, (live / elapsed) * 100.0)

    def _reset_uptime(self):
        self._pm_window_start = time.monotonic()
        self._pm_live_accum = 0.0
        self._pm_last_live_mark = 0.0
        self._pm_is_stale = True

    # ── Price application (mirrors btc_watcher.py) ────────────────────────────

    def _apply_pm_price(self, asset_id: str, best_bid: float | None, best_ask: float | None, source: str) -> bool:
        if not self._pm_data:
            return False

        tokens = self._pm_token_ids
        changed = False

        if len(tokens) >= 1 and asset_id == tokens[0]:
            label = "UP"
            if best_bid is not None and best_bid != self._pm_data.get("up_bid"):
                self._pm_data["up_bid"] = best_bid
                changed = True
            if best_ask is not None and best_ask != self._pm_data.get("up_ask"):
                old = self._pm_data.get("up_ask")
                self._pm_data["up_ask"] = best_ask
                changed = True
                if old is not None and abs(best_ask - old) > 0.05:
                    self._price_flips += 1
                    log.warning("PRICE JUMP: up_ask %.4f -> %.4f (delta=%.4f) source=%s",
                                old, best_ask, best_ask - old, source)
        elif len(tokens) >= 2 and asset_id == tokens[1]:
            label = "DOWN"
            if best_bid is not None and best_bid != self._pm_data.get("down_bid"):
                self._pm_data["down_bid"] = best_bid
                changed = True
            if best_ask is not None and best_ask != self._pm_data.get("down_ask"):
                old = self._pm_data.get("down_ask")
                self._pm_data["down_ask"] = best_ask
                changed = True
                if old is not None and abs(best_ask - old) > 0.05:
                    self._price_flips += 1
                    log.warning("PRICE JUMP: down_ask %.4f -> %.4f (delta=%.4f) source=%s",
                                old, best_ask, best_ask - old, source)
        else:
            if self._verbose:
                log.debug("IGNORED: asset_id=%s..%s not in current tokens", asset_id[:8], asset_id[-4:])
            return False

        if changed:
            self._price_updates += 1
            if self._verbose:
                log.info("PRICE [%s] bid=%.4f ask=%.4f src=%s",
                         label if 'label' in dir() else '?',
                         best_bid or 0, best_ask or 0, source)

        return changed

    # ── WS message handler (mirrors btc_watcher.py) ───────────────────────────

    async def _handle_pm_msg(self, raw: str):
        self._msg_count += 1

        if raw == "PONG":
            self._mark_pm_recv()
            return

        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return

        self._mark_pm_recv()
        messages = parsed if isinstance(parsed, list) else [parsed]

        for msg in messages:
            if not isinstance(msg, dict):
                continue

            event_type = msg.get("event_type", "unknown")
            self._sources[event_type] = self._sources.get(event_type, 0) + 1

            if event_type == "price_change":
                for pc in msg.get("price_changes", []):
                    asset_id = pc.get("asset_id", "")
                    if asset_id not in self._pm_token_ids:
                        log.warning("STALE TOKEN in price_change: %s..%s", asset_id[:8], asset_id[-4:])
                        continue
                    raw_bid = pc.get("best_bid")
                    raw_ask = pc.get("best_ask")
                    best_bid = float(raw_bid) if raw_bid is not None else None
                    best_ask = float(raw_ask) if raw_ask is not None else None
                    self._apply_pm_price(asset_id, best_bid, best_ask, "ws:price_change")

            elif event_type == "book" or (not event_type and "bids" in msg):
                asset_id = msg.get("asset_id", "")
                if asset_id not in self._pm_token_ids:
                    log.warning("STALE TOKEN in book: %s..%s", asset_id[:8], asset_id[-4:])
                    continue
                bids = msg.get("bids", [])
                asks = msg.get("asks", [])
                best_bid = max((float(b["price"]) for b in bids), default=None)
                best_ask = min((float(a["price"]) for a in asks), default=None)
                log.info("BOOK snapshot: asset=%s..%s bid=%s ask=%s depth=%d/%d",
                         asset_id[:8], asset_id[-4:], best_bid, best_ask, len(bids), len(asks))
                self._apply_pm_price(asset_id, best_bid, best_ask, f"ws:book({len(bids)}b/{len(asks)}a)")

            elif event_type == "best_bid_ask":
                asset_id = msg.get("asset_id", "")
                if asset_id not in self._pm_token_ids:
                    log.warning("STALE TOKEN in best_bid_ask: %s..%s", asset_id[:8], asset_id[-4:])
                    continue
                raw_bid = msg.get("best_bid")
                raw_ask = msg.get("best_ask")
                best_bid = float(raw_bid) if raw_bid is not None else None
                best_ask = float(raw_ask) if raw_ask is not None else None
                self._apply_pm_price(asset_id, best_bid, best_ask, "ws:best_bid_ask")

    # ── REST OB refresh (mirrors btc_watcher._pm_ob_refresh_loop) ─────────────

    async def _ob_refresh_loop(self):
        while self._running:
            try:
                await asyncio.sleep(self.OB_REFRESH_INTERVAL)
                if not self._pm_token_ids or not self._pm_data:
                    continue

                tokens = list(self._pm_token_ids)
                from concurrent.futures import ThreadPoolExecutor
                loop = asyncio.get_event_loop()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = []
                    for token in tokens[:2]:
                        results.append(await loop.run_in_executor(pool, _fetch_ob, token))

                changed = False
                if len(results) >= 1 and len(tokens) >= 1:
                    bid, ask = results[0]
                    old_ask = self._pm_data.get("up_ask")
                    if bid > 0:
                        changed |= self._apply_pm_price(tokens[0], bid, None, "rest:ob_up")
                    if ask > 0:
                        changed |= self._apply_pm_price(tokens[0], None, ask, "rest:ob_up")
                    if ask > 0 and old_ask and abs(ask - old_ask) > 0.05:
                        log.warning("REST vs WS divergence: up_ask rest=%.4f ws_was=%.4f", ask, old_ask)

                if len(results) >= 2 and len(tokens) >= 2:
                    bid, ask = results[1]
                    old_ask = self._pm_data.get("down_ask")
                    if bid > 0:
                        changed |= self._apply_pm_price(tokens[1], bid, None, "rest:ob_down")
                    if ask > 0:
                        changed |= self._apply_pm_price(tokens[1], None, ask, "rest:ob_down")
                    if ask > 0 and old_ask and abs(ask - old_ask) > 0.05:
                        log.warning("REST vs WS divergence: down_ask rest=%.4f ws_was=%.4f", ask, old_ask)

                if changed:
                    self._mark_pm_recv()

            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.debug("OB refresh error: %s", e)

    # ── Window roll ───────────────────────────────────────────────────────────

    async def _roll_loop(self):
        """Watch for 15-min window boundary and swap subscriptions."""
        while self._running:
            try:
                now = datetime.now(timezone.utc)
                minute = (now.minute // 15) * 15
                window_end = now.replace(minute=minute, second=0, microsecond=0) + timedelta(minutes=15)
                wait_secs = (window_end - now).total_seconds() + 2  # 2s buffer
                log.info("Next roll in %.0fs at %s", wait_secs, window_end.strftime("%H:%M:%S"))
                await asyncio.sleep(wait_secs)

                log.info("=" * 60)
                log.info("ROLLING: window boundary reached")
                self._print_status()

                old_tokens = list(self._pm_token_ids)
                old_slug = self._current_slug
                roll_start = time.monotonic()

                # Fetch new contract (retry loop like btc_watcher)
                pm_ok = False
                for attempt in range(1, 16):
                    new_slug = _current_15m_slug()
                    if new_slug == old_slug:
                        log.debug("ROLL attempt %d: slug unchanged, retrying...", attempt)
                        await asyncio.sleep(2)
                        continue

                    pm_data = fetch_polymarket_btc_15m()
                    if pm_data and not pm_data.get("error") and pm_data.get("slug") == new_slug:
                        self._pm_data = pm_data
                        self._pm_token_ids = pm_data.get("token_ids", [])
                        self._current_slug = new_slug
                        pm_ok = True
                        elapsed = (time.monotonic() - roll_start) * 1000
                        log.info("ROLL PM ready: attempt=%d elapsed=%.0fms slug=%s tokens=%d",
                                 attempt, elapsed, new_slug, len(self._pm_token_ids))
                        break

                    await asyncio.sleep(2)

                if not pm_ok:
                    log.warning("ROLL FAILED: could not fetch new PM contract after 15 attempts")
                    continue

                # Reset uptime AFTER roll completes (mirrors the fix)
                self._reset_uptime()
                self._mark_pm_recv()

                # Swap subscriptions on pool
                if self._pool:
                    new_tokens = list(self._pm_token_ids)
                    await self._pool.swap_subscriptions(
                        new_sub_fn=lambda: [json.dumps({
                            "assets_ids": new_tokens,
                            "operation": "subscribe",
                            "custom_feature_enabled": True,
                        })],
                        unsub_msgs=[json.dumps({"operation": "unsubscribe", "assets_ids": old_tokens})] if old_tokens else None,
                    )
                    log.info("ROLL: subscriptions swapped (old=%d tokens, new=%d tokens)",
                             len(old_tokens), len(new_tokens))

                log.info("ROLL COMPLETE: now tracking %s", self._current_slug)
                log.info("=" * 60)

            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ROLL error")
                await asyncio.sleep(5)

    # ── Status display ────────────────────────────────────────────────────────

    async def _status_loop(self):
        """Print status every 10 seconds."""
        while self._running:
            await asyncio.sleep(10)
            self._check_staleness()
            self._print_status()

    def _print_status(self):
        uptime = self._get_uptime_pct()
        up_ask = self._pm_data.get("up_ask", 0) if self._pm_data else 0
        up_bid = self._pm_data.get("up_bid", 0) if self._pm_data else 0
        down_ask = self._pm_data.get("down_ask", 0) if self._pm_data else 0
        down_bid = self._pm_data.get("down_bid", 0) if self._pm_data else 0

        elapsed = time.monotonic() - self._pm_window_start if self._pm_window_start > 0 else 0

        pool_health = self._pool.health if self._pool else {}
        conn_detail = " ".join(
            f"c{c['id']}:{c['messages']}msg/{c.get('last_recv_age','?')}s"
            for c in pool_health.get("connections", [])
        )

        log.info(
            "STATUS | up=%.2f/%.2f down=%.2f/%.2f | uptime=%.1f%% | msgs=%d prices=%d flips=%d | "
            "elapsed=%.0fs | pool_live=%s/%s | conns=[%s] | stale=%s | sources=%s",
            up_bid, up_ask, down_bid, down_ask,
            uptime or 0,
            self._msg_count, self._price_updates, self._price_flips,
            elapsed,
            pool_health.get("live", "?"), pool_health.get("total", "?"),
            conn_detail,
            self._pm_is_stale,
            json.dumps(self._sources),
        )

    # ── Main ──────────────────────────────────────────────────────────────────

    async def run(self):
        self._running = True

        # Fetch initial contract
        log.info("Fetching current PM BTC 15-min contract...")
        pm_data = fetch_polymarket_btc_15m()
        if not pm_data or pm_data.get("error"):
            log.error("Failed to fetch PM contract: %s", pm_data)
            return

        self._pm_data = pm_data
        self._pm_token_ids = pm_data.get("token_ids", [])
        self._current_slug = pm_data.get("slug", "")

        log.info("Contract: %s", self._current_slug)
        log.info("Token IDs: %s", [t[:12] + "..." for t in self._pm_token_ids])
        log.info("Initial: up_ask=%.4f up_bid=%.4f down_ask=%.4f down_bid=%.4f",
                 pm_data.get("up_ask", 0), pm_data.get("up_bid", 0),
                 pm_data.get("down_ask", 0), pm_data.get("down_bid", 0))
        log.info("Pool size: %d connections", self._pool_size)

        # Start uptime tracking
        self._reset_uptime()
        self._mark_pm_recv()

        # Start pool
        self._pool = RedundantWSPool(WSPoolConfig(
            name="pm_test",
            url=PM_WS_URL,
            pool_size=self._pool_size,
            subscribe_msgs=lambda: [json.dumps({
                "assets_ids": list(self._pm_token_ids),
                "type": "market",
                "custom_feature_enabled": True,
            })],
            ping_text="PING",
            ping_interval=self.PING_INTERVAL,
            recv_timeout=self.RECV_TIMEOUT,
            reconnect_base=self.RECONNECT_BASE,
            reconnect_max=self.RECONNECT_MAX,
            on_message=self._handle_pm_msg,
            dedup_key=lambda raw: None if raw == "PONG" else hashlib.md5(
                raw.encode(), usedforsecurity=False).hexdigest(),
        ))
        await self._pool.start()
        log.info("Pool started with %d connections", self._pool_size)

        # Run background tasks
        tasks = [
            asyncio.create_task(self._ob_refresh_loop()),
            asyncio.create_task(self._status_loop()),
            asyncio.create_task(self._roll_loop()),
        ]

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False
            if self._pool:
                await self._pool.stop()
            for t in tasks:
                t.cancel()
            log.info("Shutdown complete")


def main():
    parser = argparse.ArgumentParser(description="PM Bid/Ask Stream Test Harness")
    parser.add_argument("--pool-size", type=int, default=2, help="Number of redundant WS connections (default: 2)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Log every price update")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s.%(msecs)03d %(levelname)-5s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy libs
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)

    tester = PMStreamTester(pool_size=args.pool_size, verbose=args.verbose)
    try:
        asyncio.run(tester.run())
    except KeyboardInterrupt:
        log.info("Interrupted")


if __name__ == "__main__":
    main()
