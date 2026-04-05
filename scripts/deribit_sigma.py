#!/usr/bin/env python3
"""
Fetch BTC implied volatility from Deribit and de-annualize to 15-minute sigma.

Sources:
  1. DVOL index (Deribit's own BTC volatility index)
  2. Shortest-dated BTC options (0DTE / near-expiry ATM)

Math:
  sigma_15m = IV_annual / sqrt(35040)
  where 35040 = (365.25 * 24 * 60) / 15  (15-min intervals per year)

Usage:
  python3 scripts/deribit_sigma.py              # one-shot fetch
  python3 scripts/deribit_sigma.py --loop 60    # refresh every 60s
  python3 scripts/deribit_sigma.py --json       # JSON output for piping
"""

import argparse
import asyncio
import json
import math
import sys
import time
from datetime import datetime, timezone

try:
    import aiohttp
except ImportError:
    print("pip install aiohttp", file=sys.stderr)
    sys.exit(1)

DERIBIT_API = "https://www.deribit.com/api/v2/public"
INTERVALS_PER_YEAR = 35_040  # (365.25 * 24 * 60) / 15
SQRT_INTERVALS = math.sqrt(INTERVALS_PER_YEAR)


async def fetch_dvol(session: aiohttp.ClientSession) -> dict | None:
    """Fetch DVOL (Deribit Volatility Index) for BTC."""
    url = f"{DERIBIT_API}/get_volatility_index_data"
    params = {
        "currency": "BTC",
        "resolution": 1,  # 1-second resolution, we just want the latest
        "start_timestamp": int((time.time() - 60) * 1000),
        "end_timestamp": int(time.time() * 1000),
    }
    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            result = data.get("result", {}).get("data", [])
            if not result:
                return None
            # Each entry: [timestamp, open, high, low, close]
            latest = result[-1]
            return {
                "source": "DVOL",
                "iv_annual_pct": latest[4],  # close value
                "timestamp_ms": latest[0],
            }
    except Exception as e:
        print(f"DVOL fetch error: {e}", file=sys.stderr)
        return None


async def fetch_0dte_iv(session: aiohttp.ClientSession) -> dict | None:
    """Fetch IV from the shortest-dated ATM BTC option on Deribit."""
    # Step 1: get BTC index price
    url = f"{DERIBIT_API}/get_index_price"
    try:
        async with session.get(url, params={"index_name": "btc_usd"}) as resp:
            data = await resp.json()
            btc_price = data["result"]["index_price"]
    except Exception as e:
        print(f"Index price fetch error: {e}", file=sys.stderr)
        return None

    # Step 2: get available instruments, find shortest expiry
    url = f"{DERIBIT_API}/get_instruments"
    params = {"currency": "BTC", "kind": "option", "expired": "false"}
    try:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            instruments = data.get("result", [])
    except Exception as e:
        print(f"Instruments fetch error: {e}", file=sys.stderr)
        return None

    if not instruments:
        return None

    # Find the nearest expiry
    now_ms = int(time.time() * 1000)
    instruments = [i for i in instruments if i["expiration_timestamp"] > now_ms]
    if not instruments:
        return None

    min_expiry = min(i["expiration_timestamp"] for i in instruments)

    # Filter to nearest expiry, calls only, find ATM (closest strike to spot)
    nearest = [
        i for i in instruments
        if i["expiration_timestamp"] == min_expiry and i["option_type"] == "call"
    ]
    if not nearest:
        return None

    atm = min(nearest, key=lambda i: abs(i["strike"] - btc_price))

    # Step 3: get ticker for the ATM option
    url = f"{DERIBIT_API}/ticker"
    try:
        async with session.get(url, params={"instrument_name": atm["instrument_name"]}) as resp:
            data = await resp.json()
            ticker = data.get("result", {})
    except Exception as e:
        print(f"Ticker fetch error: {e}", file=sys.stderr)
        return None

    iv = ticker.get("mark_iv")  # mark IV in percent
    if iv is None:
        return None

    hours_to_expiry = (min_expiry - now_ms) / (1000 * 3600)

    return {
        "source": f"0DTE ATM ({atm['instrument_name']})",
        "iv_annual_pct": iv,
        "strike": atm["strike"],
        "btc_price": btc_price,
        "hours_to_expiry": round(hours_to_expiry, 2),
        "timestamp_ms": ticker.get("timestamp", now_ms),
    }


def compute_sigma_15m(iv_annual_pct: float) -> dict:
    """Convert annualized IV (in %) to 15-minute sigma."""
    iv_annual = iv_annual_pct / 100.0
    sigma_15m = iv_annual / SQRT_INTERVALS
    # Also compute expected dollar move given a BTC price (optional)
    return {
        "iv_annual_pct": round(iv_annual_pct, 2),
        "iv_annual_decimal": round(iv_annual, 4),
        "sigma_15m_decimal": round(sigma_15m, 6),
        "sigma_15m_bps": round(sigma_15m * 10_000, 2),
    }


def format_output(dvol_data: dict | None, dte_data: dict | None, btc_price: float | None) -> str:
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines.append(f"=== Deribit BTC Sigma (15m) — {ts} ===\n")

    for label, data in [("DVOL Index", dvol_data), ("0DTE ATM Option", dte_data)]:
        if data is None:
            lines.append(f"  {label}: unavailable\n")
            continue

        sigma = compute_sigma_15m(data["iv_annual_pct"])
        lines.append(f"  {label}:")
        lines.append(f"    Source:          {data['source']}")
        lines.append(f"    Annual IV:       {sigma['iv_annual_pct']}%")
        lines.append(f"    15m Sigma:       {sigma['sigma_15m_decimal']:.6f}  ({sigma['sigma_15m_bps']} bps)")

        if btc_price:
            dollar_move = btc_price * sigma["sigma_15m_decimal"]
            lines.append(f"    15m 1-SD Move:   ${dollar_move:,.2f}  (at BTC ${btc_price:,.0f})")

        if "hours_to_expiry" in data:
            lines.append(f"    Hours to Expiry: {data['hours_to_expiry']}h")
            lines.append(f"    Strike:          ${data['strike']:,.0f}")

        lines.append("")

    return "\n".join(lines)


async def run_once(as_json: bool = False) -> dict:
    """Fetch both sources, compute sigma, return results."""
    async with aiohttp.ClientSession() as session:
        dvol_data, dte_data = await asyncio.gather(
            fetch_dvol(session),
            fetch_0dte_iv(session),
        )

    # Get BTC price from whichever source succeeded
    btc_price = None
    if dte_data:
        btc_price = dte_data.get("btc_price")

    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "btc_price": btc_price,
        "dvol": None,
        "dte_atm": None,
    }

    if dvol_data:
        result["dvol"] = {**dvol_data, **compute_sigma_15m(dvol_data["iv_annual_pct"])}
    if dte_data:
        result["dte_atm"] = {**dte_data, **compute_sigma_15m(dte_data["iv_annual_pct"])}

    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(format_output(dvol_data, dte_data, btc_price))

    return result


async def run_loop(interval: int, as_json: bool = False):
    """Continuously fetch and display sigma."""
    print(f"Polling Deribit every {interval}s (Ctrl+C to stop)\n")
    while True:
        await run_once(as_json)
        await asyncio.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Deribit BTC IV -> 15-min sigma")
    parser.add_argument("--loop", type=int, metavar="SEC", help="Refresh interval in seconds")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    try:
        if args.loop:
            asyncio.run(run_loop(args.loop, args.json))
        else:
            asyncio.run(run_once(args.json))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
