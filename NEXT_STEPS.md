# Next Steps: Prediction Market Comparator

## 1. Arbitrage Detection

**Goal:** For each matched bracket pair, automatically surface opportunities where buying Yes on one platform and No on the other costs less than $1.00.

**How it works:**
- For every matched `(poly_market, kalshi_market)` pair, compute two spreads:
  - `pm_yes + ks_no` — buy Yes on Polymarket, No on Kalshi
  - `ks_yes + pm_no` — buy Yes on Kalshi, No on Polymarket
- If either sum < 1.00 (100¢), the gap is theoretical profit before fees
- Example: PM Yes 2.1¢ + Kalshi No 96.0¢ = 98.1¢ → 1.9¢ profit per $1 contract

**Implementation plan:**
- Add `find_arbitrage(market_matches)` in `comparator.py`
- Add `arb` subcommand (or `--arb` flag on `compare --brackets`) in `main.py`
- Output table sorted by profit margin descending, showing both legs of the trade
- Display effective spread after platform fees (Polymarket ~2%, Kalshi ~7¢/contract)

**Caveats:** Prices shown are mid-market; actual fill prices depend on order book depth. Always account for slippage and minimum order sizes before treating a spread as executable.

---

## 2. Persistent ID Mapping / Semantic Cache

**Goal:** Avoid re-running embedding similarity on every invocation. Cache confirmed matches so arbitrage checks are fast even at `--limit 1000`.

**How it works:**
- After matching, write a local SQLite database (or JSON file) with:
  - `polymarket_market_id`, `kalshi_ticker`, `match_score`
  - `pm_title`, `ks_title`, `cached_at` timestamp
- On subsequent runs: look up market IDs in cache first; only embed genuinely new or stale events
- Stale = market closed, or cache entry > N days old, or prices diverged beyond threshold

**Implementation plan:**
- New `cache.py` module with `load_cache()`, `save_match()`, `lookup_match()` functions
- SQLite file at `.cache/market_matches.db` (gitignored)
- `--refresh-cache` flag to force full re-embedding
- Estimated speedup: from ~90s cold to <5s warm for the same event set

---

## 3. Resolution Clause Comparison

**Goal:** Verify that semantically similar events actually resolve the same way before flagging as arbitrage.

**Problem:** "Trump out before 2027?" (PM) and "Will Trump resign during his term?" (KS) score highly on semantic similarity but resolve differently — the first includes removal by impeachment or death, the second is resignation-only.

**How it works:**
- Fetch resolution text from each platform:
  - Polymarket: `resolutionSource`, `rules_primary`, `rules_secondary` fields on market objects
  - Kalshi: `rules_primary`, `rules_secondary` fields on market objects
- Use Gemini (or a lightweight LLM call) to compare clauses and flag mismatches
- Add `resolution_compatible: bool | None` to `MarketMatchResult`

**Implementation plan:**
- New `resolution.py` module with `compare_resolution_clauses(pm_rules, ks_rules) -> float`
- Returns a compatibility score (0–1); threshold ~0.85 to flag as compatible
- Show a warning symbol (⚠) in bracket output when clauses diverge significantly
- `--strict-resolution` flag to exclude incompatible pairs from arbitrage output

---

## 4. Trade Execution

**Goal:** Place the two legs of an arbitrage trade directly from the CLI.

**Platform APIs:**

*Polymarket (CLOB):*
- Base URL: `https://clob.polymarket.com`
- Requires: API key + private key for cryptographic order signing (uses ECDSA / web3)
- Endpoint: `POST /order` with signed order payload
- Python SDK: `py-clob-client` (Polymarket's official library)

*Kalshi:*
- Base URL: `https://api.elections.kalshi.com/trade-api/v2`
- Requires: `KALSHI_API_KEY` with trading permissions (set in `.env`)
- Endpoint: `POST /portfolio/orders`

**Implementation plan:**
- New `clients/executor.py` with `place_order(platform, market_id, side, size, price)`
- Dry-run mode by default — prints what would be placed without executing
- Explicit `--execute` flag required to actually send orders
- Safety checks before execution:
  1. Re-fetch current prices to confirm spread still exists
  2. Verify order size ≤ configured max position (`MAX_ORDER_SIZE` in `.env`)
  3. Confirm both legs can be placed (sufficient liquidity at desired price)
- Add `POLYMARKET_API_KEY` and `POLYMARKET_PRIVATE_KEY` to `config.py`

---

## 5. Sort Arbitrage by Time to Resolution

**Goal:** Prioritize short-dated opportunities. Capital locked in a 3-day contract earning 2% is far better than a 6-month contract earning the same 2%.

**How it works:**
- `NormalizedMarket` already has a `close_time` / `end_date` field
- Compute `days_to_resolution = (end_date - today).days`
- Compute `annualized_return = (profit_pct / days_to_resolution) * 365`
- Sort arbitrage output by `annualized_return` descending (not raw profit)

**Implementation plan:**
- Ensure `close_time` is consistently populated from both platform clients
- Add `days_remaining` and `annualized_return` columns to arbitrage output table
- `--max-days N` flag to exclude contracts resolving more than N days out
- `--min-profit N` flag to exclude spreads below N cents (filter noise)

---

## Suggested Build Order

| Priority | Feature | Effort | Value |
|----------|---------|--------|-------|
| 1 | Arbitrage detection | Low | High — immediate actionable output |
| 2 | Time-to-resolution sort | Low | High — works alongside #1 |
| 3 | Persistent ID cache | Medium | High — makes daily use practical |
| 4 | Resolution clause check | Medium | Medium — safety layer |
| 5 | Trade execution | High | High — but needs careful testing |

Start with #1 and #2 together since they build directly on the existing bracket matching output.
