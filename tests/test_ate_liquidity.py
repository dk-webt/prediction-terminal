#!/usr/bin/env python3
"""
ATE Liquidity Check Test Harness
=================================
Validates the ATE liquidity check logic against live orderbook data
from both Polymarket and Kalshi. No trades executed — read-only.

Uses the same code paths as production:
- fetch_polymarket_btc_15m() / fetch_kalshi_btc_15m() for contract data
- fetch_kalshi_orderbook() / fetch_polymarket_orderbook() for depth
- _check_depth() logic (copied from api_server.py)
- Same ask level construction (KS inversion, PM direct)

Usage:
    python3 tests/test_ate_liquidity.py              # default 10 contracts
    python3 tests/test_ate_liquidity.py --count 5    # check for 5 contracts
    python3 tests/test_ate_liquidity.py --loop 30    # repeat every 30s
"""

from __future__ import annotations

import argparse
import json
import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from clients.btc_watcher import fetch_polymarket_btc_15m, fetch_kalshi_btc_15m
from clients.executor import (
    fetch_kalshi_orderbook,
    fetch_polymarket_orderbook,
    kalshi_auth_available,
)


# ── _check_depth: copied from api_server.py to keep test standalone ──────────

def _check_depth(levels: list[tuple[float, float]], count: int, price_cap: float) -> tuple[bool, int]:
    """Walk orderbook levels to check if `count` contracts available within price_cap.
    levels: [(price, size), ...] sorted ascending by price.
    Returns (sufficient, available_count).
    """
    available = 0
    for price, size in levels:
        if price > price_cap:
            break
        available += size
        if available >= count:
            return True, int(min(available, count))
    return False, int(available)


def fmt_price(v):
    return f"${v:.4f}" if v else "---"


def run_check(order_count: int, verbose: bool = False):
    print(f"\n{'='*70}")
    print(f"  ATE LIQUIDITY CHECK  |  {time.strftime('%H:%M:%S')}  |  target={order_count} contracts")
    print(f"{'='*70}")

    # ── Fetch contracts ──────────────────────────────────────────────────
    t0 = time.monotonic()
    pm = fetch_polymarket_btc_15m()
    t1 = time.monotonic()

    ks = None
    if kalshi_auth_available():
        ks = fetch_kalshi_btc_15m()
    t2 = time.monotonic()

    print(f"\nContracts (pm={int((t1-t0)*1000)}ms, ks={int((t2-t1)*1000)}ms):")

    if not pm or pm.get("error"):
        print(f"  PM: UNAVAILABLE — {pm.get('error') if pm else 'not found'}")
        return
    print(f"  PM: {pm['slug']}")
    print(f"      up_ask={fmt_price(pm.get('up_ask'))}  up_bid={fmt_price(pm.get('up_bid'))}")
    print(f"      down_ask={fmt_price(pm.get('down_ask'))}  down_bid={fmt_price(pm.get('down_bid'))}")

    if not ks or ks.get("error"):
        print(f"  KS: UNAVAILABLE — {ks.get('error') if ks else 'auth not configured'}")
        ks = None
    else:
        print(f"  KS: {ks['ticker']}")
        print(f"      yes_ask={fmt_price(ks.get('yes_ask'))}  yes_bid={fmt_price(ks.get('yes_bid'))}")
        print(f"      no_ask={fmt_price(ks.get('no_ask'))}  no_bid={fmt_price(ks.get('no_bid'))}")

    # ── Fetch orderbooks ─────────────────────────────────────────────────
    print(f"\nOrderbooks:")
    tokens = pm.get("token_ids", [])
    pm_up_ob = fetch_polymarket_orderbook(tokens[0]) if len(tokens) >= 1 else {}
    pm_down_ob = fetch_polymarket_orderbook(tokens[1]) if len(tokens) >= 2 else {}
    ks_ob = fetch_kalshi_orderbook(ks["ticker"]) if ks else {}

    pm_up_asks = [(float(a["price"]), float(a["size"])) for a in pm_up_ob.get("asks", [])]
    pm_up_asks.sort()
    pm_down_asks = [(float(a["price"]), float(a["size"])) for a in pm_down_ob.get("asks", [])]
    pm_down_asks.sort()

    pm_up_bids = [(float(b["price"]), float(b["size"])) for b in pm_up_ob.get("bids", [])]
    pm_up_bids.sort(reverse=True)
    pm_down_bids = [(float(b["price"]), float(b["size"])) for b in pm_down_ob.get("bids", [])]
    pm_down_bids.sort(reverse=True)

    print(f"  PM UP:   {len(pm_up_asks)} ask levels, {len(pm_up_bids)} bid levels")
    print(f"  PM DOWN: {len(pm_down_asks)} ask levels, {len(pm_down_bids)} bid levels")

    if ks_ob:
        ks_yes_raw = ks_ob.get("yes_dollars", [])
        ks_no_raw = ks_ob.get("no_dollars", [])
        print(f"  KS:      {len(ks_yes_raw)} yes levels, {len(ks_no_raw)} no levels")
    else:
        ks_yes_raw = []
        ks_no_raw = []
        print(f"  KS:      no orderbook")

    # ── Show top levels if verbose ───────────────────────────────────────
    if verbose:
        def show_levels(label, levels, max_n=5):
            print(f"\n  {label} (top {min(max_n, len(levels))}):")
            for i, (p, s) in enumerate(levels[:max_n]):
                print(f"    [{i}] price={p:.4f}  size={s:.0f}")
            total = sum(s for _, s in levels)
            print(f"    total depth: {total:.0f} contracts across {len(levels)} levels")

        show_levels("PM UP asks", pm_up_asks)
        show_levels("PM DOWN asks", pm_down_asks)
        if ks_ob:
            # Build KS ask levels same way ATE does
            ks_yes_asks = [(round(1.0 - float(p), 4), float(s)) for p, s in ks_no_raw if float(s) > 0]
            ks_yes_asks.sort()
            ks_no_asks = [(round(1.0 - float(p), 4), float(s)) for p, s in ks_yes_raw if float(s) > 0]
            ks_no_asks.sort()
            show_levels("KS YES asks (from no_dollars inverted)", ks_yes_asks)
            show_levels("KS NO asks (from yes_dollars inverted)", ks_no_asks)

    # ── Combo analysis ───────────────────────────────────────────────────
    ks_yes_ask = ks.get("yes_ask", 0) or 0 if ks else 0
    ks_no_ask = ks.get("no_ask", 0) or 0 if ks else 0
    pm_down_ask = pm.get("down_ask", 0) or 0
    pm_up_ask = pm.get("up_ask", 0) or 0

    combos = []

    # Combo A: KS YES + PM DOWN
    cost_a = ks_yes_ask + pm_down_ask
    profit_a = 1.0 - cost_a if cost_a > 0 else -999
    combos.append(("A", "KS YES + PM DOWN", ks_yes_ask, pm_down_ask, cost_a, profit_a, "yes", "down"))

    # Combo B: KS NO + PM UP
    cost_b = ks_no_ask + pm_up_ask
    profit_b = 1.0 - cost_b if cost_b > 0 else -999
    combos.append(("B", "KS NO + PM UP", ks_no_ask, pm_up_ask, cost_b, profit_b, "no", "up"))

    print(f"\nCombo Analysis:")
    for label, name, ks_price, pm_price, cost, profit, ks_side, pm_side in combos:
        profit_pct = profit * 100 if profit > -999 else 0
        status = "PROFITABLE" if profit >= 0.06 else "MARGINAL" if profit > 0 else "UNPROFITABLE"
        print(f"\n  [{label}] {name}")
        print(f"      KS {ks_side} ask: {fmt_price(ks_price)}  +  PM {pm_side} ask: {fmt_price(pm_price)}")
        print(f"      cost: {fmt_price(cost)}  profit: {fmt_price(profit)}  ({profit_pct:.1f}%)  [{status}]")

        # Liquidity check
        if ks_ob:
            if ks_side == "yes":
                ks_ask_levels = [(round(1.0 - float(p), 4), float(s)) for p, s in ks_no_raw if float(s) > 0]
            else:
                ks_ask_levels = [(round(1.0 - float(p), 4), float(s)) for p, s in ks_yes_raw if float(s) > 0]
            ks_ask_levels.sort()
        else:
            ks_ask_levels = []

        if pm_side == "down":
            pm_ask_levels = pm_down_asks
        else:
            pm_ask_levels = pm_up_asks

        ks_cap = ks_price + 0.02 if ks_price > 0 else 0
        pm_cap = pm_price + 0.02 if pm_price > 0 else 0

        if ks_ask_levels:
            ks_ok, ks_avail = _check_depth(ks_ask_levels, order_count, ks_cap)
            print(f"      KS depth: {'OK' if ks_ok else 'THIN'}  {ks_avail}/{order_count} @ cap {fmt_price(ks_cap)}")

            # Show what we'd actually fill
            filled = 0
            total_cost = 0.0
            for p, s in ks_ask_levels:
                if p > ks_cap:
                    break
                take = min(s, order_count - filled)
                total_cost += p * take
                filled += take
                if filled >= order_count:
                    break
            if filled > 0:
                avg_price = total_cost / filled
                print(f"      KS fill sim: {int(filled)} contracts, avg_price={fmt_price(avg_price)}, total=${total_cost:.2f}")
        else:
            print(f"      KS depth: NO DATA")

        pm_ok, pm_avail = _check_depth(pm_ask_levels, order_count, pm_cap)
        print(f"      PM depth: {'OK' if pm_ok else 'THIN'}  {pm_avail}/{order_count} @ cap {fmt_price(pm_cap)}")

        # Show what we'd actually fill on PM
        filled = 0
        total_cost = 0.0
        for p, s in pm_ask_levels:
            if p > pm_cap:
                break
            take = min(s, order_count - filled)
            total_cost += p * take
            filled += take
            if filled >= order_count:
                break
        if filled > 0:
            avg_price = total_cost / filled
            print(f"      PM fill sim: {int(filled)} contracts, avg_price={fmt_price(avg_price)}, total=${total_cost:.2f}")

        # Combined execution cost simulation
        if ks_ask_levels and pm_ask_levels:
            ks_ok2, ks_avail2 = _check_depth(ks_ask_levels, order_count, ks_cap)
            pm_ok2, pm_avail2 = _check_depth(pm_ask_levels, order_count, pm_cap)
            actual = min(ks_avail2, pm_avail2, order_count)
            if actual > 0:
                # Simulate actual fill on both sides
                ks_fill_cost = 0.0
                ks_filled = 0
                for p, s in ks_ask_levels:
                    if p > ks_cap:
                        break
                    take = min(s, actual - ks_filled)
                    ks_fill_cost += p * take
                    ks_filled += take
                    if ks_filled >= actual:
                        break

                pm_fill_cost = 0.0
                pm_filled = 0
                for p, s in pm_ask_levels:
                    if p > pm_cap:
                        break
                    take = min(s, actual - pm_filled)
                    pm_fill_cost += p * take
                    pm_filled += take
                    if pm_filled >= actual:
                        break

                combined_cost = ks_fill_cost + pm_fill_cost
                combined_revenue = actual * 1.0  # $1 per contract at settlement
                actual_profit = combined_revenue - combined_cost
                print(f"\n      EXECUTION SIM ({int(actual)} contracts):")
                print(f"        KS cost: ${ks_fill_cost:.2f} ({int(ks_filled)} filled)")
                print(f"        PM cost: ${pm_fill_cost:.2f} ({int(pm_filled)} filled)")
                print(f"        Total cost: ${combined_cost:.2f}")
                print(f"        Revenue: ${combined_revenue:.2f}")
                print(f"        Profit: ${actual_profit:.2f} ({actual_profit/actual*100:.1f}% per contract)")

    # ── ATE decision ─────────────────────────────────────────────────────
    ATE_MIN_PROFIT = 0.06
    ATE_MIN_COUNT = 1
    chosen = None
    if profit_a >= ATE_MIN_PROFIT and profit_a >= profit_b:
        chosen = "A"
    elif profit_b >= ATE_MIN_PROFIT:
        chosen = "B"

    print(f"\n{'─'*70}")
    if chosen:
        combo = combos[0] if chosen == "A" else combos[1]
        print(f"  ATE WOULD TRIGGER: Combo {chosen} ({combo[1]})")
        print(f"  Profit: {fmt_price(combo[5])} ({combo[5]*100:.1f}%) >= threshold {ATE_MIN_PROFIT*100:.0f}%")
    else:
        print(f"  ATE WOULD NOT TRIGGER")
        print(f"  Best profit: {fmt_price(max(profit_a, profit_b))} < threshold {ATE_MIN_PROFIT*100:.0f}%")
    print(f"{'='*70}\n")


def main():
    parser = argparse.ArgumentParser(description="ATE Liquidity Check Test Harness")
    parser.add_argument("--count", type=int, default=10, help="Number of contracts to check (default: 10)")
    parser.add_argument("--loop", type=int, default=0, help="Repeat every N seconds (0=once)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show orderbook levels")
    args = parser.parse_args()

    import logging
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    if args.loop > 0:
        print(f"Running every {args.loop}s (Ctrl+C to stop)")
        while True:
            try:
                run_check(args.count, args.verbose)
                time.sleep(args.loop)
            except KeyboardInterrupt:
                print("\nStopped")
                break
    else:
        run_check(args.count, args.verbose)


if __name__ == "__main__":
    main()
