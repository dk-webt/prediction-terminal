#!/usr/bin/env python3
"""
Poll the Polymarket crypto-price API at the next 15-min window boundary
to measure how quickly the strike price appears and settles.

Usage: python3 scripts/strike_poll_test.py
  - Calculates the next 15-min window automatically
  - Starts polling 5 seconds before the window opens
  - Polls every 0.5s for 60 seconds after window start
  - Prints timestamp, elapsed, and price for each response
"""

import time
import requests
from datetime import datetime, timezone, timedelta

CRYPTO_PRICE_URL = "https://polymarket.com/api/crypto/crypto-price"


def next_15m_window():
    """Return (start, end) datetimes for the next 15-min window."""
    now = datetime.now(timezone.utc)
    minute = (now.minute // 15) * 15
    current_start = now.replace(minute=minute, second=0, microsecond=0)
    next_start = current_start + timedelta(minutes=15)
    next_end = next_start + timedelta(minutes=15)
    return next_start, next_end


def fetch_strike(start_iso: str, end_iso: str) -> dict:
    """Fetch the strike price, return full response."""
    try:
        resp = requests.get(
            CRYPTO_PRICE_URL,
            params={
                "symbol": "BTC",
                "eventStartTime": start_iso,
                "variant": "fifteen",
                "endDate": end_iso,
            },
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


def main():
    next_start, next_end = next_15m_window()
    start_iso = next_start.strftime("%Y-%m-%dT%H:%M:%SZ")
    end_iso = next_end.strftime("%Y-%m-%dT%H:%M:%SZ")

    now = datetime.now(timezone.utc)
    wait_secs = (next_start - now).total_seconds() - 5  # start 5s early

    print(f"Next window:  {start_iso} → {end_iso}")
    print(f"Now:          {now.strftime('%H:%M:%S.%f')[:-3]} UTC")
    print(f"Waiting {wait_secs:.1f}s until 5s before window start...")
    print()

    if wait_secs > 0:
        time.sleep(wait_secs)

    print(f"{'Elapsed':>8}  {'UTC Time':>15}  {'openPrice':>14}  {'changed':>8}  Response")
    print("-" * 90)

    t0 = next_start.timestamp()  # reference = window start
    last_price = None
    poll_count = 0

    # Poll for 60 seconds after window start (plus the 5s before)
    while True:
        now_ts = time.time()
        elapsed = now_ts - t0
        if elapsed > 60:
            break

        data = fetch_strike(start_iso, end_iso)
        poll_count += 1
        price = data.get("openPrice")
        error = data.get("error")

        changed = ""
        if price is not None and price != last_price:
            changed = "  ← NEW" if last_price is not None else "  ← FIRST"
            last_price = price

        utc_now = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        price_str = f"${price:,.2f}" if isinstance(price, (int, float)) else str(price)

        if error:
            print(f"{elapsed:>+7.1f}s  {utc_now:>15}  {'ERROR':>14}  {changed:>8}  {error}")
        else:
            print(f"{elapsed:>+7.1f}s  {utc_now:>15}  {price_str:>14}{changed}")

        time.sleep(0.5)

    print()
    print(f"Done. {poll_count} polls over 65s.")
    print(f"Final strike price: ${last_price:,.2f}" if last_price else "No price received.")


if __name__ == "__main__":
    main()
