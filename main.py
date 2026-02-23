#!/usr/bin/env python3
"""
Prediction Market Comparator
Compare events across Polymarket and Kalshi.

Usage:
  python main.py list --source polymarket [--limit N] [--category CAT]
  python main.py list --source kalshi     [--limit N] [--category CAT]
  python main.py compare                  [--limit N] [--category CAT] [--min-score N]
  python main.py compare --brackets       [--limit N] [--min-score N]
"""

import argparse
import sys
from collections import defaultdict

from rich.console import Console
from rich.table import Table
from rich import box
from rich.text import Text
from rich.panel import Panel
from rich.rule import Rule

from comparator import find_matches, find_market_matches, find_arbitrage, group_by_category, normalize_category
from models import NormalizedEvent, NormalizedMarket, MatchResult, MarketMatchResult, ArbitrageResult

console = Console()
_mobile: bool = False  # set True via --mobile; narrows tables and disables hyperlinks


# ── helpers ──────────────────────────────────────────────────────────────────


def _link(text: str, url: str) -> Text:
    """Clickable OSC 8 hyperlink on desktop; plain text in mobile mode."""
    if not _mobile and url:
        return Text(text, style=f"link {url}")
    return Text(text)


def _fmt_price(price: float) -> str:
    return f"{price * 100:.1f}¢"


def _fmt_volume(vol: float) -> str:
    if vol >= 1_000_000:
        return f"${vol / 1_000_000:.1f}M"
    if vol >= 1_000:
        return f"${vol / 1_000:.1f}K"
    return f"${vol:.0f}"


def _fmt_price_pair(m: NormalizedMarket) -> str:
    return f"Yes {_fmt_price(m.yes_price)} / No {_fmt_price(m.no_price)}"


def _fmt_price_pair_short(m: NormalizedMarket) -> str:
    """Compact price pair for narrow mobile columns: 'Y:51 N:49'."""
    return f"Y:{m.yes_price * 100:.0f} N:{m.no_price * 100:.0f}"


def _top_market_price(event: NormalizedEvent) -> str:
    if not event.markets:
        return "—"
    return _fmt_price_pair(event.markets[0])


def _score_color(score: float) -> str:
    s = score / 100.0 if score > 1.0 else score
    if s >= 0.92:
        return "green"
    if s >= 0.85:
        return "yellow"
    return "red"


def _fmt_score(score: float) -> str:
    return f"{score:.3f}" if score <= 1.0 else f"{score:.0f}"


# ── list command ──────────────────────────────────────────────────────────────


def cmd_list(args: argparse.Namespace) -> None:
    source = args.source.lower()
    limit: int = args.limit
    category: str | None = args.category

    console.print(f"\n[bold]Fetching [cyan]{source}[/cyan] events[/bold] (limit={limit})…")

    try:
        if source == "polymarket":
            from clients.polymarket import fetch_events
            events = fetch_events(limit=limit, category=category)
        elif source == "kalshi":
            from clients.kalshi import fetch_events
            events = fetch_events(limit=limit, category=category)
        else:
            console.print(f"[red]Unknown source: {source}. Use 'polymarket' or 'kalshi'.[/red]")
            sys.exit(1)
    except RuntimeError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    if not events:
        console.print("[yellow]No events found.[/yellow]")
        return

    if args.group_by_category:
        groups = group_by_category(events)
        for cat, cat_events in groups.items():
            _render_event_table(cat_events, title=f"[bold]{source.title()}[/bold] — {cat}")
    else:
        _render_event_table(events, title=f"[bold]{source.title()}[/bold] Events")

    console.print(f"\n[dim]Total: {len(events)} events[/dim]\n")


def _render_event_table(events: list[NormalizedEvent], title: str) -> None:
    table = Table(
        title=title,
        box=box.SIMPLE if _mobile else box.ROUNDED,
        show_lines=False,
        header_style="bold magenta",
        title_style="bold white",
    )
    if _mobile:
        table.add_column("Title", max_width=28, no_wrap=False)
        table.add_column("Price", width=16)
        table.add_column("Ends", width=10)
    else:
        table.add_column("Title", min_width=30, max_width=55, no_wrap=False)
        table.add_column("Category", width=14)
        table.add_column("Top Market", width=22)
        table.add_column("Volume", width=10, justify="right")
        table.add_column("End Date", width=12)

    for e in events:
        title_cell = _link(e.title, e.url)
        if _mobile:
            table.add_row(title_cell, _top_market_price(e), e.end_date or "—")
        else:
            table.add_row(
                title_cell,
                normalize_category(e.category),
                _top_market_price(e),
                _fmt_volume(e.volume),
                e.end_date or "—",
            )

    console.print(table)


# ── compare command (event-level) ─────────────────────────────────────────────


def _fetch_both(limit: int, category: str | None) -> tuple[list, list]:
    from clients.polymarket import fetch_events as poly_fetch
    from clients.kalshi import fetch_events as kalshi_fetch

    poly_events: list[NormalizedEvent] = []
    kalshi_events: list[NormalizedEvent] = []

    try:
        poly_events = poly_fetch(limit=limit, category=category)
        console.print(f"  [green]✓[/green] Polymarket: {len(poly_events)} events")
    except RuntimeError as exc:
        console.print(f"  [red]✗ Polymarket:[/red] {exc}")

    try:
        kalshi_events = kalshi_fetch(limit=limit, category=category)
        console.print(f"  [green]✓[/green] Kalshi:     {len(kalshi_events)} events")
    except RuntimeError as exc:
        console.print(f"  [red]✗ Kalshi:[/red] {exc}")

    return poly_events, kalshi_events


def cmd_compare(args: argparse.Namespace) -> None:
    limit: int = args.limit
    min_score: float = args.min_score
    category: str | None = args.category
    use_embeddings = not args.no_embeddings

    console.print(f"\n[bold]Fetching events from both platforms[/bold] (limit={limit} each)…")
    poly_events, kalshi_events = _fetch_both(limit, category)

    if not poly_events and not kalshi_events:
        console.print("[red]No events retrieved from either platform.[/red]")
        sys.exit(1)

    if not poly_events or not kalshi_events:
        console.print("\n[yellow]Only one platform returned data — showing available events:[/yellow]")
        _render_event_table(poly_events or kalshi_events, title="Available Events")
        return

    if args.brackets:
        _run_bracket_compare(
            poly_events, kalshi_events,
            event_min_score=args.event_min_score,
            market_min_score=min_score,
            use_embeddings=use_embeddings,
            refresh_cache=getattr(args, "refresh_cache", False),
        )
    else:
        _run_event_compare(poly_events, kalshi_events, min_score, use_embeddings)


def _run_event_compare(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    min_score: float,
    use_embeddings: bool,
) -> None:
    mode = "semantic embeddings" if use_embeddings else "fuzzy matching"
    console.print(f"\n[bold]Comparing events[/bold] via [cyan]{mode}[/cyan] (min score: {min_score})…\n")
    matches = find_matches(poly_events, kalshi_events, min_score=min_score, use_embeddings=use_embeddings)

    if not matches:
        console.print(f"[yellow]No matching events found at score ≥ {min_score}.[/yellow]")
        hint = "Try lowering --min-score or use --brackets for sub-market matching"
        console.print(f"[dim]{hint}[/dim]")
        return

    _render_event_match_table(matches)

    matched_poly = {r.poly_event.id for r in matches}
    matched_kalshi = {r.kalshi_event.id for r in matches}
    console.print(
        f"\n[dim]Matched: {len(matches)} pairs | "
        f"Unmatched Polymarket: {len(poly_events) - len(matched_poly)} | "
        f"Unmatched Kalshi: {len(kalshi_events) - len(matched_kalshi)}[/dim]\n"
    )


def _render_event_match_table(matches: list[MatchResult]) -> None:
    table = Table(
        title="[bold]Matched Events[/bold]",
        box=box.SIMPLE if _mobile else box.ROUNDED,
        show_lines=True,
        header_style="bold magenta",
        title_style="bold white",
    )
    if _mobile:
        table.add_column("Polymarket", max_width=24, no_wrap=False)
        table.add_column("~", width=5, justify="center")
        table.add_column("Kalshi", max_width=24, no_wrap=False)
    else:
        table.add_column("Polymarket", min_width=28, max_width=40, no_wrap=False)
        table.add_column("PM Price", width=14, justify="center")
        table.add_column("Score", width=7, justify="center")
        table.add_column("Kalshi Price", width=14, justify="center")
        table.add_column("Kalshi", min_width=28, max_width=40, no_wrap=False)

    for r in matches:
        pm_title = _link(r.poly_event.title, r.poly_event.url)
        ks_title = _link(r.kalshi_event.title, r.kalshi_event.url)
        score_cell = Text(_fmt_score(r.score), style=_score_color(r.score))
        if _mobile:
            table.add_row(pm_title, score_cell, ks_title)
        else:
            table.add_row(
                pm_title,
                _top_market_price(r.poly_event),
                score_cell,
                _top_market_price(r.kalshi_event),
                ks_title,
            )

    console.print(table)
    console.print("[dim]Score: cosine similarity 0.0–1.0 (green ≥0.92, yellow ≥0.85, red <0.85)[/dim]")


# ── compare --brackets (sub-market level) ────────────────────────────────────


def _run_bracket_compare(
    poly_events: list[NormalizedEvent],
    kalshi_events: list[NormalizedEvent],
    event_min_score: float,
    market_min_score: float,
    use_embeddings: bool,
    refresh_cache: bool = False,
) -> None:
    mode = "semantic embeddings" if use_embeddings else "fuzzy matching"
    cache_note = " [dim](cache bypassed)[/dim]" if refresh_cache else ""
    console.print(
        f"\n[bold]Step 1:[/bold] Matching events via [cyan]{mode}[/cyan] "
        f"(min score: {event_min_score}){cache_note}…"
    )

    pairs = find_market_matches(
        poly_events, kalshi_events,
        event_min_score=event_min_score,
        market_min_score=market_min_score,
        use_embeddings=use_embeddings,
        refresh_cache=refresh_cache,
    )

    if not pairs:
        console.print(f"[yellow]No event matches found at score ≥ {event_min_score}.[/yellow]")
        console.print("[dim]Try lowering --event-min-score (e.g. --event-min-score 0.70)[/dim]")
        return

    total_market_matches = sum(len(ms) for _, ms in pairs)
    console.print(
        f"  Found [cyan]{len(pairs)}[/cyan] matched event pairs\n"
        f"[bold]Step 2:[/bold] Matching sub-markets within each pair "
        f"(min score: {market_min_score})…\n"
        f"  Found [cyan]{total_market_matches}[/cyan] matched brackets\n"
    )

    _render_bracket_matches(pairs)


def _render_bracket_matches(
    pairs: list[tuple],  # list[tuple[MatchResult, list[MarketMatchResult]]]
) -> None:
    for event_match, market_matches in pairs:
        pm_title = event_match.poly_event.title
        ks_title = event_match.kalshi_event.title
        event_score = _fmt_score(event_match.score)

        if pm_title == ks_title:
            header = f"[bold cyan]{pm_title}[/bold cyan]  [dim](event score: {event_score})[/dim]"
        else:
            header = (
                f"[bold cyan]{pm_title}[/bold cyan]  "
                f"[dim]↔  {ks_title}  (event score: {event_score})[/dim]"
            )
        console.print(Rule(header, style="dim cyan"))

        if not market_matches:
            pm_count = len(event_match.poly_event.markets)
            ks_count = len(event_match.kalshi_event.markets)
            console.print(
                f"  [dim]No bracket matches above threshold "
                f"(PM has {pm_count} sub-markets, Kalshi has {ks_count})[/dim]"
            )
            continue

        table = Table(
            box=box.SIMPLE,
            show_header=True,
            header_style="bold magenta",
            show_lines=False,
            padding=(0, 1),
        )
        if _mobile:
            table.add_column("Polymarket bracket", max_width=20, no_wrap=False)
            table.add_column("PM", width=10, justify="center")
            table.add_column("~", width=5, justify="center")
            table.add_column("KS", width=10, justify="center")
            table.add_column("Kalshi bracket", max_width=20, no_wrap=False)
        else:
            table.add_column("Polymarket bracket", min_width=32, max_width=50, no_wrap=False)
            table.add_column("PM price", width=14, justify="center")
            table.add_column("Score", width=7, justify="center")
            table.add_column("Kalshi price", width=14, justify="center")
            table.add_column("Kalshi bracket", min_width=32, max_width=50, no_wrap=False)

        for r in sorted(market_matches, key=lambda x: x.score, reverse=True):
            # Strip parent event prefix from Kalshi question for cleaner display
            ks_q = r.kalshi_market.question
            prefix = r.kalshi_market.parent_event_title + ": "
            if ks_q.startswith(prefix):
                ks_q = ks_q[len(prefix):]

            pm_q_cell = _link(r.poly_market.question, r.poly_market.url)
            ks_q_cell = _link(ks_q, r.kalshi_market.url)
            score_cell = Text(_fmt_score(r.score), style=_score_color(r.score))
            price_fn = _fmt_price_pair_short if _mobile else _fmt_price_pair

            table.add_row(
                pm_q_cell,
                price_fn(r.poly_market),
                score_cell,
                price_fn(r.kalshi_market),
                ks_q_cell,
            )

        console.print(table)

    console.print(
        "\n[dim]Bracket score: cosine similarity "
        "(green ≥0.92, yellow ≥0.85, red <0.85)[/dim]"
    )


# ── cache command ─────────────────────────────────────────────────────────────


def cmd_cache(args: argparse.Namespace) -> None:
    from cache import cache_stats, clear_cache, all_event_pairs

    if args.clear:
        clear_cache()
        console.print("[green]Cache cleared.[/green]")
        return

    stats = cache_stats()

    if args.list_pairs:
        pairs = all_event_pairs()
        if not pairs:
            console.print("[yellow]Cache is empty.[/yellow]")
            return
        table = Table(
            title=f"[bold]Cached Event Pairs[/bold]  ({len(pairs)} total)",
            box=box.SIMPLE if _mobile else box.ROUNDED,
            show_lines=True,
            header_style="bold magenta",
        )
        if _mobile:
            table.add_column("Polymarket Event", max_width=24, no_wrap=False)
            table.add_column("~", width=5, justify="center")
            table.add_column("Kalshi Event", max_width=24, no_wrap=False)
            table.add_column("Ticker", width=16)
        else:
            table.add_column("Polymarket Event", min_width=28, max_width=44, no_wrap=False)
            table.add_column("PM ID", width=10)
            table.add_column("Score", width=7, justify="center")
            table.add_column("Kalshi Event", min_width=28, max_width=44, no_wrap=False)
            table.add_column("KS Ticker", width=18)
            table.add_column("Cached", width=12)
        for p in pairs:
            pm_cell = _link(p["pm_title"] or p["pm_event_id"], p.get("pm_url") or "")
            ks_cell = _link(p["ks_title"] or p["ks_event_ticker"], p.get("ks_url") or "")
            score_str = f"{p['event_score']:.3f}"
            if _mobile:
                table.add_row(pm_cell, score_str, ks_cell, p["ks_event_ticker"])
            else:
                table.add_row(
                    pm_cell,
                    p["pm_event_id"],
                    score_str,
                    ks_cell,
                    p["ks_event_ticker"],
                    (p["cached_at"] or "")[:10],
                )
        console.print(table)
        return

    # Default: show stats
    console.print(Panel(
        f"[bold]Event pairs cached:[/bold]  [cyan]{stats['event_pairs']}[/cyan]\n"
        f"[bold]Market pairs cached:[/bold] [cyan]{stats['market_pairs']}[/cyan]\n"
        f"[bold]Oldest entry:[/bold]        {(stats['oldest_entry'] or '—')[:19]}\n"
        f"[bold]Newest entry:[/bold]        {(stats['newest_entry'] or '—')[:19]}\n"
        f"[bold]DB location:[/bold]         [dim]{stats['db_path']}[/dim]",
        title="[bold cyan]Semantic Match Cache[/bold cyan]",
        border_style="cyan",
    ))
    console.print("[dim]Use --list-pairs to see all cached matches, --clear to reset.[/dim]\n")


# ── arb command ───────────────────────────────────────────────────────────────


def cmd_arb(args: argparse.Namespace) -> None:
    limit: int = args.limit
    use_embeddings = not args.no_embeddings
    min_profit_frac = args.min_profit / 100.0  # CLI takes cents, internals use fraction
    max_days: int | None = args.max_days

    console.print(f"\n[bold]Fetching events from both platforms[/bold] (limit={limit} each)…")
    poly_events, kalshi_events = _fetch_both(limit, category=None)

    if not poly_events or not kalshi_events:
        console.print("[red]Need events from both platforms to detect arbitrage.[/red]")
        sys.exit(1)

    console.print(
        f"\n[bold]Step 1:[/bold] Matching events "
        f"(event threshold: {args.event_min_score}, market threshold: {args.min_score})…"
    )
    pairs = find_market_matches(
        poly_events, kalshi_events,
        event_min_score=args.event_min_score,
        market_min_score=args.min_score,
        use_embeddings=use_embeddings,
        refresh_cache=getattr(args, "refresh_cache", False),
    )

    total_brackets = sum(len(ms) for _, ms in pairs)
    console.print(
        f"  Found [cyan]{len(pairs)}[/cyan] matched event pairs, "
        f"[cyan]{total_brackets}[/cyan] matched brackets\n"
        f"[bold]Step 2:[/bold] Scanning for arbitrage "
        f"(min profit: {args.min_profit:.1f}¢"
        + (f", max days: {max_days}" if max_days else "")
        + ")…\n"
    )

    arb_results = find_arbitrage(
        pairs,
        min_profit=min_profit_frac,
        max_days=max_days,
    )

    if not arb_results:
        console.print("[yellow]No arbitrage opportunities found with current thresholds.[/yellow]")
        console.print(
            "[dim]Tips: lower --min-score / --event-min-score, "
            "or set --min-profit 0 to show any positive spread[/dim]"
        )
        return

    _render_arb_table(arb_results)


def _render_arb_table(results: list[ArbitrageResult]) -> None:
    table = Table(
        title="[bold]Arbitrage Opportunities[/bold]  [dim](sorted by annualized return)[/dim]",
        box=box.SIMPLE if _mobile else box.ROUNDED,
        show_lines=True,
        header_style="bold magenta",
        title_style="bold white",
    )
    if _mobile:
        # Compact layout: drop Spread and Days columns
        table.add_column("Polymarket bracket", max_width=20, no_wrap=False)
        table.add_column("PM leg", width=9, justify="center")
        table.add_column("Kalshi bracket", max_width=20, no_wrap=False)
        table.add_column("KS leg", width=9, justify="center")
        table.add_column("Profit", width=7, justify="right")
        table.add_column("Ann.%", width=7, justify="right")
    else:
        table.add_column("Polymarket bracket", min_width=28, max_width=44, no_wrap=False)
        table.add_column("PM leg", width=12, justify="center")
        table.add_column("Kalshi bracket", min_width=28, max_width=44, no_wrap=False)
        table.add_column("KS leg", width=12, justify="center")
        table.add_column("Spread", width=8, justify="right")
        table.add_column("Profit", width=7, justify="right")
        table.add_column("Days", width=5, justify="right")
        table.add_column("Ann.%", width=7, justify="right")

    for r in results:
        pm = r.poly_market
        ks = r.kalshi_market

        if r.best_leg == "pm_yes_ks_no":
            pm_leg = f"Y {_fmt_price(pm.yes_price)}" if _mobile else f"Yes {_fmt_price(pm.yes_price)}"
            ks_leg = f"N {_fmt_price(ks.no_price)}" if _mobile else f"No  {_fmt_price(ks.no_price)}"
        else:
            pm_leg = f"N {_fmt_price(pm.no_price)}" if _mobile else f"No  {_fmt_price(pm.no_price)}"
            ks_leg = f"Y {_fmt_price(ks.yes_price)}" if _mobile else f"Yes {_fmt_price(ks.yes_price)}"

        profit_cents = r.profit * 100
        profit_color = "green" if profit_cents >= 2.0 else "yellow" if profit_cents >= 0.5 else "white"
        ann_str = f"{r.annualized_return * 100:.1f}%" if r.annualized_return is not None else "—"
        days_str = str(r.days_to_resolution) if r.days_to_resolution is not None else "—"

        # Strip Kalshi parent event prefix from question for cleaner display
        ks_q = ks.question
        prefix = ks.parent_event_title + ": "
        if ks_q.startswith(prefix):
            ks_q = ks_q[len(prefix):]

        pm_q_cell = _link(pm.question, pm.url)
        ks_q_cell = _link(ks_q, ks.url)
        profit_cell = Text(f"{profit_cents:.1f}¢", style=profit_color)

        if _mobile:
            table.add_row(pm_q_cell, pm_leg, ks_q_cell, ks_leg, profit_cell, ann_str)
        else:
            table.add_row(
                pm_q_cell, pm_leg, ks_q_cell, ks_leg,
                f"{r.spread * 100:.1f}¢", profit_cell, days_str, ann_str,
            )

    console.print(table)
    console.print(
        f"[dim]Found [cyan]{len(results)}[/cyan] opportunities | "
        "Profit = gross spread before platform fees | "
        "Ann.% = annualized gross return[/dim]\n"
    )


# ── entry point ───────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Compare prediction market events across Polymarket and Kalshi.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # list
    p_list = sub.add_parser("list", help="List events from one platform")
    p_list.add_argument(
        "--source", choices=["polymarket", "kalshi"], required=True,
        help="Which platform to fetch from",
    )
    p_list.add_argument("--limit", type=int, default=50, metavar="N",
                        help="Max number of events to fetch (default: 50)")
    p_list.add_argument("--category", default=None, metavar="CAT",
                        help="Filter by category name")
    p_list.add_argument("--group-by-category", action="store_true",
                        help="Group results by category")
    p_list.add_argument("--mobile", action="store_true",
                        help="Narrow output for mobile terminals (72 chars, no hyperlinks)")

    # compare
    p_cmp = sub.add_parser("compare", help="Compare events across both platforms")
    p_cmp.add_argument("--limit", type=int, default=200, metavar="N",
                       help="Max events to fetch from each platform (default: 200)")
    p_cmp.add_argument("--category", default=None, metavar="CAT",
                       help="Filter by category name")
    p_cmp.add_argument("--min-score", type=float, default=0.82, metavar="N",
                       help="Min similarity: cosine 0.0–1.0 (default: 0.82)")
    p_cmp.add_argument("--no-embeddings", action="store_true",
                       help="Use fuzzy matching instead of Gemini embeddings")
    p_cmp.add_argument("--brackets", action="store_true",
                       help="Match at sub-market/bracket level (two-level: events then brackets)")
    p_cmp.add_argument("--event-min-score", type=float, default=0.75, metavar="N",
                       help="Event-level match threshold for --brackets mode (default: 0.75)")
    p_cmp.add_argument("--refresh-cache", action="store_true",
                       help="Ignore cached match scores and re-run embedding for all pairs")
    p_cmp.add_argument("--mobile", action="store_true",
                       help="Narrow output for mobile terminals (72 chars, no hyperlinks)")

    # arb
    p_arb = sub.add_parser("arb", help="Find cross-platform arbitrage opportunities")
    p_arb.add_argument("--limit", type=int, default=200, metavar="N",
                       help="Max events to fetch from each platform (default: 200)")
    p_arb.add_argument("--min-score", type=float, default=0.82, metavar="N",
                       help="Min bracket-match similarity (default: 0.82)")
    p_arb.add_argument("--event-min-score", type=float, default=0.75, metavar="N",
                       help="Min event-match similarity (default: 0.75)")
    p_arb.add_argument("--min-profit", type=float, default=0.0, metavar="CENTS",
                       help="Min gross profit in cents per $1 contract (default: 0)")
    p_arb.add_argument("--max-days", type=int, default=None, metavar="N",
                       help="Exclude opportunities expiring more than N days away")
    p_arb.add_argument("--no-embeddings", action="store_true",
                       help="Use fuzzy matching instead of Gemini embeddings")
    p_arb.add_argument("--refresh-cache", action="store_true",
                       help="Ignore cached match scores and re-run embedding for all pairs")
    p_arb.add_argument("--mobile", action="store_true",
                       help="Narrow output for mobile terminals (72 chars, no hyperlinks)")

    # cache
    p_cache = sub.add_parser("cache", help="Inspect or manage the semantic match cache")
    p_cache.add_argument("--stats", action="store_true", help="Show cache statistics")
    p_cache.add_argument("--clear", action="store_true", help="Delete all cached matches")
    p_cache.add_argument("--list-pairs", action="store_true",
                         help="Print all cached event pairs with PM and KS URLs")
    p_cache.add_argument("--mobile", action="store_true",
                         help="Narrow output for mobile terminals (72 chars, no hyperlinks)")

    return parser


def main() -> None:
    global console, _mobile

    # Parse args before printing anything so --mobile can set console width
    parser = build_parser()
    args = parser.parse_args()

    if getattr(args, "mobile", False):
        _mobile = True
        console = Console(width=72)

    console.print(Panel.fit(
        "[bold cyan]Prediction Market Comparator[/bold cyan]\n"
        "[dim]Polymarket  ↔  Kalshi[/dim]",
        border_style="cyan",
    ))

    if args.command == "list":
        cmd_list(args)
    elif args.command == "compare":
        cmd_compare(args)
    elif args.command == "arb":
        cmd_arb(args)
    elif args.command == "cache":
        cmd_cache(args)


if __name__ == "__main__":
    main()
