import requests
from models import NormalizedEvent, NormalizedMarket
from config import KALSHI_API_KEY

# NOTE: api.kalshi.com has moved; use api.elections.kalshi.com for public access
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
EVENT_URL = "https://kalshi.com/events"


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _build_question(m: dict, event_title: str) -> str:
    """
    Construct a full, embedding-ready question for a Kalshi sub-market.

    Kalshi's multi-market events have the same `title` for every sub-market.
    The distinguishing field is `no_sub_title` (e.g. "Before Jan 1, 2027",
    "Kevin Warsh", "Pietro Parolin"). Combine them so the embedding captures
    both the topic AND the specific option.

    Single-market events or markets where no_sub_title adds nothing new just
    use the event title directly.
    """
    no_sub = (m.get("no_sub_title") or "").strip()
    title = (m.get("title") or event_title).strip()

    if no_sub and no_sub.lower() not in title.lower():
        return f"{title}: {no_sub}"
    return title


def _normalize_market(m: dict, parent_event_id: str = "", parent_event_title: str = "", event_url: str = "") -> NormalizedMarket:
    # Kalshi API returns prices as *_dollars fields in 0.0–1.0 range (dollar price on $1 contract).
    # Use ask prices so the cost-to-enter calculation is accurate.
    yes_price = _safe_float(m.get("yes_ask_dollars") or m.get("last_price_dollars", 0))
    no_price = _safe_float(m.get("no_ask_dollars") or m.get("no_bid_dollars", 0))
    if no_price == 0.0 and yes_price > 0.0:
        no_price = round(1.0 - yes_price, 4)

    return NormalizedMarket(
        question=_build_question(m, parent_event_title),
        yes_price=yes_price,
        no_price=no_price,
        volume=_safe_float(m.get("volume_fp") or m.get("volume", 0)),
        source="kalshi",
        market_id=m.get("ticker", ""),
        parent_event_id=parent_event_id,
        parent_event_title=parent_event_title,
        close_time=(m.get("close_time") or "")[:10],
        url=event_url,
        rules_primary=m.get("rules_primary", ""),
        rules_secondary=m.get("rules_secondary", ""),
    )


def _normalize_event(e: dict) -> NormalizedEvent:
    ticker = e.get("event_ticker", e.get("ticker", ""))
    event_title = e.get("title", "")

    # Direct event URL — no extra API call needed.
    event_url = f"{EVENT_URL}/{ticker.lower()}"

    # Only include active sub-markets
    raw_markets = [m for m in e.get("markets", []) if m.get("status") == "active"]
    markets = [_normalize_market(m, parent_event_id=ticker, parent_event_title=event_title, event_url=event_url)
               for m in raw_markets]

    total_volume = sum(m.volume for m in markets)

    dates = [m.close_time for m in markets if m.close_time]
    end_date = min(dates) if dates else ""

    return NormalizedEvent(
        source="kalshi",
        id=ticker,
        title=event_title,
        category=e.get("category", "Other"),
        volume=total_volume,
        liquidity=_safe_float(e.get("liquidity")),
        end_date=end_date,
        url=event_url,
        markets=markets,
        sub_title=e.get("sub_title", ""),
    )


def _get_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if KALSHI_API_KEY:
        headers["Authorization"] = f"Bearer {KALSHI_API_KEY}"
    return headers


def fetch_events(limit: int = 100, status: str = "open", category: str | None = None) -> list[NormalizedEvent]:
    from comparator import normalize_category as _norm_cat

    events: list[NormalizedEvent] = []
    cursor: str | None = None
    # When filtering by category we may need to over-fetch since Kalshi has no
    # general category param (only series_ticker, which is a specific series ID).
    # Fetch up to 3× the limit to have enough events after filtering.
    fetch_limit = limit * 3 if category else limit
    page_size = min(fetch_limit, 200)

    while len(events) < fetch_limit:
        params: dict = {
            "limit": page_size,
            "with_nested_markets": "true",
            "status": status,
        }
        if cursor:
            params["cursor"] = cursor

        try:
            resp = requests.get(
                f"{BASE_URL}/events",
                params=params,
                headers=_get_headers(),
                timeout=15,
            )
            resp.raise_for_status()
        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            if status_code == 401:
                raise RuntimeError(
                    "Kalshi requires authentication. Set KALSHI_API_KEY in your .env file."
                ) from exc
            if status_code == 403:
                raise RuntimeError(
                    "Kalshi access forbidden. Check your KALSHI_API_KEY permissions."
                ) from exc
            raise RuntimeError(f"Kalshi API error: {exc}") from exc
        except requests.RequestException as exc:
            raise RuntimeError(f"Kalshi API error: {exc}") from exc

        body = resp.json()
        page = body.get("events", [])
        if not page:
            break

        for e in page:
            events.append(_normalize_event(e))
            if len(events) >= fetch_limit:
                break

        cursor = body.get("cursor")
        if not cursor or len(page) < page_size:
            break

    if category:
        target = _norm_cat(category)
        events = [e for e in events if _norm_cat(e.category) == target]

    return events[:limit]
