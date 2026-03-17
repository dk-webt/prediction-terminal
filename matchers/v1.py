"""
V1 Matcher: GeminiFuzzyMatcher
  - Primary:  Gemini gemini-embedding-001 cosine similarity
  - Fallback: rapidfuzz token_sort_ratio (when no API key or embedding fails)
  - Assignment: greedy best-first 1-to-1 from similarity matrix

All scores are normalised to [0, 1] before being stored on results.
"""

import re
import numpy as np
from rapidfuzz import fuzz
from models import NormalizedEvent, NormalizedMarket, MatchResult, MarketMatchResult


def _clean(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", text.lower()).strip()


def _greedy_assign(left: list, right: list, sim: np.ndarray, min_score: float, make_result) -> list:
    """
    Generic greedy best-first 1-to-1 assignment.
    sim values and min_score must be on the same [0, 1] scale.
    """
    results = []
    used_left: set[int] = set()
    used_right: set[int] = set()

    pairs = sorted(
        ((sim[i, j], i, j) for i in range(len(left)) for j in range(len(right))),
        reverse=True,
    )
    for score, i, j in pairs:
        if score < min_score:
            break
        if i in used_left or j in used_right:
            continue
        results.append(make_result(left[i], right[j], round(float(score), 4)))
        used_left.add(i)
        used_right.add(j)

    return results


class GeminiFuzzyMatcher:
    """
    V1 EventMatcher implementation.

    Attempts Gemini embedding-001 cosine similarity first; falls back to
    rapidfuzz token_sort_ratio if the API key is absent or a request fails.
    Fuzzy scores are divided by 100 so all stored scores are in [0, 1].
    """

    # ── Public protocol methods ───────────────────────────────────────────────

    def match_events(
        self,
        poly_events: list[NormalizedEvent],
        kalshi_events: list[NormalizedEvent],
        min_score: float,
    ) -> list[MatchResult]:
        from config import GEMINI_API_KEY
        if GEMINI_API_KEY:
            try:
                return self._events_semantic(poly_events, kalshi_events, min_score)
            except Exception as exc:
                print(f"[yellow]Embedding failed ({exc}), falling back to fuzzy.[/yellow]")
        return self._events_fuzzy(poly_events, kalshi_events, min_score)

    def match_markets(
        self,
        poly_markets: list[NormalizedMarket],
        kalshi_markets: list[NormalizedMarket],
        min_score: float,
    ) -> list[MarketMatchResult]:
        from config import GEMINI_API_KEY
        if GEMINI_API_KEY:
            try:
                return self._markets_semantic(poly_markets, kalshi_markets, min_score)
            except Exception as exc:
                print(f"[yellow]Sub-market embedding failed ({exc}), using fuzzy.[/yellow]")
        return self._markets_fuzzy(poly_markets, kalshi_markets, min_score)

    # ── Private helpers ───────────────────────────────────────────────────────

    def _events_semantic(self, poly_events, kalshi_events, min_score):
        from clients.embeddings import embed_texts, cosine_similarity_matrix
        pv = embed_texts([e.title for e in poly_events])
        kv = embed_texts([e.title for e in kalshi_events])
        sim = cosine_similarity_matrix(pv, kv)
        return _greedy_assign(
            poly_events, kalshi_events, sim, min_score,
            lambda pe, ke, s: MatchResult(poly_event=pe, kalshi_event=ke, score=s),
        )

    def _events_fuzzy(self, poly_events, kalshi_events, min_score):
        pc = [_clean(e.title) for e in poly_events]
        kc = [_clean(e.title) for e in kalshi_events]
        sim = np.zeros((len(poly_events), len(kalshi_events)), dtype=np.float32)
        for i, p in enumerate(pc):
            for j, k in enumerate(kc):
                sim[i, j] = fuzz.token_sort_ratio(p, k) / 100.0
        return _greedy_assign(
            poly_events, kalshi_events, sim, min_score,
            lambda pe, ke, s: MatchResult(poly_event=pe, kalshi_event=ke, score=s),
        )

    def _markets_semantic(self, poly_markets, kalshi_markets, min_score):
        from clients.embeddings import embed_texts, cosine_similarity_matrix
        pv = embed_texts([m.question for m in poly_markets])
        kv = embed_texts([m.question for m in kalshi_markets])
        sim = cosine_similarity_matrix(pv, kv)
        return _greedy_assign(
            poly_markets, kalshi_markets, sim, min_score,
            lambda pm, km, s: MarketMatchResult(poly_market=pm, kalshi_market=km, score=s),
        )

    def _markets_fuzzy(self, poly_markets, kalshi_markets, min_score):
        sim = np.zeros((len(poly_markets), len(kalshi_markets)), dtype=np.float32)
        for i, pm in enumerate(poly_markets):
            pc = _clean(pm.question)
            for j, km in enumerate(kalshi_markets):
                sim[i, j] = fuzz.token_sort_ratio(pc, _clean(km.question)) / 100.0
        return _greedy_assign(
            poly_markets, kalshi_markets, sim, min_score,
            lambda pm, km, s: MarketMatchResult(poly_market=pm, kalshi_market=km, score=s),
        )
