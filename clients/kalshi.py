import requests
from models import NormalizedEvent, NormalizedMarket
from config import KALSHI_API_KEY

# NOTE: api.kalshi.com has moved; use api.elections.kalshi.com for public access
BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
MARKET_URL = "https://kalshi.com/markets"
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


# Module-level cache: series_ticker -> URL slug, populated lazily on first fetch.
# Kalshi URL format: /markets/{series_ticker_lower}/{slug}/{event_ticker_lower}
# Slug = series title lowercased with spaces replaced by hyphens ("New Pope" -> "new-pope").
_series_slug_cache: dict[str, str] = {}


def _get_series_slug(series_ticker: str) -> str:
    """
    Fetch the URL slug for a series from the Kalshi /series endpoint.
    Falls back to series_ticker.lower() if the fetch fails.
    Results are cached in memory for the lifetime of the process.
    """
    if series_ticker in _series_slug_cache:
        return _series_slug_cache[series_ticker]
    try:
        resp = requests.get(
            f"{BASE_URL}/series/{series_ticker}",
            headers=_get_headers(),
            timeout=10,
        )
        resp.raise_for_status()
        title = resp.json().get("series", {}).get("title", "")
        slug = title.lower().replace(" ", "-") if title else series_ticker.lower()
    except Exception:
        slug = series_ticker.lower()
    _series_slug_cache[series_ticker] = slug
    return slug


def _normalize_market(m: dict, parent_event_id: str = "", parent_event_title: str = "", event_url: str = "") -> NormalizedMarket:
    # Prices are in cents (0-100); convert to probability (0.0-1.0)
    yes_price = _safe_float(m.get("last_price", 0)) / 100.0
    no_price = _safe_float(m.get("no_bid", 0)) / 100.0
    if no_price == 0.0 and yes_price > 0.0:
        no_price = round(1.0 - yes_price, 4)

    return NormalizedMarket(
        question=_build_question(m, parent_event_title),
        yes_price=yes_price,
        no_price=no_price,
        volume=_safe_float(m.get("volume", 0)),
        source="kalshi",
        market_id=m.get("ticker", ""),
        parent_event_id=parent_event_id,
        parent_event_title=parent_event_title,
        close_time=(m.get("close_time") or "")[:10],
        url=event_url,
    )


def _normalize_event(e: dict) -> NormalizedEvent:
    ticker = e.get("event_ticker", e.get("ticker", ""))
    series_ticker = e.get("series_ticker", ticker)
    event_title = e.get("title", "")

    # Build exact event URL: /markets/{series}/{slug}/{event_ticker}
    # e.g. https://kalshi.com/markets/kxnewpope/new-pope/kxnewpope-70
    slug = _get_series_slug(series_ticker)
    event_url = f"{MARKET_URL}/{series_ticker.lower()}/{slug}/{ticker.lower()}"

    # Only include active sub-markets
    raw_markets = [m for m in e.get("markets", []) if m.get("status") == "active"]
    markets = [_normalize_market(m, parent_event_id=ticker, parent_event_title=event_title, event_url=event_url)
               for m in raw_markets]

    total_volume = sum(m.volume for m in markets)

    end_date = ""
    if e.get("markets"):
        end_date = (e["markets"][0].get("close_time") or "")[:10]

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
    )


def _get_headers() -> dict:
    headers = {"Content-Type": "application/json"}
    if KALSHI_API_KEY:
        headers["Authorization"] = f"Bearer {KALSHI_API_KEY}"
    return headers


def fetch_events(limit: int = 100, status: str = "open", category: str | None = None) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    cursor: str | None = None
    page_size = min(limit, 200)

    while len(events) < limit:
        params: dict = {
            "limit": page_size,
            "with_nested_markets": "true",
            "status": status,
        }
        if cursor:
            params["cursor"] = cursor
        if category:
            params["series_ticker"] = category

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
            if len(events) >= limit:
                break

        cursor = body.get("cursor")
        if not cursor or len(page) < page_size:
            break

    return events
