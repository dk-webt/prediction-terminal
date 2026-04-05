#!/usr/bin/env python3
"""
Live aligned oracle spread monitor.

Connects to BRTI (6-exchange CF Benchmarks replication) and Chainlink RTDS,
aligns prices into 1-second bins, and prints each aligned tick.

Usage:
  python3 scripts/oracle_spread_live.py
  python3 scripts/oracle_spread_live.py --no-header   # skip column header
  python3 scripts/oracle_spread_live.py --adf 30       # ADF every 30s (default: 60s)
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import COINBASE_CDP_API_KEY, COINBASE_CDP_API_SECRET
from clients.brti_tracker import BRTITracker
from clients.oracle_model import SpreadAnalyzer

PM_RTDS_URL = "wss://ws-live-data.polymarket.com"

# ── Alignment state ─────────────────────────────────────────────────────────

_brti_pending: dict | None = None      # {price, local_ts, server_ts}
_chainlink_pending: dict | None = None  # {price, local_ts, server_ts}
_last_bin: int = 0
_tick_count: int = 0
_analyzer = SpreadAnalyzer(window_s=1200)  # 20-min rolling window
_last_adf_time: float = 0.0
_adf_interval: float = 60.0  # seconds between ADF prints


def _try_pair():
    """If both oracles reported in the same 1s bin, print aligned tick."""
    global _brti_pending, _chainlink_pending, _last_bin, _tick_count

    if not _brti_pending or not _chainlink_pending:
        return

    brti_ts = _brti_pending["server_ts"] if _brti_pending["server_ts"] > 0 else _brti_pending["local_ts"]
    cl_ts = _chainlink_pending["server_ts"] if _chainlink_pending["server_ts"] > 0 else _chainlink_pending["local_ts"]

    brti_bin = int(brti_ts)
    cl_bin = int(cl_ts)

    if abs(brti_bin - cl_bin) > 0:
        return

    # Same bin — emit
    if brti_bin == _last_bin:
        return  # already printed this bin
    _last_bin = brti_bin
    _tick_count += 1

    spread = _brti_pending["price"] - _chainlink_pending["price"]
    ts_str = datetime.fromtimestamp(brti_bin, tz=timezone.utc).strftime("%H:%M:%S")
    brti_lat = (_brti_pending["local_ts"] - _brti_pending["server_ts"]) * 1000 if _brti_pending["server_ts"] > 0 else 0
    cl_lat = (_chainlink_pending["local_ts"] - _chainlink_pending["server_ts"]) * 1000 if _chainlink_pending["server_ts"] > 0 else 0

    print(
        f"{ts_str}  "
        f"BRTI: ${_brti_pending['price']:>10,.2f}  "
        f"CL: ${_chainlink_pending['price']:>10,.2f}  "
        f"SPREAD: ${spread:>+8.2f}  "
        f"Latency_BRTI: {brti_lat:>4.0f}ms  "
        f"Latency_Chainlink: {cl_lat:>4.0f}ms  "
        f"#{_tick_count}",
        flush=True,
    )

    # Feed the spread analyzer
    _analyzer.add_tick(brti_bin, spread)

    # Periodic ADF + OU calibration
    _maybe_print_stats()

    _brti_pending = None
    _chainlink_pending = None


def _maybe_print_stats():
    """Print ADF + OU results if enough time has passed."""
    global _last_adf_time
    now = time.time()
    if now - _last_adf_time < _adf_interval:
        return
    _last_adf_time = now

    # ── ADF test (20-min window) ──
    adf = _analyzer.compute_adf()
    if adf is None:
        span = _analyzer.window_span_s
        print(
            f"  ┌─ ADF: insufficient data ({_analyzer.n_raw} raw ticks, {span}s span, "
            f"need ≥60 filled observations)",
            flush=True,
        )
        return

    status = "\033[32mSTATIONARY ✓\033[0m" if adf.is_stationary else "\033[31mNON-STATIONARY ✗\033[0m"
    print(
        f"  ┌─ ADF TEST ({adf.n_obs} obs, {adf.n_raw} raw, {adf.fill_pct:.0f}% filled, "
        f"{_analyzer.window_span_s}s window)",
        flush=True,
    )
    print(
        f"  │  Statistic: {adf.statistic:>8.4f}  "
        f"p-value: {adf.pvalue:.6f}  "
        f"→ {status}",
        flush=True,
    )
    print(
        f"  │  Critical: 1%={adf.critical_values.get('1%', 0):.4f}  "
        f"5%={adf.critical_values.get('5%', 0):.4f}  "
        f"10%={adf.critical_values.get('10%', 0):.4f}",
        flush=True,
    )

    # ── OU calibration (10-min window) ──
    ou = _analyzer.compute_ou(window_s=600)
    if ou is None:
        print(
            f"  └─ OU: insufficient data or b out of range",
            flush=True,
        )
        return

    hl_color = "\033[32m" if ou.half_life_s < 300 else "\033[33m" if ou.half_life_s < 600 else "\033[31m"
    print(
        f"  ├─ OU PARAMS (10-min window, {ou.n_obs} obs)",
        flush=True,
    )
    print(
        f"  │  θ (theta):    {ou.theta:.6f}/s  "
        f"(speed of reversion)",
        flush=True,
    )
    print(
        f"  │  μ (mu):      ${ou.mu:+.4f}  "
        f"(long-term equilibrium spread)",
        flush=True,
    )
    print(
        f"  │  σ (sigma):   ${ou.sigma:.4f}/√s  "
        f"(spread volatility)",
        flush=True,
    )
    print(
        f"  │  Half-life:   {hl_color}{ou.half_life_s:.1f}s\033[0m  "
        f"({ou.half_life_s/60:.1f} min)",
        flush=True,
    )
    print(
        f"  └─ AR(1): a={ou.a:.6f}  b={ou.b:.6f}  "
        f"std(ε)=${ou.residual_std:.4f}",
        flush=True,
    )


# ── BRTI callback ───────────────────────────────────────────────────────────

def on_brti_update(value, ts, server_ts=0.0):
    global _brti_pending
    _brti_pending = {"price": value, "local_ts": ts, "server_ts": server_ts}
    _try_pair()


# ── Chainlink RTDS stream ──────────────────────────────────────────────────

async def rtds_loop():
    import websockets

    while True:
        try:
            async with websockets.connect(PM_RTDS_URL, ping_interval=None, close_timeout=5) as ws:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "subscriptions": [
                        {"topic": "crypto_prices_chainlink", "type": "*"},
                    ],
                }))
                print("RTDS: connected, subscribed to chainlink btc/usd", flush=True)

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        print("RTDS: inactivity timeout, reconnecting...", flush=True)
                        break

                    if not raw or raw == "PONG":
                        continue

                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        continue

                    if not isinstance(msg, dict):
                        continue

                    payload = msg.get("payload")
                    if not isinstance(payload, dict):
                        continue

                    if payload.get("symbol", "") != "btc/usd":
                        continue

                    price = payload.get("value")
                    if price is None:
                        continue
                    try:
                        price = float(price)
                    except (TypeError, ValueError):
                        continue

                    local_ts = time.time()
                    server_ts = 0.0
                    cl_ts = payload.get("timestamp")
                    if cl_ts:
                        try:
                            server_ts = int(cl_ts) / 1000.0
                        except (TypeError, ValueError):
                            pass

                    global _chainlink_pending
                    _chainlink_pending = {"price": price, "local_ts": local_ts, "server_ts": server_ts}
                    _try_pair()

                    # Send PING
                    try:
                        await ws.send("PING")
                    except Exception:
                        break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"RTDS: error {e}, reconnecting in 3s...", flush=True)
            await asyncio.sleep(3)


# ── Main ────────────────────────────────────────────────────────────────────

async def main():
    global _adf_interval
    show_header = "--no-header" not in sys.argv

    # Parse --adf N (interval in seconds)
    if "--adf" in sys.argv:
        idx = sys.argv.index("--adf")
        if idx + 1 < len(sys.argv):
            try:
                _adf_interval = float(sys.argv[idx + 1])
            except ValueError:
                pass

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    if show_header:
        print("Oracle Spread Live — BRTI (6-exchange) vs Chainlink (PM RTDS)")
        print("Aligned to 1-second bins. Latencies = local receive − server timestamp")
        print("-" * 120)
        print(
            f"{'TIME':8s}  "
            f"{'BRTI':>14s}  "
            f"{'CHAINLINK':>14s}  "
            f"{'SPREAD':>10s}  "
            f"{'Latency_BRTI':>16s}  "
            f"{'Latency_Chainlink':>20s}  "
            f"{'TICK':>5s}"
        )
        print("-" * 120)

    tracker = BRTITracker(
        coinbase_api_key=COINBASE_CDP_API_KEY,
        coinbase_api_secret=COINBASE_CDP_API_SECRET,
        on_update=on_brti_update,
    )

    print("Starting BRTI tracker (6-exchange)...", flush=True)
    await tracker.start()

    rtds_task = asyncio.create_task(rtds_loop())

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        rtds_task.cancel()
        await tracker.stop()
        print(f"\nStopped. {_tick_count} aligned ticks recorded.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\nStopped. {_tick_count} aligned ticks recorded.")
