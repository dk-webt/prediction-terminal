import json
import requests
from models import NormalizedEvent, NormalizedMarket

BASE_URL = "https://gamma-api.polymarket.com"
MARKET_URL = "https://polymarket.com/event"


def _safe_float(val, default=0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _normalize_market(m: dict, parent_event_id: str = "", parent_event_title: str = "", parent_event_url: str = "") -> NormalizedMarket:
    # outcomePrices is a JSON-encoded string, e.g. '["0.65", "0.35"]'
    raw_prices = m.get("outcomePrices", [])
    if isinstance(raw_prices, str):
        try:
            raw_prices = json.loads(raw_prices)
        except (json.JSONDecodeError, ValueError):
            raw_prices = []

    # Prefer lastTradePrice (most current), fall back to outcomePrices[0]
    last_trade = m.get("lastTradePrice")
    if last_trade is not None:
        yes_price = _safe_float(last_trade)
    else:
        yes_price = _safe_float(raw_prices[0]) if raw_prices else 0.0
    no_price = _safe_float(raw_prices[1]) if len(raw_prices) > 1 else round(1.0 - yes_price, 4)

    return NormalizedMarket(
        question=m.get("question", ""),
        yes_price=yes_price,
        no_price=no_price,
        volume=_safe_float(m.get("volume")),
        source="polymarket",
        market_id=str(m.get("id", "")),
        parent_event_id=parent_event_id,
        parent_event_title=parent_event_title,
        close_time=(m.get("endDate") or "")[:10],
        url=parent_event_url,
    )


def _is_settled(m: NormalizedMarket) -> bool:
    """True if the market has already resolved (price at 0 or 1, no active trading)."""
    return (m.yes_price <= 0.001 and m.no_price >= 0.999) or \
           (m.yes_price >= 0.999 and m.no_price <= 0.001)


def _normalize_event(e: dict) -> NormalizedEvent:
    event_id = str(e.get("id", ""))
    event_title = e.get("title", "")

    # Only include open (non-closed) sub-markets and filter out settled ones
    event_url = f"{MARKET_URL}/{e.get('slug', '')}"
    raw_markets = [m for m in e.get("markets", []) if not m.get("closed", False)]
    markets = [_normalize_market(m, parent_event_id=event_id, parent_event_title=event_title, parent_event_url=event_url)
               for m in raw_markets]
    markets = [m for m in markets if not _is_settled(m)]

    tags = [t.get("label", "") for t in e.get("tags", [])]
    category = e.get("category") or (tags[0] if tags else "Other")
    return NormalizedEvent(
        source="polymarket",
        id=event_id,
        title=event_title,
        category=category,
        volume=_safe_float(e.get("volume")),
        liquidity=_safe_float(e.get("liquidity")),
        end_date=(e.get("endDate") or "")[:10],
        url=f"{MARKET_URL}/{e.get('slug', '')}",
        markets=markets,
    )


def fetch_events(limit: int = 100, category: str | None = None) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []
    offset = 0
    page_size = min(limit, 100)

    while len(events) < limit:
        params: dict = {
            "limit": page_size,
            "offset": offset,
            "active": "true",
            "closed": "false",
            "order": "volume24hr",
            "ascending": "false",
        }
        if category:
            params["category"] = category

        try:
            resp = requests.get(f"{BASE_URL}/events", params=params, timeout=15)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise RuntimeError(f"Polymarket API error: {exc}") from exc

        data = resp.json()
        if not data:
            break

        for e in data:
            events.append(_normalize_event(e))
            if len(events) >= limit:
                break

        if len(data) < page_size:
            break
        offset += page_size

    return events
