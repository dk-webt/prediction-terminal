#!/usr/bin/env python3
"""
Live Chainlink BTC/USD price stream via Polymarket RTDS.

Usage:
  python3 scripts/chainlink_live.py
  python3 scripts/chainlink_live.py --json    # one JSON object per line
"""

import asyncio
import json
import sys
import time
from datetime import datetime, timezone

PM_RTDS_URL = "wss://ws-live-data.polymarket.com"


async def main():
    import websockets

    as_json = "--json" in sys.argv
    count = 0

    if not as_json:
        print("Chainlink BTC/USD — via Polymarket RTDS")
        print(f"{'TIME':10s}  {'PRICE':>14s}  {'SERVER_TS':>12s}  {'LATENCY':>10s}  {'#':>5s}")
        print("-" * 60)

    while True:
        try:
            async with websockets.connect(PM_RTDS_URL, ping_interval=None, close_timeout=5) as ws:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "subscriptions": [
                        {"topic": "crypto_prices_chainlink", "type": "*"},
                    ],
                }))

                while True:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        if not as_json:
                            print("  [timeout 30s, reconnecting...]", flush=True)
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

                    count += 1
                    latency_ms = (local_ts - server_ts) * 1000 if server_ts > 0 else 0

                    if as_json:
                        print(json.dumps({
                            "price": price,
                            "local_ts": round(local_ts, 3),
                            "server_ts": round(server_ts, 3),
                            "latency_ms": round(latency_ms, 0),
                        }), flush=True)
                    else:
                        ts_str = datetime.fromtimestamp(local_ts, tz=timezone.utc).strftime("%H:%M:%S")
                        srv_str = datetime.fromtimestamp(server_ts, tz=timezone.utc).strftime("%H:%M:%S") if server_ts > 0 else "?"
                        print(
                            f"{ts_str}  ${price:>12,.2f}  {srv_str:>12s}  {latency_ms:>8.0f}ms  #{count}",
                            flush=True,
                        )

                    try:
                        await ws.send("PING")
                    except Exception:
                        break

        except asyncio.CancelledError:
            raise
        except Exception as e:
            if not as_json:
                print(f"  [error: {e}, reconnecting in 3s...]", flush=True)
            await asyncio.sleep(3)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
