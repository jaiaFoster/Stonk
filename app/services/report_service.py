"""
app/services/report_service.py — Payload and HTML report formatting.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any


NewsMap = dict[str, list[dict[str, Any]]]
Recommendations = list[dict[str, Any]]
TradierSnapshot = dict[str, dict[str, Any]]
CalendarCandidates = list[dict[str, Any]]


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


def format_payload(
    positions: list[dict[str, Any]],
    news_map: NewsMap,
    recommendations: Recommendations | None = None,
    tradier_snapshot: TradierSnapshot | None = None,
) -> str:
    today = date.today().strftime("%B %d, %Y")
    recommendations = recommendations or []
    tradier_snapshot = tradier_snapshot or {}
    calendar_candidates = calendar_candidates_from_tradier_snapshot(tradier_snapshot)
    earnings_calendar_strategy = earnings_calendar_strategy_from_tradier_snapshot(tradier_snapshot)
    open_options = open_options_from_tradier_snapshot(tradier_snapshot)
    lifecycle_checks = calendar_lifecycle_from_tradier_snapshot(tradier_snapshot)
    earnings_events = earnings_events_from_tradier_snapshot(tradier_snapshot)
    watchlist_review = watchlist_review_from_tradier_snapshot(tradier_snapshot)
    earnings_trade_discovery = earnings_trade_discovery_from_tradier_snapshot(tradier_snapshot)
    unified_calendar_engine = unified_calendar_trade_engine_from_tradier_snapshot(tradier_snapshot)

    lines = [
        f"Date: {today}",
        "",
        "=== MY STOCK POSITIONS ===",
    ]

    for p in positions:
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

    lines += ["", "=== PORTFOLIO SCORING V2 ==="]

    if not recommendations:
        lines.append("No portfolio scoring recommendations generated.")
    else:
        for rec in recommendations:
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
    market_rows = [rec for rec in recommendations if (rec.get("market_metrics") or {}).get("has_data")]
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

    lines += ["", "=== EARNINGS TIMESTAMP PROVIDER V1 ==="]
    if not earnings_events:
        lines.append("No earnings timestamp data available for this run.")
    else:
        for ticker, event in earnings_events.items():
            if event.get("has_data"):
                dte = event.get("days_until_earnings")
                dte_text = f"{dte} days" if dte is not None else "unknown DTE"
                lines.append(
                    f"{ticker}: {event.get('earnings_date') or 'Unknown date'} | "
                    f"{event.get('session_label') or 'Unknown'} | {dte_text} | "
                    f"Confirmed timestamp: {yes_no(event.get('is_timestamp_confirmed'))} | Source: {event.get('source') or 'unknown'}"
                )
            else:
                lines.append(f"{ticker}: earnings unavailable — {event.get('error') or 'No event returned.'}")

    lines += ["", "=== EARNINGS TRADE DISCOVERY V1 ==="]
    if not earnings_trade_discovery or not earnings_trade_discovery.get("has_data"):
        errors = (earnings_trade_discovery or {}).get("errors", []) or []
        if errors:
            lines.append("No earnings-discovery universe available: " + "; ".join(str(e) for e in errors[:3]))
        else:
            lines.append("No upcoming earnings events found in the configured discovery window.")
    else:
        summary = earnings_trade_discovery.get("summary", {}) or {}
        lines.append(
            f"Window {earnings_trade_discovery.get('window_start') or '—'}..{earnings_trade_discovery.get('window_end') or '—'} | "
            f"Events {summary.get('event_count', 0)} | Tickers {summary.get('ticker_count', 0)}"
        )
        for event in (earnings_trade_discovery.get("items", []) or [])[:20]:
            lines.append(
                f"{event.get('ticker', 'UNKNOWN')}: {event.get('earnings_date') or 'Unknown date'} | "
                f"{event.get('session_label') or 'Unknown'} | "
                f"DTE {event.get('days_until_earnings') if event.get('days_until_earnings') is not None else 'unknown'} | "
                f"Confirmed {yes_no(event.get('is_timestamp_confirmed'))}"
            )

    lines += ["", "=== UNIFIED CALENDAR TRADE ENGINE V1 ==="]
    lines.extend(format_unified_calendar_engine_text(unified_calendar_engine))

    lines += ["", "=== TRADIER OPTIONS SNAPSHOT ==="]
    if not tradier_snapshot:
        lines.append("No Tradier quote/options data available for this run.")
    else:
        for ticker, data in tradier_snapshot.items():
            if str(ticker).startswith("_"):
                continue
            quote = data.get("quote", {}) or {}
            atm_call = data.get("atm_call") or {}
            atm_put = data.get("atm_put") or {}
            if not data.get("has_data"):
                lines.append(f"{ticker}: unavailable — {data.get('error') or 'No Tradier data returned.'}")
                continue

            lines.append(
                f"{ticker}: quote last {money(quote.get('last'))} | "
                f"bid {money(quote.get('bid'))} | ask {money(quote.get('ask'))} | "
                f"expirations {data.get('expiration_count', 0)} | "
                f"sample expiration {data.get('selected_expiration') or 'N/A'} | "
                f"contracts {data.get('chain_contract_count', 0)} "
                f"({data.get('call_count', 0)} calls / {data.get('put_count', 0)} puts)"
            )
            if atm_call:
                lines.append(
                    f"  ATM call: {atm_call.get('symbol') or 'N/A'} | strike {option_money(atm_call.get('strike'))} | "
                    f"bid/ask {option_money(atm_call.get('bid'))}/{option_money(atm_call.get('ask'))} | "
                    f"mid {option_money(atm_call.get('mid'))} | vol {atm_call.get('volume') or 0} | "
                    f"OI {atm_call.get('open_interest') or 0} | delta {option_money(atm_call.get('delta'))} | "
                    f"theta {option_money(atm_call.get('theta'))} | IV {option_money(atm_call.get('iv'))}"
                )
            if atm_put:
                lines.append(
                    f"  ATM put: {atm_put.get('symbol') or 'N/A'} | strike {option_money(atm_put.get('strike'))} | "
                    f"bid/ask {option_money(atm_put.get('bid'))}/{option_money(atm_put.get('ask'))} | "
                    f"mid {option_money(atm_put.get('mid'))} | vol {atm_put.get('volume') or 0} | "
                    f"OI {atm_put.get('open_interest') or 0} | delta {option_money(atm_put.get('delta'))} | "
                    f"theta {option_money(atm_put.get('theta'))} | IV {option_money(atm_put.get('iv'))}"
                )

    lines += ["", "=== CALENDAR SPREAD SCREENER V1 ==="]
    if not calendar_candidates:
        lines.append("No calendar spread candidates generated for this run.")
    else:
        for cand in calendar_candidates:
            lines.append(
                f"{cand.get('ticker', 'UNKNOWN')} {cand.get('strategy', 'Calendar')}: "
                f"Score {number(cand.get('score'), 1)} | Action: {cand.get('action', 'WATCH')} | "
                f"Strike {option_money(cand.get('strike'))} {str(cand.get('option_type') or 'call').upper()} | "
                f"Short {cand.get('front_expiration')} ({cand.get('front_dte')} DTE) / "
                f"Long {cand.get('back_expiration')} ({cand.get('back_dte')} DTE) | "
                f"Debit conservative {option_money(cand.get('conservative_debit'))} | "
                f"Mid debit {option_money(cand.get('mid_debit'))} | "
                f"Max leg spread {pct(cand.get('max_leg_spread_pct'))} | "
                f"Min OI {compact_big_number(cand.get('min_leg_open_interest'))} | "
                f"Min Vol {compact_big_number(cand.get('min_leg_volume'))}"
            )
            for reason in cand.get("reasons", []) or []:
                lines.append(f"  + {reason}")
            for risk in cand.get("risks", []) or []:
                lines.append(f"  - {risk}")
            lines.append(f"  Next check: {cand.get('next_check') or 'Recheck before entry.'}")

    lines += ["", "=== EARNINGS CALENDAR STRATEGY V1 ==="]
    if not earnings_calendar_strategy:
        lines.append("Earnings calendar strategy did not run for this report.")
    else:
        summary = earnings_calendar_strategy.get("summary", {}) or {}
        lines.append(
            f"Candidates evaluated: {summary.get('candidate_count', 0)} | "
            f"Preferred earnings setups: {summary.get('preferred_count', 0)} | "
            f"Urgent review: {summary.get('urgent_count', 0)} | "
            f"Avoid: {summary.get('avoid_count', 0)}"
        )
        items = earnings_calendar_strategy.get("items", []) or []
        if not items:
            lines.append("No earnings-calendar candidates evaluated.")
        else:
            for item in items:
                earnings = item.get("earnings", {}) or {}
                lines.append(
                    f"{item.get('ticker', 'UNKNOWN')} Earnings Long Call Calendar: "
                    f"Score {number(item.get('score'), 1)} | Action: {item.get('action', 'MANUAL REVIEW')} | "
                    f"Strike {option_money(item.get('strike'))} {str(item.get('option_type') or 'call').upper()} | "
                    f"Short {item.get('front_expiration')} ({item.get('front_dte')} DTE) / "
                    f"Long {item.get('back_expiration')} ({item.get('back_dte')} DTE) | "
                    f"Earnings {earnings.get('earnings_date') or 'unknown'} ({earnings.get('session_label') or 'Unknown'}) | "
                    f"Relation {item.get('earnings_relation') or 'unknown'}"
                )
                for reason in item.get("reasons", []) or []:
                    lines.append(f"  + {reason}")
                for risk in item.get("risks", []) or []:
                    lines.append(f"  - {risk}")
                lines.append(f"  Next check: {item.get('next_check') or 'Manual review before entry.'}")

    lines += ["", "=== OPEN OPTIONS POSITION DETECTOR V1 ==="]
    if not open_options:
        lines.append("Open options detector did not run for this report.")
    else:
        summary = open_options.get("summary", {}) or {}
        errors = open_options.get("errors", []) or []
        lines.append(
            f"Accounts checked: {summary.get('account_count', 0)} | "
            f"Total Tradier positions: {summary.get('total_positions', 0)} | "
            f"Option legs: {summary.get('option_leg_count', 0)} | "
            f"Detected calendars: {summary.get('calendar_count', 0)}"
        )
        if errors:
            for error in errors[:3]:
                lines.append(f"  - {error}")
        calendars = open_options.get("calendars", []) or []
        if calendars:
            lines.append("Detected calendar spreads:")
            for cal in calendars:
                lines.append(
                    f"  - {cal.get('underlying', 'UNKNOWN')} {option_money(cal.get('strike'))} "
                    f"{str(cal.get('option_type') or 'call').upper()} calendar | "
                    f"Qty {option_money(cal.get('quantity'))} | "
                    f"Short {cal.get('front_expiration')} ({cal.get('front_dte')} DTE) / "
                    f"Long {cal.get('back_expiration')} ({cal.get('back_dte')} DTE) | "
                    f"Current mid debit {option_money(cal.get('current_mid_debit'))} | "
                    f"Action: {cal.get('action', 'MONITOR')}"
                )
                for risk in cal.get('risks', []) or []:
                    lines.append(f"    - {risk}")
                lines.append(f"    Next check: {cal.get('next_check') or 'Monitor daily.'}")
        else:
            lines.append("No open calendar spreads detected from Tradier positions.")

    lines += ["", "=== CALENDAR LIFECYCLE CHECK V1 ==="]
    if not lifecycle_checks:
        lines.append("Calendar lifecycle checker did not run for this report.")
    else:
        summary = lifecycle_checks.get("summary", {}) or {}
        lines.append(
            f"Open calendars checked: {summary.get('calendar_count', 0)} | "
            f"Urgent: {summary.get('urgent_count', 0)} | "
            f"Exit-review: {summary.get('exit_review_count', 0)}"
        )
        checks = lifecycle_checks.get("checks", []) or []
        if not checks:
            lines.append("No open calendars to lifecycle-check.")
        else:
            for check in checks:
                lines.append(
                    f"{check.get('ticker', 'UNKNOWN')} {option_money(check.get('strike'))} "
                    f"{str(check.get('option_type') or 'call').upper()} calendar | "
                    f"Action: {check.get('action', 'HOLD / MONITOR')} | "
                    f"Current debit {option_money(check.get('current_mid_debit'))} | "
                    f"Entry debit est. {option_money(check.get('entry_debit_estimate'))} | "
                    f"P/L est. {signed_pct(check.get('estimated_pnl_pct'))} | "
                    f"Short DTE {check.get('front_dte')} | "
                    f"Short moneyness {signed_pct(check.get('short_leg_moneyness_pct'))} | "
                    f"Earnings {check.get('earnings_date') or 'unknown'} ({check.get('earnings_session') or 'Unknown'})"
                )
                for reason in check.get("reasons", []) or []:
                    lines.append(f"  + {reason}")
                for risk in check.get("risks", []) or []:
                    lines.append(f"  - {risk}")
                lines.append(f"  Next check: {check.get('next_check') or 'Monitor daily.'}")

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
        "account grouping, relevance-scored news, Finnhub price-history metrics,",
        "and Tradier quote/options-chain snapshots, including an earnings-discovery calendar screener,",
        "watchlist candidate review, read-only open options-position detection,",
        "earnings timestamp context, and calendar lifecycle checks for Tradier-held option legs",
        "so the portfolio can be evaluated using numerical and strategic qualifiers.",
        "",
        "Current scoring style: Aggressive Quality-Momentum Snapshot v2.",
        "This version uses current position data, allocation risk, duplicate exposure,",
        "asset risk, structured news, price momentum, relative strength, 50/200-day",
        "trend state, 52-week high/low distance, volatility, and liquidity.",
        "It does not yet include fundamentals, earnings surprises, analyst revisions,",
        "or persistent trade-memory yet. Tradier data is now used for",
        "quote/options liquidity, earnings-driven long-call calendar candidate screening,",
        "watchlist idea triage, detecting existing Tradier-held calendar spreads,",
        "and basic hold/exit review checks.",
        "",
        "Provide a practical daily portfolio briefing based only on the data above.",
        "Include: overall portfolio summary, strongest add/hold candidates, names to",
        "avoid adding to, major risk flags, news relevance, and practical watch items.",
        "Keep it under 600 words, plain text.",
    ]

    return "\n".join(lines)


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

    position_rows = format_position_rows(positions)
    recommendation_rows = format_recommendation_rows(parsed_recommendations)
    market_rows = format_market_rows(parsed_recommendations)
    news_rows = format_news_rows(parsed_news)
    tradier_rows = format_tradier_rows(parsed_tradier_snapshot)
    calendar_rows = format_calendar_spread_rows(calendar_candidates_from_tradier_snapshot(parsed_tradier_snapshot))
    earnings_calendar_rows = format_earnings_calendar_strategy_rows(earnings_calendar_strategy_from_tradier_snapshot(parsed_tradier_snapshot))
    open_options_rows = format_open_options_rows(open_options_from_tradier_snapshot(parsed_tradier_snapshot))
    lifecycle_rows = format_calendar_lifecycle_rows(calendar_lifecycle_from_tradier_snapshot(parsed_tradier_snapshot))
    earnings_rows = format_earnings_rows(earnings_events_from_tradier_snapshot(parsed_tradier_snapshot))
    watchlist_rows = format_watchlist_review_rows(watchlist_review_from_tradier_snapshot(parsed_tradier_snapshot))
    earnings_discovery_rows = format_earnings_trade_discovery_rows(earnings_trade_discovery_from_tradier_snapshot(parsed_tradier_snapshot))
    unified_calendar_rows = format_unified_calendar_engine_rows(unified_calendar_trade_engine_from_tradier_snapshot(parsed_tradier_snapshot))
    payload_html = escape(payload)
    log_html = escape("\n".join(parsed_log_lines))
    today = date.today().strftime("%B %d, %Y")

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stock Advisor — {today}</title>
    <style>
        body {{
            font-family: monospace;
            background: #0f0f0f;
            color: #e0e0e0;
            padding: 2rem;
            max-width: 1400px;
            margin: auto;
        }}
        h1 {{ color: #00ff88; }}
        h2 {{
            color: #888;
            border-bottom: 1px solid #333;
            padding-bottom: 4px;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-bottom: 2rem;
        }}
        th {{
            background: #1a1a1a;
            color: #aaa;
            padding: 8px 12px;
            text-align: left;
            vertical-align: top;
        }}
        td {{
            padding: 8px 12px;
            border-bottom: 1px solid #222;
            vertical-align: top;
        }}
        tr:hover td {{ background: #1a1a1a; }}
        a {{ color: #00ff88; }}
        pre {{
            background: #1a1a1a;
            padding: 1.5rem;
            border-radius: 6px;
            white-space: pre-wrap;
            word-break: break-word;
            font-size: 0.85rem;
            line-height: 1.5;
        }}
        .payload {{
            background: #0a1a0a;
            border: 1px solid #00ff8844;
            color: #00ff88;
        }}
        .log {{
            background: #1a0a0a;
            border: 1px solid #ff444444;
            color: #ff8888;
            font-size: 0.78rem;
        }}
        .copy-btn {{
            background: #00ff88;
            color: #000;
            border: none;
            padding: 8px 16px;
            cursor: pointer;
            border-radius: 4px;
            font-family: monospace;
            font-weight: bold;
            margin-bottom: 1rem;
        }}
        .copy-btn:hover {{ background: #00cc66; }}
        .muted {{ color: #999; font-size: 0.9rem; }}
        .score {{ font-weight: bold; }}
        .empty {{ color: #777; font-style: italic; }}
        .pill {{
            display: inline-block;
            padding: 2px 8px;
            border-radius: 999px;
            background: #1f2937;
            color: #e5e7eb;
            font-size: 0.78rem;
            white-space: nowrap;
        }}
        .action-add {{ background: #064e3b; color: #a7f3d0; }}
        .action-hold {{ background: #1e3a8a; color: #bfdbfe; }}
        .action-watch {{ background: #78350f; color: #fde68a; }}
        .action-risk {{ background: #7f1d1d; color: #fecaca; }}
        ul.compact {{ margin: 0; padding-left: 1.2rem; }}
        .yes {{ color: #00ff88; }}
        .no {{ color: #ff8888; }}
        .nowrap {{ white-space: nowrap; }}
        .urgent {{ background: #7f1d1d; color: #fecaca; font-weight: bold; }}
        .candidate {{ background: #064e3b; color: #a7f3d0; }}
    </style>
</head>
<body>
    <h1>📈 Stock Advisor — {today}</h1>
    <p class="muted">
        Aggressive Quality-Momentum Snapshot v2 uses current portfolio data,
        relevance-scored news, Finnhub momentum, relative strength, trend,
        volatility, liquidity, Tradier quote/options snapshots, calendar candidates, earnings timestamps, earnings-calendar strategy scoring, watchlist candidate review, open-options detection, and calendar lifecycle checks. Fundamentals, persistence, and full options strategy scoring will be added later.
    </p>

    <h2>Portfolio Advisor Scores ({len(parsed_recommendations)} scored)</h2>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Account</th>
            <th>Score</th>
            <th>Action</th>
            <th>Allocation</th>
            <th>G/L</th>
            <th>Trend/Momentum</th>
            <th>Reasons</th>
            <th>Risks / Limits</th>
            <th>Next Check</th>
        </tr>
        {recommendation_rows}
    </table>

    <h2>Market Momentum / Trend</h2>
    <table>
        <tr>
            <th>Ticker</th>
            <th>As Of</th>
            <th>1M</th>
            <th>3M</th>
            <th>6M</th>
            <th>12M</th>
            <th>6M RS</th>
            <th>Above 50D</th>
            <th>Above 200D</th>
            <th>52W High Dist.</th>
            <th>Vol30</th>
            <th>AvgVol30</th>
        </tr>
        {market_rows}
    </table>

    <h2>Positions ({len(positions)} total)</h2>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Account</th>
            <th>Quantity</th>
            <th>Avg Cost</th>
            <th>Current</th>
            <th>G/L</th>
            <th>Market Value</th>
        </tr>
        {position_rows}
    </table>


    <h2>Watchlist Stock Candidate Review v2</h2>
    <p class="muted">Robinhood/manual watchlist tickers reviewed primarily as normal stock candidates. Earnings/calendar logic is an overlay only when an actual earnings setup exists. These are not treated as owned positions.</p>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Stock Score / Category</th>
            <th>Portfolio</th>
            <th>Watchlist Source</th>
            <th>Earnings</th>
            <th>Earnings / Calendar Overlay</th>
            <th>Reasons / Next</th>
        </tr>
        {watchlist_rows}
    </table>

    <h2>Earnings Timestamp Provider v1</h2>
    <p class="muted">Read-only earnings-date context for portfolio tickers. Uses the configured earnings provider when available and does not block the run if data is unavailable.</p>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Earnings Date</th>
            <th>Session</th>
            <th>DTE</th>
            <th>Confirmed?</th>
            <th>EPS / Revenue</th>
            <th>Status</th>
        </tr>
        {earnings_rows}
    </table>

    <h2>Earnings Trade Discovery v1</h2>
    <p class="muted">Independent earnings-calendar trade universe. This starts from upcoming earnings events, not your watchlist. Calendar strategy candidates are generated only from this universe.</p>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Earnings</th>
            <th>DTE</th>
            <th>Confirmed?</th>
            <th>Source / Notes</th>
        </tr>
        {earnings_discovery_rows}
    </table>

    <h2>Unified Calendar Trade Engine v1</h2>
    <p class="muted">One workflow for earnings-calendar trades: discover upcoming earnings, pass/fail requirements, propose spreads when valid, rank entry candidates, and review already-entered calendars.</p>
    <table>
        <tr>
            <th>Type</th>
            <th>Ticker / Score</th>
            <th>Earnings / Verdict</th>
            <th>Possible Spread / Current Position</th>
            <th>Requirements</th>
            <th>Entry / Next Action</th>
        </tr>
        {unified_calendar_rows}
    </table>

    <h2>Tradier Quote / Options Snapshot</h2>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Quote</th>
            <th>Expirations</th>
            <th>Sample Chain</th>
            <th>ATM Call</th>
            <th>ATM Put</th>
            <th>Liquidity</th>
        </tr>
        {tradier_rows}
    </table>

    <h2>Calendar Spread Screener v1</h2>
    <p class="muted">Read-only scan for possible new long call calendars. This does not detect open positions or recommend exits yet.</p>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Score / Action</th>
            <th>Structure</th>
            <th>Debit</th>
            <th>Liquidity</th>
            <th>IV / Spread</th>
            <th>Reasons</th>
            <th>Risks / Next</th>
        </tr>
        {calendar_rows}
    </table>

    <h2>Earnings Calendar Strategy v1</h2>
    <p class="muted">Earnings-aware review of calendar candidates. This flags whether the structure captures earnings, whether the short leg spans the event, and whether manual review is urgent.</p>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Score / Action</th>
            <th>Structure</th>
            <th>Earnings Fit</th>
            <th>Debit / Liquidity</th>
            <th>Reasons</th>
            <th>Risks / Next</th>
        </tr>
        {earnings_calendar_rows}
    </table>

    <h2>Open Options Position Detector v1</h2>
    <p class="muted">Read-only Tradier account-position parser. Detects existing calendar spreads only when TRADIER_ACCOUNT_ID/profile access is available.</p>
    <table>
        <tr>
            <th>Status</th>
            <th>Detected Calendar</th>
            <th>Legs</th>
            <th>Current Value</th>
            <th>Risks / Next</th>
        </tr>
        {open_options_rows}
    </table>

    <h2>Calendar Lifecycle Check v1</h2>
    <p class="muted">Read-only hold/exit review for detected open calendars. Exact P/L requires broker cost basis or a later persistent trade-memory module.</p>
    <table>
        <tr>
            <th>Status</th>
            <th>Calendar</th>
            <th>Value / P&L</th>
            <th>Risk State</th>
            <th>Earnings</th>
            <th>Reasons / Risks</th>
            <th>Next Check</th>
        </tr>
        {lifecycle_rows}
    </table>

    <h2>Relevant News</h2>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Score</th>
            <th>Headline</th>
            <th>Source</th>
            <th>Published</th>
            <th>Link</th>
        </tr>
        {news_rows}
    </table>

    <h2>Full Advisor Payload</h2>
    <button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('payload').innerText)">
        Copy to Clipboard
    </button>
    <pre id="payload" class="payload">{payload_html}</pre>

    <h2>Run Log</h2>
    <pre class="log">{log_html}</pre>
</body>
</html>"""


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
        combined = reasons[:2] + risks[:3]
        next_check = escape(str(check.get("next_check") or "Monitor daily."))
        calendar = (
            f"<strong>{ticker} {option_money(check.get('strike'))} {option_type}</strong><br>"
            f"Short {escape(str(check.get('front_expiration') or '—'))} "
            f"({check.get('front_dte') if check.get('front_dte') is not None else '—'} DTE)<br>"
            f"Long {escape(str(check.get('back_expiration') or '—'))} "
            f"({check.get('back_dte') if check.get('back_dte') is not None else '—'} DTE)<br>"
            f"<span class='pill {action_class}'>{action}</span>"
        )
        value = (
            f"Current debit {option_money(check.get('current_mid_debit'))}<br>"
            f"Entry debit est. {option_money(check.get('entry_debit_estimate'))}<br>"
            f"P/L est. {signed_pct(check.get('estimated_pnl_pct'))}<br>"
            f"Value {money(check.get('current_value_estimate'))}"
        )
        risk_state = (
            f"Underlying {money(check.get('underlying_price'))}<br>"
            f"Short moneyness {signed_pct(check.get('short_leg_moneyness_pct'))}<br>"
            f"Short ITM {yes_no(check.get('short_leg_itm'))}"
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
            <td>{value}</td>
            <td>{risk_state}</td>
            <td>{earnings}</td>
            <td>{format_compact_list(combined)}</td>
            <td>{next_check}</td>
        </tr>"""
        status = ""
    return rows

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
        return '<span class="empty">No market data</span>'

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


def unified_calendar_trade_engine_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_unified_calendar_trade_engine", {}) or {}
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
                f"Earnings {earnings.get('earnings_date') or 'unknown'} ({earnings.get('session_label') or 'Unknown'}) | "
                f"{spread_text}"
            )
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

        rows += f"""
        <tr>
            <td>New earnings calendar</td>
            <td class="score"><strong>{ticker}</strong><br>{number(item.get('score'), 1)}</td>
            <td>{earnings_text}</td>
            <td>{spread_text}</td>
            <td>{format_requirement_list(requirements)}</td>
            <td>{entry_plan}</td>
        </tr>"""

    for item in open_rows[:30]:
        ticker = escape(str(item.get("ticker") or "UNKNOWN"))
        verdict = escape(str(item.get("verdict") or "HOLD / MONITOR"))
        verdict_class = _calendar_verdict_class(str(item.get("verdict") or ""))
        next_action = escape(str(item.get("next_action") or "Recheck before market close."))
        structure = escape(str(item.get("structure") or "—"))
        value = escape(str(item.get("value") or "Value unavailable"))
        reasons = [str(r) for r in (item.get("reasons", []) or [])]
        risks = [str(r) for r in (item.get("risks", []) or [])]
        rows += f"""
        <tr>
            <td>Open calendar</td>
            <td class="score"><strong>{ticker}</strong><br>{number(item.get('score'), 1)}</td>
            <td><span class="pill {verdict_class}">{verdict}</span></td>
            <td>{structure}<br><span class="muted">{value}</span></td>
            <td>{format_compact_list(reasons + risks)}</td>
            <td>{next_action}</td>
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


def _calendar_verdict_class(verdict: str) -> str:
    text = str(verdict or "").upper()
    if text.startswith("PASS") or "TAKE PROFIT" in text:
        return "candidate"
    if text.startswith("FAIL") or "AVOID" in text or "CUT" in text:
        return "urgent"
    if "URGENT" in text:
        return "urgent"
    return "action-watch"
