from dataclasses import dataclass, field


@dataclass
class NormalizedMarket:
    question: str          # Full sub-market question (embedding-ready)
    yes_price: float       # probability 0.0 - 1.0
    no_price: float
    volume: float
    # Parent context — set by the client after normalisation
    source: str = ""                # "polymarket" or "kalshi"
    market_id: str = ""             # Unique market identifier
    parent_event_id: str = ""
    parent_event_title: str = ""
    close_time: str = ""            # ISO date "YYYY-MM-DD" when market resolves
    url: str = ""                   # Direct link to this market/bracket


@dataclass
class NormalizedEvent:
    source: str        # "polymarket" or "kalshi"
    id: str
    title: str
    category: str
    volume: float
    liquidity: float
    end_date: str
    url: str
    markets: list = field(default_factory=list)  # list[NormalizedMarket]


@dataclass
class MatchResult:
    """Event-level match (legacy)."""
    poly_event: NormalizedEvent
    kalshi_event: NormalizedEvent
    score: float       # 0-100 fuzzy or 0.0-1.0 cosine


@dataclass
class MarketMatchResult:
    """Sub-market / bracket level match."""
    poly_market: NormalizedMarket
    kalshi_market: NormalizedMarket
    score: float       # cosine similarity 0.0-1.0 or fuzzy 0-100


@dataclass
class ArbitrageResult:
    """A cross-platform arbitrage opportunity from a matched bracket pair."""
    poly_market: NormalizedMarket
    kalshi_market: NormalizedMarket
    match_score: float
    # Which leg pair is cheaper: "pm_yes_ks_no" or "ks_yes_pm_no"
    best_leg: str
    # Total cost to buy both legs (fraction 0.0–1.0; <1.0 means profit)
    spread: float
    # Gross profit: 1.0 - spread  (in cents: profit * 100)
    profit: float
    # Days until the earlier of the two markets closes (None if unknown)
    days_to_resolution: int | None
    # Annualized gross return: (profit / days) * 365  (None if days unknown/zero)
    annualized_return: float | None
