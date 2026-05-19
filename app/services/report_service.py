"""
app/services/report_service.py — Payload and HTML report formatting.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any


NewsMap = dict[str, list[dict[str, Any]]]
Recommendations = list[dict[str, Any]]


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


def format_payload(
    positions: list[dict[str, Any]],
    news_map: NewsMap,
    recommendations: Recommendations | None = None,
) -> str:
    today = date.today().strftime("%B %d, %Y")
    recommendations = recommendations or []

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

    lines += ["", "=== PORTFOLIO SCORING V1 ==="]

    if not recommendations:
        lines.append("No portfolio scoring recommendations generated.")
    else:
        for rec in recommendations:
            reasons = rec.get("reasons", []) or []
            risks = rec.get("risks", []) or []
            score = rec.get("score")
            lines.append(
                f"{rec.get('ticker', 'UNKNOWN')} ({rec.get('account', 'Unknown')}): "
                f"Score {number(score, 1)} | Action: {rec.get('action', 'WATCH')} | "
                f"Confidence: {rec.get('confidence', 'Low')} | "
                f"Allocation: {pct(rec.get('allocation_pct'))} | "
                f"G/L: {signed_pct(rec.get('gain_loss_pct'))}"
            )
            if reasons:
                lines.append("  Reasons:")
                for reason in reasons[:3]:
                    lines.append(f"    - {reason}")
            if risks:
                lines.append("  Risks / limits:")
                for risk in risks[:3]:
                    lines.append(f"    - {risk}")
            next_check = rec.get("next_check")
            if next_check:
                lines.append(f"  Next check: {next_check}")

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
        "This project is intended to gather portfolio data, current prices, gain/loss,",
        "market value, account grouping, relevance-scored news, and strategy outputs",
        "so the portfolio can be evaluated using defined numerical and strategic qualifiers.",
        "",
        "Current scoring style: Aggressive Quality-Momentum Snapshot v1.",
        "This v1 score uses current position data, allocation risk, duplicate exposure,",
        "asset risk, and structured news. It does not yet include price trend, relative",
        "strength, fundamentals, earnings surprises, or options data.",
        "",
        "Provide a practical daily portfolio briefing based only on the data above.",
        "Include: overall portfolio summary, major winners/losers, advisor actions,",
        "news relevance, and practical watch items. Keep it under 500 words, plain text.",
    ]

    return "\n".join(lines)


def format_html(
    payload: str,
    positions: list[dict[str, Any]],
    news_map: NewsMap | list[str] | None = None,
    recommendations: Recommendations | list[str] | None = None,
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

    if isinstance(news_map, list) and all(isinstance(item, str) for item in news_map):
        parsed_log_lines = news_map
    elif isinstance(news_map, dict):
        parsed_news = news_map

    if isinstance(recommendations, list):
        if all(isinstance(item, str) for item in recommendations):
            parsed_log_lines = recommendations
        else:
            parsed_recommendations = recommendations  # type: ignore[assignment]

    if log_lines is not None:
        parsed_log_lines = log_lines

    position_rows = format_position_rows(positions)
    recommendation_rows = format_recommendation_rows(parsed_recommendations)
    news_rows = format_news_rows(parsed_news)
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
            max-width: 1300px;
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
    </style>
</head>
<body>
    <h1>📈 Stock Advisor — {today}</h1>
    <p class="muted">
        Aggressive Quality-Momentum Snapshot v1 uses current portfolio data, allocation risk,
        duplicate exposure, asset risk, and relevance-scored news. Trend, fundamentals,
        earnings, and options data will be added later.
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
            <th>Reasons</th>
            <th>Risks / Limits</th>
            <th>Next Check</th>
        </tr>
        {recommendation_rows}
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
            <td colspan="9" class="empty">No portfolio advisor scores generated.</td>
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

        rows += f"""
        <tr>
            <td><strong>{ticker}</strong></td>
            <td>{account}</td>
            <td class="score">{number(rec.get('score'), 1)}<br><span class="muted">{confidence}</span></td>
            <td><span class="pill {action_class}">{escape(action)}</span></td>
            <td>{pct(rec.get('allocation_pct'))}<br><span class="muted">{money(rec.get('position_value'))}</span></td>
            <td>{signed_pct(rec.get('gain_loss_pct'))}</td>
            <td>{format_compact_list(reasons)}</td>
            <td>{format_compact_list(risks)}</td>
            <td>{next_check}</td>
        </tr>"""

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
