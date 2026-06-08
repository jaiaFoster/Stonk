"""
app/services/report_service.py — Payload and HTML report formatting.
"""

from __future__ import annotations

import json
from datetime import date
from html import escape
from typing import Any

from app import config
from app.services.report_assets import REPORT_CSS, collapsible_pre


NewsMap = dict[str, list[dict[str, Any]]]
Recommendations = list[dict[str, Any]]
TradierSnapshot = dict[str, dict[str, Any]]
CalendarCandidates = list[dict[str, Any]]


UI_OVERHAUL_CSS = """
        :root {
            color-scheme: dark;
            --bg: #000000;
            --panel: #090b0f;
            --panel-2: #10141b;
            --panel-3: #151a23;
            --text: #d8dee9;
            --muted: #7f8a99;
            --line: #222936;
            --accent: #7aa2c7;
            --accent-2: #9fb3c8;
            --good: #83b88f;
            --bad: #c56f75;
            --warn: #c6a15b;
            --neutral: #8a94a3;
            --accent-dim: rgba(122, 162, 199, 0.24);
        }
        body {
            max-width: 1320px;
            background: var(--bg);
            color: var(--text);
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
            padding: 0;
        }
        a { color: var(--accent-2); }
        h1, h2, h3 { letter-spacing: 0; }
        h1 {
            color: var(--text);
            margin: 0;
            font-size: clamp(1.1rem, 2.5vw, 1.5rem);
        }
        h2 {
            color: var(--text);
            border: 0;
            margin: 0;
            padding: 0;
            font-size: clamp(1rem, 2.1vw, 1.18rem);
        }
        table { margin: 0.75rem 0 1rem; }
        th { top: 0; background: var(--panel-3); color: var(--accent-2); }
        td { border-bottom-color: var(--line); }
        tr:hover td { background: rgba(122, 162, 199, 0.07); }
        .report-shell {
            min-height: 100vh;
            background:
                linear-gradient(180deg, rgba(122, 162, 199, 0.05), rgba(0, 0, 0, 0) 260px),
                var(--bg);
            padding: 1rem;
        }
        .top-summary {
            position: sticky;
            top: 0;
            z-index: 10;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 0.75rem;
            padding: 0.7rem 0.85rem;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: rgba(9, 11, 15, 0.96);
            backdrop-filter: blur(12px);
            box-shadow: 0 16px 35px rgba(0, 0, 0, 0.42);
        }
        .summary-title {
            display: flex;
            flex-direction: column;
            gap: 0.15rem;
            min-width: 180px;
        }
        .summary-title span { color: var(--muted); font-size: 0.78rem; }
        .summary-chips, .provider-chips, .chip-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.4rem;
            align-items: center;
        }
        .summary-chips { justify-content: flex-end; }
        .chip, .pill {
            display: inline-flex;
            align-items: center;
            min-height: 24px;
            padding: 0.18rem 0.5rem;
            border: 1px solid var(--line);
            border-radius: 999px;
            background: var(--panel-2);
            color: var(--text);
            font-size: 0.76rem;
            line-height: 1.2;
            white-space: nowrap;
        }
        .chip strong { color: var(--accent-2); margin-left: 0.28rem; }
        .chip.good, .action-add, .candidate { color: var(--good); border-color: rgba(131, 184, 143, 0.35); background: rgba(131, 184, 143, 0.09); }
        .chip.bad, .action-risk, .urgent { color: var(--bad); border-color: rgba(197, 111, 117, 0.4); background: rgba(197, 111, 117, 0.1); }
        .chip.warn, .action-watch { color: var(--warn); border-color: rgba(198, 161, 91, 0.4); background: rgba(198, 161, 91, 0.1); }
        .chip.neutral, .action-hold { color: var(--accent-2); border-color: rgba(122, 162, 199, 0.32); background: rgba(122, 162, 199, 0.09); }
        .quick-nav {
            position: sticky;
            top: 70px;
            z-index: 9;
            background: rgba(0, 0, 0, 0.86);
            border: 0;
            border-bottom: 1px solid var(--line);
            border-radius: 0;
            margin: 0 -1rem 1rem;
            padding: 0.65rem 1rem;
        }
        .quick-nav a {
            border-color: var(--line);
            background: var(--panel);
            color: var(--accent-2);
            border-radius: 6px;
        }
        html { scroll-padding-top: 126px; }
        .top-summary a.chip { text-decoration: none; }
        .top-summary a.chip:hover, .quick-nav a:hover, .export-btn:hover { border-color: var(--accent); background: var(--panel-3); }
        .export-toolbar {
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            align-items: center;
            margin: 0.9rem 0;
        }
        .export-btn, .copy-btn {
            min-height: 36px;
            border: 1px solid var(--line);
            border-radius: 6px;
            background: var(--panel-2);
            color: var(--accent-2);
            padding: 0.45rem 0.65rem;
            font: inherit;
            cursor: pointer;
        }
        .fallback-copy {
            display: none;
            width: 100%;
            min-height: 120px;
            margin: 0.55rem 0;
            border: 1px solid var(--warn);
            border-radius: 8px;
            background: #080a0f;
            color: var(--text);
            padding: 0.65rem;
            font: inherit;
        }
        .toast {
            position: fixed;
            right: 1rem;
            bottom: 1rem;
            z-index: 50;
            max-width: min(92vw, 420px);
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel-3);
            color: var(--text);
            padding: 0.7rem 0.85rem;
            box-shadow: 0 16px 35px rgba(0, 0, 0, 0.45);
            display: none;
        }
        .toast.show { display: block; }
        .report-section {
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel);
            margin: 0 0 0.9rem;
            overflow: hidden;
        }
        .section-head {
            display: flex;
            justify-content: space-between;
            gap: 0.75rem;
            align-items: flex-start;
            padding: 0.85rem;
            border-bottom: 1px solid var(--line);
            background: linear-gradient(180deg, var(--panel-2), var(--panel));
        }
        .section-kicker {
            color: var(--muted);
            font-size: 0.78rem;
            margin-top: 0.18rem;
        }
        .section-body { padding: 0.85rem; }
        .macro-strip {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 0.55rem;
        }
        .macro-cell, .metric, .risk-card {
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel-2);
            padding: 0.65rem;
            min-width: 0;
        }
        .label {
            display: block;
            color: var(--muted);
            font-size: 0.72rem;
            text-transform: uppercase;
        }
        .value {
            display: block;
            color: var(--text);
            font-size: 0.94rem;
            margin-top: 0.16rem;
            word-break: break-word;
        }
        details.decision-card {
            margin: 0 0 0.55rem;
            padding: 0;
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel-2);
        }
        details.decision-card[open] { border-color: var(--accent-dim); }
        details.decision-card summary {
            list-style: none;
            color: var(--text);
            padding: 0.7rem;
        }
        details.decision-card summary::-webkit-details-marker { display: none; }
        .strip-summary, .holding-row, .add-row, .blocked-row {
            display: grid;
            gap: 0.55rem;
            align-items: center;
        }
        .strip-summary { grid-template-columns: 0.8fr 1.4fr 0.8fr 0.7fr 0.8fr 1fr; }
        .holding-row { grid-template-columns: 0.75fr 1.1fr 0.65fr 0.7fr 0.8fr 1.2fr; }
        .add-row { grid-template-columns: 0.75fr 0.65fr 1fr 1.55fr 1fr; }
        .blocked-row { grid-template-columns: 0.75fr 1fr 0.9fr 1.8fr 0.85fr; }
        .ticker {
            color: #eef3f8;
            font-weight: 700;
            font-size: 1.02rem;
        }
        .metric-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 0.55rem;
            padding: 0 0.7rem 0.7rem;
        }
        .detail-block {
            border-top: 1px solid var(--line);
            padding: 0.7rem;
            color: var(--text);
        }
        .muted, .empty { color: var(--muted); }
        .positive { color: var(--good); }
        .negative { color: var(--bad); }
        .warn-text { color: var(--warn); }
        .compact-list { margin: 0; padding-left: 1rem; }
        .compact-list li { margin: 0.12rem 0; }
        .portfolio-bars {
            display: grid;
            grid-template-columns: 1.4fr 0.8fr 1.8fr 0.9fr;
            gap: 0.45rem 0.7rem;
            align-items: center;
        }
        .bucket-details {
            border: 1px solid var(--line);
            border-radius: 8px;
            background: var(--panel-2);
            margin: 0 0 0.45rem;
            padding: 0.55rem;
        }
        .bucket-details summary { color: var(--text); cursor: pointer; }
        .subsection-title {
            color: var(--accent-2);
            font-size: 0.9rem;
            margin: 0.8rem 0 0.45rem;
            text-transform: uppercase;
        }
        .quiet-list { opacity: 0.78; }
        .refresh-row {
            display: flex;
            flex-wrap: wrap;
            gap: 0.55rem;
            align-items: center;
            margin-bottom: 0.7rem;
        }
        .refresh-status { color: var(--muted); font-size: 0.8rem; }
        .bar-track {
            position: relative;
            height: 8px;
            border-radius: 999px;
            background: #07090d;
            border: 1px solid var(--line);
            overflow: hidden;
        }
        .bar-fill {
            position: absolute;
            inset: 0 auto 0 0;
            width: var(--bar-width, 0%);
            background: var(--accent);
            opacity: 0.82;
        }
        .risk-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 0.55rem;
            margin-top: 0.7rem;
        }
        details.debug-details {
            background: var(--panel);
            border-color: var(--line);
            border-radius: 8px;
        }
        details.debug-details summary { color: var(--accent-2); }
        .table-scroll { overflow-x: auto; }
        @media (max-width: 900px) {
            .top-summary { align-items: flex-start; flex-direction: column; }
            .summary-chips { justify-content: flex-start; overflow-x: auto; flex-wrap: nowrap; width: 100%; padding-bottom: 0.15rem; }
            .quick-nav { top: 104px; overflow-x: auto; flex-wrap: nowrap; }
            .quick-nav a { flex: 0 0 auto; }
            .export-toolbar { align-items: stretch; flex-direction: column; }
            .export-btn, .copy-btn { width: 100%; }
            .macro-strip, .metric-grid, .risk-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .strip-summary, .holding-row, .add-row, .blocked-row {
                grid-template-columns: 1fr;
                align-items: start;
            }
            .portfolio-bars { grid-template-columns: 1fr; }
        }
        @media (max-width: 520px) {
            .report-shell { padding: 0.65rem; }
            .quick-nav { margin-left: -0.65rem; margin-right: -0.65rem; padding-left: 0.65rem; }
            .section-head { flex-direction: column; }
            .macro-strip, .metric-grid, .risk-grid { grid-template-columns: 1fr; }
            .chip { white-space: normal; }
        }
"""


def money(value):
    """Format a numeric value as money, or em dash if missing."""
    if value is None:
        return "—"
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def number(value, decimals=4):
    """Format a numeric value, or em dash if missing."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "—"


def signed_money(value):
    """Format a signed dollar value."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):+.2f}"
    except (TypeError, ValueError):
        return "N/A"


def signed_pct(value):
    """Format a signed percentage."""
    if value is None:
        return "N/A"
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return "N/A"


def pct(value):
    """Format a percentage value."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.1f}%"
    except (TypeError, ValueError):
        return "—"


def option_money(value):
    """Format an option quote/debit value without adding contract multiplier."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def compact_big_number(value):
    if value is None:
        return "—"
    try:
        val = float(value)
    except (TypeError, ValueError):
        return "—"
    if abs(val) >= 1_000_000_000:
        return f"{val / 1_000_000_000:.1f}B"
    if abs(val) >= 1_000_000:
        return f"{val / 1_000_000:.1f}M"
    if abs(val) >= 1_000:
        return f"{val / 1_000:.1f}K"
    return f"{val:.0f}"


def yes_no(value):
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    return "—"


def safe_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def is_zero_value_position(position: dict[str, Any]) -> bool:
    """Return True for empty broker/crypto rows that should stay out of main UI."""
    quantity = safe_float(position.get("quantity"))
    market_value = safe_float(position.get("market_value") if position.get("market_value") is not None else position.get("position_value"))
    return quantity is not None and market_value is not None and abs(quantity) <= 1e-9 and abs(market_value) <= 0.01


def format_payload(
    positions: list[dict[str, Any]],
    news_map: NewsMap,
    recommendations: Recommendations | None = None,
    tradier_snapshot: TradierSnapshot | None = None,
) -> str:
    today = date.today().strftime("%B %d, %Y")
    recommendations = recommendations or []
    tradier_snapshot = tradier_snapshot or {}
    zero_tickers = _zero_tickers_from_positions_and_recommendations(positions, recommendations)
    display_positions = _filter_nonzero_positions(positions, zero_tickers)
    display_recommendations = _filter_nonzero_recommendations(recommendations, zero_tickers)
    calendar_candidates = calendar_candidates_from_tradier_snapshot(tradier_snapshot)
    earnings_calendar_strategy = earnings_calendar_strategy_from_tradier_snapshot(tradier_snapshot)
    open_options = open_options_from_tradier_snapshot(tradier_snapshot)
    lifecycle_checks = calendar_lifecycle_from_tradier_snapshot(tradier_snapshot)
    earnings_events = earnings_events_from_tradier_snapshot(tradier_snapshot)
    watchlist_review = watchlist_review_from_tradier_snapshot(tradier_snapshot)
    earnings_trade_discovery = earnings_trade_discovery_from_tradier_snapshot(tradier_snapshot)
    unified_calendar_engine = unified_calendar_trade_engine_from_tradier_snapshot(tradier_snapshot)
    portfolio_gap = portfolio_gap_from_tradier_snapshot(tradier_snapshot)
    stock_momentum = stock_momentum_from_tradier_snapshot(tradier_snapshot)
    daily_opportunity = daily_opportunity_from_tradier_snapshot(tradier_snapshot)
    calendar_ranking = calendar_ranking_from_tradier_snapshot(tradier_snapshot)
    earnings_mini_backtest = earnings_mini_backtest_from_tradier_snapshot(tradier_snapshot)

    lines = [
        f"Date: {today}",
        "",
        "=== MY STOCK POSITIONS ===",
    ]

    for p in display_positions:
        gain_loss = p.get("gain_loss")
        gain_loss_pct = p.get("gain_loss_pct")

        if gain_loss is not None:
            gl = f"{signed_money(gain_loss)} ({signed_pct(gain_loss_pct)})"
        else:
            gl = "N/A"

        lines.append(
            f"{p.get('ticker', 'UNKNOWN')}: "
            f"{number(p.get('quantity'), 4)} shares | "
            f"Avg cost {money(p.get('avg_buy_price'))} | "
            f"Current {money(p.get('current_price'))} | "
            f"G/L: {gl} | "
            f"Value: {money(p.get('market_value'))} | "
            f"Account: {p.get('account', 'Unknown')}"
        )

    lines += ["", "=== DAILY OPPORTUNITY ENGINE V1 ==="]
    lines.extend(format_daily_opportunity_text(_filter_daily_opportunity_engine(daily_opportunity, zero_tickers)))

    lines += ["", "=== ACTIVE CALENDAR TRADES ==="]
    lines.extend(format_unified_calendar_engine_text(unified_calendar_engine))

    lines += ["", "=== CALENDAR RANKING V2 ==="]
    lines.extend(format_calendar_ranking_text(calendar_ranking))

    lines += ["", "=== EARNINGS MINI-BACKTEST V1 ==="]
    lines.extend(format_earnings_mini_backtest_text(earnings_mini_backtest))

    lines += ["", "=== STOCK MOMENTUM ADD STRATEGY V1 ==="]
    lines.extend(format_stock_momentum_text(stock_momentum))

    lines += ["", "=== WATCHLIST STOCK CANDIDATE REVIEW V2 ==="]
    if not watchlist_review or not watchlist_review.get("items"):
        errors = (watchlist_review or {}).get("errors", []) or []
        if errors:
            lines.append("No watchlist candidates reviewed: " + "; ".join(str(e) for e in errors[:3]))
        else:
            lines.append("No watchlist candidates reviewed this run. Add Robinhood watchlist items or set WATCHLIST_TICKERS.")
    else:
        summary = watchlist_review.get("summary", {}) or {}
        lines.append(
            f"Candidates {summary.get('candidate_count', 0)} | "
            f"New {summary.get('new_candidate_count', 0)} | "
            f"Already held {summary.get('already_held_count', 0)} | "
            f"Stock candidates {summary.get('stock_candidate_count', 0)} | Potential calendar setups {summary.get('potential_trade_count', 0)} | "
            f"Urgent {summary.get('urgent_count', 0)}"
        )
        for item in watchlist_review.get("items", []) or []:
            earnings = item.get("earnings", {}) or {}
            strategy = item.get("earnings_calendar_strategy", {}) or {}
            lines.append(
                f"{item.get('ticker', 'UNKNOWN')}: Score {number(item.get('score'), 1)} | "
                f"Category: {item.get('category', 'WATCH')} | "
                f"Portfolio: {item.get('portfolio_status', 'Unknown')} | "
                f"Watchlists: {', '.join(item.get('watchlists', []) or []) or '—'}"
            )
            if earnings.get("has_data"):
                lines.append(
                    f"  Earnings: {earnings.get('earnings_date') or 'Unknown'} | "
                    f"{earnings.get('session_label') or 'Unknown'} | "
                    f"DTE {earnings.get('days_until_earnings') if earnings.get('days_until_earnings') is not None else 'unknown'}"
                )
            if strategy:
                lines.append(
                    f"  Earnings strategy: {strategy.get('action') or '—'} | "
                    f"Score {number(strategy.get('score'), 1)}"
                )
            for reason in (item.get("reasons", []) or [])[:3]:
                lines.append(f"  + {reason}")
            for risk in (item.get("risks", []) or [])[:3]:
                lines.append(f"  - {risk}")
            if item.get("next_check"):
                lines.append(f"  Next check: {item.get('next_check')}")

    lines += ["", "=== PORTFOLIO GAP / SECTOR SUGGESTIONS V1 ==="]
    lines.extend(format_portfolio_gap_text(portfolio_gap))

    lines += ["", "=== PORTFOLIO SCORING V2 ==="]

    if not display_recommendations:
        lines.append("No portfolio scoring recommendations generated.")
    else:
        for rec in display_recommendations:
            reasons = rec.get("reasons", []) or []
            risks = rec.get("risks", []) or []
            score = rec.get("score")
            metrics = rec.get("market_metrics", {}) or {}
            lines.append(
                f"{rec.get('ticker', 'UNKNOWN')} ({rec.get('account', 'Unknown')}): "
                f"Score {number(score, 1)} | Action: {rec.get('action', 'WATCH')} | "
                f"Confidence: {rec.get('confidence', 'Low')} | "
                f"Allocation: {pct(rec.get('allocation_pct'))} | "
                f"G/L: {signed_pct(rec.get('gain_loss_pct'))}"
            )

            if metrics.get("has_data"):
                lines.append(
                    "  Market: "
                    f"3M {signed_pct(metrics.get('return_3m_pct'))}, "
                    f"6M {signed_pct(metrics.get('return_6m_pct'))}, "
                    f"12M {signed_pct(metrics.get('return_12m_pct'))}, "
                    f"6M vs {metrics.get('benchmark_ticker') or 'benchmark'} "
                    f"{signed_pct(metrics.get('relative_strength_6m_pct'))}, "
                    f"Above 200D: {yes_no(metrics.get('above_sma_200'))}, "
                    f"52W high distance: {signed_pct(metrics.get('distance_from_52w_high_pct'))}"
                )
            else:
                err = metrics.get("error") if metrics else "No market metrics attached."
                lines.append(f"  Market: unavailable ({err})")

            if reasons:
                lines.append("  Reasons:")
                for reason in reasons[:4]:
                    lines.append(f"    - {reason}")
            if risks:
                lines.append("  Risks / limits:")
                for risk in risks[:4]:
                    lines.append(f"    - {risk}")
            next_check = rec.get("next_check")
            if next_check:
                lines.append(f"  Next check: {next_check}")

    lines += ["", "=== MARKET DATA SNAPSHOT ==="]
    market_rows = [rec for rec in display_recommendations if (rec.get("market_metrics") or {}).get("has_data")]
    if not market_rows:
        lines.append("No market metrics available for this run.")
    else:
        for rec in market_rows:
            metrics = rec.get("market_metrics", {}) or {}
            lines.append(
                f"{rec.get('ticker', 'UNKNOWN')}: "
                f"1M {signed_pct(metrics.get('return_1m_pct'))} | "
                f"3M {signed_pct(metrics.get('return_3m_pct'))} | "
                f"6M {signed_pct(metrics.get('return_6m_pct'))} | "
                f"12M {signed_pct(metrics.get('return_12m_pct'))} | "
                f"RS 6M {signed_pct(metrics.get('relative_strength_6m_pct'))} | "
                f"Above 50D {yes_no(metrics.get('above_sma_50'))} | "
                f"Above 200D {yes_no(metrics.get('above_sma_200'))} | "
                f"Vol30 {pct(metrics.get('volatility_30d_pct'))} | "
                f"AvgVol30 {compact_big_number(metrics.get('avg_volume_30d'))}"
            )

    lines += ["", "=== UNIFIED CALENDAR TRADE ENGINE V1 ==="]
    lines.extend(format_unified_calendar_engine_text(unified_calendar_engine))

    lines += ["", "=== STRUCTURED NEWS ==="]

    for ticker, articles in news_map.items():
        lines.append(f"{ticker}:")

        if not articles:
            lines.append("  - No relevant company news found.")
            continue

        for article in articles:
            normalized = normalize_news_item(ticker, article)
            title = normalized["title"]
            source = normalized["source"]
            published_at = normalized["published_at"] or "Unknown date"
            score = normalized["relevance_score"]
            url = normalized["url"]

            lines.append(
                f"  - {title} | Source: {source} | Published: {published_at} | "
                f"Relevance: {score:.2f}"
            )
            if url:
                lines.append(f"    URL: {url}")

    lines += [
        "",
        "=== ADVISOR CONTEXT ===",
        "This project gathers portfolio data, current prices, gain/loss, market value,",
        "account grouping, relevance-scored news, market trend/momentum metrics,",
        "watchlist candidate review, and one unified calendar trade engine",
        "that combines earnings discovery, spread screening, open-position detection,",
        "and lifecycle checks for Tradier-held option legs",
        "so the portfolio can be evaluated using numerical and strategic qualifiers.",
        "",
        "Current scoring style: Aggressive Quality-Momentum Snapshot v2.",
        "This version uses current position data, allocation risk, duplicate exposure,",
        "asset risk, structured news, price momentum, relative strength, 50/200-day",
        "trend state, 52-week high/low distance, volatility, and liquidity.",
        "It does not yet include fundamentals, earnings surprises, analyst revisions,",
        "or persistent trade-memory yet. Tradier data is now used for",
        "market-data fallback, quote/options liquidity, earnings-driven long-call",
        "calendar candidate screening, watchlist idea triage, detecting existing",
        "Tradier-held calendar spreads, and basic hold/exit review checks.",
        "",
        "Provide a practical daily portfolio briefing based only on the data above.",
        "Include: overall portfolio summary, strongest add/hold candidates, names to",
        "avoid adding to, major risk flags, news relevance, and practical watch items.",
        "Keep it under 600 words, plain text.",
    ]

    return "\n".join(lines)


def _safe_text(value: Any, fallback: str = "—") -> str:
    text = str(value) if value not in (None, "") else fallback
    return escape(text)


def _chip(label: str, value: Any | None = None, css_class: str = "neutral", href: str | None = None) -> str:
    value_html = f"<strong>{_safe_text(value)}</strong>" if value is not None else ""
    tag = "a" if href else "span"
    href_attr = f' href="{escape(href, quote=True)}"' if href else ""
    return f'<{tag}{href_attr} class="chip {escape(css_class)}">{escape(label)}{value_html}</{tag}>'


def _tone_for_text(value: Any) -> str:
    text = str(value or "").upper()
    if any(token in text for token in ("FAIL", "AVOID", "REDUCE", "CUT", "RISK", "ELEVATED")):
        return "bad"
    if any(token in text for token in ("URGENT", "WATCH", "REVIEW", "WARN", "PULLBACK", "CONSIDER")):
        return "warn"
    if any(token in text for token in ("PASS", "ADD", "PROFIT", "OK", "HOLD")):
        return "good"
    return "neutral"


def _signed_class(value: Any) -> str:
    try:
        return "positive" if float(value) >= 0 else "negative"
    except (TypeError, ValueError):
        return "muted"


def _get_first_present(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, "", []):
            return row.get(key)
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    for key in keys:
        if key in raw and raw.get(key) not in (None, "", []):
            return raw.get(key)
    return default


def _ticker_set_from_zero_positions(positions: list[dict[str, Any]]) -> set[str]:
    return {str(pos.get("ticker") or "").upper().strip() for pos in positions if is_zero_value_position(pos)}


def _zero_tickers_from_positions_and_recommendations(
    positions: list[dict[str, Any]],
    recommendations: Recommendations | None,
) -> set[str]:
    zero_tickers = _ticker_set_from_zero_positions(positions)
    for rec in recommendations or []:
        ticker = str(rec.get("ticker") or "").upper().strip()
        if ticker and _zero_value_recommendation(rec):
            zero_tickers.add(ticker)
    return zero_tickers


def _ticker_is_zero_value(ticker: Any, zero_tickers: set[str]) -> bool:
    return str(ticker or "").upper().strip() in zero_tickers


def _filter_nonzero_positions(positions: list[dict[str, Any]], zero_tickers: set[str]) -> list[dict[str, Any]]:
    return [
        pos for pos in positions or []
        if not _ticker_is_zero_value(pos.get("ticker"), zero_tickers)
        and not is_zero_value_position(pos)
    ]


def _filter_nonzero_recommendations(recommendations: Recommendations, zero_tickers: set[str]) -> Recommendations:
    return [
        rec for rec in recommendations
        if not _ticker_is_zero_value(rec.get("ticker"), zero_tickers)
        and not _zero_value_recommendation(rec)
    ]


def _zero_value_recommendation(rec: dict[str, Any]) -> bool:
    value = safe_float(_get_first_present(rec, "position_value", "market_value"))
    allocation = safe_float(rec.get("allocation_pct"))
    quantity = safe_float(rec.get("quantity"))
    if quantity is not None and value is not None:
        return abs(quantity) <= 1e-9 and abs(value) <= 0.01
    if value is not None and allocation is not None:
        return abs(value) <= 0.01 and abs(allocation) <= 1e-9
    return False


def _zero_value_action(row: dict[str, Any]) -> bool:
    value = safe_float(_get_first_present(row, "market_value", "position_value"))
    allocation = safe_float(row.get("allocation_pct"))
    quantity = safe_float(row.get("quantity"))
    if quantity is not None and value is not None:
        return abs(quantity) <= 1e-9 and abs(value) <= 0.01
    if value is not None and allocation is not None:
        return abs(value) <= 0.01 and abs(allocation) <= 1e-9
    return False


def _action_group(action: Any) -> str:
    text = str(action or "").upper()
    if any(token in text for token in ("AVOID", "REDUCE", "CUT", "TRIM", "DO NOT ADD", "FAIL")):
        return "risk"
    if any(token in text for token in ("WATCH", "RESEARCH", "CONFIRM TREND", "STOCK CANDIDATE")):
        return "watch"
    if any(token in text for token in ("CONSIDER ADDING", "ADD ON PULLBACK", "REVIEW ADD", "HIGH-PRIORITY CONSIDER ADDING")):
        return "actionable"
    if "ADD" in text:
        return "actionable"
    return "watch"


def _source_or_text_indicates_risk(raw: dict[str, Any], source: str) -> bool:
    text = " ".join(
        str(value)
        for value in [
            source,
            raw.get("source"),
            raw.get("type"),
            raw.get("why"),
            raw.get("main_reason"),
            raw.get("reason"),
            " ".join(str(x) for x in (raw.get("reasons", []) or [])),
            " ".join(str(x) for x in (raw.get("risks", []) or [])),
        ]
        if value
    ).upper()
    return any(token in text for token in ("RISK REVIEW", "AVOID", "REDUCE", "CUT", "TRIM", "DO NOT ADD", "FAIL"))


def _filter_daily_opportunity_engine(engine: dict[str, Any], zero_tickers: set[str]) -> dict[str, Any]:
    if not isinstance(engine, dict):
        return {}
    filtered = dict(engine)
    actions = [
        item for item in (engine.get("actions", []) or [])
        if isinstance(item, dict)
        and not _ticker_is_zero_value(item.get("ticker"), zero_tickers)
        and not _zero_value_action(item)
    ]
    filtered["actions"] = actions
    summary = dict(filtered.get("summary") or {})
    summary["action_count"] = len(actions)
    summary["calendar_count"] = sum(1 for item in actions if str(item.get("type") or "") in {"calendar", "active_calendar"})
    summary["stock_count"] = sum(1 for item in actions if str(item.get("type") or "") in {"stock", "stock_add"})
    summary["gap_count"] = sum(1 for item in actions if str(item.get("type") or "") == "gap")
    summary["risk_count"] = sum(
        1 for item in actions
        if _action_group(item.get("action")) == "risk"
        or str(item.get("type") or "") in {"risk", "portfolio_risk"}
    )
    filtered["summary"] = summary
    filtered["has_data"] = bool(actions)
    return filtered


def _normalized_backtest_label(row: dict[str, Any]) -> str:
    final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
    raw = str(_get_first_present(row, "backtest_mode", "backtest_status", default=final.get("backtest_status") or "not_eligible"))
    text = raw.lower()
    blocker = _first_text(row.get("main_blocker"), final.get("main_blocker"), final.get("hard_fail_reason"), fallback="")
    if "diagnostic" in text:
        return f"Diagnostic only{f' - {blocker}' if blocker else ''}"
    if "no_data" in text or "no historical" in text:
        return "No historical data available"
    if "untradeable" in text:
        return "Not eligible - options market untradeable"
    if "not_true" in text or "not a true" in text:
        return "Not eligible - not a true earnings IV-crush calendar"
    if "skip" in text or "not_eligible" in text or "not eligible" in text:
        return f"Not eligible{f' - {blocker}' if blocker else ''}"
    return raw.replace("_", " ")


def _deep_itm_warning(row: dict[str, Any]) -> str | None:
    option_type = str(_get_first_present(row, "option_type", default="call") or "call").lower()
    moneyness = safe_float(_get_first_present(row, "short_leg_moneyness_pct", "short_moneyness_pct", "short_moneyness", "moneyness"))
    dte = safe_float(_get_first_present(row, "front_dte", "short_dte", "short_leg_dte"))
    earnings_dte = safe_float(_get_first_present(row, "days_until_earnings", "earnings_dte"))
    if moneyness is None or dte is None:
        return None
    label = "SHORT LEG ITM - CLOSE / ROLL REVIEW"
    if moneyness > 10 and (earnings_dte is None or earnings_dte <= 1):
        label = "SHORT LEG DEEP ITM - CLOSE / ROLL REVIEW REQUIRED"
    elif moneyness <= 5 or dte > 3:
        return None
    if option_type == "put":
        label = label.replace("SHORT LEG", "SHORT PUT")
    return label + ". Original calendar thesis may be broken because underlying has moved far beyond the short strike."


def _compact_ul(items: list[Any], limit: int = 4) -> str:
    clean = [str(item) for item in items if item not in (None, "")]
    if not clean:
        return '<span class="empty">—</span>'
    return '<ul class="compact-list">' + "".join(f"<li>{escape(item)}</li>" for item in clean[:limit]) + "</ul>"


def _first_text(*values: Any, fallback: str = "—") -> str:
    for value in values:
        if isinstance(value, list) and value:
            return str(value[0])
        if value not in (None, "", []):
            return str(value)
    return fallback


def _daily_actions(daily_opportunity: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (daily_opportunity or {}).get("actions", []) or [] if isinstance(item, dict)]


def _active_calendar_rows(
    unified_calendar_engine: dict[str, Any],
    lifecycle_checks: dict[str, Any] | list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = [row for row in (unified_calendar_engine or {}).get("open_trade_rows", []) or [] if isinstance(row, dict)]
    if rows:
        return rows
    if isinstance(lifecycle_checks, dict):
        return [row for row in lifecycle_checks.get("checks", []) or [] if isinstance(row, dict)]
    return [row for row in lifecycle_checks or [] if isinstance(row, dict)]


def _potential_add_groups(
    daily_opportunity: dict[str, Any],
    stock_momentum: dict[str, Any],
    portfolio_gap: dict[str, Any],
    zero_tickers: set[str],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"actionable": [], "watch": [], "risk": []}
    seen: set[str] = set()

    def add_item(raw: dict[str, Any], source: str) -> None:
        ticker = str(raw.get("ticker") or "").upper().strip()
        if not ticker or ticker in seen or _ticker_is_zero_value(ticker, zero_tickers) or _zero_value_action(raw):
            return
        action = str(raw.get("action") or raw.get("category") or raw.get("verdict") or "WATCH / RESEARCH")
        normalized = {
            "ticker": ticker,
            "priority_score": _get_first_present(raw, "priority_score", "score", "rank_score"),
            "action": action,
            "why": _first_text(raw.get("why"), raw.get("main_reason"), raw.get("reason"), raw.get("reasons", []), fallback="Review setup"),
            "next_step": _first_text(raw.get("next_step"), raw.get("next_check"), fallback="Next check pending"),
            "source": raw.get("source") or raw.get("category_source") or source,
            "risks": raw.get("risks", []) or [],
        }
        group = "risk" if _source_or_text_indicates_risk(raw, source) else _action_group(action)
        groups[group].append(normalized)
        seen.add(ticker)

    for item in _daily_actions(daily_opportunity):
        if str(item.get("type") or "").lower() == "active_calendar":
            continue
        add_item(item, "daily")

    for item in (stock_momentum or {}).get("items", []) or []:
        if isinstance(item, dict):
            add_item(item, "momentum")

    for item in (portfolio_gap or {}).get("suggestions", []) or []:
        if isinstance(item, dict):
            item = dict(item)
            item.setdefault("action", "REVIEW ADD")
            item.setdefault("source", "sector_gap")
            add_item(item, "sector_gap")

    for key in groups:
        groups[key] = sorted(groups[key], key=lambda row: safe_float(row.get("priority_score")) or 0.0, reverse=True)
    return groups


def _blocked_calendar_rows(
    unified_calendar_engine: dict[str, Any],
    calendar_ranking: dict[str, Any],
) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for row in (unified_calendar_engine or {}).get("new_trade_rows", []) or []:
        if not isinstance(row, dict):
            continue
        verdict = str(row.get("verdict") or row.get("final_verdict") or "").upper()
        if "FAIL" in verdict or "WATCH" in verdict or row.get("main_blocker"):
            blocked.append(row)
    seen = {str(row.get("ticker")) for row in blocked}
    for row in (calendar_ranking or {}).get("items", []) or []:
        if not isinstance(row, dict):
            continue
        final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
        ticker = str(row.get("ticker") or "")
        verdict = str(final.get("final_verdict") or row.get("action") or "").upper()
        if ticker not in seen and ("FAIL" in verdict or "WATCH" in verdict or final.get("main_blocker")):
            blocked.append(row)
            seen.add(ticker)
    return blocked


def _portfolio_risk_count(portfolio_gap: dict[str, Any], recommendations: Recommendations) -> int:
    risks = len((portfolio_gap or {}).get("risk_rows", []) or [])
    risks += sum(1 for rec in recommendations if rec.get("risks"))
    return risks


def _normalized_provider_status(
    pipeline_status: dict[str, Any],
    log_lines: list[str],
    recommendations: Recommendations | None = None,
    provider_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config_snapshot = pipeline_status.get("config_snapshot", {}) if isinstance(pipeline_status, dict) else {}
    metric_text = " ".join(
        str((rec.get("market_metrics") or {}).get("error") or "")
        for rec in (recommendations or [])
        if isinstance(rec.get("market_metrics"), dict)
    )
    text = " ".join([str(pipeline_status), " ".join(log_lines), metric_text]).lower()
    run_mode = str(pipeline_status.get("run_mode") or pipeline_status.get("mode") or "prod").lower() if pipeline_status else "prod"
    finnhub_key = bool(config_snapshot.get("has_finnhub_api_key"))
    tradier_key = bool(config_snapshot.get("has_tradier_access_token"))
    av_key = bool(config_snapshot.get("has_alpha_vantage_api_key"))
    rh_seen = "robinhood" in text or "positions" in text
    finnhub_blocked = "finnhub" in text and any(token in text for token in ("403", "forbidden", "candle", "candles unavailable", "stock/candle", "unavailable"))
    tradier_fallback = "tradier fallback" in text or ("fallback" in text and "tradier" in text)
    rh_meta = ((provider_meta or {}).get("robinhood") or {}) if isinstance(provider_meta, dict) else {}
    candle_meta = ((provider_meta or {}).get("candles") or {}) if isinstance(provider_meta, dict) else {}
    rh_status = str(rh_meta.get("status") or "").lower().strip()
    rh_failed = rh_status in {"rate_limited", "auth_required", "auth_failed"}
    rh = {
        "positions": bool(rh_meta.get("success")) or (rh_seen and not rh_failed),
        "status": rh_status or ("ok" if rh_seen else "unknown"),
        "error": rh_meta.get("error"),
        "rate_limited": bool(rh_meta.get("rate_limited") or rh_status == "rate_limited"),
        "auth_required": bool(rh_meta.get("auth_required") or rh_status == "auth_required"),
        "auth_failed": rh_status == "auth_failed",
        "configured": rh_meta.get("configured"),
    }
    return {
        "mode": run_mode,
        "dev_limited": run_mode == "dev",
        "robinhood": rh,
        "tradier": {"key": tradier_key, "usable": tradier_key, "historical_fallback": tradier_fallback},
        "finnhub": {"key": finnhub_key, "candles": False if finnhub_blocked else None, "candles_blocked": finnhub_blocked},
        "alpha_vantage": {"key": av_key},
        "candles": candle_meta,
    }


def _top_summary_html(
    today: str,
    active_count: int,
    urgent_count: int,
    risk_count: int,
    adds_count: int,
    blocked_count: int,
    pipeline_status: dict[str, Any],
    provider_status: dict[str, Any],
) -> str:
    mode = _first_text(pipeline_status.get("mode"), pipeline_status.get("run_mode"), fallback="dev/prod")
    return f"""
    <header class="top-summary">
        <div class="summary-title">
            <h1>Stock Advisor</h1>
            <span>{escape(today)} · read-only decision dashboard</span>
        </div>
        <div class="summary-chips" aria-label="Run summary">
            {_chip("ACTIVE", active_count, "warn" if active_count else "neutral", "#active-calendars")}
            {_chip("URGENT", urgent_count, "bad" if urgent_count else "neutral", "#active-calendars")}
            {_chip("RISK", risk_count, "warn" if risk_count else "neutral", "#risk-review")}
            {_chip("ADDS", adds_count, "good" if adds_count else "neutral", "#actionable-adds")}
            {_chip("BLOCKED", blocked_count, "bad" if blocked_count else "neutral", "#blocked-calendars")}
            {_chip("MODE", mode, "neutral")}
            {_provider_chips(provider_status)}
        </div>
    </header>"""


def _provider_chips(provider_status: dict[str, Any]) -> str:
    rh = provider_status.get("robinhood", {}) or {}
    tradier = provider_status.get("tradier", {}) or {}
    finnhub = provider_status.get("finnhub", {}) or {}
    av = provider_status.get("alpha_vantage", {}) or {}
    candles = provider_status.get("candles", {}) or {}
    rh_status = str(rh.get("status") or "").lower()
    if rh.get("rate_limited") or rh_status == "rate_limited":
        rh_label, rh_tone = "RATE LIMITED", "bad"
    elif rh.get("auth_required") or rh_status == "auth_required":
        rh_label, rh_tone = "AUTH REQUIRED", "warn"
    elif rh.get("auth_failed") or rh_status == "auth_failed":
        rh_label, rh_tone = "AUTH FAILED", "bad"
    elif rh.get("positions") or rh_status == "ok":
        rh_label, rh_tone = "OK", "good"
    else:
        rh_label, rh_tone = "UNKNOWN", "neutral"
    chips = [
        _chip("RH", rh_label, rh_tone),
        _chip("TRADIER", "OK" if tradier.get("usable") else "KEY MISSING", "good" if tradier.get("usable") else "warn"),
    ]
    if tradier.get("historical_fallback"):
        chips.append(_chip("TRADIER", "FALLBACK ACTIVE", "warn"))
    if finnhub.get("candles_blocked"):
        chips.append(_chip("FINNHUB", "KEY OK · CANDLES BLOCKED", "warn"))
    elif finnhub.get("key"):
        chips.append(_chip("FINNHUB", "KEY OK", "neutral"))
    else:
        chips.append(_chip("FINNHUB", "KEY MISSING", "neutral"))
    chips.append(_chip("AV", "OK" if av.get("key") else "—", "good" if av.get("key") else "neutral"))
    if candles.get("ticker_count"):
        selected = ", ".join(str(item).upper() for item in (candles.get("selected_providers") or [])) or "NONE"
        chips.append(_chip("CANDLES", f"{candles.get('success_count', 0)}/{candles.get('ticker_count', 0)} {selected}", "good" if candles.get("success_count") else "warn"))
    if provider_status.get("dev_limited"):
        chips.append(_chip("MODE", "dev · market data scope limited", "warn"))
    return "".join(chips)


def _macro_context_html(recommendations: Recommendations, provider_status: dict[str, Any]) -> str:
    metrics = [rec.get("market_metrics", {}) or {} for rec in recommendations if isinstance(rec.get("market_metrics"), dict)]
    with_data = [m for m in metrics if m.get("has_data")]
    above_200 = [m.get("above_sma_200") for m in with_data if m.get("above_sma_200") is not None]
    positive_6m = [m.get("return_6m_pct") for m in with_data if m.get("return_6m_pct") is not None]
    if not with_data:
        regime = "partial data"
        trend = "trend unavailable"
        bias = "use position signals"
    else:
        above_rate = sum(1 for value in above_200 if value is True) / max(len(above_200), 1)
        numeric_6m = []
        for value in positive_6m:
            try:
                numeric_6m.append(float(value))
            except (TypeError, ValueError):
                continue
        pos_rate = sum(1 for value in numeric_6m if value > 0) / max(len(numeric_6m), 1)
        regime = "risk-on" if above_rate >= 0.65 and pos_rate >= 0.65 else "risk-off" if above_rate <= 0.35 else "neutral"
        trend = f"{int(above_rate * 100)}% above 200D" if above_200 else "trend unavailable"
        bias = "adds allowed" if regime == "risk-on" else "defensive / avoid chasing" if regime == "risk-off" else "selective adds"
    cells = [
        ("Macro", regime),
        ("Benchmark Trend", trend),
        ("Growth / Tech", "constructive" if regime == "risk-on" else "pressured" if regime == "risk-off" else "mixed"),
        ("Volatility", "macro module pending"),
        ("Action Bias", bias),
        ("Scope", "dev-limited market-data subset" if provider_status.get("dev_limited") else "configured market-data subset"),
    ]
    return '<div class="macro-strip">' + "".join(
        f'<div class="macro-cell"><span class="label">{escape(label)}</span><span class="value">{escape(value)}</span></div>'
        for label, value in cells
    ) + "</div>"


def _robinhood_unavailable(provider_status: dict[str, Any] | None) -> bool:
    rh = (provider_status or {}).get("robinhood", {}) or {}
    status = str(rh.get("status") or "").lower()
    return bool(rh.get("rate_limited") or rh.get("auth_required") or rh.get("auth_failed") or status in {"rate_limited", "auth_required", "auth_failed"})


def _active_calendar_section_html(rows: list[dict[str, Any]], provider_status: dict[str, Any] | None = None) -> str:
    refresh = """
        <div class="refresh-row">
            <button type="button" class="export-btn" onclick="refreshActiveTrades()">Refresh Active Trades</button>
            <span id="refreshActiveStatus" class="refresh-status">Reprices broker-detected open option positions only.</span>
        </div>
    """
    if not rows and _robinhood_unavailable(provider_status):
        body = refresh + (
            '<p class="empty">Robinhood unavailable during this run.</p>'
            '<p class="muted">Portfolio data could not be refreshed, so active broker-detected calendars were not recalculated. '
            'Manual trade entry is intentionally avoided.</p>'
        )
    elif not rows:
        body = refresh + (
            '<p class="empty">No broker-detected active calendars were found.</p>'
            '<p class="muted">Use Refresh Active Trades to recheck broker positions and live option quotes. '
            'Manual trade entry is intentionally avoided.</p>'
        )
    else:
        cards = []
        for row in rows[:20]:
            ticker = _safe_text(row.get("ticker"), "UNKNOWN")
            action = _first_text(row.get("verdict"), row.get("action"), fallback="HOLD / MONITOR")
            current_debit = _get_first_present(row, "current_mid_debit", "current_debit", "current_spread_debit", "current_spread_value")
            entry_debit = _get_first_present(row, "entry_debit_estimate", "entry_debit", "entry_debit_est")
            pnl_pct = _get_first_present(row, "estimated_pnl_pct", "estimated_pl_pct", "pnl_pct")
            pnl_dollars = _get_first_present(row, "pnl_total_estimate", "estimated_pnl_dollars", "estimated_pl_dollars", "pnl_dollars")
            pnl = f'<span class="{_signed_class(pnl_pct)}">{signed_pct(pnl_pct)} / {signed_money(pnl_dollars)}</span>'
            short_dte = _first_text(row.get("front_dte"), row.get("short_dte"), row.get("short_leg_dte"), fallback="—")
            moneyness_value = _get_first_present(row, "short_leg_moneyness_pct", "short_moneyness_pct", "short_moneyness", "moneyness", "distance_to_strike_pct")
            moneyness = _first_text(row.get("short_leg_moneyness_label"), signed_pct(moneyness_value) if moneyness_value is not None else None, fallback="Unavailable")
            assignment = _first_text(row.get("assignment_risk_level"), row.get("assignment_risk"), fallback="—")
            next_check = _first_text(row.get("next_check"), row.get("next_action"), fallback="Recheck before market close.")
            structure = _first_text(row.get("structure"), fallback=(
                f"{option_money(_get_first_present(row, 'short_strike', 'strike'))} {str(row.get('option_type') or '').upper()} · "
                f"short {row.get('front_expiration') or row.get('short_expiration') or '—'} / "
                f"long {row.get('back_expiration') or row.get('long_expiration') or '—'}"
            ))
            pricing_quality = row.get("pricing_quality") if isinstance(row.get("pricing_quality"), dict) else {}
            pricing_warnings = pricing_quality.get("warnings", []) if isinstance(pricing_quality, dict) else []
            warnings = [
                _deep_itm_warning(row),
                row.get("historical_move_warning"),
                *pricing_warnings,
                *(row.get("pricing_warnings", []) or []),
                *(row.get("risks", []) or []),
            ]
            cards.append(f"""
            <details class="decision-card" open>
                <summary>
                    <div class="strip-summary">
                        <span class="ticker">{ticker}</span>
                        <span>{_chip(action, None, _tone_for_text(action))}</span>
                        <span>{pnl}</span>
                        <span>Short {escape(short_dte)} DTE</span>
                        <span>{escape(str(moneyness))}</span>
                        <span>{_chip("Assignment", assignment, _tone_for_text(assignment))}</span>
                    </div>
                    <div class="section-kicker">{escape(next_check)}</div>
                </summary>
                <div class="metric-grid">
                    <div class="metric"><span class="label">Current Debit</span><span class="value">{option_money(current_debit)}</span></div>
                    <div class="metric"><span class="label">Entry Debit</span><span class="value">{option_money(entry_debit)}</span></div>
                    <div class="metric"><span class="label">Target / Stop</span><span class="value">{option_money(row.get('target_debit'))} / {option_money(row.get('stop_debit'))}</span></div>
                    <div class="metric"><span class="label">Underlying</span><span class="value">{money(_get_first_present(row, 'underlying_price', 'underlying', 'underlying_last'))}</span></div>
                    <div class="metric"><span class="label">Hold-Through</span><span class="value">{number(row.get('hold_through_score'), 1)} · {_safe_text(row.get('hold_through_action'), 'review')}</span></div>
                    <div class="metric"><span class="label">Short Leg</span><span class="value">{escape(str(moneyness))}<br><span class="muted">Strike {option_money(_get_first_present(row, 'short_strike', 'strike'))}</span></span></div>
                    <div class="metric"><span class="label">Risk</span><span class="value">{escape(str(assignment))}</span></div>
                    <div class="metric"><span class="label">Structure</span><span class="value">{escape(structure)}</span></div>
                </div>
                <div class="detail-block">
                    <strong>Reasons</strong>{_compact_ul((row.get('reasons', []) or []) + [item for item in warnings if item])}
                </div>
            </details>""")
        body = refresh + "".join(cards)
    return _section("active-calendars", "Active Calendar Lifecycle", "Broker-detected open calendars; no manual trade tracking.", body, str(len(rows)))


def _holdings_section_html(recommendations: Recommendations, provider_status: dict[str, Any] | None = None) -> str:
    if not recommendations and _robinhood_unavailable(provider_status):
        body = (
            '<p class="empty">Robinhood unavailable during this run.</p>'
            '<p class="muted">Portfolio data could not be refreshed. Existing holdings should not be interpreted as empty.</p>'
        )
    elif not recommendations:
        body = '<p class="empty">No portfolio advisor scores generated.</p>'
    else:
        rows = []
        for rec in recommendations[:40]:
            action = str(rec.get("action") or "WATCH / REVIEW")
            rows.append(f"""
            <details class="decision-card">
                <summary>
                    <div class="holding-row">
                        <span class="ticker">{_safe_text(rec.get('ticker'), 'UNKNOWN')}</span>
                        <span>{_chip(action, None, _tone_for_text(action))}</span>
                        <span>Alloc {pct(rec.get('allocation_pct'))}</span>
                        <span class="{_signed_class(rec.get('gain_loss_pct'))}">{signed_pct(rec.get('gain_loss_pct'))}</span>
                        <span>{money(rec.get('position_value'))}</span>
                        <span class="muted">{_safe_text(rec.get('next_check'), 'Next check pending')}</span>
                    </div>
                </summary>
                <div class="detail-block">
                    <strong>Trend</strong><br>{format_trend_summary(rec.get('market_metrics', {}) or {})}
                    <br><br><strong>Reasons</strong>{_compact_ul(rec.get('reasons', []) or [])}
                    <strong>Risks / Limits</strong>{_compact_ul(rec.get('risks', []) or [])}
                </div>
            </details>""")
        body = "".join(rows)
    return _section("holdings", "Holdings / Portfolio Advisor", "Owned-position management appears before new ideas.", body, str(len(recommendations)))


def _potential_adds_section_html(
    groups: dict[str, list[dict[str, Any]]],
) -> str:
    def rows_for(items: list[dict[str, Any]], quiet: bool = False) -> str:
        rows = []
        for item in items[:30]:
            action = str(item.get("action") or "REVIEW")
            source = str(item.get("source") or "daily")
            rows.append(f"""
            <div class="decision-card add-row {'quiet-list' if quiet else ''}" role="group">
                <span class="ticker">{_safe_text(item.get('ticker'), 'UNKNOWN')}</span>
                <span class="score">{number(item.get('priority_score'), 1)}</span>
                <span>{_chip(action, None, _tone_for_text(action))}</span>
                <span>{_safe_text(item.get('why'), 'Review setup')}</span>
                <span class="chip-row">{_chip(source, None, 'neutral')}{''.join(_chip(str(risk), None, _tone_for_text(risk)) for risk in (item.get('risks', []) or [])[:2])}</span>
                <span class="muted">{_safe_text(item.get('next_step'), 'Next check pending')}</span>
            </div>""")
        return "".join(rows)

    actionable = groups.get("actionable", []) or []
    watch = groups.get("watch", []) or []
    body = ""
    body += '<h3 class="subsection-title" id="actionable-adds">Actionable Adds</h3>'
    body += rows_for(actionable) if actionable else '<p class="empty">No actionable add candidates cleared this run.</p>'
    body += '<h3 class="subsection-title">Watch / Research</h3>'
    body += rows_for(watch, quiet=True) if watch else '<p class="empty">No watch/research add candidates this run.</p>'
    return _section("potential-adds", "Unified Potential Adds", "Avoid/reduce/risk rows are separated from actionable stock-add ideas.", body, str(len(actionable)))


def _risk_review_section_html(groups: dict[str, list[dict[str, Any]]], recommendations: Recommendations) -> str:
    risk_items = list(groups.get("risk", []) or [])
    seen = {str(item.get("ticker") or "").upper().strip() for item in risk_items}
    for rec in recommendations:
        action = str(rec.get("action") or "")
        ticker = str(rec.get("ticker") or "").upper().strip()
        if ticker not in seen and (rec.get("risks") or _action_group(action) == "risk"):
            risk_items.append({
                "ticker": rec.get("ticker"),
                "priority_score": rec.get("score"),
                "action": action or "WATCH / REVIEW",
                "why": _first_text(rec.get("risks", []), rec.get("reasons", []), fallback="Review risk controls"),
                "next_step": rec.get("next_check"),
                "source": "holding",
                "risks": rec.get("risks", []) or [],
            })
            seen.add(ticker)
    if not risk_items:
        body = '<p class="empty">No avoid/reduce/cut risk controls surfaced this run.</p>'
    else:
        body = ""
        for item in risk_items[:40]:
            action = str(item.get("action") or "REVIEW")
            body += f"""
            <div class="decision-card add-row quiet-list" role="group">
                <span class="ticker">{_safe_text(item.get('ticker'), 'UNKNOWN')}</span>
                <span class="score">{number(item.get('priority_score'), 1)}</span>
                <span>{_chip(action, None, _tone_for_text(action))}</span>
                <span>{_safe_text(item.get('why'), 'Review risk controls')}</span>
                <span class="chip-row">{_chip(str(item.get('source') or 'risk'), None, 'neutral')}</span>
                <span class="muted">{_safe_text(item.get('next_step'), 'Next check pending')}</span>
            </div>"""
    return _section("risk-review", "Risk Review", "Avoid, reduce, cut, and existing-position risk controls are separated from add ideas.", body, str(len(risk_items)))


def _line_item(row: dict[str, Any], fields: list[str]) -> str:
    parts = []
    for field in fields:
        value = row.get(field)
        if value not in (None, "", []):
            parts.append(f"{field.replace('_', ' ')}: {value}")
    return " | ".join(parts) or "No details attached."


def _build_daily_brief_export(
    today: str,
    provider_status: dict[str, Any],
    active_rows: list[dict[str, Any]],
    recommendations: Recommendations,
    groups: dict[str, list[dict[str, Any]]],
    blocked_rows: list[dict[str, Any]],
    portfolio_gap: dict[str, Any],
) -> str:
    lines = [
        f"Daily Brief - {today}",
        f"Mode: {provider_status.get('mode', 'unknown')}",
        "Provider status: " + _provider_status_text(provider_status),
        "",
        f"Active calendars: {len(active_rows)}",
    ]
    if active_rows:
        for row in active_rows[:5]:
            lines.append(f"- {row.get('ticker', 'UNKNOWN')}: {_first_text(row.get('next_action'), row.get('action'), row.get('verdict'))}; P/L {signed_pct(_get_first_present(row, 'estimated_pnl_pct', 'pnl_pct'))}; assignment {_first_text(row.get('assignment_risk_level'), row.get('assignment_risk'))}")
    lines += ["", "Top holding actions:"]
    for rec in recommendations[:8]:
        lines.append(f"- {rec.get('ticker', 'UNKNOWN')}: {rec.get('action', 'WATCH')} | alloc {pct(rec.get('allocation_pct'))} | risk {_first_text(rec.get('risks', []), fallback='—')}")
    lines += ["", "Actionable adds:"]
    if groups.get("actionable"):
        for item in groups["actionable"][:8]:
            lines.append(f"- {item.get('ticker')}: {item.get('action')} | score {number(item.get('priority_score'), 1)} | {item.get('why')}")
    else:
        lines.append("- None cleared.")
    lines += ["", f"Risk review rows: {len(groups.get('risk', []))}", f"Blocked calendar candidates: {len(blocked_rows)}"]
    exposures = portfolio_gap.get("exposure_rows", []) or []
    if exposures:
        lines += ["", "Portfolio context:"]
        for row in exposures[:5]:
            lines.append(f"- {_first_text(row.get('bucket'), row.get('sector'), row.get('theme'), fallback='Bucket')}: {pct(row.get('actual_pct') or row.get('current_pct'))} / {pct(row.get('target_pct'))} | {_first_text(row.get('status'), row.get('label'), fallback='review')}")
    return "\n".join(lines)


def _build_calendar_report_export(active_rows: list[dict[str, Any]], unified_calendar_engine: dict[str, Any], blocked_rows: list[dict[str, Any]]) -> str:
    lines = ["Calendar Report", ""]
    lines.append("Active broker-detected calendars")
    if not active_rows:
        lines.append("- No broker-detected active calendars.")
    for row in active_rows:
        lines.append(
            f"- {row.get('ticker', 'UNKNOWN')}: {_first_text(row.get('structure'), fallback='structure unavailable')} | "
            f"action {_first_text(row.get('next_action'), row.get('action'), row.get('verdict'))} | "
            f"current {option_money(_get_first_present(row, 'current_mid_debit', 'current_debit'))} | "
            f"entry {option_money(_get_first_present(row, 'entry_debit_estimate', 'entry_debit'))} | "
            f"P/L {signed_pct(_get_first_present(row, 'estimated_pnl_pct', 'pnl_pct'))} / {signed_money(_get_first_present(row, 'pnl_total_estimate', 'pnl_dollars'))} | "
            f"target/stop {option_money(row.get('target_debit'))}/{option_money(row.get('stop_debit'))} | "
            f"short DTE {_first_text(row.get('front_dte'), row.get('short_dte'))} | "
            f"moneyness {signed_pct(_get_first_present(row, 'short_leg_moneyness_pct', 'short_moneyness_pct'))} | "
            f"assignment {_first_text(row.get('assignment_risk_level'), row.get('assignment_risk'))} | "
            f"hold-through {number(row.get('hold_through_score'), 1)} {row.get('hold_through_action') or ''} | "
            f"next {_first_text(row.get('next_check'), row.get('next_action'))}"
        )
    lines += ["", "Accepted / qualified calendar candidates"]
    accepted = [
        row for row in (unified_calendar_engine or {}).get("new_trade_rows", []) or []
        if isinstance(row, dict) and str(row.get("verdict") or "").upper().startswith("PASS")
    ]
    if not accepted:
        lines.append("- No qualified calendar entries passed all criteria.")
    for row in accepted:
        lines.append(f"- {row.get('ticker', 'UNKNOWN')}: {row.get('verdict')} | {row.get('trade_type_label')} | debit {option_money((row.get('possible_spread') or {}).get('conservative_debit') if isinstance(row.get('possible_spread'), dict) else row.get('debit'))} | backtest {_normalized_backtest_label(row)}")
    lines += ["", "Blocked calendar candidates"]
    if not blocked_rows:
        lines.append("- No blocked calendar candidates.")
    for row in blocked_rows:
        final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
        lines.append(f"- {row.get('ticker', 'UNKNOWN')}: {_first_text(row.get('verdict'), final.get('final_verdict'), row.get('action'))} | {_first_text(row.get('trade_type_label'), final.get('trade_type_label'))} | blocker {_first_text(row.get('main_blocker'), final.get('main_blocker'), final.get('hard_fail_reason'))} | backtest {_normalized_backtest_label(row)} | why not actionable: final verdict failed.")
    return "\n".join(lines)


def _build_holdings_report_export(recommendations: Recommendations) -> str:
    lines = ["Holdings Report", ""]
    if not recommendations:
        return "Holdings Report\n\nNo owned nonzero positions available."
    for rec in recommendations:
        lines.append(
            f"- {rec.get('ticker', 'UNKNOWN')}: {rec.get('action', 'WATCH')} | "
            f"allocation {pct(rec.get('allocation_pct'))} | G/L {signed_pct(rec.get('gain_loss_pct'))} | "
            f"value {money(rec.get('position_value'))} | reason {_first_text(rec.get('reasons', []), fallback='—')} | "
            f"risk {_first_text(rec.get('risks', []), fallback='—')} | next {rec.get('next_check') or '—'}"
        )
    return "\n".join(lines)


def _build_potential_adds_export(groups: dict[str, list[dict[str, Any]]]) -> str:
    lines = ["Potential Adds Report", "", "Actionable Adds"]
    for key, title in [("actionable", "Actionable Adds"), ("watch", "Watch / Research"), ("risk", "Risk Controls")]:
        if title != "Actionable Adds":
            lines += ["", title]
        items = groups.get(key, []) or []
        if not items:
            lines.append("- None.")
        for item in items:
            lines.append(f"- {item.get('ticker')}: {item.get('action')} | score {number(item.get('priority_score'), 1)} | source {item.get('source')} | {item.get('why')}")
    return "\n".join(lines)


def _provider_status_text(provider_status: dict[str, Any]) -> str:
    parts = []
    rh = provider_status.get("robinhood", {}) or {}
    tradier = provider_status.get("tradier", {}) or {}
    finnhub = provider_status.get("finnhub", {}) or {}
    candles = provider_status.get("candles", {}) or {}
    rh_status = str(rh.get("status") or "").lower()
    if rh.get("rate_limited") or rh_status == "rate_limited":
        parts.append("RH rate limited")
    elif rh.get("auth_required") or rh_status == "auth_required":
        parts.append("RH auth required")
    elif rh.get("auth_failed") or rh_status == "auth_failed":
        parts.append("RH auth failed")
    else:
        parts.append("RH OK" if rh.get("positions") or rh_status == "ok" else "RH unknown")
    parts.append("TRADIER OK" if tradier.get("usable") else "TRADIER key missing")
    if tradier.get("historical_fallback"):
        parts.append("TRADIER fallback active")
    if finnhub.get("candles_blocked"):
        parts.append("FINNHUB key OK; candles blocked")
    elif finnhub.get("key"):
        parts.append("FINNHUB key OK")
    if candles.get("ticker_count"):
        selected = ",".join(str(item) for item in (candles.get("selected_providers") or [])) or "none"
        parts.append(f"candles {candles.get('success_count', 0)}/{candles.get('ticker_count', 0)} via {selected}")
    if provider_status.get("dev_limited"):
        parts.append("dev-limited market data scope")
    return "; ".join(parts)


def _export_toolbar_html(exports: dict[str, str]) -> str:
    exports_json = escape(json.dumps(exports), quote=False)
    return f"""
    <section class="report-section" id="exports">
        <div class="section-head">
            <div>
                <h2>Exports</h2>
                <div class="section-kicker">Purpose-specific exports; full payload is debug/troubleshooting only.</div>
            </div>
        </div>
        <div class="section-body">
            <div class="export-toolbar">
                <button type="button" class="export-btn" onclick="copyExport('dailyBrief')">Copy Daily Brief</button>
                <button type="button" class="export-btn" onclick="copyExport('calendarReport')">Copy Calendar Report</button>
                <button type="button" class="export-btn" onclick="copyExport('holdingsReport')">Copy Holdings Report</button>
                <button type="button" class="export-btn" onclick="copyExport('potentialAdds')">Copy Potential Adds</button>
                <button type="button" class="export-btn" onclick="downloadExport('fullDebugPayload', 'algo-stock-advisor-full-debug-payload.txt')">Download Full Debug Payload</button>
            </div>
            <textarea id="copyFallback" class="fallback-copy" aria-label="Copy fallback text"></textarea>
            <script id="exportPayloads" type="application/json">{exports_json}</script>
        </div>
    </section>"""


def _dashboard_script_html() -> str:
    return """
    <div id="toast" class="toast" role="status" aria-live="polite"></div>
    <script>
        const exportPayloads = JSON.parse(document.getElementById('exportPayloads')?.textContent || '{}');
        function showToast(message, isError) {
            const toast = document.getElementById('toast');
            toast.textContent = message;
            toast.style.borderColor = isError ? 'var(--bad)' : 'var(--good)';
            toast.classList.add('show');
            window.setTimeout(() => toast.classList.remove('show'), 3600);
        }
        async function copyTextWithFallback(text, fallbackElementId, successMessage, failureMessage) {
            const fallback = document.getElementById(fallbackElementId);
            try {
                if (!text) throw new Error('Nothing to copy.');
                if (!navigator.clipboard || !window.isSecureContext) throw new Error('Clipboard API unavailable.');
                await navigator.clipboard.writeText(text);
                if (fallback) fallback.style.display = 'none';
                showToast(successMessage || 'Payload copied.', false);
                return true;
            } catch (err) {
                if (fallback) {
                    fallback.value = text || '';
                    fallback.style.display = 'block';
                    fallback.focus();
                    fallback.select();
                }
                showToast(failureMessage || 'Copy failed - payload available below.', true);
                return false;
            }
        }
        function copyExport(key) {
            return copyTextWithFallback(exportPayloads[key] || '', 'copyFallback', key === 'fullDebugPayload' ? 'Payload copied.' : 'Copied.', key === 'fullDebugPayload' ? 'Copy failed - payload available below.' : 'Clipboard failed. Fallback text area is ready to select/copy.');
        }
        function downloadExport(key, filename) {
            const text = exportPayloads[key] || '';
            const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = filename;
            document.body.appendChild(link);
            link.click();
            link.remove();
            URL.revokeObjectURL(url);
            showToast('Download started.', false);
        }
        async function refreshActiveTrades() {
            const status = document.getElementById('refreshActiveStatus');
            const token = new URLSearchParams(window.location.search).get('token') || '';
            status.textContent = 'repricing...';
            try {
                const response = await fetch('/refresh-active-trades?token=' + encodeURIComponent(token), { cache: 'no-store' });
                const data = await response.json();
                if (!response.ok || data.status === 'error') throw new Error(data.error || 'Refresh failed.');
                const count = data.summary?.calendar_count ?? 0;
                status.textContent = 'success ' + new Date().toLocaleTimeString() + ' · active calendars ' + count;
                showToast('Active trades refreshed. Rerun the full report to rebuild all cards.', false);
            } catch (err) {
                status.textContent = 'failure: ' + err.message;
                showToast('Active refresh failed: ' + err.message, true);
            }
        }
    </script>"""


def _blocked_calendar_section_html(rows: list[dict[str, Any]]) -> str:
    if not rows:
        body = '<p class="empty">No blocked calendar candidates to review.</p>'
    else:
        cards = []
        for row in rows[:30]:
            final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
            verdict = _first_text(row.get("verdict"), final.get("final_verdict"), row.get("action"), fallback="WATCH")
            blocker = _first_text(row.get("main_blocker"), final.get("main_blocker"), final.get("hard_fail_reason"), row.get("main_reason"), fallback="No main blocker recorded.")
            trade_type = _first_text(row.get("trade_type_label"), final.get("trade_type_label"), fallback="Unknown")
            backtest = _normalized_backtest_label(row)
            account = _first_text(row.get("account_risk_status"), final.get("account_risk_status"), final.get("account_risk_warning"), fallback="—")
            debit = _get_first_present(row, "debit", "conservative_debit", "mid_debit", default=(row.get("possible_spread") or {}).get("conservative_debit") if isinstance(row.get("possible_spread"), dict) else None)
            max_loss = _get_first_present(row, "estimated_max_loss", "max_loss", "max_loss_estimate")
            guardrail = _first_text(row.get("account_risk_warning"), final.get("account_risk_warning"), row.get("guardrail_detail"), fallback="Configured account guardrail failed; account budget details unavailable." if "DEBIT" in verdict.upper() else "—")
            research_note = ""
            if "DEBIT" in verdict.upper() or "DEBIT" in blocker.upper():
                research_note = "Candidate research idea: try nearer back expiry, lower-debit same-strike structure, put-calendar alternative, or true earnings-calendar expiry that includes the event. No trade until a lower-debit structure passes all filters."
            if "PRE-EARNINGS" in trade_type.upper() or "LONG-VOL" in trade_type.upper():
                research_note = (research_note + " " if research_note else "") + "Research-only: short leg expires before earnings, so this is not a true IV-crush earnings calendar."
            cards.append(f"""
            <details class="decision-card">
                <summary>
                    <div class="blocked-row">
                        <span class="ticker">{_safe_text(row.get('ticker'), 'UNKNOWN')}</span>
                        <span>{_chip(verdict, None, _tone_for_text(verdict))}</span>
                        <span>{escape(trade_type)}</span>
                        <span>{escape(blocker)}</span>
                        <span class="muted">Backtest {escape(backtest)}</span>
                    </div>
                </summary>
                <div class="detail-block">
                    <strong>Account risk</strong>: {escape(account)}<br>
                    <strong>Debit / max loss</strong>: {option_money(debit)} / {money(max_loss)}<br>
                    <strong>Guardrail</strong>: {escape(guardrail)}<br>
                    <strong>Why not actionable</strong>: final verdict failed; raw scanner output is not an entry signal.<br>
                    {'<strong>Research hook</strong>: ' + escape(research_note) + '<br>' if research_note else ''}
                    <strong>Raw scanner note</strong>: {_calendar_raw_scanner_note(row)}<br>
                    <strong>Reasons / risks</strong>{_compact_ul((row.get('reasons', []) or []) + (row.get('risks', []) or []))}
                </div>
            </details>""")
        body = "".join(cards)
    return _section("blocked-calendars", "Calendar Candidates / Blocked Setups", "Rejected and watch-only setups are informational, not actionable orders.", body, str(len(rows)))


def _calendar_reliability_section_html(
    candle_status: dict[str, Any],
    opportunity_cache: dict[str, Any],
    earnings_quality: dict[str, Any],
    calendar_ranking: dict[str, Any],
    earnings_backtest: dict[str, Any],
) -> str:
    quality_summary = (earnings_quality or {}).get("summary", {}) or {}
    cache_summary = (opportunity_cache or {}).get("summary", {}) or {}
    ranking_summary = (calendar_ranking or {}).get("summary", {}) or {}
    backtest_summary = (earnings_backtest or {}).get("summary", {}) or {}
    candle_rows = [row for row in (candle_status or {}).values() if isinstance(row, dict)]
    candle_success = sum(1 for row in candle_rows if row.get("provider"))
    metrics = f"""
        <div class="metric-grid">
            <div class="metric"><span class="label">Earnings Window</span><span class="value">+{getattr(config, 'EARNINGS_DISCOVERY_START_DAYS', 4)}..+{getattr(config, 'EARNINGS_DISCOVERY_END_DAYS', 21)} days</span></div>
            <div class="metric"><span class="label">Raw / Checked</span><span class="value">{quality_summary.get('raw_event_count', 0)} / {quality_summary.get('checked_count', 0)}</span></div>
            <div class="metric"><span class="label">Final Candidates</span><span class="value">{ranking_summary.get('candidate_count', 0)}</span></div>
            <div class="metric"><span class="label">Candle Success</span><span class="value">{candle_success}/{len(candle_rows)}</span></div>
            <div class="metric"><span class="label">Cache Writes</span><span class="value">{cache_summary.get('write_count', 0)}</span></div>
            <div class="metric"><span class="label">Backtests</span><span class="value">{backtest_summary.get('with_history_count', 0)} with history</span></div>
        </div>
    """
    recent_rows = []
    for row in (opportunity_cache or {}).get("recent", []) or []:
        if not isinstance(row, dict):
            continue
        recent_rows.append(
            f"""
            <div class="decision-card add-row">
                <span class="ticker">{_safe_text(row.get('symbol'), 'UNKNOWN')}</span>
                <span>{_safe_text(row.get('earnings_date'), 'unknown earnings')}</span>
                <span>{_chip(_safe_text(row.get('final_verdict'), 'WATCH'), None, _tone_for_text(row.get('final_verdict')))}</span>
                <span>{_safe_text(row.get('main_blocker'), 'No main blocker')}</span>
                <span class="muted">Seen {row.get('seen_count', 1)}x; last {escape(str(row.get('last_seen_at') or 'unknown'))}</span>
            </div>
            """
        )
    recent = "".join(recent_rows) or '<p class="empty">No cached calendar opportunities yet.</p>'
    body = metrics + f'<details class="debug-details"><summary>Recent Calendar Opportunities</summary>{recent}</details>'
    return _section("calendar-reliability", "Calendar Reliability", "Provider fallback, discovery coverage, and scanner-generated opportunity history.", body, None)


def _portfolio_infographic_html(portfolio_gap: dict[str, Any]) -> str:
    exposures = (portfolio_gap or {}).get("exposure_rows", []) or (portfolio_gap or {}).get("sector_rows", []) or []
    risk_rows = (portfolio_gap or {}).get("risk_rows", []) or []
    if not exposures and not risk_rows:
        body = '<p class="empty">Portfolio gap details were not available for this run.</p>'
    else:
        bar_rows = []
        alignment_notes = []
        for row in exposures[:12]:
            if not isinstance(row, dict):
                continue
            label = _first_text(row.get("bucket"), row.get("sector"), row.get("theme"), fallback="Bucket")
            actual = row.get("actual_pct") if row.get("actual_pct") is not None else row.get("current_pct")
            target = row.get("target_pct")
            status = _first_text(row.get("status"), row.get("label"), fallback="review")
            if status and status != "review":
                alignment_notes.append(f"{label}: {status}")
            width = 0.0
            try:
                width = max(0.0, min(float(actual or 0), 40.0)) / 40.0 * 100.0
            except (TypeError, ValueError):
                width = 0.0
            fallback = _bucket_fallback_tickers(label)
            holdings = row.get("holdings") or row.get("holding_tickers") or row.get("owned_tickers") or row.get("tickers") or fallback.get("holdings") or []
            candidates = row.get("candidates") or row.get("watchlist_tickers") or row.get("suggestion_tickers") or row.get("add_tickers") or fallback.get("candidates") or []
            detail = (
                f'<details class="bucket-details"><summary><strong>{escape(label)}</strong> · {pct(actual)} / {pct(target)} · '
                f'{_chip(status, None, _tone_for_text(status))}</summary>'
                f'<div class="bar-track"><span class="bar-fill" style="--bar-width:{width:.0f}%"></span></div>'
                f'<p class="muted">Holdings: {escape(", ".join(str(x) for x in holdings) if holdings else "Not attached")}</p>'
                f'<p class="muted">Watchlist/add candidates: {escape(", ".join(str(x) for x in candidates) if candidates else "Not attached")}</p>'
                f'</details>'
            )
            bar_rows.append(detail)
        risks = "".join(
            f'<div class="risk-card"><span class="label">{_safe_text(row.get("name") or row.get("bucket"), "Risk")}</span>'
            f'<span class="value">{_safe_text(row.get("detail") or row.get("status") or row.get("value"), "review")}</span></div>'
            for row in risk_rows[:6] if isinstance(row, dict)
        )
        alignment = "Portfolio vs macro: " + ("; ".join(alignment_notes[:3]) if alignment_notes else "exposure bucket details limited this run.")
        body = f'<p class="muted">{escape(alignment)}</p><div>{"".join(bar_rows) or "<span class=empty>No target bars available.</span>"}</div><div class="risk-grid">{risks}</div>'
    return _section("portfolio-infographic", "Portfolio + Macro Infographic", "Target-vs-actual exposure and portfolio-wide risk context.", body, None)


def _bucket_fallback_tickers(label: str) -> dict[str, list[str]]:
    text = str(label or "").lower()
    mapping = [
        (("ai", "semiconductor", "semi"), {"holdings": ["NVDA", "MU", "SOXL"], "candidates": ["CRDO"]}),
        (("energy", "utilities", "infrastructure"), {"holdings": ["VST", "FSLR"], "candidates": []}),
        (("healthcare", "biotech"), {"holdings": ["ALGN", "NVO"], "candidates": []}),
        (("mega-cap", "cloud", "mega cap"), {"holdings": ["AMZN", "GOOGL", "META", "ORCL"], "candidates": []}),
        (("consumer", "retail"), {"holdings": ["NKE", "SBUX"], "candidates": []}),
        (("financial",), {"holdings": ["JPM"], "candidates": []}),
        (("software", "fintech"), {"holdings": ["SOFI", "PYPL", "HOOD"], "candidates": []}),
    ]
    for needles, payload in mapping:
        if any(needle in text for needle in needles):
            return payload
    return {"holdings": [], "candidates": []}


def _monitor_debug_section_html(
    pipeline_summary_html: str,
    pipeline_status_rows: str,
    market_rows: str,
    position_rows: str,
    recommendation_rows: str,
    stock_momentum_rows: str,
    watchlist_rows: str,
    portfolio_gap_rows: str,
    unified_calendar_rows: str,
    calendar_ranking_rows: str,
    earnings_mini_backtest_rows: str,
    news_rows: str,
    tradier_rows: str,
    payload_debug_html: str,
    log_debug_html: str,
) -> str:
    body = f"""
        <details class="debug-details">
            <summary>Pipeline Status</summary>
            {pipeline_summary_html}
            <div class="table-scroll"><table><tr><th>Status</th><th>Step</th><th>Message</th><th>Duration</th><th>Finished</th></tr>{pipeline_status_rows}</table></div>
        </details>
        <details class="debug-details">
            <summary>Raw Advisor Tables</summary>
            <div class="table-scroll"><h3>Market Momentum / Trend</h3><table><tr><th>Ticker</th><th>As Of</th><th>1M</th><th>3M</th><th>6M</th><th>12M</th><th>6M RS</th><th>Above 50D</th><th>Above 200D</th><th>52W High Dist.</th><th>Vol30</th><th>AvgVol30</th></tr>{market_rows}</table></div>
            <div class="table-scroll"><h3>Positions</h3><table><tr><th>Ticker</th><th>Account</th><th>Quantity</th><th>Avg Cost</th><th>Current</th><th>G/L</th><th>Market Value</th></tr>{position_rows}</table></div>
            <div class="table-scroll"><h3>Portfolio Advisor Scores</h3><table><tr><th>Ticker</th><th>Account</th><th>Score</th><th>Action</th><th>Allocation</th><th>G/L</th><th>Trend/Momentum</th><th>Reasons</th><th>Risks / Limits</th><th>Next Check</th></tr>{recommendation_rows}</table></div>
            <div class="table-scroll"><h3>Stock Momentum</h3><table><tr><th>Ticker</th><th>Score / Action</th><th>Portfolio</th><th>Trend</th><th>Reasons</th><th>Risks</th><th>Next Check</th></tr>{stock_momentum_rows}</table></div>
            <div class="table-scroll"><h3>Watchlist Review</h3><table><tr><th>Ticker</th><th>Stock Score / Category</th><th>Portfolio</th><th>Watchlist Source</th><th>Earnings</th><th>Earnings / Calendar Overlay</th><th>Reasons / Next</th></tr>{watchlist_rows}</table></div>
            <h3>Portfolio Gap Raw</h3>{portfolio_gap_rows}
            <div class="table-scroll"><h3>Unified Calendar Engine Raw</h3><table><tr><th>Type</th><th>Ticker / Score</th><th>Earnings / Verdict</th><th>Possible Spread / Current Position</th><th>Requirements</th><th>Entry / Next Action</th></tr>{unified_calendar_rows}</table></div>
            <div class="table-scroll"><h3>Calendar Ranking Raw</h3><table><tr><th>Ticker / Score</th><th>Action</th><th>Entry Timing</th><th>Criteria</th><th>Reasons / Risks</th><th>Next</th></tr>{calendar_ranking_rows}</table></div>
            <div class="table-scroll"><h3>Earnings Mini-Backtest</h3><table><tr><th>Ticker</th><th>Events</th><th>Avg / Max Move</th><th>Gap / Run-up</th><th>Interpretation</th><th>Notes</th></tr>{earnings_mini_backtest_rows}</table></div>
            <div class="table-scroll"><h3>Relevant News</h3><table><tr><th>Ticker</th><th>Score</th><th>Headline</th><th>Source</th><th>Published</th><th>Link</th></tr>{news_rows}</table></div>
            <div class="table-scroll"><h3>Tradier Snapshot</h3><table><tr><th>Ticker</th><th>Quote</th><th>Expirations</th><th>Chain</th><th>ATM Call</th><th>ATM Put</th><th>Liquidity</th></tr>{tradier_rows}</table></div>
        </details>
        <button class="copy-btn" onclick="copyExport('fullDebugPayload')">Copy Advisor Payload</button>
        {payload_debug_html}
        {log_debug_html}
    """
    return _section("monitor-debug", "Monitor / Debug", "Provider details, raw tables, payload, and run log are collapsed by default.", body, None)


def _section(section_id: str, title: str, kicker: str, body: str, count: str | None) -> str:
    count_html = _chip("COUNT", count, "neutral") if count is not None else ""
    return f"""
    <section class="report-section" id="{escape(section_id)}">
        <div class="section-head">
            <div>
                <h2>{escape(title)}</h2>
                <div class="section-kicker">{escape(kicker)}</div>
            </div>
            <div>{count_html}</div>
        </div>
        <div class="section-body">{body}</div>
    </section>"""


def format_html(
    payload: str,
    positions: list[dict[str, Any]],
    news_map: NewsMap | list[str] | None = None,
    recommendations: Recommendations | list[str] | None = None,
    tradier_snapshot: TradierSnapshot | list[str] | None = None,
    log_lines: list[str] | None = None,
) -> str:
    """
    Wrap the report in a clean HTML page for browser viewing.

    New call style:
    - format_html(payload, positions, news_map, recommendations, log_lines)

    Backward-compatible call styles:
    - format_html(payload, positions, log_lines)
    - format_html(payload, positions, news_map, log_lines)
    """
    parsed_news: NewsMap = {}
    parsed_recommendations: Recommendations = []
    parsed_log_lines: list[str] = []
    parsed_tradier_snapshot: TradierSnapshot = {}

    if isinstance(news_map, list) and all(isinstance(item, str) for item in news_map):
        parsed_log_lines = news_map
    elif isinstance(news_map, dict):
        parsed_news = news_map

    if isinstance(recommendations, list):
        if all(isinstance(item, str) for item in recommendations):
            parsed_log_lines = recommendations
        else:
            parsed_recommendations = recommendations  # type: ignore[assignment]

    if isinstance(tradier_snapshot, dict):
        parsed_tradier_snapshot = tradier_snapshot
    elif isinstance(tradier_snapshot, list) and all(isinstance(item, str) for item in tradier_snapshot):
        parsed_log_lines = tradier_snapshot

    if log_lines is not None:
        parsed_log_lines = log_lines

    zero_tickers = _zero_tickers_from_positions_and_recommendations(positions, parsed_recommendations)
    display_positions = _filter_nonzero_positions(positions, zero_tickers)
    display_recommendations = _filter_nonzero_recommendations(parsed_recommendations, zero_tickers)
    daily_opportunity = _filter_daily_opportunity_engine(
        daily_opportunity_from_tradier_snapshot(parsed_tradier_snapshot),
        zero_tickers,
    )
    unified_calendar_engine = unified_calendar_trade_engine_from_tradier_snapshot(parsed_tradier_snapshot)
    lifecycle_checks = calendar_lifecycle_from_tradier_snapshot(parsed_tradier_snapshot)
    portfolio_gap = portfolio_gap_from_tradier_snapshot(parsed_tradier_snapshot)
    stock_momentum = stock_momentum_from_tradier_snapshot(parsed_tradier_snapshot)
    calendar_ranking = calendar_ranking_from_tradier_snapshot(parsed_tradier_snapshot)
    earnings_backtest = earnings_mini_backtest_from_tradier_snapshot(parsed_tradier_snapshot)
    candle_status = _snapshot_dict(parsed_tradier_snapshot, "_candle_status")
    opportunity_cache = _snapshot_dict(parsed_tradier_snapshot, "_calendar_opportunity_cache")
    earnings_quality = _snapshot_dict(parsed_tradier_snapshot, "_earnings_discovery_quality")
    pipeline_status = pipeline_status_from_tradier_snapshot(parsed_tradier_snapshot)
    provider_status = _normalized_provider_status(
        pipeline_status,
        parsed_log_lines,
        parsed_recommendations,
        parsed_tradier_snapshot.get("_provider_status") if isinstance(parsed_tradier_snapshot, dict) else None,
    )
    potential_groups = _potential_add_groups(daily_opportunity, stock_momentum, portfolio_gap, zero_tickers)

    position_rows = format_position_rows(display_positions)
    recommendation_rows = format_recommendation_rows(display_recommendations)
    market_rows = format_market_rows(display_recommendations)
    news_rows = format_news_rows(parsed_news)
    tradier_rows = format_tradier_rows(parsed_tradier_snapshot)
    watchlist_rows = format_watchlist_review_rows(watchlist_review_from_tradier_snapshot(parsed_tradier_snapshot))
    unified_calendar_rows = format_unified_calendar_engine_rows(unified_calendar_engine)
    portfolio_gap_rows = format_portfolio_gap_rows(portfolio_gap)
    stock_momentum_rows = format_stock_momentum_rows(stock_momentum)
    calendar_ranking_rows = format_calendar_ranking_rows(calendar_ranking)
    earnings_mini_backtest_rows = format_earnings_mini_backtest_rows(earnings_backtest)
    pipeline_status_rows = format_pipeline_status_rows(pipeline_status)
    pipeline_summary_html = format_pipeline_summary(pipeline_status)
    payload_debug_html = collapsible_pre("Full Advisor Payload", payload, "payload", "payload")
    log_debug_html = collapsible_pre("Run Log", "\n".join(parsed_log_lines), None, "log")
    today = date.today().strftime("%B %d, %Y")
    active_rows = _active_calendar_rows(unified_calendar_engine, lifecycle_checks)
    blocked_rows = _blocked_calendar_rows(unified_calendar_engine, calendar_ranking)
    daily_actions = [item for item in _daily_actions(daily_opportunity) if not _ticker_is_zero_value(item.get("ticker"), zero_tickers)]
    urgent_count = sum(
        1
        for row in active_rows
        if _tone_for_text(_first_text(row.get("verdict"), row.get("action"), row.get("next_action"))) in {"bad", "warn"}
    )
    add_count = len(potential_groups.get("actionable", []) or [])
    risk_tickers = {
        str(item.get("ticker") or "").upper().strip()
        for item in (potential_groups.get("risk", []) or [])
        if item.get("ticker")
    }
    risk_tickers.update(
        str(rec.get("ticker") or "").upper().strip()
        for rec in display_recommendations
        if rec.get("risks") or _action_group(rec.get("action")) == "risk"
    )
    risk_count = len({ticker for ticker in risk_tickers if ticker}) + len((portfolio_gap or {}).get("risk_rows", []) or [])
    exports = {
        "dailyBrief": _build_daily_brief_export(today, provider_status, active_rows, display_recommendations, potential_groups, blocked_rows, portfolio_gap),
        "calendarReport": _build_calendar_report_export(active_rows, unified_calendar_engine, blocked_rows),
        "holdingsReport": _build_holdings_report_export(display_recommendations),
        "potentialAdds": _build_potential_adds_export(potential_groups),
        "fullDebugPayload": payload,
    }

    top_summary_html = _top_summary_html(
        today=today,
        active_count=len(active_rows),
        urgent_count=urgent_count,
        risk_count=risk_count,
        adds_count=add_count,
        blocked_count=len(blocked_rows),
        pipeline_status=pipeline_status,
        provider_status=provider_status,
    )
    macro_html = _section(
        "macro-context",
        "Macro Context Strip",
        "Compact market context; missing macro inputs show as placeholders.",
        _macro_context_html(display_recommendations, provider_status),
        None,
    )
    active_calendar_html = _active_calendar_section_html(active_rows, provider_status)
    holdings_html = _holdings_section_html(display_recommendations, provider_status)
    potential_adds_html = _potential_adds_section_html(potential_groups)
    risk_review_html = _risk_review_section_html(potential_groups, display_recommendations)
    blocked_calendar_html = _blocked_calendar_section_html(blocked_rows)
    calendar_reliability_html = _calendar_reliability_section_html(
        candle_status,
        opportunity_cache,
        earnings_quality,
        calendar_ranking,
        earnings_backtest,
    )
    portfolio_infographic_html = _portfolio_infographic_html(portfolio_gap)
    export_toolbar_html = _export_toolbar_html(exports)
    monitor_debug_html = _monitor_debug_section_html(
        pipeline_summary_html=pipeline_summary_html,
        pipeline_status_rows=pipeline_status_rows,
        market_rows=market_rows,
        position_rows=position_rows,
        recommendation_rows=recommendation_rows,
        stock_momentum_rows=stock_momentum_rows,
        watchlist_rows=watchlist_rows,
        portfolio_gap_rows=portfolio_gap_rows,
        unified_calendar_rows=unified_calendar_rows,
        calendar_ranking_rows=calendar_ranking_rows,
        earnings_mini_backtest_rows=earnings_mini_backtest_rows,
        news_rows=news_rows,
        tradier_rows=tradier_rows,
        payload_debug_html=payload_debug_html,
        log_debug_html=log_debug_html,
    )

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Stock Advisor — {today}</title>
    <style>
        {REPORT_CSS}
        {UI_OVERHAUL_CSS}
    </style>
</head>
<body>
    <main class="report-shell">
        {top_summary_html}
        <nav class="quick-nav" aria-label="Report sections">
            <a href="#macro-context">Macro</a>
            <a href="#active-calendars">Active Calendars</a>
            <a href="#holdings">Holdings</a>
            <a href="#potential-adds">Potential Adds</a>
            <a href="#risk-review">Risk Review</a>
            <a href="#blocked-calendars">Blocked Calendars</a>
            <a href="#calendar-reliability">Calendar Reliability</a>
            <a href="#portfolio-infographic">Portfolio</a>
            <a href="#exports">Exports</a>
            <a href="#monitor-debug">Monitor</a>
        </nav>
        {export_toolbar_html}
        {macro_html}
        {active_calendar_html}
        {holdings_html}
        {potential_adds_html}
        {risk_review_html}
        {blocked_calendar_html}
        {calendar_reliability_html}
        {portfolio_infographic_html}
        {monitor_debug_html}
        {_dashboard_script_html()}
    </main>
</body>
</html>"""



def pipeline_status_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_pipeline_status", {}) or {}
    return raw if isinstance(raw, dict) else {}


def _snapshot_dict(tradier_snapshot: TradierSnapshot | None, key: str) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get(key, {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_pipeline_status_rows(status: dict[str, Any]) -> str:
    steps = (status or {}).get("steps", []) or []
    if not steps:
        return '<tr><td colspan="5" class="empty">Pipeline status was not attached to this run.</td></tr>'
    rows = ""
    for step in steps:
        state = str(step.get("status") or "unknown")
        label = escape(str(step.get("label") or step.get("key") or "Step"))
        message = escape(str(step.get("message") or ""))
        duration = step.get("duration_ms")
        duration_text = f"{duration} ms" if duration is not None else "—"
        css = "candidate" if state == "complete" else "urgent" if state == "error" else "action-watch" if state in {"warning", "skipped"} else "action-hold"
        rows += f"""
        <tr>
            <td><span class="pill {css}">{escape(state.upper())}</span></td>
            <td>{label}</td>
            <td>{message}</td>
            <td>{escape(duration_text)}</td>
            <td class="muted">{escape(str(step.get('finished_at') or '—'))}</td>
        </tr>"""
    return rows


def format_pipeline_summary(status: dict[str, Any]) -> str:
    if not status:
        return '<p class="muted">Pipeline status unavailable.</p>'
    summary = status.get("summary", {}) or {}
    mode = escape(str(status.get("run_mode") or "prod").upper())
    overall = escape(str(status.get("overall_status") or "unknown").upper())
    return (
        f'<p class="muted">Mode <strong>{mode}</strong> | Overall <strong>{overall}</strong> | '
        f"Steps {summary.get('step_count', 0)} | "
        f"Complete {summary.get('completed_count', 0)} | "
        f"Warnings {summary.get('warning_count', 0)} | "
        f"Errors {summary.get('error_count', 0)}</p>"
    )

def format_position_rows(positions: list[dict[str, Any]]) -> str:
    rows = ""

    for p in positions:
        ticker = escape(str(p.get("ticker", "—")))
        account = escape(str(p.get("account", "—")))

        gl_val = p.get("gain_loss")
        gl_pct = p.get("gain_loss_pct")

        if gl_val is not None:
            try:
                gl_float = float(gl_val)
                color = "green" if gl_float >= 0 else "red"
                gl_str = (
                    f'<span style="color:{color}">'
                    f"{signed_money(gl_val)} ({signed_pct(gl_pct)})"
                    f"</span>"
                )
            except (TypeError, ValueError):
                gl_str = "N/A"
        else:
            gl_str = "N/A"

        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td>{account}</td>
            <td>{number(p.get('quantity'), 4)}</td>
            <td>{money(p.get('avg_buy_price'))}</td>
            <td>{money(p.get('current_price'))}</td>
            <td>{gl_str}</td>
            <td>{money(p.get('market_value'))}</td>
        </tr>"""

    return rows


def format_recommendation_rows(recommendations: Recommendations) -> str:
    if not recommendations:
        return """
        <tr>
            <td colspan="10" class="empty">No portfolio advisor scores generated.</td>
        </tr>"""

    rows = ""
    for rec in recommendations:
        ticker = escape(str(rec.get("ticker", "UNKNOWN")))
        account = escape(str(rec.get("account", "Unknown")))
        action = str(rec.get("action", "WATCH"))
        action_class = action_css_class(action)
        confidence = escape(str(rec.get("confidence", "Low")))
        reasons = rec.get("reasons", []) or []
        risks = rec.get("risks", []) or []
        next_check = escape(str(rec.get("next_check", "—") or "—"))
        metrics = rec.get("market_metrics", {}) or {}
        trend_summary = format_trend_summary(metrics)

        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td>{account}</td>
            <td class="score">{number(rec.get('score'), 1)}<br><span class="muted">{confidence}</span></td>
            <td><span class="pill {action_class}">{escape(action)}</span></td>
            <td>{pct(rec.get('allocation_pct'))}<br><span class="muted">{money(rec.get('position_value'))}</span></td>
            <td>{signed_pct(rec.get('gain_loss_pct'))}</td>
            <td>{trend_summary}</td>
            <td>{format_compact_list(reasons)}</td>
            <td>{format_compact_list(risks)}</td>
            <td>{next_check}</td>
        </tr>"""

    return rows


def format_market_rows(recommendations: Recommendations) -> str:
    if not recommendations:
        return """
        <tr>
            <td colspan="12" class="empty">No market metrics available.</td>
        </tr>"""

    seen: set[str] = set()
    rows = ""
    for rec in recommendations:
        ticker = str(rec.get("ticker", "UNKNOWN"))
        if ticker in seen:
            continue
        seen.add(ticker)

        metrics = rec.get("market_metrics", {}) or {}
        safe_ticker = escape(ticker)

        if not metrics.get("has_data"):
            error = escape(str(metrics.get("error", "No data") if metrics else "No data"))
            rows += f"""
            <tr>
                <td><strong>{safe_ticker}</strong></td>
                <td colspan="11" class="empty">No market data: {error}</td>
            </tr>"""
            continue

        rows += f"""
        <tr>
            <td><strong>{safe_ticker}</strong></td>
            <td>{escape(str(metrics.get('as_of') or '—'))}</td>
            <td>{signed_pct(metrics.get('return_1m_pct'))}</td>
            <td>{signed_pct(metrics.get('return_3m_pct'))}</td>
            <td>{signed_pct(metrics.get('return_6m_pct'))}</td>
            <td>{signed_pct(metrics.get('return_12m_pct'))}</td>
            <td>{signed_pct(metrics.get('relative_strength_6m_pct'))}<br><span class="muted">vs {escape(str(metrics.get('benchmark_ticker') or 'benchmark'))}</span></td>
            <td>{bool_badge(metrics.get('above_sma_50'))}</td>
            <td>{bool_badge(metrics.get('above_sma_200'))}</td>
            <td>{signed_pct(metrics.get('distance_from_52w_high_pct'))}</td>
            <td>{pct(metrics.get('volatility_30d_pct'))}</td>
            <td>{compact_big_number(metrics.get('avg_volume_30d'))}</td>
        </tr>"""

    return rows or """
        <tr>
            <td colspan="12" class="empty">No market metrics available.</td>
        </tr>"""


def format_tradier_rows(tradier_snapshot: TradierSnapshot) -> str:
    if not tradier_snapshot:
        return """
        <tr>
            <td colspan="7" class="empty">No Tradier data available. Set TRADIER_ACCESS_TOKEN to enable quote/options snapshots.</td>
        </tr>"""

    rows = ""
    for ticker, data in tradier_snapshot.items():
        if str(ticker).startswith("_"):
            continue
        safe_ticker = escape(str(ticker))
        if not data.get("has_data"):
            error = escape(str(data.get("error") or "No Tradier data returned."))
            rows += f"""
            <tr>
                <td><strong>{safe_ticker}</strong></td>
                <td colspan="6" class="empty">Tradier unavailable: {error}</td>
            </tr>"""
            continue

        quote = data.get("quote", {}) or {}
        atm_call = data.get("atm_call") or {}
        atm_put = data.get("atm_put") or {}
        selected_expiration = escape(str(data.get("selected_expiration") or "—"))
        quote_html = (
            f"Last {money(quote.get('last'))}<br>"
            f"Bid/Ask {money(quote.get('bid'))} / {money(quote.get('ask'))}<br>"
            f"Vol {compact_big_number(quote.get('volume'))}"
        )
        expiration_html = (
            f"{int(data.get('expiration_count') or 0)} available<br>"
            f"<span class='muted'>Sample: {selected_expiration}</span>"
        )
        chain_html = (
            f"{int(data.get('chain_contract_count') or 0)} contracts<br>"
            f"<span class='muted'>{int(data.get('call_count') or 0)} calls / {int(data.get('put_count') or 0)} puts</span>"
        )
        liquidity_html = (
            f"Vol {compact_big_number(data.get('total_volume'))}<br>"
            f"OI {compact_big_number(data.get('total_open_interest'))}"
        )

        rows += f"""
        <tr>
            <td><strong>{safe_ticker}</strong></td>
            <td>{quote_html}</td>
            <td>{expiration_html}</td>
            <td>{chain_html}</td>
            <td>{format_compact_option(atm_call)}</td>
            <td>{format_compact_option(atm_put)}</td>
            <td>{liquidity_html}</td>
        </tr>"""

    return rows


def format_compact_option(option: dict[str, Any] | None) -> str:
    if not option:
        return '<span class="empty">—</span>'
    symbol = escape(str(option.get("symbol") or "N/A"))
    strike = option_money(option.get("strike"))
    bid = option_money(option.get("bid"))
    ask = option_money(option.get("ask"))
    mid = option_money(option.get("mid"))
    volume = compact_big_number(option.get("volume"))
    oi = compact_big_number(option.get("open_interest"))
    delta = option_money(option.get("delta"))
    theta = option_money(option.get("theta"))
    iv = option_money(option.get("iv"))
    spread = pct(option.get("spread_pct"))
    return (
        f"<strong>{symbol}</strong><br>"
        f"Strike {strike} | Mid {mid}<br>"
        f"Bid/Ask {bid} / {ask}<br>"
        f"Vol {volume} | OI {oi}<br>"
        f"Δ {delta} | Θ {theta} | IV {iv}<br>"
        f"<span class='muted'>Spread {spread}</span>"
    )



def calendar_candidates_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> CalendarCandidates:
    if not tradier_snapshot:
        return []
    raw = tradier_snapshot.get("_calendar_spread_candidates", {}) or {}
    items = raw.get("items", []) if isinstance(raw, dict) else []
    return [item for item in items if isinstance(item, dict)]


def format_calendar_spread_rows(candidates: CalendarCandidates) -> str:
    if not candidates:
        return """
        <tr>
            <td colspan="8" class="empty">No calendar spread candidates generated for this run.</td>
        </tr>"""

    rows = ""
    for cand in candidates:
        ticker = escape(str(cand.get("ticker") or "UNKNOWN"))
        action = escape(str(cand.get("action") or "WATCH"))
        action_class = action_css_class(action)
        option_type = escape(str(cand.get("option_type") or "call").upper())
        front_leg = cand.get("short_front_leg") or {}
        back_leg = cand.get("long_back_leg") or {}
        reasons = cand.get("reasons", []) or []
        risks = cand.get("risks", []) or []
        next_check = escape(str(cand.get("next_check") or "Recheck before entry."))

        structure = (
            f"Strike {option_money(cand.get('strike'))} {option_type}<br>"
            f"Short {escape(str(cand.get('front_expiration') or '—'))} "
            f"({cand.get('front_dte') if cand.get('front_dte') is not None else '—'} DTE)<br>"
            f"Long {escape(str(cand.get('back_expiration') or '—'))} "
            f"({cand.get('back_dte') if cand.get('back_dte') is not None else '—'} DTE)<br>"
            f"<span class='muted'>Front {escape(str(front_leg.get('symbol') or '—'))}<br>Back {escape(str(back_leg.get('symbol') or '—'))}</span>"
        )
        debit = (
            f"Conservative {option_money(cand.get('conservative_debit'))}<br>"
            f"Mid {option_money(cand.get('mid_debit'))}<br>"
            f"<span class='muted'>{pct(cand.get('debit_pct_underlying'))} of underlying</span>"
        )
        liquidity = (
            f"Min OI {compact_big_number(cand.get('min_leg_open_interest'))}<br>"
            f"Min Vol {compact_big_number(cand.get('min_leg_volume'))}<br>"
            f"<span class='muted'>Underlying {money(cand.get('underlying_price'))}</span>"
        )
        iv_spread = (
            f"Front IV {option_money(cand.get('front_iv'))}<br>"
            f"Back IV {option_money(cand.get('back_iv'))}<br>"
            f"IV edge {option_money(cand.get('iv_edge'))}<br>"
            f"<span class='muted'>Max leg spread {pct(cand.get('max_leg_spread_pct'))}</span>"
        )
        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td class="score">{number(cand.get('score'), 1)}<br><span class="pill {action_class}">{action}</span></td>
            <td>{structure}</td>
            <td>{debit}</td>
            <td>{liquidity}</td>
            <td>{iv_spread}</td>
            <td>{format_compact_list(reasons)}</td>
            <td>{format_compact_list(risks)}<br><span class="muted">{next_check}</span></td>
        </tr>"""
    return rows


def open_options_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_open_options_positions", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_open_options_rows(open_options: dict[str, Any]) -> str:
    if not open_options:
        return """
        <tr>
            <td colspan="5" class="empty">Open options detector did not run for this report.</td>
        </tr>"""

    summary = open_options.get("summary", {}) or {}
    errors = open_options.get("errors", []) or []
    calendars = open_options.get("calendars", []) or []

    status = (
        f"Accounts {summary.get('account_count', 0)}<br>"
        f"Positions {summary.get('total_positions', 0)}<br>"
        f"Option legs {summary.get('option_leg_count', 0)}<br>"
        f"Calendars {summary.get('calendar_count', 0)}"
    )

    if not calendars:
        error_html = format_compact_list([str(e) for e in errors[:3]]) if errors else '<span class="empty">No open calendars detected.</span>'
        return f"""
        <tr>
            <td>{status}</td>
            <td colspan="4" class="empty">{error_html}</td>
        </tr>"""

    rows = ""
    for cal in calendars:
        underlying = escape(str(cal.get("underlying") or cal.get("ticker") or "UNKNOWN"))
        option_type = escape(str(cal.get("option_type") or "call").upper())
        action = escape(str(cal.get("action") or "MONITOR"))
        action_class = action_css_class(action)
        short_leg = cal.get("short_front_leg", {}) or {}
        long_leg = cal.get("long_back_leg", {}) or {}
        risks = cal.get("risks", []) or []
        if not risks:
            risks = cal.get("reasons", []) or []
        next_check = escape(str(cal.get("next_check") or "Monitor daily."))
        detected = (
            f"<strong>{underlying} {option_money(cal.get('strike'))} {option_type}</strong><br>"
            f"Qty {option_money(cal.get('quantity'))}<br>"
            f"<span class='pill {action_class}'>{action}</span>"
        )
        legs = (
            f"Short {escape(str(cal.get('front_expiration') or '—'))} "
            f"({cal.get('front_dte') if cal.get('front_dte') is not None else '—'} DTE)<br>"
            f"<span class='muted'>{escape(str(short_leg.get('symbol') or '—'))}</span><br>"
            f"Long {escape(str(cal.get('back_expiration') or '—'))} "
            f"({cal.get('back_dte') if cal.get('back_dte') is not None else '—'} DTE)<br>"
            f"<span class='muted'>{escape(str(long_leg.get('symbol') or '—'))}</span>"
        )
        value = (
            f"Mid debit {option_money(cal.get('current_mid_debit'))}<br>"
            f"Value {money(cal.get('current_value_estimate'))}<br>"
            f"Cost basis est. {money(cal.get('cost_basis_estimate'))}"
        )
        rows += f"""
        <tr>
            <td>{status}</td>
            <td>{detected}</td>
            <td>{legs}</td>
            <td>{value}</td>
            <td>{format_compact_list([str(r) for r in risks[:3]])}<br><span class="muted">{next_check}</span></td>
        </tr>"""
        status = ""
    return rows




def earnings_trade_discovery_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_earnings_trade_discovery", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_earnings_trade_discovery_rows(discovery: dict[str, Any]) -> str:
    if not discovery:
        return """
        <tr>
            <td colspan="5" class="empty">Earnings trade discovery did not run for this report.</td>
        </tr>"""

    errors = discovery.get("errors", []) or []
    items = discovery.get("items", []) or []
    if not items:
        msg = "; ".join(str(e) for e in errors[:3]) if errors else "No upcoming earnings events found in the configured discovery window."
        return f"""
        <tr>
            <td colspan="5" class="empty">{escape(msg)}</td>
        </tr>"""

    rows = ""
    for event in items[:30]:
        ticker = escape(str(event.get("ticker") or event.get("symbol") or "UNKNOWN"))
        earnings_date = escape(str(event.get("earnings_date") or event.get("date") or "—"))
        session = escape(str(event.get("session_label") or "Unknown"))
        dte = event.get("days_until_earnings")
        dte_text = f"{dte} days" if dte is not None else "—"
        source = escape(str(event.get("source") or discovery.get("provider") or "unknown"))
        note = escape(str(event.get("discovery_reason") or "Upcoming earnings event in configured discovery window."))
        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td>{earnings_date}<br><span class="muted">{session}</span></td>
            <td>{escape(str(dte_text))}</td>
            <td>{bool_badge(event.get('is_timestamp_confirmed'))}</td>
            <td>{source}<br><span class="muted">{note}</span></td>
        </tr>"""
    return rows


def earnings_events_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, dict[str, Any]]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_earnings_events", {}) or {}
    if not isinstance(raw, dict):
        return {}
    items = raw.get("items", {})
    return items if isinstance(items, dict) else {}


def format_earnings_rows(earnings_events: dict[str, dict[str, Any]]) -> str:
    if not earnings_events:
        return """
        <tr>
            <td colspan="7" class="empty">No earnings timestamp data available.</td>
        </tr>"""

    rows = ""
    for ticker, event in earnings_events.items():
        safe_ticker = escape(str(ticker))
        if not isinstance(event, dict) or not event.get("has_data"):
            error = escape(str((event or {}).get("error") or "No event returned.")) if isinstance(event, dict) else "No event returned."
            rows += f"""
            <tr>
                <td><strong>{safe_ticker}</strong></td>
                <td colspan="6" class="empty">Earnings unavailable: {error}</td>
            </tr>"""
            continue

        dte = event.get("days_until_earnings")
        dte_text = f"{dte} days" if dte is not None else "—"
        eps = option_money(event.get("eps_estimate"))
        eps_actual = option_money(event.get("eps_actual"))
        revenue_est = compact_big_number(event.get("revenue_estimate"))
        revenue_actual = compact_big_number(event.get("revenue_actual"))
        status = "Confirmed timestamp" if event.get("is_timestamp_confirmed") else "Timestamp unknown"
        rows += f"""
        <tr>
            <td><strong>{safe_ticker}</strong></td>
            <td>{escape(str(event.get('earnings_date') or '—'))}</td>
            <td>{escape(str(event.get('session_label') or 'Unknown'))}</td>
            <td>{escape(str(dte_text))}</td>
            <td>{bool_badge(event.get('is_timestamp_confirmed'))}</td>
            <td>EPS est/act {eps}/{eps_actual}<br><span class="muted">Rev est/act {revenue_est}/{revenue_actual}</span></td>
            <td>{escape(status)}<br><span class="muted">Source: {escape(str(event.get('source') or 'unknown'))}</span></td>
        </tr>"""
    return rows


def calendar_lifecycle_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_calendar_lifecycle_checks", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_calendar_lifecycle_rows(lifecycle: dict[str, Any]) -> str:
    if not lifecycle:
        return """
        <tr>
            <td colspan="7" class="empty">Calendar lifecycle checker did not run for this report.</td>
        </tr>"""

    summary = lifecycle.get("summary", {}) or {}
    errors = lifecycle.get("errors", []) or []
    checks = lifecycle.get("checks", []) or []
    status = (
        f"Open calendars {summary.get('calendar_count', 0)}<br>"
        f"Urgent {summary.get('urgent_count', 0)}<br>"
        f"Exit-review {summary.get('exit_review_count', 0)}"
    )

    if not checks:
        msg = format_compact_list([str(e) for e in errors[:3]]) if errors else '<span class="empty">No open calendars to lifecycle-check.</span>'
        return f"""
        <tr>
            <td>{status}</td>
            <td colspan="6" class="empty">{msg}</td>
        </tr>"""

    rows = ""
    for check in checks:
        ticker = escape(str(check.get("ticker") or "UNKNOWN"))
        option_type = escape(str(check.get("option_type") or "call").upper())
        action = escape(str(check.get("action") or "HOLD / MONITOR"))
        action_class = action_css_class(action)
        reasons = [str(r) for r in (check.get("reasons", []) or [])]
        risks = [str(r) for r in (check.get("risks", []) or [])]
        leg_bits = _format_calendar_leg_quote_bits(check)
        combined = leg_bits + reasons[:2] + risks[:3]
        next_check = escape(str(check.get("next_check") or "Monitor daily."))
        calendar = (
            f"<strong>{ticker} {option_money(check.get('strike'))} {option_type}</strong><br>"
            f"Short {escape(str(check.get('front_expiration') or '—'))} "
            f"({check.get('front_dte') if check.get('front_dte') is not None else '—'} DTE)<br>"
            f"Long {escape(str(check.get('back_expiration') or '—'))} "
            f"({check.get('back_dte') if check.get('back_dte') is not None else '—'} DTE)<br>"
            f"<span class='pill {action_class}'>{action}</span>"
        )
        pricing_quality = check.get("pricing_quality") if isinstance(check.get("pricing_quality"), dict) else {}
        value = (
            f"Current debit {option_money(check.get('current_mid_debit'))}<br>"
            f"Entry debit est. {option_money(check.get('entry_debit_estimate'))}<br>"
            f"P/L est. {signed_pct(check.get('estimated_pnl_pct'))} "
            f"/ {money(check.get('pnl_total_estimate'))}<br>"
            f"Spread value {money(check.get('current_value_estimate'))}<br>"
            f"Target {option_money(check.get('target_debit'))} | Stop {option_money(check.get('stop_debit'))}<br>"
            f"<span class='muted'>Entry source: {escape(str(check.get('entry_debit_source') or 'broker'))}; "
            f"pricing {escape(str(pricing_quality.get('confidence') or 'unknown'))}</span>"
        )
        hold = (
            f"Hold-through {number(check.get('hold_through_score'), 1)}<br>"
            f"<span class='pill {_calendar_verdict_class(str(check.get('hold_through_action') or ''))}'>{escape(str(check.get('hold_through_action') or 'ACTIVE REVIEW'))}</span><br>"
            f"<span class='muted'>{escape(str(check.get('historical_move_warning') or check.get('trade_type_label') or ''))}</span>"
        )
        risk_state = (
            f"Underlying {money(check.get('underlying_price'))} "
            f"<span class='muted'>({escape(str(check.get('underlying_price_source') or 'source unknown'))})</span><br>"
            f"Short moneyness {signed_pct(check.get('short_leg_moneyness_pct'))}<br>"
            f"Distance to strike {money(check.get('distance_to_strike'))} "
            f"/ {signed_pct(check.get('distance_to_strike_pct'))}<br>"
            f"Short ITM {yes_no(check.get('short_leg_itm'))}<br>"
            f"Assignment risk {escape(str(check.get('assignment_risk_level') or 'Unknown'))}<br>"
            f"Short extrinsic {option_money(check.get('short_leg_extrinsic_value'))}<br>"
            f"Net Δ {number(check.get('net_delta_estimate'))} | Net Θ {number(check.get('net_theta_estimate'))}"
        )
        earnings = (
            f"{escape(str(check.get('earnings_date') or 'Unknown'))}<br>"
            f"{escape(str(check.get('earnings_session') or 'Unknown'))}<br>"
            f"<span class='muted'>DTE {check.get('days_until_earnings') if check.get('days_until_earnings') is not None else '—'}</span>"
        )
        rows += f"""
        <tr>
            <td>{status}</td>
            <td>{calendar}</td>
            <td>{value}<br>{hold}</td>
            <td>{risk_state}</td>
            <td>{earnings}</td>
            <td>{format_compact_list(combined)}</td>
            <td>{next_check}</td>
        </tr>"""
        status = ""
    return rows


def _format_calendar_leg_quote_bits(check: dict[str, Any]) -> list[str]:
    bits: list[str] = []
    short_q = check.get("short_leg_quote") if isinstance(check.get("short_leg_quote"), dict) else {}
    long_q = check.get("long_leg_quote") if isinstance(check.get("long_leg_quote"), dict) else {}
    if short_q:
        bits.append(
            "Short leg: "
            f"mid {option_money(short_q.get('mid'))}, "
            f"bid/ask {option_money(short_q.get('bid'))}/{option_money(short_q.get('ask'))}, "
            f"Δ {number(short_q.get('delta'), 2)}, Θ {number(short_q.get('theta'), 2)}"
        )
    if long_q:
        bits.append(
            "Long leg: "
            f"mid {option_money(long_q.get('mid'))}, "
            f"bid/ask {option_money(long_q.get('bid'))}/{option_money(long_q.get('ask'))}, "
            f"Δ {number(long_q.get('delta'), 2)}, Θ {number(long_q.get('theta'), 2)}"
        )
    return bits

def format_news_rows(news_map: NewsMap) -> str:
    if not news_map:
        return """
        <tr>
            <td colspan="6" class="empty">No structured news data available.</td>
        </tr>"""

    rows = ""

    for ticker, articles in news_map.items():
        safe_ticker = escape(str(ticker))

        if not articles:
            rows += f"""
            <tr>
                <td><strong>{safe_ticker}</strong></td>
                <td>—</td>
                <td class="empty">No relevant company news found.</td>
                <td>—</td>
                <td>—</td>
                <td>—</td>
            </tr>"""
            continue

        for article in articles:
            normalized = normalize_news_item(str(ticker), article)
            title = escape(normalized["title"])
            source = escape(normalized["source"])
            published_at = escape(normalized["published_at"] or "Unknown")
            score = normalized["relevance_score"]
            url = normalized["url"]
            link_html = (
                f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open</a>'
                if url
                else "—"
            )

            rows += f"""
            <tr>
                <td><strong>{safe_ticker}</strong></td>
                <td class="score">{score:.2f}</td>
                <td>{title}</td>
                <td>{source}</td>
                <td>{published_at}</td>
                <td>{link_html}</td>
            </tr>"""

    return rows


def format_trend_summary(metrics: dict[str, Any]) -> str:
    if not metrics or not metrics.get("has_data"):
        return '<span class="empty">Market trend data unavailable in this dev-limited/fallback-limited run.</span>'

    parts = [
        f"6M {signed_pct(metrics.get('return_6m_pct'))}",
        f"RS {signed_pct(metrics.get('relative_strength_6m_pct'))}",
        f"200D {yes_no(metrics.get('above_sma_200'))}",
        f"52H {signed_pct(metrics.get('distance_from_52w_high_pct'))}",
    ]
    return "<br>".join(escape(part) for part in parts)


def bool_badge(value: Any) -> str:
    if value is True:
        return '<span class="yes">Yes</span>'
    if value is False:
        return '<span class="no">No</span>'
    return '<span class="empty">—</span>'


def format_compact_list(items: list[str]) -> str:
    if not items:
        return '<span class="empty">—</span>'

    safe_items = "".join(f"<li>{escape(str(item))}</li>" for item in items[:3])
    return f'<ul class="compact">{safe_items}</ul>'


def action_css_class(action: str) -> str:
    upper = action.upper()
    if "ADD" in upper:
        return "action-add"
    if upper == "HOLD" or "HOLD" in upper:
        return "action-hold"
    if "REDUCE" in upper or "CUT" in upper or "AVOID" in upper:
        return "action-risk"
    return "action-watch"


def normalize_news_item(ticker: str, article: dict[str, Any] | str) -> dict[str, Any]:
    """Normalize structured news dictionaries and old headline strings."""
    if isinstance(article, str):
        return {
            "ticker": ticker,
            "title": article,
            "source": "Unknown source",
            "url": "",
            "published_at": "",
            "relevance_score": 0.0,
        }

    try:
        relevance_score = float(article.get("relevance_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        relevance_score = 0.0

    return {
        "ticker": str(article.get("ticker", ticker)),
        "title": str(article.get("title", "Untitled")),
        "source": str(article.get("source", "Unknown source")),
        "url": str(article.get("url", "")),
        "published_at": str(article.get("published_at", "")),
        "relevance_score": max(0.0, min(1.0, relevance_score)),
    }


def earnings_calendar_strategy_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_earnings_calendar_strategy", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_earnings_calendar_strategy_rows(strategy: dict[str, Any]) -> str:
    if not strategy:
        return """
        <tr>
            <td colspan="7" class="empty">Earnings Calendar Strategy v1 did not run for this report.</td>
        </tr>"""

    items = strategy.get("items", []) or []
    summary = strategy.get("summary", {}) or {}
    errors = strategy.get("errors", []) or []

    if not items:
        message = format_compact_list([str(e) for e in errors[:3]]) if errors else '<span class="empty">No earnings-calendar candidates evaluated.</span>'
        status = (
            f"Candidates {summary.get('candidate_count', 0)}<br>"
            f"Preferred {summary.get('preferred_count', 0)}<br>"
            f"Urgent {summary.get('urgent_count', 0)}"
        )
        return f"""
        <tr>
            <td>{status}</td>
            <td colspan="6" class="empty">{message}</td>
        </tr>"""

    rows = ""
    for item in items:
        ticker = escape(str(item.get("ticker") or "UNKNOWN"))
        action = escape(str(item.get("action") or "MANUAL REVIEW"))
        action_class = action_css_class(action)
        earnings = item.get("earnings", {}) or {}
        reasons = [str(r) for r in (item.get("reasons", []) or [])]
        risks = [str(r) for r in (item.get("risks", []) or [])]
        next_check = escape(str(item.get("next_check") or "Manual review before entry."))
        option_type = escape(str(item.get("option_type") or "call").upper())

        structure = (
            f"Strike {option_money(item.get('strike'))} {option_type}<br>"
            f"Short {escape(str(item.get('front_expiration') or '—'))} "
            f"({item.get('front_dte') if item.get('front_dte') is not None else '—'} DTE)<br>"
            f"Long {escape(str(item.get('back_expiration') or '—'))} "
            f"({item.get('back_dte') if item.get('back_dte') is not None else '—'} DTE)"
        )
        earnings_fit = (
            f"{escape(str(earnings.get('earnings_date') or 'Unknown'))}<br>"
            f"{escape(str(earnings.get('session_label') or 'Unknown'))}<br>"
            f"<span class='muted'>Relation: {escape(str(item.get('earnings_relation') or 'unknown'))}</span><br>"
            f"<span class='muted'>Preferred: {yes_no(item.get('is_preferred_setup'))} | Urgent: {yes_no(item.get('urgent_review'))}</span>"
        )
        debit_liquidity = (
            f"Conservative debit {option_money(item.get('conservative_debit'))}<br>"
            f"Mid debit {option_money(item.get('mid_debit'))}<br>"
            f"Max spread {pct(item.get('max_leg_spread_pct'))}<br>"
            f"Min OI {compact_big_number(item.get('min_leg_open_interest'))} | "
            f"Min Vol {compact_big_number(item.get('min_leg_volume'))}"
        )
        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td class="score">{number(item.get('score'), 1)}<br><span class="pill {action_class}">{action}</span></td>
            <td>{structure}</td>
            <td>{earnings_fit}</td>
            <td>{debit_liquidity}</td>
            <td>{format_compact_list(reasons)}</td>
            <td>{format_compact_list(risks)}<br><span class="muted">{next_check}</span></td>
        </tr>"""
    return rows


def watchlist_review_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_watchlist_review", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_watchlist_review_rows(review: dict[str, Any]) -> str:
    if not review:
        return """
        <tr>
            <td colspan="7" class="empty">Watchlist Stock Candidate Review v2 did not run for this report.</td>
        </tr>"""

    items = review.get("items", []) or []
    summary = review.get("summary", {}) or {}
    errors = review.get("errors", []) or []

    if not items:
        message = format_compact_list([str(e) for e in errors[:3]]) if errors else '<span class="empty">No watchlist candidates found. Add Robinhood watchlist items or set WATCHLIST_TICKERS.</span>'
        status = (
            f"Candidates {summary.get('candidate_count', 0)}<br>"
            f"New {summary.get('new_candidate_count', 0)}<br>"
            f"Urgent {summary.get('urgent_count', 0)}"
        )
        return f"""
        <tr>
            <td>{status}</td>
            <td colspan="6">{message}</td>
        </tr>"""

    rows = ""
    for item in items:
        ticker = escape(str(item.get("ticker") or "UNKNOWN"))
        category = escape(str(item.get("category") or "WATCH"))
        category_upper = category.upper()
        category_class = "urgent" if "URGENT" in category_upper or "AVOID" in category_upper else "candidate" if "CALENDAR" in category_upper else action_css_class(category)
        earnings = item.get("earnings", {}) or {}
        strategy = item.get("earnings_calendar_strategy", {}) or {}
        calendar = item.get("calendar_candidate", {}) or {}
        watchlists = ", ".join(str(w) for w in (item.get("watchlists", []) or [])) or "—"
        sources = ", ".join(str(src) for src in (item.get("sources", []) or [])) or "—"

        if earnings.get("has_data"):
            earnings_text = (
                f"{escape(str(earnings.get('earnings_date') or 'Unknown'))}<br>"
                f"{escape(str(earnings.get('session_label') or 'Unknown'))}<br>"
                f"DTE {earnings.get('days_until_earnings') if earnings.get('days_until_earnings') is not None else 'unknown'}"
            )
        else:
            earnings_text = f"<span class='empty'>{escape(str(earnings.get('error') or 'No earnings event this run.'))}</span>"

        overlay_bits = []
        overlay = item.get("earnings_calendar_overlay")
        if overlay:
            overlay_bits.append(f"Overlay: {escape(str(overlay))}")
        if calendar:
            overlay_bits.append(
                f"Calendar candidate score {number(calendar.get('score'), 1)}"
            )
        if strategy:
            overlay_bits.append(
                f"Earnings strategy: {escape(str(strategy.get('action') or '—'))}"
            )
            if strategy.get("score") is not None:
                overlay_bits.append(f"Strategy score {number(strategy.get('score'), 1)}")
        if item.get("news_article_count"):
            overlay_bits.append(f"News articles {item.get('news_article_count')}")
        tradier_compact = item.get("tradier_snapshot", {}) or {}
        if tradier_compact.get("has_data"):
            spread = tradier_compact.get("quote_spread_pct")
            vol = tradier_compact.get("volume")
            if spread is not None:
                overlay_bits.append(f"Stock quote spread {pct(spread)}")
            if vol is not None:
                overlay_bits.append(f"Underlying vol {compact_big_number(vol)}")
        options_text = "<br>".join(overlay_bits) if overlay_bits else '<span class="empty">No earnings/calendar overlay; stock watch only.</span>'

        reasons = [str(r) for r in (item.get("reasons", []) or [])]
        risks = [str(r) for r in (item.get("risks", []) or [])]
        next_check = escape(str(item.get("next_check") or "Keep watching."))
        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td class="score">Total {number(item.get('score'), 1)}<br>Stock {number(item.get('stock_score'), 1)}<br><span class="pill {category_class}">{category}</span></td>
            <td>{escape(str(item.get('portfolio_status') or 'Unknown'))}</td>
            <td>{escape(watchlists)}<br><span class="muted">{escape(sources)}</span></td>
            <td>{earnings_text}</td>
            <td>{options_text}</td>
            <td>{format_compact_list(reasons + risks)}<br><span class="muted">{next_check}</span></td>
        </tr>"""
    return rows


def portfolio_gap_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not isinstance(tradier_snapshot, dict):
        return {}
    raw = tradier_snapshot.get("_portfolio_gap", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_portfolio_gap_text(gap: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if not gap or not gap.get("enabled", True):
        return ["Portfolio gap analysis disabled."]
    if not gap.get("has_data"):
        errors = gap.get("errors", []) or []
        if errors:
            return ["Portfolio gap analysis unavailable: " + "; ".join(str(e) for e in errors[:3])]
        return ["Portfolio gap analysis unavailable for this run."]

    summary = gap.get("summary", {}) or {}
    lines.append(
        f"Profile {gap.get('target_profile', 'aggressive_macro_growth')} | "
        f"Suggestions {summary.get('suggestion_count', 0)} | "
        f"Underweight/missing {summary.get('underweight_count', 0)} | "
        f"Overweight/high {summary.get('overweight_count', 0)}"
    )

    exposure_rows = gap.get("exposure_rows", []) or []
    if exposure_rows:
        lines.append("Exposure gaps / macro buckets:")
        for row in exposure_rows[:10]:
            lines.append(
                f"  {row.get('bucket', 'Unknown')}: current {pct(row.get('current_pct'))} | "
                f"target {pct(row.get('target_pct'))} | gap {signed_pct(row.get('gap_pct'))} | "
                f"{row.get('status', 'REVIEW')} | {row.get('macro_bias', 'Neutral')}"
            )

    risk_rows = gap.get("risk_rows", []) or []
    if risk_rows:
        lines.append("Risk buckets:")
        for row in risk_rows[:8]:
            lines.append(
                f"  {row.get('bucket', 'Unknown')}: current {pct(row.get('current_pct'))} | "
                f"target/max {pct(row.get('target_pct'))} | {row.get('status', 'REVIEW')}"
            )

    suggestions = gap.get("suggestions", []) or []
    if suggestions:
        lines.append("Suggested watchlist candidates:")
        for item in suggestions[:10]:
            lines.append(
                f"  {item.get('ticker', 'UNKNOWN')}: Score {number(item.get('score'), 1)} | "
                f"{item.get('category', 'WATCH')} | "
                f"Buckets: {', '.join(item.get('core_buckets', []) or []) or 'Unknown'}"
            )
            for reason in (item.get("reasons", []) or [])[:2]:
                lines.append(f"    + {reason}")
            for risk in (item.get("risks", []) or [])[:2]:
                lines.append(f"    - {risk}")
            if item.get("next_check"):
                lines.append(f"    Next: {item.get('next_check')}")
    else:
        lines.append("No watchlist candidates cleared the portfolio-gap suggestion threshold this run.")

    for note in (gap.get("notes", []) or [])[:4]:
        lines.append(f"Note: {note}")
    return lines


def format_portfolio_gap_rows(gap: dict[str, Any]) -> str:
    if not gap or not gap.get("enabled", True):
        return '<p class="empty">Portfolio gap analysis disabled.</p>'
    if not gap.get("has_data"):
        errors = gap.get("errors", []) or []
        msg = "; ".join(str(e) for e in errors[:3]) if errors else "Portfolio gap analysis unavailable."
        return f'<p class="empty">{escape(msg)}</p>'

    summary = gap.get("summary", {}) or {}
    exposure_rows = gap.get("exposure_rows", []) or []
    risk_rows = gap.get("risk_rows", []) or []
    suggestions = gap.get("suggestions", []) or []

    exposure_html = ""
    for row in exposure_rows[:12]:
        status = str(row.get("status", "REVIEW"))
        cls = "candidate" if status in {"UNDERWEIGHT", "MISSING"} else ("urgent" if status in {"OVERWEIGHT", "HIGH / MONITOR"} else "")
        exposure_html += f"""
        <tr>
            <td><strong>{escape(str(row.get('bucket', 'Unknown')))}</strong></td>
            <td>{pct(row.get('current_pct'))}</td>
            <td>{pct(row.get('target_pct'))}</td>
            <td>{signed_pct(row.get('gap_pct'))}</td>
            <td><span class="pill {cls}">{escape(status)}</span></td>
            <td>{escape(str(row.get('macro_bias', 'Neutral')))}</td>
            <td>{escape(str(row.get('guidance', '—')))}</td>
        </tr>"""

    if not exposure_html:
        exposure_html = '<tr><td colspan="7" class="empty">No exposure rows generated.</td></tr>'

    risk_html = ""
    for row in risk_rows[:10]:
        status = str(row.get("status", "REVIEW"))
        cls = "urgent" if "ABOVE" in status else ""
        risk_html += f"""
        <tr>
            <td><strong>{escape(str(row.get('bucket', 'Unknown')))}</strong></td>
            <td>{pct(row.get('current_pct'))}</td>
            <td>{pct(row.get('target_pct'))}</td>
            <td><span class="pill {cls}">{escape(status)}</span></td>
            <td>{escape(str(row.get('guidance', '—')))}</td>
        </tr>"""

    if not risk_html:
        risk_html = '<tr><td colspan="5" class="empty">No risk bucket rows generated.</td></tr>'

    suggestion_html = ""
    for item in suggestions[:10]:
        category = str(item.get("category", "WATCH"))
        cls = "candidate" if "CONSIDER" in category or "HIGH" in category else ""
        reasons = format_compact_list(item.get("reasons", []) or [])
        risks = format_compact_list(item.get("risks", []) or [])
        core = ", ".join(str(b) for b in (item.get("core_buckets", []) or [])) or "Unknown"
        risk = ", ".join(str(b) for b in (item.get("risk_buckets", []) or [])) or "—"
        suggestion_html += f"""
        <tr>
            <td><strong>{escape(str(item.get('ticker', 'UNKNOWN')))}</strong></td>
            <td class="score">{number(item.get('score'), 1)}<br><span class="pill {cls}">{escape(category)}</span></td>
            <td>{escape(core)}<br><span class="muted">Risk: {escape(risk)}</span></td>
            <td>{'Already held' if item.get('already_held') else 'New candidate'}<br><span class="muted">{escape(', '.join(item.get('watchlists', []) or []) or '—')}</span></td>
            <td>{reasons}</td>
            <td>{risks}</td>
            <td>{escape(str(item.get('next_check', '—') or '—'))}</td>
        </tr>"""

    if not suggestion_html:
        suggestion_html = '<tr><td colspan="7" class="empty">No portfolio-gap suggestions cleared the score threshold this run.</td></tr>'

    notes = "".join(f"<li>{escape(str(note))}</li>" for note in (gap.get("notes", []) or [])[:4])

    return f"""
    <p class="muted">Profile: {escape(str(gap.get('target_profile', 'aggressive_macro_growth')))} | Suggestions: {summary.get('suggestion_count', 0)} | Underweight/missing: {summary.get('underweight_count', 0)} | Overweight/high: {summary.get('overweight_count', 0)}</p>
    <h3>Core Sector / Theme Exposure</h3>
    <table>
        <tr>
            <th>Bucket</th>
            <th>Current</th>
            <th>Target</th>
            <th>Gap</th>
            <th>Status</th>
            <th>Macro Bias</th>
            <th>Guidance</th>
        </tr>
        {exposure_html}
    </table>
    <h3>Risk Buckets</h3>
    <table>
        <tr>
            <th>Risk Bucket</th>
            <th>Current</th>
            <th>Target / Max</th>
            <th>Status</th>
            <th>Guidance</th>
        </tr>
        {risk_html}
    </table>
    <h3>Suggested Watchlist Candidates</h3>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Score / Category</th>
            <th>Buckets</th>
            <th>Portfolio / Source</th>
            <th>Reasons</th>
            <th>Risks</th>
            <th>Next Check</th>
        </tr>
        {suggestion_html}
    </table>
    <p class="muted"><strong>Notes:</strong></p><ul class="compact">{notes}</ul>
    """



def stock_momentum_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_stock_momentum_strategy", {}) or {}
    return raw if isinstance(raw, dict) else {}



def trade_memory_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    data = tradier_snapshot.get("_trade_memory") if isinstance(tradier_snapshot, dict) else {}
    return data if isinstance(data, dict) else {}


def format_trade_memory_text(trade_memory: dict[str, Any] | None) -> list[str]:
    trade_memory = trade_memory or {}
    summary = trade_memory.get("summary", {}) or {}
    errors = trade_memory.get("errors", []) or []
    lines = [
        f"Open {summary.get('open_count', 0)} | Watch {summary.get('watch_count', 0)} | Closed {summary.get('closed_count', 0)} | Matches {summary.get('match_count', 0)}",
        f"DB: {summary.get('db_path') or trade_memory.get('db_path') or 'not configured'}",
    ]
    if errors:
        lines.append("Errors: " + "; ".join(str(e) for e in errors[:3]))
    trades = list(trade_memory.get("open_trades", []) or []) + list(trade_memory.get("watch_trades", []) or [])
    if not trades:
        lines.append("Manual trade memory is disabled; active calendars should come from broker-detected option positions.")
        return lines
    for trade in trades[:12]:
        lines.append(
            f"#{trade.get('id')} {str(trade.get('ticker') or '').upper()} {trade.get('strike')} {str(trade.get('option_type') or 'call').upper()} | "
            f"Short {trade.get('short_expiration')} / Long {trade.get('long_expiration')} | "
            f"Qty {trade.get('quantity')} | Entry debit {option_money(trade.get('entry_debit'))} | "
            f"Target {pct(trade.get('profit_target_pct'))} | Max loss {pct(trade.get('max_loss_pct'))} | Status {trade.get('status')}"
        )
        if trade.get("notes"):
            lines.append(f"  Notes: {trade.get('notes')}")
    return lines


def format_trade_memory_rows(trade_memory: dict[str, Any] | None) -> str:
    trade_memory = trade_memory or {}
    summary = trade_memory.get("summary", {}) or {}
    errors = trade_memory.get("errors", []) or []
    trades = list(trade_memory.get("open_trades", []) or []) + list(trade_memory.get("watch_trades", []) or []) + list(trade_memory.get("closed_trades", []) or [])[:10]
    if not trades:
        detail = "No stored trades yet. Use /trades to add manual calendar entries after entering a spread."
        if errors:
            detail += " Errors: " + "; ".join(escape(str(e)) for e in errors[:3])
        return f'<tr><td>Open {summary.get("open_count", 0)}<br>Watch {summary.get("watch_count", 0)}<br>Closed {summary.get("closed_count", 0)}</td><td colspan="5">{escape(detail)}</td></tr>'
    rows = []
    for trade in trades:
        status = escape(str(trade.get("status") or "open"))
        ticker = escape(str(trade.get("ticker") or ""))
        option_type = escape(str(trade.get("option_type") or "call").upper())
        strike = escape(option_money(trade.get("strike")))
        trade_cell = f"#{trade.get('id')} <strong>{ticker}</strong> {strike} {option_type}<br>Short {escape(str(trade.get('short_expiration') or '—'))}<br>Long {escape(str(trade.get('long_expiration') or '—'))}<br>Qty {escape(str(trade.get('quantity') or 1))}"
        entry_cell = f"Debit {escape(option_money(trade.get('entry_debit')))}<br>Total {escape(money(trade.get('entry_total')))}<br>Underlying {escape(money(trade.get('entry_underlying_price')))}"
        target_cell = f"Profit {escape(pct(trade.get('profit_target_pct')))}<br>Max loss {escape(pct(trade.get('max_loss_pct')))}"
        close_cell = f"Value {escape(option_money(trade.get('close_value')))}<br>Total {escape(money(trade.get('close_total')))}<br>{escape(str(trade.get('closed_at') or '—'))}"
        notes = escape(str(trade.get("notes") or trade.get("close_notes") or "—"))
        rows.append(f"<tr><td>{status}</td><td>{trade_cell}</td><td>{entry_cell}</td><td>{target_cell}</td><td>{close_cell}</td><td>{notes}</td></tr>")
    return "\n".join(rows)

def daily_opportunity_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_daily_opportunity_engine", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_daily_opportunity_text(engine: dict[str, Any]) -> list[str]:
    if not engine:
        return ["Daily Opportunity Engine did not run for this report."]
    summary = engine.get("summary", {}) or {}
    lines = [
        f"Actions {summary.get('action_count', 0)} | Calendar {summary.get('calendar_count', 0)} | "
        f"Stock {summary.get('stock_count', 0)} | Gap {summary.get('gap_count', 0)} | Risk {summary.get('risk_count', 0)}"
    ]
    actions = engine.get("actions", []) or []
    if not actions:
        lines.append("No daily actions cleared the opportunity threshold this run.")
    for item in actions[:20]:
        lines.append(
            f"{item.get('ticker', 'UNKNOWN')}: {item.get('action', 'REVIEW')} | "
            f"Score {number(item.get('priority_score'), 1)} | Type {item.get('type', 'idea')}"
        )
        if item.get("why"):
            lines.append(f"  Why: {item.get('why')}")
        if item.get("next_step"):
            lines.append(f"  Next: {item.get('next_step')}")
    return lines


def format_daily_opportunity_rows(engine: dict[str, Any]) -> str:
    actions = (engine or {}).get("actions", []) or []
    if not actions:
        return '<tr><td colspan="6" class="empty">No daily actions cleared the opportunity threshold this run.</td></tr>'
    rows = ""
    for item in actions[:30]:
        typ = escape(str(item.get("type") or "idea"))
        ticker = escape(str(item.get("ticker") or "UNKNOWN"))
        action = str(item.get("action") or "REVIEW")
        cls = "candidate" if "ADD" in action or "PASS" in action or "CONSIDER" in action else "urgent" if "AVOID" in action or "REDUCE" in action or "FAIL" in action else "action-watch"
        rows += f"""
        <tr>
            <td>{typ}</td>
            <td class="score"><strong>{ticker}</strong><br>{number(item.get('priority_score'), 1)}</td>
            <td><span class="pill {cls}">{escape(action)}</span></td>
            <td>{escape(str(item.get('why') or '—'))}</td>
            <td>{escape(str(item.get('next_step') or '—'))}</td>
            <td>{escape(str(item.get('source') or '—'))}</td>
        </tr>"""
    return rows


def format_stock_momentum_text(strategy: dict[str, Any]) -> list[str]:
    if not strategy:
        return ["Stock Momentum Add Strategy did not run for this report."]
    summary = strategy.get("summary", {}) or {}
    lines = [
        f"Candidates {summary.get('candidate_count', 0)} | Consider add {summary.get('consider_add_count', 0)} | "
        f"Add on pullback {summary.get('pullback_count', 0)} | Watch {summary.get('watch_count', 0)} | Avoid {summary.get('avoid_count', 0)}"
    ]
    for item in (strategy.get("items", []) or [])[:20]:
        lines.append(
            f"{item.get('ticker', 'UNKNOWN')}: Score {number(item.get('score'), 1)} | "
            f"{item.get('action', 'WATCH')} | {item.get('portfolio_status', 'Unknown')}"
        )
        for reason in (item.get("reasons", []) or [])[:3]:
            lines.append(f"  + {reason}")
        for risk in (item.get("risks", []) or [])[:3]:
            lines.append(f"  - {risk}")
        if item.get("next_check"):
            lines.append(f"  Next: {item.get('next_check')}")
    return lines


def format_stock_momentum_rows(strategy: dict[str, Any]) -> str:
    items = (strategy or {}).get("items", []) or []
    if not items:
        return '<tr><td colspan="7" class="empty">No stock momentum candidates generated.</td></tr>'
    rows = ""
    for item in items[:30]:
        ticker = escape(str(item.get("ticker") or "UNKNOWN"))
        action = str(item.get("action") or "WATCH")
        cls = "candidate" if action == "CONSIDER ADDING" else "action-watch" if "WATCH" in action or "PULLBACK" in action else "urgent" if "AVOID" in action else "action-hold"
        metrics = item.get("market_metrics", {}) or {}
        if metrics.get("has_data"):
            trend = (
                f"3M {signed_pct(metrics.get('return_3m_pct'))}<br>"
                f"6M {signed_pct(metrics.get('return_6m_pct'))}<br>"
                f"12M {signed_pct(metrics.get('return_12m_pct'))}<br>"
                f"200D {yes_no(metrics.get('above_sma_200'))}"
            )
        else:
            trend = '<span class="empty">No trend data</span>'
        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td class="score">{number(item.get('score'), 1)}<br><span class="pill {cls}">{escape(action)}</span></td>
            <td>{escape(str(item.get('portfolio_status') or 'Unknown'))}<br><span class="muted">Alloc {pct(item.get('allocation_pct'))}</span></td>
            <td>{trend}</td>
            <td>{format_compact_list(item.get('reasons', []) or [])}</td>
            <td>{format_compact_list(item.get('risks', []) or [])}</td>
            <td>{escape(str(item.get('next_check') or '—'))}</td>
        </tr>"""
    return rows

def unified_calendar_trade_engine_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_unified_calendar_trade_engine", {}) or {}
    return raw if isinstance(raw, dict) else {}


def calendar_ranking_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_calendar_ranking", {}) or {}
    return raw if isinstance(raw, dict) else {}


def earnings_mini_backtest_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_earnings_mini_backtest", {}) or {}
    return raw if isinstance(raw, dict) else {}


def format_unified_calendar_engine_text(engine: dict[str, Any]) -> list[str]:
    if not engine:
        return ["Unified calendar engine did not run for this report."]

    summary = engine.get("summary", {}) or {}
    lines = [
        f"New earnings rows {summary.get('new_trade_count', 0)} | "
        f"Pass {summary.get('pass_count', 0)} | "
        f"Watch/manual {summary.get('watch_count', 0)} | "
        f"Fail {summary.get('fail_count', 0)} | "
        f"Open calendars {summary.get('open_trade_count', 0)}"
    ]

    new_rows = engine.get("new_trade_rows", []) or []
    if not new_rows:
        lines.append("No new earnings-calendar opportunities were discovered/evaluated.")
    else:
        for row in new_rows[:20]:
            spread = row.get("possible_spread", {}) or {}
            earnings = row.get("earnings", {}) or {}
            spread_text = "No proposed spread"
            if spread:
                spread_text = (
                    f"{option_money(spread.get('strike'))} {str(spread.get('option_type') or 'call').upper()} | "
                    f"short {spread.get('short_expiration') or '—'} / long {spread.get('long_expiration') or '—'} | "
                    f"debit {option_money(spread.get('conservative_debit'))}"
                )
            lines.append(
                f"{row.get('ticker', 'UNKNOWN')}: Score {number(row.get('score'), 1)} | "
                f"{row.get('verdict') or 'WATCH'} | "
                f"{row.get('trade_type_label') or 'TRADE TYPE UNKNOWN'} | "
                f"Earnings {earnings.get('earnings_date') or 'unknown'} ({earnings.get('session_label') or 'Unknown'}) | "
                f"{spread_text}"
            )
            if row.get("main_blocker") or row.get("main_reason"):
                lines.append(f"  Final verdict: {row.get('main_reason') or row.get('main_blocker')}")
            for req in row.get("requirements", [])[:6]:
                lines.append(f"  {req.get('status', 'WARN')}: {req.get('name')}: {req.get('detail')}")
            lines.append(f"  Entry plan: {row.get('entry_plan') or 'Manual review before entry.'}")

    open_rows = engine.get("open_trade_rows", []) or []
    if not open_rows:
        lines.append("No open calendars detected for lifecycle action.")
    else:
        for row in open_rows:
            lines.append(
                f"Open {row.get('ticker', 'UNKNOWN')}: Score {number(row.get('score'), 1)} | "
                f"{row.get('verdict') or 'HOLD / MONITOR'} | {row.get('structure') or '—'} | "
                f"{row.get('value') or 'value unavailable'}"
            )
            lines.append(f"  Next action: {row.get('next_action') or 'Recheck before market close.'}")
    return lines


def format_unified_calendar_engine_rows(engine: dict[str, Any]) -> str:
    if not engine:
        return """
        <tr>
            <td colspan="6" class="empty">Unified Calendar Trade Engine v1 did not run for this report.</td>
        </tr>"""

    errors = engine.get("errors", []) or []
    new_rows = engine.get("new_trade_rows", []) or []
    open_rows = engine.get("open_trade_rows", []) or []

    if not new_rows and not open_rows:
        message = format_compact_list([str(e) for e in errors[:3]]) if errors else '<span class="empty">No new earnings-calendar candidates or open calendars were found.</span>'
        return f"""
        <tr>
            <td colspan="6" class="empty">{message}</td>
        </tr>"""

    rows = ""
    for item in new_rows[:30]:
        ticker = escape(str(item.get("ticker") or "UNKNOWN"))
        verdict = escape(str(item.get("verdict") or "WATCH"))
        verdict_class = _calendar_verdict_class(str(item.get("verdict") or ""))
        earnings = item.get("earnings", {}) or {}
        spread = item.get("possible_spread", {}) or {}
        requirements = item.get("requirements", []) or []
        entry_plan = escape(str(item.get("entry_plan") or "Manual review before entry."))

        earnings_text = (
            f"{escape(str(earnings.get('earnings_date') or 'Unknown'))}<br>"
            f"{escape(str(earnings.get('session_label') or 'Unknown'))}<br>"
            f"<span class='muted'>DTE {escape(str(earnings.get('days_until_earnings') if earnings.get('days_until_earnings') is not None else 'unknown'))}</span><br>"
            f"<span class='pill {verdict_class}'>{verdict}</span>"
        )

        if spread:
            spread_text = (
                f"Strike {option_money(spread.get('strike'))} {escape(str(spread.get('option_type') or 'call').upper())}<br>"
                f"Short {escape(str(spread.get('short_expiration') or '—'))} "
                f"({spread.get('front_dte') if spread.get('front_dte') is not None else '—'} DTE)<br>"
                f"Long {escape(str(spread.get('long_expiration') or '—'))} "
                f"({spread.get('back_dte') if spread.get('back_dte') is not None else '—'} DTE)<br>"
                f"Debit {option_money(spread.get('conservative_debit'))} | Mid {option_money(spread.get('mid_debit'))}<br>"
                f"<span class='muted'>Spread {pct(spread.get('max_leg_spread_pct'))} | Min OI {compact_big_number(spread.get('min_leg_open_interest'))} | Min Vol {compact_big_number(spread.get('min_leg_volume'))}</span>"
            )
        else:
            spread_text = '<span class="empty">No proposed spread — failed scanner requirements.</span>'
        raw_note = _calendar_raw_scanner_note(item)

        rows += f"""
        <tr>
            <td>New earnings calendar</td>
            <td class="score"><strong>{ticker}</strong><br>{number(item.get('score'), 1)}</td>
            <td>{earnings_text}</td>
            <td>{spread_text}</td>
            <td>{format_requirement_list(requirements)}<br><span class="muted">Trade type: {escape(str(item.get('trade_type_label') or 'Unknown'))}<br>Main blocker: {escape(str(item.get('main_blocker') or '—'))}<br>Backtest: {escape(str(item.get('backtest_status') or '—'))}<br>Account risk: {escape(str(item.get('account_risk_status') or '—'))}</span></td>
            <td>{entry_plan}<br><span class="muted">{raw_note}<br>{escape(str(item.get('main_reason') or ''))}</span></td>
        </tr>"""

    for item in open_rows[:30]:
        ticker = escape(str(item.get("ticker") or "UNKNOWN"))
        verdict = escape(str(item.get("verdict") or "HOLD / MONITOR"))
        verdict_class = _calendar_verdict_class(str(item.get("verdict") or ""))
        next_action = escape(str(item.get("next_action") or "Recheck before market close."))
        structure = escape(str(item.get("structure") or "—"))
        value = escape(str(item.get("value") or "Value unavailable"))
        hold = ""
        if item.get("hold_through_score") is not None or item.get("hold_through_action"):
            hold = f"<br><span class='muted'>Hold-through {number(item.get('hold_through_score'), 1)}: {escape(str(item.get('hold_through_action') or 'ACTIVE REVIEW'))}</span>"
        reasons = [str(r) for r in (item.get("reasons", []) or [])]
        risks = [str(r) for r in (item.get("risks", []) or [])]
        rows += f"""
        <tr>
            <td>Open calendar</td>
            <td class="score"><strong>{ticker}</strong><br>{number(item.get('score'), 1)}</td>
            <td><span class="pill {verdict_class}">{verdict}</span></td>
            <td>{structure}<br><span class="muted">{value}</span>{hold}</td>
            <td>{format_compact_list(reasons + risks)}</td>
            <td>{next_action}</td>
        </tr>"""

    return rows


def format_calendar_ranking_text(ranking: dict[str, Any]) -> list[str]:
    if not ranking or not ranking.get("items"):
        errors = ranking.get("errors", []) if isinstance(ranking, dict) else []
        return ["No ranked calendar candidates." + (" " + "; ".join(str(e) for e in errors[:2]) if errors else "")]
    summary = ranking.get("summary", {}) or {}
    lines = [
        f"Candidates {summary.get('candidate_count', 0)} | Pass all criteria {summary.get('pass_count', 0)} | Backtest eligible {summary.get('backtest_eligible_count', 0)}"
    ]
    for item in ranking.get("items", [])[:10]:
        final = item.get("final_verdict") if isinstance(item.get("final_verdict"), dict) else {}
        lines.append(
            f"{item.get('ticker', 'UNKNOWN')}: Rank {number(item.get('rank_score'), 1)} | {final.get('final_verdict') or item.get('action') or 'WATCH'} | "
            f"Timing {item.get('entry_timing') or 'UNKNOWN'} | DTE {item.get('days_until_earnings') if item.get('days_until_earnings') is not None else '—'} | "
            f"Pass {yes_no(item.get('passes_all_criteria'))} | Backtest {yes_no(item.get('backtest_eligible'))}"
        )
        if final:
            lines.append(f"  Final: {final.get('trade_type_label')}; blocker={final.get('main_blocker') or '—'}; backtest={final.get('backtest_status')}")
        for crit in (item.get("criteria", []) or [])[:5]:
            lines.append(f"  {crit.get('status')}: {crit.get('name')}: {crit.get('detail')}")
        if item.get("next_check"):
            lines.append(f"  Next: {item.get('next_check')}")
    return lines


def format_earnings_mini_backtest_text(backtest: dict[str, Any]) -> list[str]:
    if not backtest or not backtest.get("items"):
        errors = backtest.get("errors", []) if isinstance(backtest, dict) else []
        return ["Mini-backtest skipped." + (" " + "; ".join(str(e) for e in errors[:2]) if errors else "")]
    lines = []
    for item in backtest.get("items", []) or []:
        summary = item.get("summary", {}) or {}
        if not item.get("has_data"):
            lines.append(f"{item.get('ticker', 'UNKNOWN')}: no historical earnings/candle data available. {'; '.join(str(e) for e in (item.get('errors', []) or [])[:2])}")
            continue
        lines.append(
            f"{item.get('ticker', 'UNKNOWN')}: {summary.get('event_count', 0)} events | "
            f"avg abs move {pct(summary.get('avg_abs_event_move_pct'))} | max abs move {pct(summary.get('max_abs_event_move_pct'))} | "
            f"avg gap {pct(summary.get('avg_abs_gap_pct'))} | avg pre-run {signed_pct(summary.get('avg_pre_event_runup_pct'))}"
        )
        lines.append(f"  {summary.get('interpretation') or 'No interpretation.'}")
    return lines


def format_calendar_ranking_rows(ranking: dict[str, Any]) -> str:
    if not ranking or not ranking.get("items"):
        errors = ranking.get("errors", []) if isinstance(ranking, dict) else []
        message = format_compact_list([str(e) for e in errors[:3]]) if errors else '<span class="empty">No calendar candidates were ranked.</span>'
        return f'<tr><td colspan="6" class="empty">{message}</td></tr>'
    rows = ""
    for item in (ranking.get("items", []) or [])[:20]:
        final = item.get("final_verdict") if isinstance(item.get("final_verdict"), dict) else {}
        action = escape(str(final.get("final_verdict") or item.get("action") or "WATCH"))
        cls = _calendar_verdict_class(action)
        rows += f"""
        <tr>
            <td class="score"><strong>{escape(str(item.get('ticker') or 'UNKNOWN'))}</strong><br>{number(item.get('rank_score'), 1)}</td>
            <td><span class="pill {cls}">{action}</span><br><span class="muted">Base {number(item.get('base_score'), 1)}<br>{escape(str(final.get('trade_type_label') or item.get('trade_type_label') or 'Unknown'))}</span></td>
            <td>{escape(str(item.get('entry_timing') or 'UNKNOWN'))}<br><span class="muted">DTE {escape(str(item.get('days_until_earnings') if item.get('days_until_earnings') is not None else '—'))}</span></td>
            <td>{format_requirement_list(item.get('criteria', []) or [])}</td>
            <td>{format_compact_list([str(x) for x in ([final.get('main_blocker'), final.get('hard_fail_reason'), final.get('account_risk_warning')] + (item.get('reasons', []) or []) + (item.get('risks', []) or []))[:8] if x])}</td>
            <td>{escape(str(final.get('backtest_status') or item.get('backtest_status') or 'not_eligible'))}<br><span class="muted">{escape(str(item.get('next_check') or 'Recheck later.'))}</span></td>
        </tr>"""
    return rows


def format_earnings_mini_backtest_rows(backtest: dict[str, Any]) -> str:
    if not backtest or not backtest.get("items"):
        errors = backtest.get("errors", []) if isinstance(backtest, dict) else []
        message = format_compact_list([str(e) for e in errors[:3]]) if errors else '<span class="empty">No backtest was run.</span>'
        return f'<tr><td colspan="6" class="empty">{message}</td></tr>'
    rows = ""
    for item in backtest.get("items", []) or []:
        summary = item.get("summary", {}) or {}
        notes = [str(n) for n in (backtest.get("notes", []) or [])[:2]]
        if not item.get("has_data"):
            rows += f"""
            <tr>
                <td><strong>{escape(str(item.get('ticker') or 'UNKNOWN'))}</strong></td>
                <td colspan="5" class="empty">{escape(str(item.get('mode_status') or item.get('mode') or 'diagnostic'))}: No historical backtest data. {format_compact_list([str(e) for e in (item.get('errors', []) or [])[:3]])}</td>
            </tr>"""
            continue
        rows += f"""
        <tr>
            <td><strong>{escape(str(item.get('ticker') or 'UNKNOWN'))}</strong><br><span class="muted">Rank {number(item.get('ranking_score'), 1)}<br>{escape(str(item.get('mode_status') or item.get('mode') or 'eligibility'))}</span></td>
            <td>{summary.get('event_count', 0)} historical event(s)</td>
            <td>Avg abs {pct(summary.get('avg_abs_event_move_pct'))}<br>Max abs {pct(summary.get('max_abs_event_move_pct'))}<br>Small-move rate {pct(summary.get('small_move_rate_pct'))}</td>
            <td>Avg gap {pct(summary.get('avg_abs_gap_pct'))}<br>Avg pre-run {signed_pct(summary.get('avg_pre_event_runup_pct'))}</td>
            <td>{escape(str(summary.get('interpretation') or 'No interpretation.'))}</td>
            <td>{format_compact_list(notes)}</td>
        </tr>"""
    return rows


def format_requirement_list(requirements: list[dict[str, Any]]) -> str:
    if not requirements:
        return '<span class="empty">No requirement details.</span>'
    items = []
    for req in requirements:
        status = escape(str(req.get("status") or "WARN"))
        name = escape(str(req.get("name") or "Requirement"))
        detail = escape(str(req.get("detail") or ""))
        cls = "yes" if status == "PASS" else "no" if status == "FAIL" else "muted"
        items.append(f"<li><strong class='{cls}'>{status}</strong> — {name}: <span class='muted'>{detail}</span></li>")
    return '<ul class="compact">' + ''.join(items) + '</ul>'


def _calendar_raw_scanner_note(item: dict[str, Any]) -> str:
    raw = escape(str(item.get("raw_scanner_verdict") or "—"))
    verdict = str(item.get("verdict") or "").upper()
    has_spread = bool(item.get("possible_spread"))
    if has_spread and verdict.startswith("FAIL"):
        return "Raw scanner found a structure, but final verdict rejected it."
    return f"Raw scanner: {raw}"


def _calendar_verdict_class(verdict: str) -> str:
    text = str(verdict or "").upper()
    if text.startswith("PASS") or "TAKE PROFIT" in text:
        return "candidate"
    if text.startswith("FAIL") or "AVOID" in text or "CUT" in text:
        return "urgent"
    if "URGENT" in text:
        return "urgent"
    return "action-watch"
