"""
app/services/report_service.py — Payload and HTML report formatting.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from app.services.report_assets import REPORT_CSS, collapsible_pre


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

    lines += ["", "=== DAILY OPPORTUNITY ENGINE V1 ==="]
    lines.extend(format_daily_opportunity_text(daily_opportunity))

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
    portfolio_gap_rows = format_portfolio_gap_rows(portfolio_gap_from_tradier_snapshot(parsed_tradier_snapshot))
    stock_momentum_rows = format_stock_momentum_rows(stock_momentum_from_tradier_snapshot(parsed_tradier_snapshot))
    daily_opportunity_rows = format_daily_opportunity_rows(daily_opportunity_from_tradier_snapshot(parsed_tradier_snapshot))
    calendar_ranking_rows = format_calendar_ranking_rows(calendar_ranking_from_tradier_snapshot(parsed_tradier_snapshot))
    earnings_mini_backtest_rows = format_earnings_mini_backtest_rows(earnings_mini_backtest_from_tradier_snapshot(parsed_tradier_snapshot))
    pipeline_status = pipeline_status_from_tradier_snapshot(parsed_tradier_snapshot)
    pipeline_status_rows = format_pipeline_status_rows(pipeline_status)
    pipeline_summary_html = format_pipeline_summary(pipeline_status)
    payload_html = escape(payload)
    log_html = escape("\n".join(parsed_log_lines))
    payload_debug_html = collapsible_pre("Full Advisor Payload", payload, "payload", "payload")
    log_debug_html = collapsible_pre("Run Log", "\n".join(parsed_log_lines), None, "log")
    today = date.today().strftime("%B %d, %Y")

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Stock Advisor — {today}</title>
    <style>
        {REPORT_CSS}
    </style>
</head>
<body>
    <h1>📈 Stock Advisor — {today}</h1>
    <p class="muted top-note">
        Aggressive Quality-Momentum Snapshot v2 uses current portfolio data,
        relevance-scored news, market momentum/trend, watchlist stock review,
        and a unified calendar trade engine that combines earnings discovery, spread screening, open-calendar detection, and lifecycle next actions. Fundamentals and deeper options strategy scoring will be added later. Manual trade entry is intentionally avoided; active trades should come from broker detection.
    </p>

    <nav class="quick-nav" aria-label="Report sections">
        <a href="#daily-opportunity">Daily</a>
        <a href="#calendar-engine">Active Trades</a>
        <a href="#portfolio-scores">Portfolio</a>
        <a href="#stock-momentum">Stock Ideas</a>
        <a href="#portfolio-gap">Sector Gaps</a>
        <a href="#calendar-engine">Calendars</a>
        <a href="#calendar-ranking">Ranking</a>
        <a href="#monitor-details">Monitor</a>
        <a href="#debug-output">Debug</a>
    </nav>

    <h2 id="daily-opportunity">Daily Opportunity Engine v1</h2>
    <p class="muted">One ranked action list combining calendar trades, stock momentum adds, portfolio-gap ideas, and risk review items.</p>
    <table>
        <tr>
            <th>Type</th>
            <th>Ticker / Score</th>
            <th>Action</th>
            <th>Why</th>
            <th>Next Step</th>
            <th>Source</th>
        </tr>
        {daily_opportunity_rows}
    </table>


    <h2 id="portfolio-scores">Portfolio Advisor Scores ({len(parsed_recommendations)} scored)</h2>
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

    <h2 id="monitor-details">Monitor Details</h2>
    <p class="muted">Detailed market, position, watchlist, and news tables. These are primarily for verification and deeper review.</p>

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



    <h2 id="stock-momentum">Stock Momentum Add Strategy v1</h2>
    <p class="muted">Normal-stock entry strategy for portfolio and watchlist names. It uses market trend/momentum when available and separates consider-add from add-on-pullback/watch/avoid.</p>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Score / Action</th>
            <th>Portfolio</th>
            <th>Trend</th>
            <th>Reasons</th>
            <th>Risks</th>
            <th>Next Check</th>
        </tr>
        {stock_momentum_rows}
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

    <h2 id="portfolio-gap">Portfolio Gap / Sector Suggestions v1</h2>
    <p class="muted">Aggressive-growth sector/theme exposure, macro-priority buckets, risk buckets, and watchlist suggestions. This is stock-focused and separate from the calendar trade engine.</p>
    {portfolio_gap_rows}

    <h2 id="calendar-engine">Unified Calendar Trade Engine v1</h2>
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

    <h2 id="calendar-ranking">Calendar Ranking v2</h2>
    <p class="muted">Ranks discovered earnings-calendar candidates. Mini-backtest eligibility requires all core criteria to pass.</p>
    <table>
        <tr>
            <th>Ticker / Score</th>
            <th>Action</th>
            <th>Entry Timing</th>
            <th>Criteria</th>
            <th>Reasons / Risks</th>
            <th>Next</th>
        </tr>
        {calendar_ranking_rows}
    </table>

    <h2 id="earnings-backtest">Earnings Mini-Backtest v1</h2>
    <p class="muted">Candle-based historical earnings move review. Runs only for fully-qualified Calendar Ranking v2 candidates.</p>
    <table>
        <tr>
            <th>Ticker</th>
            <th>Events</th>
            <th>Avg / Max Move</th>
            <th>Gap / Run-up</th>
            <th>Interpretation</th>
            <th>Notes</th>
        </tr>
        {earnings_mini_backtest_rows}
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

    <div class="debug-section" id="debug-output">
        <h2>Debug / Copyable Output</h2>
        <details class="section-details">
            <summary>Pipeline Status</summary>
            {pipeline_summary_html}
            <table>
                <tr>
                    <th>Status</th>
                    <th>Step</th>
                    <th>Message</th>
                    <th>Duration</th>
                    <th>Finished</th>
                </tr>
                {pipeline_status_rows}
            </table>
        </details>
        <button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('payload').innerText)">
            Copy Advisor Payload
        </button>
        {payload_debug_html}
        {log_debug_html}
    </div>
</body>
</html>"""



def pipeline_status_from_tradier_snapshot(tradier_snapshot: TradierSnapshot | None) -> dict[str, Any]:
    if not tradier_snapshot:
        return {}
    raw = tradier_snapshot.get("_pipeline_status", {}) or {}
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
