from datetime import date as _date
from models import NormalizedEvent, NormalizedMarket, MatchResult, MarketMatchResult, ArbitrageResult
from matchers import EventMatcher, default_matcher

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


def _days_apart(date1: str, date2: str) -> int | None:
    """Absolute day difference between two ISO date strings, or None if either is missing/unparseable."""
    if not date1 or not date2:
        return None
    try:
        return abs((_date.fromisoformat(date1[:10]) - _date.fromisoformat(date2[:10])).days)
    except ValueError:
        return None


def _filter_events_by_days(events: list[NormalizedEvent], max_days: int) -> list[NormalizedEvent]:
    """Drop events whose end_date is more than max_days away (or has no date)."""
    today = _date.today()
    result = []
    for e in events:
        if not e.end_date:
            continue
        try:
            if (_date.fromisoformat(e.end_date[:10]) - today).days <= max_days:
                result.append(e)
        except ValueError:
            pass
    return result


def normalize_category(raw: str) -> str:
    key = raw.strip().lower()
    return CATEGORY_ALIASES.get(key, raw.title() if raw else "Other")


# ── Sub-market / bracket level matching (two-level) ──────────────────────────


def find_market_matches(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    event_min_score: float = 0.75,
    market_min_score: float = 0.85,
    use_cache: bool = True,
    refresh_cache: bool = False,
    max_days: int | None = None,
    matcher: EventMatcher | None = None,
) -> list[tuple[MatchResult, list[MarketMatchResult]]]:
    """
    Two-level matching:
      1. Match parent events by title (loose threshold — just find topic overlap).
      2. For each matched event pair, match their sub-markets (stricter threshold).

    Returns a list of (event_match, [market_matches]) tuples, one per matched
    event pair. Pairs where neither side has sub-markets are included with an
    empty market list so callers can still show the event-level match.

    Args:
        matcher:   EventMatcher implementation. Defaults to GeminiFuzzyMatcher (V1).
                   Pass a different instance to swap the matching algorithm entirely.
        max_days:  Pre-filter events before embedding (loose +365d buffer for Kalshi
                   settlement date quirk). find_arbitrage enforces the strict cutoff.
    """
    if matcher is None:
        matcher = default_matcher()

    if max_days is not None:
        # Pre-filter loosely — Kalshi settlement dates can be 1+ year past the
        # actual event date, so find_arbitrage's min(pm, ks) is the strict gate.
        poly_events = _filter_events_by_days(poly_events, max_days + 365)
        kalshi_events = _filter_events_by_days(kalshi_events, max_days + 365)

    event_matches = matcher.match_events(poly_events, kalshi_events, event_min_score)

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

        # Check cache before calling the matcher
        if use_cache and not refresh_cache:
            from cache import load_cached_market_matches
            cached = load_cached_market_matches(em.poly_event, em.kalshi_event)
            if cached is not None:
                results.append((em, cached))
                continue

        market_matches = matcher.match_markets(pm_markets, ks_markets, market_min_score)
        results.append((em, market_matches))

        if use_cache:
            from cache import save_match
            save_match(em, market_matches)

    return results


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
