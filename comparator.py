import re
from datetime import date as _date
import numpy as np
from rapidfuzz import fuzz
from models import NormalizedEvent, NormalizedMarket, MatchResult, MarketMatchResult, ArbitrageResult

# Map common category name variants to a canonical label
CATEGORY_ALIASES: dict[str, str] = {
    "crypto": "Crypto",
    "cryptocurrency": "Crypto",
    "bitcoin": "Crypto",
    "politics": "Politics",
    "political": "Politics",
    "elections": "Politics",
    "election": "Politics",
    "sports": "Sports",
    "sport": "Sports",
    "finance": "Finance",
    "financial": "Finance",
    "economics": "Finance",
    "economy": "Finance",
    "science": "Science",
    "tech": "Tech",
    "technology": "Tech",
    "entertainment": "Entertainment",
    "pop culture": "Entertainment",
    "weather": "Weather",
    "climate": "Weather",
    "health": "Health",
    "medicine": "Health",
    "covid": "Health",
    "geopolitics": "Geopolitics",
    "world": "Geopolitics",
    "ai": "AI",
    "artificial intelligence": "AI",
}


def _clean(text: str) -> str:
    """Lowercase and strip punctuation for fuzzy matching."""
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def normalize_category(raw: str) -> str:
    key = raw.strip().lower()
    return CATEGORY_ALIASES.get(key, raw.title() if raw else "Other")


# ── Shared greedy best-first assignment ──────────────────────────────────────


def _greedy_match_events(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    sim_matrix: np.ndarray,
    min_score: float,
) -> list[MatchResult]:
    """
    Scores are stored as-is (cosine: 0.0–1.0, fuzzy: 0–100).
    min_score must be in the same units as the sim_matrix values.
    """
    results: list[MatchResult] = []
    used_poly: set[int] = set()
    used_kalshi: set[int] = set()

    pairs = sorted(
        ((sim_matrix[i, j], i, j)
         for i in range(len(poly_events))
         for j in range(len(kalshi_events))),
        reverse=True,
    )

    for score, i, j in pairs:
        if score < min_score:
            break
        if i in used_poly or j in used_kalshi:
            continue
        results.append(MatchResult(
            poly_event=poly_events[i],
            kalshi_event=kalshi_events[j],
            score=round(float(score), 4),
        ))
        used_poly.add(i)
        used_kalshi.add(j)

    return results


def _greedy_match_markets(
    poly_markets: list[NormalizedMarket],
    kalshi_markets: list[NormalizedMarket],
    sim_matrix: np.ndarray,
    min_score: float,
) -> list[MarketMatchResult]:
    """
    Scores are stored as-is (cosine: 0.0–1.0, fuzzy: 0–100).
    min_score must be in the same units as the sim_matrix values.
    """
    results: list[MarketMatchResult] = []
    used_poly: set[int] = set()
    used_kalshi: set[int] = set()

    pairs = sorted(
        ((sim_matrix[i, j], i, j)
         for i in range(len(poly_markets))
         for j in range(len(kalshi_markets))),
        reverse=True,
    )

    for score, i, j in pairs:
        if score < min_score:
            break
        if i in used_poly or j in used_kalshi:
            continue
        results.append(MarketMatchResult(
            poly_market=poly_markets[i],
            kalshi_market=kalshi_markets[j],
            score=round(float(score), 4),
        ))
        used_poly.add(i)
        used_kalshi.add(j)

    return results


# ── Event-level matching ──────────────────────────────────────────────────────


def find_matches_semantic(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    min_score: float = 0.82,
) -> list[MatchResult]:
    from clients.embeddings import embed_texts, cosine_similarity_matrix
    pv = embed_texts([e.title for e in poly_events])
    kv = embed_texts([e.title for e in kalshi_events])
    sim = cosine_similarity_matrix(pv, kv)
    return _greedy_match_events(poly_events, kalshi_events, sim, min_score)


def find_matches_fuzzy(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    min_score: float = 65.0,
) -> list[MatchResult]:
    poly_cleaned = [_clean(e.title) for e in poly_events]
    kalshi_cleaned = [_clean(e.title) for e in kalshi_events]
    n, m = len(poly_events), len(kalshi_events)
    sim = np.zeros((n, m), dtype=np.float32)
    for i, p in enumerate(poly_cleaned):
        for j, k in enumerate(kalshi_cleaned):
            sim[i, j] = fuzz.token_sort_ratio(p, k)
    return _greedy_match_events(poly_events, kalshi_events, sim, min_score)


def find_matches(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    min_score: float = 0.82,
    use_embeddings: bool = True,
) -> list[MatchResult]:
    from config import GEMINI_API_KEY
    if use_embeddings and GEMINI_API_KEY:
        try:
            return find_matches_semantic(poly_events, kalshi_events, min_score=min_score)
        except Exception as exc:
            print(f"[yellow]Embedding failed ({exc}), falling back to fuzzy matching.[/yellow]")
    # Convert cosine threshold (0-1) to fuzzy scale (0-100) for fallback
    fuzzy_min = min_score * 100.0 if min_score <= 1.0 else min_score
    return find_matches_fuzzy(poly_events, kalshi_events, min_score=fuzzy_min)


# ── Sub-market / bracket level matching (two-level) ──────────────────────────


def find_market_matches(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    event_min_score: float = 0.75,
    market_min_score: float = 0.85,
    use_embeddings: bool = True,
    use_cache: bool = True,
    refresh_cache: bool = False,
) -> list[tuple[MatchResult, list[MarketMatchResult]]]:
    """
    Two-level matching:
      1. Match parent events by title (loose threshold — just find topic overlap).
      2. For each matched event pair, match their sub-markets (stricter threshold).

    Returns a list of (event_match, [market_matches]) tuples, one per matched
    event pair.  Pairs where neither side has sub-markets are included with an
    empty market list so callers can still show the event-level match.
    """
    event_matches = find_matches(
        poly_events, kalshi_events,
        min_score=event_min_score,
        use_embeddings=use_embeddings,
    )

    results: list[tuple[MatchResult, list[MarketMatchResult]]] = []

    for em in event_matches:
        pm_markets = em.poly_event.markets
        ks_markets = em.kalshi_event.markets

        if not pm_markets or not ks_markets:
            results.append((em, []))
            continue

        # Single-market on both sides: the event match IS the bracket match.
        # Skip re-embedding to avoid false negatives when market question
        # differs slightly from event title (common on Kalshi).
        if len(pm_markets) == 1 and len(ks_markets) == 1:
            single_mm = [MarketMatchResult(
                poly_market=pm_markets[0],
                kalshi_market=ks_markets[0],
                score=em.score,
            )]
            results.append((em, single_mm))
            if use_cache:
                from cache import save_match
                save_match(em, single_mm)
            continue

        # Check cache before embedding
        if use_cache and not refresh_cache:
            from cache import load_cached_market_matches
            cached = load_cached_market_matches(em.poly_event, em.kalshi_event)
            if cached is not None:
                results.append((em, cached))
                continue

        market_matches = _match_pair_markets(
            pm_markets, ks_markets, market_min_score, use_embeddings
        )
        results.append((em, market_matches))

        if use_cache:
            from cache import save_match
            save_match(em, market_matches)

    return results


def _match_pair_markets(
    poly_markets: list[NormalizedMarket],
    kalshi_markets: list[NormalizedMarket],
    min_score: float,
    use_embeddings: bool,
) -> list[MarketMatchResult]:
    """Match sub-markets within a single already-confirmed event pair."""
    from config import GEMINI_API_KEY

    if use_embeddings and GEMINI_API_KEY:
        try:
            return _market_matches_semantic(poly_markets, kalshi_markets, min_score)
        except Exception as exc:
            print(f"[yellow]Sub-market embedding failed ({exc}), using fuzzy.[/yellow]")

    fuzzy_min = min_score * 100.0 if min_score <= 1.0 else min_score
    return _market_matches_fuzzy(poly_markets, kalshi_markets, fuzzy_min)


def _market_matches_semantic(
    poly_markets: list[NormalizedMarket],
    kalshi_markets: list[NormalizedMarket],
    min_score: float,
) -> list[MarketMatchResult]:
    from clients.embeddings import embed_texts, cosine_similarity_matrix
    pv = embed_texts([m.question for m in poly_markets])
    kv = embed_texts([m.question for m in kalshi_markets])
    sim = cosine_similarity_matrix(pv, kv)
    return _greedy_match_markets(poly_markets, kalshi_markets, sim, min_score)


def _market_matches_fuzzy(
    poly_markets: list[NormalizedMarket],
    kalshi_markets: list[NormalizedMarket],
    min_score: float,
) -> list[MarketMatchResult]:
    n, m = len(poly_markets), len(kalshi_markets)
    sim = np.zeros((n, m), dtype=np.float32)
    for i, pm in enumerate(poly_markets):
        pc = _clean(pm.question)
        for j, km in enumerate(kalshi_markets):
            sim[i, j] = fuzz.token_sort_ratio(pc, _clean(km.question))
    return _greedy_match_markets(poly_markets, kalshi_markets, sim, min_score)


# ── Arbitrage detection ───────────────────────────────────────────────────────


def find_arbitrage(
    pairs: list[tuple[MatchResult, list[MarketMatchResult]]],
    today: _date | None = None,
    min_profit: float = 0.0,
    max_days: int | None = None,
) -> list[ArbitrageResult]:
    """
    For each matched bracket pair compute cross-platform arbitrage:
      - "pm_yes_ks_no": buy Yes on Polymarket + No on Kalshi
      - "ks_yes_pm_no": buy Yes on Kalshi + No on Polymarket

    Only returns pairs where the cheaper leg combination costs < $1.00
    (i.e. profit > min_profit after fees are considered).

    Results are sorted by annualized_return descending (highest first),
    with unknown-date entries ranked after dated entries (sorted by raw profit).

    Args:
        pairs:       Output of find_market_matches().
        today:       Reference date for days-to-resolution (defaults to today).
        min_profit:  Minimum gross profit as a fraction 0.0–1.0 (default 0 = any positive spread).
        max_days:    Exclude opportunities expiring more than this many days away.
    """
    if today is None:
        today = _date.today()

    results: list[ArbitrageResult] = []

    for _event_match, market_matches in pairs:
        for mm in market_matches:
            pm = mm.poly_market
            ks = mm.kalshi_market

            spread_pm_yes = pm.yes_price + ks.no_price   # buy Yes on PM, No on KS
            spread_ks_yes = ks.yes_price + pm.no_price   # buy Yes on KS, No on PM

            if spread_pm_yes <= spread_ks_yes:
                best_leg = "pm_yes_ks_no"
                spread = spread_pm_yes
            else:
                best_leg = "ks_yes_pm_no"
                spread = spread_ks_yes

            profit = 1.0 - spread
            if profit <= min_profit:
                continue

            # Days to resolution: use the earlier of the two close dates
            days: int | None = None
            ann: float | None = None
            close_dates = []
            for ct in [pm.close_time, ks.close_time]:
                if ct:
                    try:
                        close_dates.append(_date.fromisoformat(ct[:10]))
                    except ValueError:
                        pass
            if close_dates:
                earliest = min(close_dates)
                days = (earliest - today).days
                if max_days is not None and days > max_days:
                    continue
                if days > 0:
                    ann = (profit / days) * 365

            results.append(ArbitrageResult(
                poly_market=pm,
                kalshi_market=ks,
                match_score=mm.score,
                best_leg=best_leg,
                spread=round(spread, 4),
                profit=round(profit, 4),
                days_to_resolution=days,
                annualized_return=round(ann, 4) if ann is not None else None,
            ))

    # Sort: dated entries by annualized_return desc, then undated by profit desc
    results.sort(key=lambda r: (
        r.annualized_return is None,          # False (0) sorts before True (1)
        -(r.annualized_return or 0),
        -r.profit,
    ))
    return results


# ── Category grouping ─────────────────────────────────────────────────────────


def group_by_category(events: list[NormalizedEvent]) -> dict[str, list[NormalizedEvent]]:
    groups: dict[str, list[NormalizedEvent]] = {}
    for e in events:
        cat = normalize_category(e.category)
        groups.setdefault(cat, []).append(e)
    return dict(sorted(groups.items()))
