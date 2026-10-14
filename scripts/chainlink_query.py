#!/usr/bin/env python3
"""
Query Chainlink BTC/USD on-chain aggregator for all round updates
in a given time window. Uses binary search to find the right rounds.

Usage:
    python3 scripts/chainlink_query.py
    python3 scripts/chainlink_query.py --rpc https://eth.llamarpc.com
"""

import argparse
import json
import time
from datetime import datetime, timezone, timedelta
from web3 import Web3

# Chainlink BTC/USD aggregator proxy on Ethereum mainnet
AGGREGATOR_PROXY = "0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c"

# Minimal ABI for price feed queries
AGGREGATOR_ABI = json.loads("""[
    {
        "inputs": [],
        "name": "latestRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "_roundId", "type": "uint80"}],
        "name": "getRoundData",
        "outputs": [
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "phaseId",
        "outputs": [{"name": "", "type": "uint16"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "phaseAggregators",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]""")

EST = timezone(timedelta(hours=-4))  # EDT (April = daylight saving)


def round_id_for_phase(phase_id: int, aggregator_round: int) -> int:
    """Chainlink proxy round ID = (phaseId << 64) | aggregatorRoundId"""
    return (phase_id << 64) | aggregator_round


def decompose_round_id(round_id: int) -> tuple[int, int]:
    """Extract (phaseId, aggregatorRoundId) from proxy roundId"""
    phase_id = round_id >> 64
    agg_round = round_id & 0xFFFFFFFFFFFFFFFF
    return phase_id, agg_round


def get_round_safe(contract, round_id: int) -> dict | None:
    """Fetch a round, return None if it doesn't exist."""
    try:
        data = contract.functions.getRoundData(round_id).call()
        rid, answer, started_at, updated_at, answered_in = data
        if updated_at == 0:
            return None
        return {
            "roundId": rid,
            "answer": answer,
            "startedAt": started_at,
            "updatedAt": updated_at,
            "answeredInRound": answered_in,
        }
    except Exception:
        return None


def binary_search_round(contract, phase_id: int, target_ts: int,
                        lo: int, hi: int) -> int:
    """Find the first aggregator round whose updatedAt >= target_ts."""
    while lo < hi:
        mid = (lo + hi) // 2
        rid = round_id_for_phase(phase_id, mid)
        data = get_round_safe(contract, rid)
        if data is None or data["updatedAt"] < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def main():
    parser = argparse.ArgumentParser(description="Query Chainlink BTC/USD rounds")
    parser.add_argument("--rpc", default="https://ethereum-rpc.publicnode.com",
                        help="Ethereum RPC endpoint")
    parser.add_argument("--start", default="2026-04-06 22:59:30",
                        help="Start time in EST/EDT (default: 2026-04-06 22:59:30)")
    parser.add_argument("--end", default="2026-04-06 23:01:30",
                        help="End time in EST/EDT (default: 2026-04-06 23:01:30)")
    parser.add_argument("--expand", type=int, default=5,
                        help="Extra rounds to fetch before/after window (default: 5)")
    args = parser.parse_args()

    # Parse time window
    start_local = datetime.strptime(args.start, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EST)
    end_local = datetime.strptime(args.end, "%Y-%m-%d %H:%M:%S").replace(tzinfo=EST)
    start_ts = int(start_local.timestamp())
    end_ts = int(end_local.timestamp())

    print(f"Window: {start_local.isoformat()} to {end_local.isoformat()}")
    print(f"  UTC : {datetime.fromtimestamp(start_ts, tz=timezone.utc).isoformat()} to "
          f"{datetime.fromtimestamp(end_ts, tz=timezone.utc).isoformat()}")
    print(f"  Unix: {start_ts} to {end_ts}")
    print()

    # Connect
    w3 = Web3(Web3.HTTPProvider(args.rpc, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        print(f"ERROR: cannot connect to {args.rpc}")
        return
    print(f"Connected to {args.rpc} (block {w3.eth.block_number})")

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(AGGREGATOR_PROXY),
        abi=AGGREGATOR_ABI,
    )

    decimals = contract.functions.decimals().call()
    print(f"Decimals: {decimals}")

    # Get current phase and latest round
    latest = contract.functions.latestRoundData().call()
    latest_rid, latest_answer, _, latest_updated, _ = latest
    phase_id, latest_agg_round = decompose_round_id(latest_rid)
    print(f"Phase: {phase_id}, latest aggregator round: {latest_agg_round}")
    print(f"Latest price: ${latest_answer / 10**decimals:,.2f} at "
          f"{datetime.fromtimestamp(latest_updated, tz=EST).isoformat()}")
    print()

    # Binary search for first round >= start_ts
    print("Binary searching for window start...")
    # Search from round 1 to latest
    first_agg = binary_search_round(contract, phase_id, start_ts, 1, latest_agg_round)

    # Now collect all rounds from (first_agg - expand) to the first round past end_ts
    collect_from = max(1, first_agg - args.expand)
    rounds = []
    miss_count = 0

    print(f"Fetching rounds starting from aggregator round {collect_from}...")
    print()

    agg_round = collect_from
    while miss_count < 3:
        rid = round_id_for_phase(phase_id, agg_round)
        data = get_round_safe(contract, rid)
        if data is None:
            miss_count += 1
            agg_round += 1
            continue
        miss_count = 0
        rounds.append((agg_round, data))

        # Stop well past the end of our window
        if data["updatedAt"] > end_ts + 300:  # 5 min past end
            break
        agg_round += 1

    # Print results
    print(f"{'AggRound':>10}  {'Price':>14}  {'UpdatedAt (EST)':>28}  {'UpdatedAt (UTC)':>28}  {'Unix':>12}  {'In Window':>9}")
    print("-" * 120)

    for agg_round, data in rounds:
        price = data["answer"] / 10**decimals
        ts = data["updatedAt"]
        dt_est = datetime.fromtimestamp(ts, tz=EST)
        dt_utc = datetime.fromtimestamp(ts, tz=timezone.utc)
        in_window = "  <---" if start_ts <= ts <= end_ts else ""
        print(f"{agg_round:>10}  ${price:>13,.2f}  {dt_est.strftime('%Y-%m-%d %H:%M:%S %Z'):>28}  "
              f"{dt_utc.strftime('%Y-%m-%d %H:%M:%S %Z'):>28}  {ts:>12}{in_window}")

    # Summary of in-window rounds
    in_window_rounds = [(ar, d) for ar, d in rounds if start_ts <= d["updatedAt"] <= end_ts]
    print()
    print(f"Total rounds fetched: {len(rounds)}")
    print(f"Rounds in window: {len(in_window_rounds)}")

    if in_window_rounds:
        print()
        print("=== IN-WINDOW PRICES ===")
        for agg_round, data in in_window_rounds:
            price = data["answer"] / 10**decimals
            ts = data["updatedAt"]
            dt_est = datetime.fromtimestamp(ts, tz=EST)
            print(f"  Round {agg_round}: ${price:,.2f} at {dt_est.strftime('%Y-%m-%d %H:%M:%S.%f %Z')}")

        # The price closest to 11:00:00 PM EST
        target_ts = int(datetime(2026, 4, 6, 23, 0, 0, tzinfo=EST).timestamp())
        closest = min(in_window_rounds, key=lambda x: abs(x[1]["updatedAt"] - target_ts))
        price = closest[1]["answer"] / 10**decimals
        dt = datetime.fromtimestamp(closest[1]["updatedAt"], tz=EST)
        print()
        print(f"Closest to 11:00:00 PM EST: Round {closest[0]} = ${price:,.2f} at {dt.strftime('%H:%M:%S %Z')}")
    else:
        # Find the last round before window and first after
        before = [(ar, d) for ar, d in rounds if d["updatedAt"] < start_ts]
        after = [(ar, d) for ar, d in rounds if d["updatedAt"] > end_ts]
        print()
        print("No rounds updated within the window.")
        if before:
            d = before[-1][1]
            price = d["answer"] / 10**decimals
            dt = datetime.fromtimestamp(d["updatedAt"], tz=EST)
            print(f"  Last before window: Round {before[-1][0]} = ${price:,.2f} at {dt.strftime('%H:%M:%S %Z')}")
        if after:
            d = after[0][1]
            price = d["answer"] / 10**decimals
            dt = datetime.fromtimestamp(d["updatedAt"], tz=EST)
            print(f"  First after window: Round {after[0][0]} = ${price:,.2f} at {dt.strftime('%H:%M:%S %Z')}")
        print()
        print("NOTE: The on-chain Chainlink BTC/USD feed updates on ~1hr heartbeat or 0.5% deviation.")
        print("Polymarket uses Chainlink RTDS (off-chain streams) which has sub-second granularity.")
        print("The on-chain price that was 'active' at 11:00 PM would be the last update before that time.")


if __name__ == "__main__":
    main()
