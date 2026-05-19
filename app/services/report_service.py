"""
app/services/report_service.py — Payload and HTML report formatting.
"""

from __future__ import annotations

from datetime import date
from html import escape
from typing import Any


NewsMap = dict[str, list[dict[str, Any]]]


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


def format_payload(positions: list[dict[str, Any]], news_map: NewsMap) -> str:
    today = date.today().strftime("%B %d, %Y")
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
        "market value, account grouping, and relevance-scored news so the portfolio",
        "can later be evaluated using defined numerical and strategic qualifiers.",
        "",
        "For now, provide a practical daily portfolio briefing based only on the data above.",
        "Include: overall portfolio summary, major winners/losers, news relevance,",
        "and practical watch items. Keep it under 400 words, plain text.",
    ]

    return "\n".join(lines)


def format_html(
    payload: str,
    positions: list[dict[str, Any]],
    news_map_or_log_lines: NewsMap | list[str],
    maybe_log_lines: list[str] | None = None,
) -> str:
    """
    Wrap the report in a clean HTML page for browser viewing.

    Supports both call styles for safety:
    - format_html(payload, positions, log_lines)
    - format_html(payload, positions, news_map, log_lines)
    """
    if maybe_log_lines is None:
        news_map: NewsMap = {}
        log_lines = news_map_or_log_lines if isinstance(news_map_or_log_lines, list) else []
    else:
        news_map = news_map_or_log_lines if isinstance(news_map_or_log_lines, dict) else {}
        log_lines = maybe_log_lines

    rows = format_position_rows(positions)
    news_rows = format_news_rows(news_map)
    payload_html = escape(payload)
    log_html = escape("\n".join(log_lines))
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
            max-width: 1200px;
            margin: auto;
        }}
        h1 {{
            color: #00ff88;
        }}
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
        tr:hover td {{
            background: #1a1a1a;
        }}
        a {{
            color: #00ff88;
        }}
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
        .copy-btn:hover {{
            background: #00cc66;
        }}
        .muted {{
            color: #999;
            font-size: 0.9rem;
        }}
        .score {{
            font-weight: bold;
        }}
        .empty {{
            color: #777;
            font-style: italic;
        }}
    </style>
</head>
<body>
    <h1>📈 Stock Advisor — {today}</h1>
    <p class="muted">
        Portfolio data collection is working toward future numerical and strategic advisor qualifiers.
    </p>

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
        {rows}
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
            link_html = f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">Open</a>' if url else "—"

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
