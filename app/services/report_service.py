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
        lines.append("No Finnhub market metrics available for this run.")
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

    lines += ["", "=== TRADIER OPTIONS SNAPSHOT ==="]
    if not tradier_snapshot:
        lines.append("No Tradier quote/options data available for this run.")
    else:
        for ticker, data in tradier_snapshot.items():
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
        "and Tradier quote/options-chain snapshots",
        "so the portfolio can be evaluated using numerical and strategic qualifiers.",
        "",
        "Current scoring style: Aggressive Quality-Momentum Snapshot v2.",
        "This version uses current position data, allocation risk, duplicate exposure,",
        "asset risk, structured news, price momentum, relative strength, 50/200-day",
        "trend state, 52-week high/low distance, volatility, and liquidity.",
        "It does not yet include fundamentals, earnings surprises, analyst revisions,",
        "or full options-chain strategy scoring yet. Tradier data is currently used as",
        "a connectivity/options-liquidity snapshot for future calendar spread scanning.",
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
    </style>
</head>
<body>
    <h1>📈 Stock Advisor — {today}</h1>
    <p class="muted">
        Aggressive Quality-Momentum Snapshot v2 uses current portfolio data,
        relevance-scored news, Finnhub momentum, relative strength, trend,
        volatility, liquidity, and Tradier quote/options snapshots. Fundamentals, earnings, and full options strategy scoring will be added later.
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
                <td colspan="11" class="empty">No Finnhub market data: {error}</td>
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
