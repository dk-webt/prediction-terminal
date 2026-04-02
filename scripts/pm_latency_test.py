#!/usr/bin/env python3
"""
Polymarket WebSocket Latency Test

Measures end-to-end latency to Polymarket's CLOB WebSocket from this server:
  1. ICMP ping to Cloudflare edge
  2. WebSocket connect time
  3. Confirms live data stream (real orderbook events)
  4. PING/PONG round-trip on confirmed-live connection
  5. REST keep-alive TTFB
  6. Cloudflare edge location

Usage:
  pip3 install websockets requests
  python3 scripts/pm_latency_test.py

Run from different server locations and compare results.
"""

import asyncio
import json
import subprocess
import time

import requests
import websockets


async def full_latency_test():
    print("=== Polymarket WebSocket Latency Test ===")
    print()

    # Step 0: Where are we?
    try:
        loc = requests.get("https://ipinfo.io/json", timeout=5).json()
        print(
            f'Server location: {loc.get("city")}, {loc.get("region")}, '
            f'{loc.get("country")} ({loc.get("org")})'
        )
    except Exception:
        print("Server location: unknown")
    print()

    # Step 1: ICMP ping to CF edge
    print("--- Step 1: ICMP ping to Cloudflare edge ---")
    try:
        r = subprocess.run(
            ["ping", "-c", "5", "ws-subscriptions-clob.polymarket.com"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in r.stdout.strip().split("\n")[-2:]:
            print(f"  {line}")
    except Exception:
        print("  (ping failed)")
    print()

    # Step 2: Get active tokens from top markets
    print("--- Step 2: Finding active markets ---")
    resp = requests.get(
        "https://gamma-api.polymarket.com/events",
        params={"active": "true", "limit": 10, "order": "volume24hr", "ascending": "false"},
        timeout=10,
    )
    events = resp.json()
    all_tokens = []
    for e in events:
        for m in e["markets"]:
            toks = json.loads(m.get("clobTokenIds", "[]"))
            all_tokens.extend(toks)
    print(f"  Subscribing to {len(all_tokens)} tokens from {len(events)} markets")
    print()

    # Step 3: Connect and subscribe
    print("--- Step 3: WebSocket connect + subscribe ---")
    url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
    t0 = time.time()
    async with websockets.connect(url, ping_interval=None, close_timeout=5) as ws:
        connect_ms = (time.time() - t0) * 1000
        print(f"  WS connected: {connect_ms:.0f}ms")

        await ws.send(
            json.dumps(
                {
                    "assets_ids": all_tokens,
                    "type": "market",
                    "custom_feature_enabled": True,
                }
            )
        )
        print(f"  Subscribed to {len(all_tokens)} tokens")
        print()

        # Step 4: Confirm live data
        print("--- Step 4: Confirming live data stream ---")
        data_count = 0
        t_start = time.time()
        while data_count < 3:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
            except asyncio.TimeoutError:
                print("  TIMEOUT: no data in 15s — market may be quiet")
                break
            if raw == "PONG":
                continue
            data_count += 1
            elapsed = (time.time() - t_start) * 1000
            try:
                parsed = json.loads(raw)
                msgs = parsed if isinstance(parsed, list) else [parsed]
                for m in msgs:
                    if isinstance(m, dict) and m.get("event_type"):
                        print(f'  Data msg #{data_count} [{elapsed:.0f}ms]: {m["event_type"]}')
                        break
            except Exception:
                print(f"  Data msg #{data_count} [{elapsed:.0f}ms]: (parse error)")

        if data_count >= 3:
            print("  Stream confirmed LIVE")
        else:
            print(f"  Only {data_count} data msgs received — proceeding anyway")
        print()

        # Drain any pending messages
        while True:
            try:
                await asyncio.wait_for(ws.recv(), timeout=0.1)
            except asyncio.TimeoutError:
                break

        # Step 5: PING/PONG measurement
        print("--- Step 5: PING/PONG round-trip (10 samples) ---")
        times = []
        for i in range(10):
            t0 = time.time()
            await ws.send("PING")
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=5)
                if raw == "PONG":
                    break
            elapsed = (time.time() - t0) * 1000
            times.append(elapsed)
            print(f"  ping {i + 1}: {elapsed:.1f}ms")

        print()

    # Step 6: REST timing (outside WS context)
    print("--- Step 6: REST keep-alive TTFB (10 samples) ---")
    session = requests.Session()
    session.get("https://clob.polymarket.com/time", timeout=5)  # warm up
    rest_times = []
    for i in range(10):
        t0 = time.time()
        session.get("https://clob.polymarket.com/time", timeout=5)
        elapsed = (time.time() - t0) * 1000
        rest_times.append(elapsed)
        print(f"  REST #{i + 1}: {elapsed:.0f}ms")

    # Step 7: CF edge location
    print()
    print("--- Step 7: Cloudflare edge info ---")
    r = session.get("https://clob.polymarket.com/time", timeout=5)
    cf_ray = r.headers.get("cf-ray", "?")
    edge_code = cf_ray.split("-")[-1] if "-" in cf_ray else "?"
    print(f"  CF-Ray: {cf_ray}")
    print(f"  CF Edge: {edge_code}")

    # Summary
    print()
    print("========== SUMMARY ==========")
    print(f"  WS Connect:     {connect_ms:.0f}ms")
    print(
        f"  WS PING/PONG:   avg={sum(times) / len(times):.1f}ms  "
        f"min={min(times):.1f}ms  max={max(times):.1f}ms"
    )
    print(
        f"  REST TTFB:      avg={sum(rest_times) / len(rest_times):.0f}ms  "
        f"min={min(rest_times):.0f}ms  max={max(rest_times):.0f}ms"
    )
    print(f"  CF Edge:        {edge_code}")
    print(f'  Data stream:    {"LIVE" if data_count >= 3 else "QUIET"} ({data_count} msgs)')
    print()
    print("Copy these results and compare across locations.")


if __name__ == "__main__":
    asyncio.run(full_latency_test())
