"""
EventMatcher protocol — the interface all matching implementations must satisfy.

To add a new matcher:
  1. Create matchers/vN.py and implement a class that satisfies this protocol.
  2. Pass an instance to find_market_matches(matcher=YourMatcher()).

Scores on MatchResult / MarketMatchResult must always be normalised to [0, 1].
The orchestration layer (comparator.find_market_matches) is matcher-agnostic;
it handles caching, date pre-filtering, and the single-market shortcut.
"""

from typing import Protocol, runtime_checkable
from models import NormalizedEvent, NormalizedMarket, MatchResult, MarketMatchResult


@runtime_checkable
class EventMatcher(Protocol):
    """
    Protocol for two-level event/market matching algorithms.

    match_events  — given two event lists, return deduplicated pairs (score in [0,1])
    match_markets — given two sub-market lists from a single matched event pair,
                    return deduplicated pairs (score in [0,1])

    Implementations decide internally how similarity is computed (embeddings,
    fuzzy string, LLM re-ranking, etc.) and how matches are assigned (greedy,
    bipartite ILP, etc.).
    """

    def match_events(
        self,
        poly_events: list[NormalizedEvent],
        kalshi_events: list[NormalizedEvent],
        min_score: float,
    ) -> list[MatchResult]:
        """
        Return deduplicated event-level matches, score in [0, 1].
        min_score is always on the [0, 1] scale regardless of internal method.
        """
        ...

    def match_markets(
        self,
        poly_markets: list[NormalizedMarket],
        kalshi_markets: list[NormalizedMarket],
        min_score: float,
    ) -> list[MarketMatchResult]:
        """
        Return deduplicated sub-market matches, score in [0, 1].
        Called once per confirmed event pair.
        min_score is always on the [0, 1] scale.
        """
        ...
