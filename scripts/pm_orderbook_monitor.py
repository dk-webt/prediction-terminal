#!/usr/bin/env python3
"""
Polymarket Orderbook Monitor — Isolated WS test

Connects to PM CLOB WebSocket for BTC 15-min tokens and logs every
message with timestamps. Use this to verify:
  1. How frequently the WS sends updates for BTC 15-min markets
  2. Whether bid/ask values match what you see on PM UI
  3. Whether PONG responses are arriving (heartbeat health)

Usage:
  python3 scripts/pm_orderbook_monitor.py

Compare the printed bid/ask with the PM UI in real-time.
"""

import asyncio
import json
import time

import requests
import websockets


async def monitor():
    # Find active BTC 15-min tokens
    print("Finding active BTC 15-min market...")
    resp = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={"slug_contains": "btc-updown-15m", "active": "true", "limit": 1},
        timeout=10,
    )
    events = resp.json()
    if not events:
        print("No active BTC 15-min market found")
        return

    market = events[0]["markets"][0]
    tokens = json.loads(market.get("clobTokenIds", "[]"))
    slug = events[0].get("slug", "")
    print(f"Slug: {slug}")
    print(f"UP token:   {tokens[0][:30]}...")
    print(f"DOWN token: {tokens[1][:30]}...")
    print()

    # Also fetch current REST orderbook for comparison
    for i, label in enumerate(["UP", "DOWN"]):
        ob = requests.get(
            "https://clob.polymarket.com/book",
            params={"token_id": tokens[i]},
            timeout=5,
        ).json()
        best_bid = max((float(b["price"]) for b in ob.get("bids", [])), default=0)
        best_ask = min((float(a["price"]) for a in ob.get("asks", [])), default=0)
        print(f"REST {label}: bid={best_bid} ask={best_ask}")
    print()

    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    print(f"Connecting to {url}...")
    async with websockets.connect(url, ping_interval=None, close_timeout=5) as ws:
        await ws.send(json.dumps({
            "assets_ids": tokens,
            "type": "market",
            "custom_feature_enabled": True,
        }))
        print("Subscribed. Monitoring all messages (Ctrl+C to stop):\n")

        msg_count = 0
        start = time.time()
        ping_interval = 8  # match btc_watcher's PM_PING_INTERVAL
        last_ping = time.time()

        while True:
            # Send PING if needed
            if time.time() - last_ping > ping_interval:
                await ws.send("PING")
                last_ping = time.time()

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
            except asyncio.TimeoutError:
                elapsed = time.time() - start
                print(f"  [{elapsed:.0f}s] (no message for 2s)")
                continue

            elapsed = time.time() - start
            ts = time.strftime("%H:%M:%S")

            if raw == "PONG":
                print(f"  [{ts}] [{elapsed:.1f}s] PONG")
                continue

            msg_count += 1
            try:
                parsed = json.loads(raw)
                msgs = parsed if isinstance(parsed, list) else [parsed]
                for m in msgs:
                    if not isinstance(m, dict):
                        continue
                    et = m.get("event_type", "unknown")

                    if et == "price_change":
                        for pc in m.get("price_changes", []):
                            aid = pc.get("asset_id", "")
                            side = "UP" if aid == tokens[0] else "DOWN" if aid == tokens[1] else "?"
                            print(
                                f"  [{ts}] [{elapsed:.1f}s] #{msg_count} price_change "
                                f"{side}: bid={pc.get('best_bid')} ask={pc.get('best_ask')} "
                                f"price={pc.get('price')} size={pc.get('size')} side={pc.get('side')}"
                            )

                    elif et == "best_bid_ask":
                        aid = m.get("asset_id", "")
                        side = "UP" if aid == tokens[0] else "DOWN" if aid == tokens[1] else "?"
                        print(
                            f"  [{ts}] [{elapsed:.1f}s] #{msg_count} best_bid_ask "
                            f"{side}: bid={m.get('best_bid')} ask={m.get('best_ask')}"
                        )

                    elif et == "book":
                        aid = m.get("asset_id", "")
                        side = "UP" if aid == tokens[0] else "DOWN" if aid == tokens[1] else "?"
                        bids = m.get("bids", [])
                        asks = m.get("asks", [])
                        bb = max((float(b["price"]) for b in bids), default=0)
                        ba = min((float(a["price"]) for a in asks), default=0)
                        print(
                            f"  [{ts}] [{elapsed:.1f}s] #{msg_count} book SNAPSHOT "
                            f"{side}: bid={bb} ask={ba} ({len(bids)}b/{len(asks)}a)"
                        )

                    elif et == "last_trade_price":
                        aid = m.get("asset_id", "")
                        side = "UP" if aid == tokens[0] else "DOWN" if aid == tokens[1] else "?"
                        print(
                            f"  [{ts}] [{elapsed:.1f}s] #{msg_count} last_trade "
                            f"{side}: price={m.get('price')} size={m.get('size')}"
                        )

                    else:
                        print(f"  [{ts}] [{elapsed:.1f}s] #{msg_count} {et}")

            except Exception as e:
                print(f"  [{ts}] [{elapsed:.1f}s] #{msg_count} parse error: {e}")


if __name__ == "__main__":
    try:
        asyncio.run(monitor())
    except KeyboardInterrupt:
        print("\nStopped.")
