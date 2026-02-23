"""Persistent cache for semantic match results.

Stores matched (PM market_id, KS market_ticker) pairs and their scores in a
local SQLite database so that subsequent runs can skip re-embedding already-
known market pairs.

The cache is keyed at the event-pair level: (pm_event_id, ks_event_ticker).
For each event pair we store every sub-market match. On a cache hit we
reconstruct MarketMatchResult objects using live market data (fresh prices)
and cached match scores — so prices are always current even on cache runs.

Cache invalidation: if any market in the current fetch has an ID not present
in the cached set (i.e. a new bracket appeared), the event pair is considered
stale and the full embedding pipeline is re-run for that pair.

Cache location: .cache/market_matches.db  (SQLite, sibling to this file)
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from models import NormalizedEvent, NormalizedMarket, MatchResult, MarketMatchResult

_CACHE_DIR = Path(__file__).parent / ".cache"
_CACHE_DB = _CACHE_DIR / "market_matches.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS event_pairs (
    pm_event_id     TEXT NOT NULL,
    ks_event_ticker TEXT NOT NULL,
    event_score     REAL NOT NULL,
    pm_title        TEXT,
    ks_title        TEXT,
    pm_url          TEXT,
    ks_url          TEXT,
    cached_at       TEXT NOT NULL,
    PRIMARY KEY (pm_event_id, ks_event_ticker)
);

CREATE TABLE IF NOT EXISTS market_pairs (
    pm_event_id      TEXT NOT NULL,
    ks_event_ticker  TEXT NOT NULL,
    pm_market_id     TEXT NOT NULL,
    ks_market_ticker TEXT NOT NULL,
    match_score      REAL NOT NULL,
    pm_question      TEXT,
    ks_question      TEXT,
    pm_url           TEXT,
    ks_url           TEXT,
    pm_close_time    TEXT,
    ks_close_time    TEXT,
    cached_at        TEXT NOT NULL,
    PRIMARY KEY (pm_market_id, ks_market_ticker)
);
"""


def _conn() -> sqlite3.Connection:
    _CACHE_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(_CACHE_DB)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


# ── Write ─────────────────────────────────────────────────────────────────────


def save_match(event_match: MatchResult, market_matches: list[MarketMatchResult]) -> None:
    """Persist an event pair and all its sub-market matches to the cache."""
    now = datetime.now(timezone.utc).isoformat()
    pe = event_match.poly_event
    ke = event_match.kalshi_event

    conn = _conn()
    with conn:
        conn.execute(
            """INSERT OR REPLACE INTO event_pairs
               (pm_event_id, ks_event_ticker, event_score,
                pm_title, ks_title, pm_url, ks_url, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (pe.id, ke.id, event_match.score,
             pe.title, ke.title, pe.url, ke.url, now),
        )
        for mm in market_matches:
            conn.execute(
                """INSERT OR REPLACE INTO market_pairs
                   (pm_event_id, ks_event_ticker,
                    pm_market_id, ks_market_ticker, match_score,
                    pm_question, ks_question, pm_url, ks_url,
                    pm_close_time, ks_close_time, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    pe.id, ke.id,
                    mm.poly_market.market_id,
                    mm.kalshi_market.market_id,
                    mm.score,
                    mm.poly_market.question,
                    mm.kalshi_market.question,
                    mm.poly_market.url,
                    mm.kalshi_market.url,
                    mm.poly_market.close_time,
                    mm.kalshi_market.close_time,
                    now,
                ),
            )
    conn.close()


# ── Read ──────────────────────────────────────────────────────────────────────


def load_cached_market_matches(
    pm_event: NormalizedEvent,
    ks_event: NormalizedEvent,
) -> list[MarketMatchResult] | None:
    """Return cached sub-market matches for an event pair, with fresh prices.

    Reconstructs MarketMatchResult objects using the live NormalizedMarket
    data (so yes/no prices are always current) combined with cached scores.

    Returns None if:
    - No cache entry exists for this (pm_event_id, ks_event_ticker) pair.
    - Any current market ID/ticker is absent from the cached set (new bracket
      appeared since last run — must re-embed to cover it).
    """
    conn = _conn()
    rows = conn.execute(
        "SELECT pm_market_id, ks_market_ticker, match_score "
        "FROM market_pairs WHERE pm_event_id = ? AND ks_event_ticker = ?",
        (pm_event.id, ks_event.id),
    ).fetchall()
    conn.close()

    if not rows:
        return None

    pm_by_id = {m.market_id: m for m in pm_event.markets}
    ks_by_ticker = {m.market_id: m for m in ks_event.markets}

    cached_pm_ids = {r["pm_market_id"] for r in rows}
    cached_ks_tickers = {r["ks_market_ticker"] for r in rows}

    # If any current market is NOT covered by cache, invalidate (new bracket)
    if not set(pm_by_id).issubset(cached_pm_ids) or not set(ks_by_ticker).issubset(cached_ks_tickers):
        return None

    results = []
    for row in rows:
        pm_m = pm_by_id.get(row["pm_market_id"])
        ks_m = ks_by_ticker.get(row["ks_market_ticker"])
        if pm_m and ks_m:
            results.append(MarketMatchResult(
                poly_market=pm_m,
                kalshi_market=ks_m,
                score=row["match_score"],
            ))

    return results if results else None


def lookup_event_pair(pm_event_id: str, ks_event_ticker: str) -> dict | None:
    """Return the cached event pair record, or None if not cached."""
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM event_pairs WHERE pm_event_id = ? AND ks_event_ticker = ?",
        (pm_event_id, ks_event_ticker),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def all_event_pairs() -> list[dict]:
    """Return every cached event pair (useful for external ID mapping scripts)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT pm_event_id, ks_event_ticker, event_score, "
        "pm_title, ks_title, pm_url, ks_url, cached_at "
        "FROM event_pairs ORDER BY cached_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def all_market_pairs() -> list[dict]:
    """Return every cached market pair with full metadata."""
    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM market_pairs ORDER BY cached_at DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Maintenance ───────────────────────────────────────────────────────────────


def clear_cache() -> None:
    """Delete all cached match data."""
    conn = _conn()
    with conn:
        conn.execute("DELETE FROM market_pairs")
        conn.execute("DELETE FROM event_pairs")
    conn.close()


def cache_stats() -> dict:
    """Return a summary of what's stored in the cache."""
    conn = _conn()
    ep = conn.execute("SELECT COUNT(*) FROM event_pairs").fetchone()[0]
    mp = conn.execute("SELECT COUNT(*) FROM market_pairs").fetchone()[0]
    oldest = conn.execute("SELECT MIN(cached_at) FROM event_pairs").fetchone()[0]
    newest = conn.execute("SELECT MAX(cached_at) FROM event_pairs").fetchone()[0]
    conn.close()
    return {
        "event_pairs": ep,
        "market_pairs": mp,
        "oldest_entry": oldest,
        "newest_entry": newest,
        "db_path": str(_CACHE_DB),
    }
