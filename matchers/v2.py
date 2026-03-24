"""
V2 Matcher: GeminiRichMatcher
  - Rich embedding text (title + description/rules + tags + category + dates)
  - Gemini-only (no fuzzy fallback — raises if no API key)
  - Composite confidence scoring at event level
  - Greedy best-first 1-to-1 assignment (incremental-friendly for V2.1)
  - Embedding persistence in SQLite (avoids re-embedding unchanged events)

All scores are normalised to [0, 1].
"""

import hashlib
import math
import numpy as np
from models import NormalizedEvent, NormalizedMarket, MatchResult, MarketMatchResult


# ── Embedding text builders ──────────────────────────────────────────────────
# Platform-specific templates that combine all available fields.
# The embedding model handles cross-platform alignment in vector space.


def _build_event_text_polymarket(e: NormalizedEvent) -> str:
    parts = []
    tag_str = ", ".join(e.tags) if e.tags else e.category
    if tag_str:
        parts.append(f"[{tag_str}]")
    parts.append(e.title)
    if e.description:
        parts.append(e.description[:400])
    if e.end_date:
        parts.append(f"(resolves {e.end_date})")
    bracket_labels = [m.group_item_title for m in e.markets if m.group_item_title]
    if bracket_labels:
        parts.append(f"Brackets: {', '.join(bracket_labels[:10])}")
    return " ".join(parts)


def _build_event_text_kalshi(e: NormalizedEvent) -> str:
    parts = []
    if e.category:
        parts.append(f"[{e.category}]")
    parts.append(e.title)
    if e.sub_title and e.sub_title.lower() not in e.title.lower():
        parts.append(e.sub_title)
    # Use rules_primary from the first market as event-level context
    # (Kalshi has no event-level description field)
    if e.markets and e.markets[0].rules_primary:
        parts.append(e.markets[0].rules_primary[:400])
    if e.end_date:
        parts.append(f"(resolves {e.end_date})")
    return " ".join(parts)


def _build_event_text(e: NormalizedEvent) -> str:
    if e.source == "polymarket":
        return _build_event_text_polymarket(e)
    return _build_event_text_kalshi(e)


def _build_market_text_polymarket(m: NormalizedMarket) -> str:
    parts = [m.question]
    if m.group_item_title and m.group_item_title.lower() not in m.question.lower():
        parts.append(f"Option: {m.group_item_title}")
    if m.description:
        parts.append(m.description[:300])
    if m.close_time:
        parts.append(f"(resolves {m.close_time})")
    return " ".join(parts)


def _build_market_text_kalshi(m: NormalizedMarket) -> str:
    parts = [m.question]
    if m.rules_primary:
        parts.append(m.rules_primary[:300])
    if m.rules_secondary:
        parts.append(m.rules_secondary[:200])
    if m.close_time:
        parts.append(f"(resolves {m.close_time})")
    return " ".join(parts)


def _build_market_text(m: NormalizedMarket) -> str:
    if m.source == "polymarket":
        return _build_market_text_polymarket(m)
    return _build_market_text_kalshi(m)


# ── Structural scoring signals ───────────────────────────────────────────────


def _category_score(poly_event: NormalizedEvent, kalshi_event: NormalizedEvent) -> float:
    """1.0 if same canonical category, 0.5 if either is 'Other'/unknown, 0.0 if different."""
    from comparator import normalize_category
    pc = normalize_category(poly_event.category)
    kc = normalize_category(kalshi_event.category)
    if pc == kc:
        return 1.0
    if pc == "Other" or kc == "Other":
        return 0.5
    return 0.0


def _bracket_count_score(poly_event: NormalizedEvent, kalshi_event: NormalizedEvent) -> float:
    """1.0 if identical bracket count, decaying toward 0.5 as counts diverge."""
    pc = len(poly_event.markets)
    kc = len(kalshi_event.markets)
    if pc == 0 or kc == 0:
        return 0.75
    ratio = min(pc, kc) / max(pc, kc)
    return 0.5 + 0.5 * ratio


def _date_proximity_score(poly_event: NormalizedEvent, kalshi_event: NormalizedEvent) -> float:
    """Soft exponential decay based on end_date difference.

    Uses a very slow decay (2000-day constant) and a high floor (0.6) because
    Kalshi settlement dates are systematically 1+ year past the actual event
    date. The arbitrage layer enforces the strict date cutoff — the matcher
    should not aggressively penalize date differences.
    """
    from comparator import _days_apart
    days = _days_apart(poly_event.end_date, kalshi_event.end_date)
    if days is None:
        return 0.85
    if days == 0:
        return 1.0
    return max(0.6, math.exp(-days / 2000.0))


def _composite_event_score(
    cosine: float,
    cat_score: float,
    bracket_score: float,
    date_score: float,
) -> float:
    """
    Weighted geometric mean: cosine dominates (70%), structural signals
    are tie-breakers (10% each). Geometric mean ensures all signals must
    be reasonable.
    """
    return min(1.0,
        math.pow(max(cosine, 0.01), 0.70)
        * math.pow(max(cat_score, 0.01), 0.10)
        * math.pow(max(bracket_score, 0.01), 0.10)
        * math.pow(max(date_score, 0.01), 0.10)
    )


# ── Embedding persistence ───────────────────────────────────────────────────


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _get_embedding_cache():
    """Lazy import to avoid circular dependencies."""
    from cache import _conn, _CACHE_DIR
    _CACHE_DIR.mkdir(exist_ok=True)
    conn = _conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS embeddings (
            entity_id   TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            source      TEXT NOT NULL,
            text_hash   TEXT NOT NULL,
            vector      BLOB NOT NULL,
            cached_at   TEXT NOT NULL,
            PRIMARY KEY (entity_id, entity_type, source)
        );
    """)
    return conn


def _load_cached_vectors(
    entities: list,
    entity_type: str,
    get_id: callable,
    get_source: callable,
    get_text: callable,
) -> tuple[dict[int, np.ndarray], list[int]]:
    """
    Load cached embeddings for entities. Returns:
      - cached: dict mapping entity index → vector
      - misses: list of indices that need embedding
    """
    conn = _get_embedding_cache()
    cached: dict[int, np.ndarray] = {}
    misses: list[int] = []

    for i, entity in enumerate(entities):
        eid = get_id(entity)
        source = get_source(entity)
        text = get_text(entity)
        thash = _text_hash(text)

        row = conn.execute(
            "SELECT text_hash, vector FROM embeddings "
            "WHERE entity_id = ? AND entity_type = ? AND source = ?",
            (eid, entity_type, source),
        ).fetchone()

        if row and row["text_hash"] == thash:
            vec = np.frombuffer(row["vector"], dtype=np.float32).copy()
            cached[i] = vec
        else:
            misses.append(i)

    conn.close()
    return cached, misses


def _save_vectors(
    entities: list,
    indices: list[int],
    vectors: np.ndarray,
    entity_type: str,
    get_id: callable,
    get_source: callable,
    get_text: callable,
) -> None:
    """Save newly computed embeddings to the cache."""
    from datetime import datetime, timezone
    conn = _get_embedding_cache()
    now = datetime.now(timezone.utc).isoformat()

    with conn:
        for idx_pos, entity_idx in enumerate(indices):
            entity = entities[entity_idx]
            conn.execute(
                "INSERT OR REPLACE INTO embeddings "
                "(entity_id, entity_type, source, text_hash, vector, cached_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    get_id(entity),
                    entity_type,
                    get_source(entity),
                    _text_hash(get_text(entity)),
                    vectors[idx_pos].tobytes(),
                    now,
                ),
            )
    conn.close()


def _embed_with_cache(
    entities: list,
    entity_type: str,
    get_id: callable,
    get_source: callable,
    get_text: callable,
) -> np.ndarray:
    """
    Embed entities, using cached vectors where available.
    Only calls the Gemini API for entities whose text has changed or is not cached.
    Returns an (N, D) array aligned with the input list.
    """
    if not entities:
        return np.empty((0, 0), dtype=np.float32)

    cached, misses = _load_cached_vectors(
        entities, entity_type, get_id, get_source, get_text,
    )

    if misses:
        from clients.embeddings import embed_texts
        texts_to_embed = [get_text(entities[i]) for i in misses]
        new_vectors = embed_texts(texts_to_embed)
        _save_vectors(entities, misses, new_vectors, entity_type, get_id, get_source, get_text)
        for idx_pos, entity_idx in enumerate(misses):
            cached[entity_idx] = new_vectors[idx_pos]

    dim = next(iter(cached.values())).shape[0] if cached else 0
    result = np.zeros((len(entities), dim), dtype=np.float32)
    for i, vec in cached.items():
        result[i] = vec
    return result


# ── Greedy assignment ────────────────────────────────────────────────────────


def _greedy_assign(left: list, right: list, sim: np.ndarray, min_score: float, make_result) -> list:
    """Greedy best-first 1-to-1 assignment (same as V1, incremental-friendly for V2.1)."""
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


# ── V2 Matcher class ────────────────────────────────────────────────────────


class GeminiRichMatcher:
    """
    V2 EventMatcher implementation.

    - Rich embedding text (title + description/rules + tags + dates + brackets)
    - Gemini-only (no fuzzy fallback; raises if no API key)
    - Composite confidence scoring at event level (cosine + structural signals)
    - Greedy best-first assignment (incremental-friendly for V2.1)
    - Embedding persistence in SQLite (avoids re-embedding unchanged events)
    """

    def match_events(
        self,
        poly_events: list[NormalizedEvent],
        kalshi_events: list[NormalizedEvent],
        min_score: float,
    ) -> list[MatchResult]:
        from config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            raise RuntimeError("V2 matcher requires GEMINI_API_KEY (no fuzzy fallback)")

        if not poly_events or not kalshi_events:
            return []

        from clients.embeddings import cosine_similarity_matrix

        # Embed with caching — only new/changed events hit the API
        pv = _embed_with_cache(
            poly_events, "event",
            lambda e: e.id, lambda e: e.source, _build_event_text,
        )
        kv = _embed_with_cache(
            kalshi_events, "event",
            lambda e: e.id, lambda e: e.source, _build_event_text,
        )
        cosine_sim = cosine_similarity_matrix(pv, kv)

        # Build composite score matrix
        composite = np.zeros_like(cosine_sim)
        for i, pe in enumerate(poly_events):
            for j, ke in enumerate(kalshi_events):
                composite[i, j] = _composite_event_score(
                    cosine_sim[i, j],
                    _category_score(pe, ke),
                    _bracket_count_score(pe, ke),
                    _date_proximity_score(pe, ke),
                )

        return _greedy_assign(
            poly_events, kalshi_events, composite, min_score,
            lambda pe, ke, s: MatchResult(poly_event=pe, kalshi_event=ke, score=s),
        )

    def match_markets(
        self,
        poly_markets: list[NormalizedMarket],
        kalshi_markets: list[NormalizedMarket],
        min_score: float,
    ) -> list[MarketMatchResult]:
        from config import GEMINI_API_KEY
        if not GEMINI_API_KEY:
            raise RuntimeError("V2 matcher requires GEMINI_API_KEY (no fuzzy fallback)")

        if not poly_markets or not kalshi_markets:
            return []

        from clients.embeddings import cosine_similarity_matrix

        # Embed with caching — only new/changed markets hit the API
        pv = _embed_with_cache(
            poly_markets, "market",
            lambda m: m.market_id, lambda m: m.source, _build_market_text,
        )
        kv = _embed_with_cache(
            kalshi_markets, "market",
            lambda m: m.market_id, lambda m: m.source, _build_market_text,
        )
        sim = cosine_similarity_matrix(pv, kv)

        return _greedy_assign(
            poly_markets, kalshi_markets, sim, min_score,
            lambda pm, km, s: MarketMatchResult(poly_market=pm, kalshi_market=km, score=s),
        )
