# Semantic Matching V2 — API Field Research

> Research conducted 2026-03-17 via live API calls. All field names verified against actual responses.

---

## 1. API Structure & Endpoints

### Polymarket — `GET https://gamma-api.polymarket.com/events`

```bash
curl -s "https://gamma-api.polymarket.com/events?limit=5&active=true&closed=false&order=volume24hr&ascending=false"
```

- Returns: flat JSON array of event objects, each with nested `markets[]`
- Pagination: offset-based (`limit` + `offset`, max 100 per page)
- Two levels: **Event → Markets** (sub-markets/brackets)

### Kalshi — `GET https://api.elections.kalshi.com/trade-api/v2/events`

```bash
curl -s "https://api.elections.kalshi.com/trade-api/v2/events?limit=5&with_nested_markets=true&status=open"
```

- Returns: `{ "events": [...], "cursor": "..." }`
- Pagination: cursor-based (max 200 per page)
- Two levels: **Event → Markets** (sub-markets/brackets)
- Note: there is also a **Series** level (`series_ticker` on each event), but no `/series/{id}` endpoint returns additional metadata worth using. The series just groups related events.

### Other endpoints

Both platforms have trade execution endpoints (Phase 3), but no additional *read* endpoints that return richer metadata than the list endpoints above.

---

## 2. Complete Field Inventory

### Polymarket: Event-Level Fields

| Field | Present | Used in V1? | Useful for V2? | Notes |
|-------|---------|-------------|-----------------|-------|
| `id` | Always | Yes | — | Event identifier |
| `title` | Always | Yes (embedded) | Yes | Primary text for matching |
| `description` | **Always** | **No** | **Yes — high value** | Full paragraph: what the event is, how it resolves. ~100-500 chars |
| `category` | **Sometimes null** | Fallback to tags | Yes | Often `null`; tags are more reliable |
| `tags[]` | Always (may be empty) | First tag as cat fallback | **Yes — high value** | Array of `{label}` objects. Rich semantic labels like "Fed Rates", "Jerome Powell", "Soccer", "UCL" |
| `slug` | Always | Yes (URL) | — | |
| `endDate` | Always | Yes (filtering) | Yes (embed temporal context) | ISO date |
| `volume` / `volume24hr` | Always | volume only | volume24hr for recency weighting | |
| `liquidity` | Always | Yes | — | |
| `resolutionSource` | Sometimes (URL) | No | Low value | Usually empty or a URL like `https://www.uefa.com/` |
| `competitive` | Always | No | Maybe | 0.0-1.0 competitiveness score |
| `negRisk` | Always | No | Structural signal | Indicates negative-risk / mutually-exclusive bracket structure |
| `commentCount` | Always | No | Low value | Popularity signal |

### Polymarket: Market-Level Fields

| Field | Present | Used in V1? | Useful for V2? | Notes |
|-------|---------|-------------|-----------------|-------|
| `question` | Always | Yes (embedded) | Yes | Full yes/no question text |
| `description` | **Always** | **No** | **Yes — high value** | Same as event description (resolution criteria). Identical across brackets of same event |
| `groupItemTitle` | On bracket markets | **No** | **Yes — medium value** | Short label: "50+ bps decrease", "Chelsea FC", "Stephen A. Smith". Only on multi-market events |
| `bestAsk` / `bestBid` | Usually | Yes | — | |
| `outcomePrices` | Always | Yes (fallback) | — | JSON-encoded string |
| `endDate` | Always | Yes | — | |
| `volume` / `volume24hr` | Always | volume only | — | |
| `lastTradePrice` | Usually | No | — | |
| `spread` | Usually | No | Low | Bid-ask spread |
| `resolutionSource` | Sometimes | No | Low | |

### Kalshi: Event-Level Fields

| Field | Present | Used in V1? | Useful for V2? | Notes |
|-------|---------|-------------|-----------------|-------|
| `event_ticker` | Always | Yes (id) | — | |
| `title` | Always | Yes (embedded) | Yes | Primary text |
| `sub_title` | **Always** | **No** | **Yes — medium value** | Temporal qualifier: "Before 2099", "Before 2050", "Between 2025 and 2035?" |
| `category` | Always | Yes (filtering) | Yes | Always populated (unlike PM). Values: "World", "Politics", "Financials", "Climate and Weather", "Science and Technology", "Social", "Entertainment" |
| `series_ticker` | Always | No | Low | Groups related events (e.g. KXNEWPOPE) |
| `mutually_exclusive` | Always | **No** | **Yes — structural** | Whether outcomes partition the space. `true` for "who will be pope", `false` for "will X happen" |
| `liquidity` | Always | Yes | — | |

**Kalshi has NO `description` field at the event level.**

### Kalshi: Market-Level Fields

| Field | Present | Used in V1? | Useful for V2? | Notes |
|-------|---------|-------------|-----------------|-------|
| `title` | Always | Yes (via `_build_question`) | Yes | Repeats event title for all sub-markets |
| `no_sub_title` | Always | Yes (combined with title) | Yes | Distinguishing option: "Pietro Parolin", "Before 2050", "Brex" |
| `yes_sub_title` | Always | **No** | Low | Usually mirrors `no_sub_title` |
| `rules_primary` | **Always** | **No** | **Yes — highest value** | Explicit resolution condition. 1-2 sentences. E.g. *"If Pietro Parolin becomes the first person elected Pope before Jan 1, 2070, then the market resolves to Yes."* |
| `rules_secondary` | Sometimes | **No** | **Yes** | Additional resolution detail. Often empty, but when present it's valuable (e.g. *"At least two of the Source Agencies must report..."*) |
| `early_close_condition` | Always | No | Low | Usually "This market will close and expire early if the event occurs." |
| `subtitle` | Always | No | Low | Always empty string in all samples |
| `expected_expiration_time` | Always | No | Yes (date fix) | Better than `close_time` for true resolution date |
| `close_time` | Always | Yes | — | Settlement deadline (often 1+ year out) |
| `volume_fp` / `volume_24h_fp` | Always | volume_fp only | — | |
| `open_interest_fp` | Always | No | Low | |

---

## 3. Which Fields Are Universal vs Category-Specific?

**Tested across categories**: Politics, Sports, Financials, Weather, Science, Entertainment, Culture, World, Social

### Universal on ALL event types (both platforms)

| Field | Polymarket | Kalshi |
|-------|-----------|--------|
| Event title | `title` — always | `title` — always |
| Event end date | `endDate` — always | derived from `close_time` — always |
| Market question | `question` — always | `title` + `no_sub_title` — always |
| Market prices | `bestAsk`/`bestBid`/`outcomePrices` — always | `yes_ask_dollars`/`no_ask_dollars` — always |
| Volume | `volume` — always | `volume_fp` — always |
| **Description** | `description` — **always populated** | **NOT AVAILABLE at event level** |
| **Tags** | `tags[]` — **always present** (0-7 labels) | **NOT AVAILABLE** |
| **Resolution rules** | **NOT AVAILABLE as structured text** | `rules_primary` — **always populated** |
| **Category** | **Often null** (use tags fallback) | **Always populated** |

### Category-specific behavior

| Field | When present | When absent/empty |
|-------|-------------|-------------------|
| PM `groupItemTitle` | Multi-market bracket events (Fed rates, elections, sports matchups) | Single-market events |
| PM `resolutionSource` | Sports events (URL to UEFA, ESPN, etc.) | Most non-sports events (empty string) |
| KS `sub_title` | Always present, but content varies by type | — |
| KS `rules_secondary` | Complex events (weather with multiple source agencies) | Simple binary events (empty string) |
| KS `mutually_exclusive` | `true` for "who will X be" (pope, nominee) | `false` for "will X happen" |
| KS `no_sub_title` | Multi-market: candidate names, dates, thresholds | Single-market: often echoes the temporal qualifier |

**Key finding**: `description` (PM) and `rules_primary` (KS) are the richest text fields and are **always populated across all categories**. They are the #1 opportunity for V2 enrichment.

---

## 4. Kalshi Data Levels

Kalshi has a 3-tier conceptual hierarchy, but only 2 are accessible via the API:

```
Series (e.g. KXNEWPOPE)          <- NOT directly queryable (no /series/{id} endpoint)
  +-- Event (e.g. KXNEWPOPE-70)   <- GET /events -- title, category, sub_title
       +-- Market (e.g. KXNEWPOPE-70-PPAR)  <- nested in event via with_nested_markets=true
                                                title, no_sub_title, rules_primary, prices
```

### Does Kalshi have a description at another level?

- **Event level**: No `description` field exists.
- **Market level**: No `description` field, BUT `rules_primary` serves a similar purpose. It's a 1-2 sentence resolution condition that describes exactly what the market is about.
- **Series level**: Not queryable. The old code had a `GET /series/{series_ticker}` call but it was removed (N+1 problem). Unknown if it returned descriptions.

**Practical equivalent**: Kalshi's `rules_primary` (market-level) is the functional equivalent of Polymarket's `description` (event-level). Both explain what the market resolves on.

---

## 5. The Asymmetry Problem

The two platforms expose different rich-text fields:

| Rich text field | Polymarket | Kalshi |
|-----------------|-----------|--------|
| Description / Resolution logic | `description` (event + market level, identical text) | `rules_primary` (market level only) |
| Semantic tags | `tags[].label` (event level) | Not available |
| Temporal qualifier | Embedded in title/description | `sub_title` (event level) |
| Bracket label | `groupItemTitle` (market level) | `no_sub_title` (market level) |
| Category | Often null | Always populated |

### V2 approach: platform-specific embedding strings

Build a platform-specific "embedding string" that combines all available fields, then let the embedding model handle cross-platform alignment in vector space.

**Polymarket** (Fed March decision):
```
[Economy, Fed Rates] Fed decision in March?
Resolves: The FED interest rates are defined in this market by the upper bound
of the target federal funds range...
Brackets: 50+ bps decrease, 25 bps decrease, No change, 25 bps increase
(resolves 2026-03-18)
```

**Kalshi** (equivalent event):
```
[Financials] Fed rate decision (Before March 2026)
Rule: If the Federal Reserve decreases the federal funds target rate by 25bps
at the March 2026 FOMC meeting, then the market resolves to Yes.
(resolves 2026-03-19)
```

Despite different field sources, both produce semantically rich vectors in the same embedding space.

---

## 6. V2 Improvement Ideas

1. **Rich embedding text** — Combine title + description/rules + tags + category + date into a single embedding string per platform
2. **Category pre-filter** — Skip embedding PM events against KS events in different categories (requires cross-platform category mapping)
3. **Hungarian assignment** — Replace greedy with `scipy.optimize.linear_sum_assignment` for globally optimal 1:1 matching
4. **Composite confidence** — Weight cosine score by structural signals (category match, bracket count similarity, date proximity decay)
5. **Drop fuzzy fallback** — Gemini-only (user decision for V2)
6. **Re-ranking pass** (future V2.5) — LLM-based validation of top-K candidates to catch false positives

---

## 7. Real API Response Examples

### Polymarket — Fed decision event (excerpt)

```json
{
  "id": "67284",
  "title": "Fed decision in March?",
  "description": "The FED interest rates are defined in this market by the upper bound of the target federal funds range...",
  "category": null,
  "tags": [
    {"label": "Economy"},
    {"label": "Fed Rates"},
    {"label": "Jerome Powell"},
    {"label": "Economic Policy"},
    {"label": "Fed"}
  ],
  "endDate": "2026-03-18T00:00:00Z",
  "volume": 473867126.33,
  "negRisk": true,
  "markets": [
    {
      "question": "Will the Fed decrease interest rates by 50+ bps after the March 2026 meeting?",
      "description": "The FED interest rates are defined in this market by...",
      "groupItemTitle": "50+ bps decrease",
      "bestAsk": 0.001,
      "bestBid": 0.001,
      "outcomePrices": "[\"0.0005\", \"0.9995\"]"
    }
  ]
}
```

### Kalshi — Pope election event (excerpt)

```json
{
  "event_ticker": "KXNEWPOPE-70",
  "title": "Who will the next Pope be?",
  "sub_title": "Before 2070",
  "category": "World",
  "mutually_exclusive": true,
  "series_ticker": "KXNEWPOPE",
  "markets": [
    {
      "ticker": "KXNEWPOPE-70-PPAR",
      "title": "Who will the next Pope be?",
      "no_sub_title": "Pietro Parolin",
      "yes_sub_title": "Pietro Parolin",
      "rules_primary": "If Pietro Parolin becomes the first person elected Pope before Jan 1, 2070, then the market resolves to Yes.",
      "rules_secondary": "",
      "close_time": "2070-01-01T00:00:00Z",
      "expected_expiration_time": "2070-01-01T15:00:00Z",
      "yes_ask_dollars": "0.0900",
      "no_ask_dollars": "0.9100",
      "volume_fp": "11470.00"
    }
  ]
}
```
