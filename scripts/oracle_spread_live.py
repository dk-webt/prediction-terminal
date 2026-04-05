#!/usr/bin/env python3
"""
Live aligned oracle spread monitor.

Connects to BRTI (6-exchange CF Benchmarks replication) and Chainlink RTDS,
aligns prices into 1-second bins, and prints each aligned tick.

Usage:
  python3 scripts/oracle_spread_live.py                 # full output (strikes + all models, ADF every 10s)
  python3 scripts/oracle_spread_live.py --adf 30        # ADF/model output every 30s (default: 10s)
  python3 scripts/oracle_spread_live.py --no-strikes    # disable strike fetching / Model A / Model C
  python3 scripts/oracle_spread_live.py --no-header     # skip column header
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
from clients.deribit import DeribitPoller
from clients.oracle_model import (
    SpreadAnalyzer, model_a_both_platforms, model_c_joint, calibrate_copula,
)

PM_RTDS_URL = "wss://ws-live-data.polymarket.com"

# ── Alignment state ─────────────────────────────────────────────────────────

_brti_pending: dict | None = None      # {price, local_ts, server_ts}
_chainlink_pending: dict | None = None  # {price, local_ts, server_ts}
_last_bin: int = 0
_tick_count: int = 0
_analyzer = SpreadAnalyzer(window_s=1200)  # 20-min rolling window
_deribit: DeribitPoller | None = None       # initialized in main()
# Aligned price arrays for rolling correlation (Model C)
_aligned_brti: list[float] = []
_aligned_cl: list[float] = []
_MAX_ALIGNED = 1200  # keep 20 min of aligned prices
_last_adf_time: float = 0.0
_adf_interval: float = 10.0  # seconds between ADF/OU/Model A/C prints

# Current 15-min window strike prices (fetched at startup + each roll)
_ks_strike: float = 0.0
_pm_strike: float = 0.0
_window_end_ts: float = 0.0  # epoch seconds when current window expires


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

    # Track aligned prices for correlation (Model C)
    _aligned_brti.append(_brti_pending["price"])
    _aligned_cl.append(_chainlink_pending["price"])
    if len(_aligned_brti) > _MAX_ALIGNED:
        _aligned_brti.pop(0)
        _aligned_cl.pop(0)

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
        f"  │  AR(1): a={ou.a:.6f}  b={ou.b:.6f}  "
        f"std(ε)=${ou.residual_std:.4f}",
        flush=True,
    )

    # ── Model A: per-platform probabilities ──
    _print_model_a()


def _print_model_a():
    """Print Model A probabilities if we have all inputs."""
    sigma = _deribit.sigma_15m if _deribit else None
    if not sigma:
        print(f"  └─ MODEL A: waiting for Deribit IV...", flush=True)
        return

    # Need both oracle prices and strikes
    brti = _brti_pending["price"] if _brti_pending else None
    cl = _chainlink_pending["price"] if _chainlink_pending else None

    # Use last known prices if pending are cleared
    if not brti or not cl:
        latest = _analyzer._raw[-1] if _analyzer._raw else None
        if not latest:
            print(f"  └─ MODEL A: no oracle prices available", flush=True)
            return
        # We don't have individual prices from the analyzer, skip if not available
        print(f"  └─ MODEL A: σ_15m={sigma:.6f} ({_deribit.source}) — no strike data", flush=True)
        return

    if _ks_strike <= 0 or _pm_strike <= 0 or _window_end_ts <= 0:
        print(
            f"  └─ MODEL A: σ_15m={sigma:.6f} ({_deribit.source}) "
            f"— run with --strikes to enable probability calc",
            flush=True,
        )
        return

    # Compute tau (fraction of 15-min window remaining)
    now = time.time()
    window_duration = 15 * 60  # 15 minutes in seconds
    time_remaining = max(0, _window_end_ts - now)
    tau = time_remaining / window_duration
    tau = min(1.0, max(0.0, tau))

    result = model_a_both_platforms(
        brti_price=brti,
        chainlink_price=cl,
        ks_strike=_ks_strike,
        pm_strike=_pm_strike,
        tau=tau,
        sigma_15m=sigma,
    )

    ks = result["kalshi"]
    pm = result["polymarket"]

    tau_min = tau * 15
    print(
        f"  ├─ MODEL A (σ_15m={sigma:.6f}, τ={tau:.3f} = {tau_min:.1f}min left, {_deribit.source})",
        flush=True,
    )
    if ks:
        print(
            f"  │  KS:  P(above ${_ks_strike:,.0f}) = {ks.p_above:.4f}  "
            f"P(below) = {ks.p_below:.4f}  d2={ks.d2:+.4f}  "
            f"(BRTI=${brti:,.2f})",
            flush=True,
        )
    if pm:
        print(
            f"  │  PM:  P(above ${_pm_strike:,.0f}) = {pm.p_above:.4f}  "
            f"P(below) = {pm.p_below:.4f}  d2={pm.d2:+.4f}  "
            f"(CL=${cl:,.2f})",
            flush=True,
        )

    # ── Model C: joint probabilities ──
    if ks and pm:
        _print_model_c(ks.p_above, pm.p_above)
    else:
        print(f"  └─", flush=True)


def _print_model_c(p_ks_above: float, p_pm_above: float):
    """Print Model C joint outcome probabilities with calibrated copula."""
    import numpy as np_local

    cal = calibrate_copula(
        np_local.array(_aligned_brti),
        np_local.array(_aligned_cl),
    )

    if cal is None:
        print(f"  └─ MODEL C: insufficient data for copula ({len(_aligned_brti)} ticks)", flush=True)
        return

    # Compute for both strategies
    strat_a = model_c_joint(p_ks_above, p_pm_above, cal.rho, cal.nu, strategy="A")
    strat_b = model_c_joint(p_ks_above, p_pm_above, cal.rho, cal.nu, strategy="B")

    rho_color = "\033[32m" if cal.rho > 0.8 else "\033[33m" if cal.rho > 0.5 else "\033[31m"
    nu_color = "\033[33m" if cal.nu < 5 else "\033[0m"
    print(
        f"  ├─ MODEL C (τ={cal.kendall_tau:+.4f}, ρ={rho_color}{cal.rho:+.4f}\033[0m, "
        f"ν={nu_color}{cal.nu:.1f}\033[0m, LL={cal.log_likelihood:.1f}, {cal.n_obs} obs)",
        flush=True,
    )

    # Strategy A
    ll_color_a = "\033[32m" if strat_a.p_ll < 0.05 else "\033[33m" if strat_a.p_ll < 0.15 else "\033[31m"
    print(
        f"  │  Strat A (KS YES + PM NO):  "
        f"WW={strat_a.p_ww:.4f}  WL={strat_a.p_wl:.4f}  LW={strat_a.p_lw:.4f}  "
        f"LL={ll_color_a}{strat_a.p_ll:.4f}\033[0m",
        flush=True,
    )

    # Strategy B
    ll_color_b = "\033[32m" if strat_b.p_ll < 0.05 else "\033[33m" if strat_b.p_ll < 0.15 else "\033[31m"
    print(
        f"  └─ Strat B (KS NO  + PM YES): "
        f"WW={strat_b.p_ww:.4f}  WL={strat_b.p_wl:.4f}  LW={strat_b.p_lw:.4f}  "
        f"LL={ll_color_b}{strat_b.p_ll:.4f}\033[0m",
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

def _fetch_strikes():
    """Fetch current 15-min window strike prices and expiry from both platforms."""
    global _ks_strike, _pm_strike, _window_end_ts
    try:
        from clients.btc_watcher import fetch_btc_snapshot
        snap = fetch_btc_snapshot()
        ks = snap.get("kalshi") or {}
        pm = snap.get("polymarket") or {}

        _ks_strike = float(ks.get("floor_strike", 0) or 0)
        _pm_strike = float(pm.get("floor_strike", 0) or 0)

        # Parse window end time
        close_time = ks.get("close_time", "") or pm.get("end_time", "")
        if close_time:
            from datetime import datetime as dt, timezone as tz
            try:
                if close_time.endswith("Z"):
                    close_time = close_time[:-1] + "+00:00"
                _window_end_ts = dt.fromisoformat(close_time).timestamp()
            except Exception:
                pass

        print(
            f"Strikes: KS=${_ks_strike:,.0f}  PM=${_pm_strike:,.0f}  "
            f"Window ends: {datetime.fromtimestamp(_window_end_ts, tz=timezone.utc).strftime('%H:%M:%S') if _window_end_ts else '?'}",
            flush=True,
        )
    except Exception as e:
        print(f"Strike fetch error: {e}", flush=True)


async def _strike_refresh_loop():
    """Refresh strikes every 15 minutes (on window roll)."""
    while True:
        await asyncio.sleep(15 * 60)
        try:
            await asyncio.to_thread(_fetch_strikes)
        except Exception as e:
            print(f"Strike refresh error: {e}", flush=True)


async def main():
    global _adf_interval, _deribit
    show_header = "--no-header" not in sys.argv
    use_strikes = "--no-strikes" not in sys.argv  # strikes ON by default

    # Parse --adf N (interval in seconds, default 10)
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

    # Start Deribit IV poller (5-min interval)
    _deribit = DeribitPoller(interval_s=300)
    print("Starting Deribit IV poller...", flush=True)
    await _deribit.start()

    # Fetch initial strikes if requested
    if use_strikes:
        print("Fetching contract strike prices...", flush=True)
        await asyncio.to_thread(_fetch_strikes)

    tracker = BRTITracker(
        coinbase_api_key=COINBASE_CDP_API_KEY,
        coinbase_api_secret=COINBASE_CDP_API_SECRET,
        on_update=on_brti_update,
    )

    print("Starting BRTI tracker (6-exchange)...", flush=True)
    await tracker.start()

    rtds_task = asyncio.create_task(rtds_loop())
    strike_task = asyncio.create_task(_strike_refresh_loop()) if use_strikes else None

    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        rtds_task.cancel()
        if strike_task:
            strike_task.cancel()
        await tracker.stop()
        await _deribit.stop()
        print(f"\nStopped. {_tick_count} aligned ticks recorded.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print(f"\nStopped. {_tick_count} aligned ticks recorded.")
