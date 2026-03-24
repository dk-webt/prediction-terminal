#!/usr/bin/env python3
"""
Test harness for semantic matching algorithms.

Usage:
    # Snapshot live events to a fixture file (run once, reuse for all tests)
    python3 test_matching.py snapshot --limit 200

    # Compare V1 vs V2 on the saved fixture
    python3 test_matching.py compare

    # Run a single matcher and inspect results
    python3 test_matching.py run --matcher v2 --event-min 0.75 --market-min 0.82

    # Show the top-N cosine pairs with score breakdowns (no assignment)
    python3 test_matching.py topk --k 30

    # Grade cached results: mark matches as correct/wrong, save labels
    python3 test_matching.py grade

    # Score matchers against saved labels
    python3 test_matching.py score
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

from models import NormalizedEvent, NormalizedMarket

FIXTURE_DIR = Path(__file__).parent / "test_fixtures"
FIXTURE_PM = FIXTURE_DIR / "pm_events.json"
FIXTURE_KS = FIXTURE_DIR / "ks_events.json"
LABELS_FILE = FIXTURE_DIR / "labels.json"


# ── Serialization ────────────────────────────────────────────────────────────


def _event_to_dict(e: NormalizedEvent) -> dict:
    return {
        "source": e.source, "id": e.id, "title": e.title,
        "category": e.category, "volume": e.volume, "liquidity": e.liquidity,
        "end_date": e.end_date, "url": e.url,
        "description": e.description, "tags": e.tags, "sub_title": e.sub_title,
        "markets": [_market_to_dict(m) for m in e.markets],
    }


def _market_to_dict(m: NormalizedMarket) -> dict:
    return {
        "question": m.question, "yes_price": m.yes_price, "no_price": m.no_price,
        "volume": m.volume, "source": m.source, "market_id": m.market_id,
        "parent_event_id": m.parent_event_id, "parent_event_title": m.parent_event_title,
        "close_time": m.close_time, "url": m.url,
        "description": m.description, "group_item_title": m.group_item_title,
        "rules_primary": m.rules_primary, "rules_secondary": m.rules_secondary,
    }


def _dict_to_market(d: dict) -> NormalizedMarket:
    return NormalizedMarket(
        question=d["question"], yes_price=d["yes_price"], no_price=d["no_price"],
        volume=d["volume"], source=d.get("source", ""), market_id=d.get("market_id", ""),
        parent_event_id=d.get("parent_event_id", ""),
        parent_event_title=d.get("parent_event_title", ""),
        close_time=d.get("close_time", ""), url=d.get("url", ""),
        description=d.get("description", ""),
        group_item_title=d.get("group_item_title", ""),
        rules_primary=d.get("rules_primary", ""),
        rules_secondary=d.get("rules_secondary", ""),
    )


def _dict_to_event(d: dict) -> NormalizedEvent:
    return NormalizedEvent(
        source=d["source"], id=d["id"], title=d["title"],
        category=d["category"], volume=d["volume"], liquidity=d["liquidity"],
        end_date=d["end_date"], url=d["url"],
        markets=[_dict_to_market(m) for m in d.get("markets", [])],
        description=d.get("description", ""),
        tags=d.get("tags", []),
        sub_title=d.get("sub_title", ""),
    )


def save_fixture(pm_events: list[NormalizedEvent], ks_events: list[NormalizedEvent]) -> None:
    FIXTURE_DIR.mkdir(exist_ok=True)
    with open(FIXTURE_PM, "w") as f:
        json.dump([_event_to_dict(e) for e in pm_events], f, indent=2)
    with open(FIXTURE_KS, "w") as f:
        json.dump([_event_to_dict(e) for e in ks_events], f, indent=2)
    print(f"Saved {len(pm_events)} PM events and {len(ks_events)} KS events to {FIXTURE_DIR}/")


def load_fixture() -> tuple[list[NormalizedEvent], list[NormalizedEvent]]:
    if not FIXTURE_PM.exists() or not FIXTURE_KS.exists():
        print("No fixture found. Run: python3 test_matching.py snapshot --limit 200")
        sys.exit(1)
    with open(FIXTURE_PM) as f:
        pm = [_dict_to_event(d) for d in json.load(f)]
    with open(FIXTURE_KS) as f:
        ks = [_dict_to_event(d) for d in json.load(f)]
    print(f"Loaded fixture: {len(pm)} PM events, {len(ks)} KS events")
    return pm, ks


# ── Labels (human ground truth) ─────────────────────────────────────────────


def load_labels() -> dict[str, bool]:
    """Load saved labels. Key format: 'pm_id::ks_id'."""
    if not LABELS_FILE.exists():
        return {}
    with open(LABELS_FILE) as f:
        return json.load(f)


def save_labels(labels: dict[str, bool]) -> None:
    FIXTURE_DIR.mkdir(exist_ok=True)
    with open(LABELS_FILE, "w") as f:
        json.dump(labels, f, indent=2)


def _label_key(pm_id: str, ks_id: str) -> str:
    return f"{pm_id}::{ks_id}"


# ── Matcher helpers ──────────────────────────────────────────────────────────


def get_matcher(name: str):
    if name == "v1":
        from matchers.v1 import GeminiFuzzyMatcher
        return GeminiFuzzyMatcher()
    elif name == "v2":
        from matchers.v2 import GeminiRichMatcher
        return GeminiRichMatcher()
    else:
        raise ValueError(f"Unknown matcher: {name}. Use 'v1' or 'v2'.")


# ── Commands ─────────────────────────────────────────────────────────────────


def cmd_snapshot(args):
    """Fetch live events and save as a test fixture."""
    from clients.polymarket import fetch_events as pm_fetch
    from clients.kalshi import fetch_events as ks_fetch

    print(f"Fetching {args.limit} events from each platform...")
    pm = pm_fetch(limit=args.limit)
    ks = ks_fetch(limit=args.limit)
    print(f"  PM: {len(pm)} events, KS: {len(ks)} events")
    save_fixture(pm, ks)


def cmd_run(args):
    """Run a single matcher and show results."""
    pm, ks = load_fixture()
    matcher = get_matcher(args.matcher)
    labels = load_labels()

    print(f"\nRunning {args.matcher} matcher (event_min={args.event_min}, market_min={args.market_min})...")
    start = time.time()
    matches = matcher.match_events(pm, ks, args.event_min)
    elapsed = time.time() - start

    print(f"  {len(matches)} event matches in {elapsed:.1f}s\n")

    for i, m in enumerate(sorted(matches, key=lambda x: x.score, reverse=True)):
        key = _label_key(m.poly_event.id, m.kalshi_event.id)
        label = labels.get(key)
        label_str = " ✓" if label is True else " ✗" if label is False else ""
        print(f"  {i+1:3d}. [{m.score:.4f}]{label_str}")
        print(f"       PM: {m.poly_event.title[:70]} ({len(m.poly_event.markets)} mkts)")
        print(f"       KS: {m.kalshi_event.title[:70]} ({len(m.kalshi_event.markets)} mkts)")

        # Show sub-market matches
        if args.show_markets:
            pm_mkts = m.poly_event.markets
            ks_mkts = m.kalshi_event.markets
            if pm_mkts and ks_mkts:
                market_matches = matcher.match_markets(pm_mkts, ks_mkts, args.market_min)
                for mm in market_matches[:5]:
                    print(f"         [{mm.score:.4f}] {mm.poly_market.question[:40]} ↔ {mm.kalshi_market.question[:40]}")
        print()


def cmd_compare(args):
    """Run V1 and V2 side by side on the same fixture."""
    pm, ks = load_fixture()
    labels = load_labels()

    results = {}
    for name in ["v1", "v2"]:
        matcher = get_matcher(name)
        start = time.time()
        matches = matcher.match_events(pm, ks, args.event_min)
        elapsed = time.time() - start
        results[name] = {m.poly_event.id + "::" + m.kalshi_event.id: m for m in matches}
        print(f"{name.upper()}: {len(matches)} matches in {elapsed:.1f}s")

    v1_set = set(results["v1"].keys())
    v2_set = set(results["v2"].keys())
    both = v1_set & v2_set
    v1_only = v1_set - v2_set
    v2_only = v2_set - v1_set

    print(f"\n  Both:    {len(both)}")
    print(f"  V1 only: {len(v1_only)}")
    print(f"  V2 only: {len(v2_only)}")

    if labels:
        for name, result_set in [("v1", v1_set), ("v2", v2_set)]:
            correct = sum(1 for k in result_set if labels.get(k) is True)
            wrong = sum(1 for k in result_set if labels.get(k) is False)
            unlabeled = len(result_set) - correct - wrong
            print(f"  {name.upper()} accuracy: {correct} correct, {wrong} wrong, {unlabeled} unlabeled")

    if v1_only:
        print(f"\n── V1-only matches ──")
        for key in sorted(v1_only):
            m = results["v1"][key]
            label = labels.get(key)
            tag = " ✓" if label is True else " ✗" if label is False else ""
            print(f"  [{m.score:.4f}]{tag} {m.poly_event.title[:40]} ↔ {m.kalshi_event.title[:40]}")

    if v2_only:
        print(f"\n── V2-only matches ──")
        for key in sorted(v2_only):
            m = results["v2"][key]
            label = labels.get(key)
            tag = " ✓" if label is True else " ✗" if label is False else ""
            print(f"  [{m.score:.4f}]{tag} {m.poly_event.title[:40]} ↔ {m.kalshi_event.title[:40]}")

    if both:
        print(f"\n── Shared matches (score comparison) ──")
        shared = []
        for key in both:
            v1_score = results["v1"][key].score
            v2_score = results["v2"][key].score
            title = results["v1"][key].poly_event.title[:40]
            shared.append((v2_score - v1_score, v1_score, v2_score, title, key))
        shared.sort(reverse=True)
        for diff, v1s, v2s, title, key in shared[:20]:
            label = labels.get(key)
            tag = " ✓" if label is True else " ✗" if label is False else ""
            print(f"  V1={v1s:.4f} V2={v2s:.4f} (Δ{diff:+.4f}){tag} {title}")


def cmd_topk(args):
    """Show top-K cosine pairs with full score breakdowns (no assignment)."""
    pm, ks = load_fixture()

    from matchers.v2 import (
        _build_event_text, _embed_with_cache, _composite_event_score,
        _category_score, _bracket_count_score, _date_proximity_score,
    )
    from clients.embeddings import cosine_similarity_matrix

    print("Computing embeddings...")
    pv = _embed_with_cache(pm, "event", lambda e: e.id, lambda e: e.source, _build_event_text)
    kv = _embed_with_cache(ks, "event", lambda e: e.id, lambda e: e.source, _build_event_text)
    cosine = cosine_similarity_matrix(pv, kv)

    pairs = []
    for i in range(len(pm)):
        for j in range(len(ks)):
            cs = _category_score(pm[i], ks[j])
            bs = _bracket_count_score(pm[i], ks[j])
            ds = _date_proximity_score(pm[i], ks[j])
            comp = _composite_event_score(cosine[i, j], cs, bs, ds)
            pairs.append((comp, cosine[i, j], cs, bs, ds, i, j))
    pairs.sort(reverse=True)

    labels = load_labels()
    print(f"\nTop {args.k} pairs by composite score:\n")
    print(f"{'#':>3}  {'Comp':>6}  {'Cos':>6}  {'Cat':>4}  {'Bkt':>4}  {'Date':>5}  {'Label':>5}  PM → KS")
    print("─" * 100)
    for rank, (comp, cos, cs, bs, ds, i, j) in enumerate(pairs[:args.k], 1):
        key = _label_key(pm[i].id, ks[j].id)
        label = labels.get(key)
        tag = "  ✓" if label is True else "  ✗" if label is False else "   "
        pm_title = pm[i].title[:35]
        ks_title = ks[j].title[:35]
        print(f"{rank:3d}  {comp:.4f}  {cos:.4f}  {cs:.2f}  {bs:.2f}  {ds:.3f}  {tag}  {pm_title} → {ks_title}")


def cmd_grade(args):
    """Interactively grade matches as correct or wrong."""
    pm, ks = load_fixture()
    labels = load_labels()
    matcher = get_matcher(args.matcher)

    print(f"Running {args.matcher} matcher...")
    matches = matcher.match_events(pm, ks, args.event_min)
    matches.sort(key=lambda m: m.score, reverse=True)

    labeled_count = 0
    print(f"\n{len(matches)} matches to grade. Press Enter to skip, 'y' for correct, 'n' for wrong, 'q' to quit.\n")

    for i, m in enumerate(matches):
        key = _label_key(m.poly_event.id, m.kalshi_event.id)
        existing = labels.get(key)
        if existing is not None and not args.regrade:
            continue

        existing_str = f" [currently: {'✓' if existing else '✗'}]" if existing is not None else ""
        print(f"  {i+1}/{len(matches)} [{m.score:.4f}]{existing_str}")
        print(f"    PM: {m.poly_event.title}")
        print(f"    KS: {m.kalshi_event.title}")

        # Show bracket context
        pm_brackets = [mk.group_item_title or mk.question[:30] for mk in m.poly_event.markets[:5]]
        ks_brackets = [mk.question[:30] for mk in m.kalshi_event.markets[:5]]
        if pm_brackets:
            print(f"    PM brackets: {', '.join(pm_brackets)}")
        if ks_brackets:
            print(f"    KS brackets: {', '.join(ks_brackets)}")

        try:
            answer = input("    Grade [y/n/skip/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if answer == "q":
            break
        elif answer == "y":
            labels[key] = True
            labeled_count += 1
        elif answer == "n":
            labels[key] = False
            labeled_count += 1

    save_labels(labels)
    total_labeled = sum(1 for v in labels.values() if v is not None)
    correct = sum(1 for v in labels.values() if v is True)
    wrong = sum(1 for v in labels.values() if v is False)
    print(f"\nSaved {labeled_count} new labels. Total: {total_labeled} ({correct} correct, {wrong} wrong)")


def cmd_score(args):
    """Score all matchers against saved labels."""
    pm, ks = load_fixture()
    labels = load_labels()

    if not labels:
        print("No labels found. Run: python3 test_matching.py grade")
        sys.exit(1)

    print(f"Labels: {sum(1 for v in labels.values() if v is True)} correct, "
          f"{sum(1 for v in labels.values() if v is False)} wrong\n")

    for name in ["v1", "v2"]:
        matcher = get_matcher(name)
        matches = matcher.match_events(pm, ks, args.event_min)

        matched_keys = {_label_key(m.poly_event.id, m.kalshi_event.id) for m in matches}

        # True positives: matched and labeled correct
        tp = sum(1 for k in matched_keys if labels.get(k) is True)
        # False positives: matched but labeled wrong
        fp = sum(1 for k in matched_keys if labels.get(k) is False)
        # False negatives: not matched but labeled correct
        fn = sum(1 for k, v in labels.items() if v is True and k not in matched_keys)
        # Unlabeled matches
        unlabeled = len(matched_keys) - tp - fp

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        print(f"  {name.upper()}: {len(matches)} matches | "
              f"TP={tp} FP={fp} FN={fn} unlabeled={unlabeled} | "
              f"P={precision:.2f} R={recall:.2f} F1={f1:.2f}")


# ── CLI ──────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Test harness for semantic matching")
    sub = parser.add_subparsers(dest="command")

    p_snap = sub.add_parser("snapshot", help="Fetch live events and save as fixture")
    p_snap.add_argument("--limit", type=int, default=200)

    p_run = sub.add_parser("run", help="Run a matcher and show results")
    p_run.add_argument("--matcher", default="v2", choices=["v1", "v2"])
    p_run.add_argument("--event-min", type=float, default=0.75)
    p_run.add_argument("--market-min", type=float, default=0.82)
    p_run.add_argument("--show-markets", action="store_true")

    p_cmp = sub.add_parser("compare", help="Compare V1 vs V2 side by side")
    p_cmp.add_argument("--event-min", type=float, default=0.75)

    p_topk = sub.add_parser("topk", help="Show top-K pairs with score breakdowns")
    p_topk.add_argument("--k", type=int, default=30)

    p_grade = sub.add_parser("grade", help="Interactively grade matches")
    p_grade.add_argument("--matcher", default="v2", choices=["v1", "v2"])
    p_grade.add_argument("--event-min", type=float, default=0.75)
    p_grade.add_argument("--regrade", action="store_true", help="Re-grade already labeled pairs")

    p_score = sub.add_parser("score", help="Score matchers against saved labels")
    p_score.add_argument("--event-min", type=float, default=0.75)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    {"snapshot": cmd_snapshot, "run": cmd_run, "compare": cmd_compare,
     "topk": cmd_topk, "grade": cmd_grade, "score": cmd_score}[args.command](args)


if __name__ == "__main__":
    main()
