"""
app/services/report_service.py — Payload and HTML report formatting.
"""

from datetime import date
from html import escape


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


def format_payload(positions, news_map):
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

    lines += ["", "=== TODAY'S NEWS ==="]

    for ticker, headlines in news_map.items():
        lines.append(f"{ticker}:")
        for h in headlines:
            lines.append(f"  - {h}")

    lines += [
        "",
        "=== ADVISOR CONTEXT ===",
        "This project is intended to gather portfolio data, current prices, gain/loss,",
        "market value, account grouping, and relevant news so the portfolio can later",
        "be evaluated using defined numerical and strategic qualifiers.",
        "",
        "For now, provide a practical daily portfolio briefing based only on the data above.",
        "Include: overall portfolio summary, major winners/losers, news relevance,",
        "and practical watch items. Keep it under 400 words, plain text.",
    ]

    return "\n".join(lines)


def format_html(payload, positions, log_lines):
    """Wrap the report in a clean HTML page for browser viewing."""

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
            max-width: 1100px;
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
        }}
        td {{
            padding: 8px 12px;
            border-bottom: 1px solid #222;
        }}
        tr:hover td {{
            background: #1a1a1a;
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

    <h2>Full Advisor Payload</h2>
    <button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('payload').innerText)">
        Copy to Clipboard
    </button>
    <pre id="payload" class="payload">{payload_html}</pre>

    <h2>Run Log</h2>
    <pre class="log">{log_html}</pre>
</body>
</html>"""
