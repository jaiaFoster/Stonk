"""
main.py — Daily stock data orchestrator + Flask trigger endpoint.
Fetches positions + news, formats a prompt payload, returns it as HTML.
Hit /run?token=YOUR_TOKEN to trigger a run and see the full report in the browser.
"""

import os
import traceback
from flask import Flask, request, abort
from datetime import date

app = Flask(__name__)


def format_payload(positions, news_map):
    today = date.today().strftime("%B %d, %Y")
    lines = [
        f"Date: {today}",
        "",
        "=== MY STOCK POSITIONS ===",
    ]
    for p in positions:
        gl = (
            f"{p['gain_loss']:+.2f} ({p['gain_loss_pct']:+.1f}%)"
            if p["gain_loss"] is not None
            else "N/A"
        )
        lines.append(
            f"{p['ticker']}: {p['quantity']:.4f} shares | "
            f"Avg cost ${p['avg_buy_price']:.2f} | "
            f"Current ${p['current_price']:.2f} | "
            f"G/L: {gl} | "
            f"Value: ${p['market_value']:.2f} | "
            f"Account: {p.get('account', 'Unknown')}"
        )
    lines += ["", "=== TODAY'S NEWS ==="]
    for ticker, headlines in news_map.items():
        lines.append(f"{ticker}:")
        for h in headlines:
            lines.append(f"  - {h}")
    lines += [
        "",
        "=== INSTRUCTIONS FOR CLAUDE ===",
        "Please give me a brief daily briefing on my portfolio based on the above.",
        "Include: overall portfolio summary, what today's news means for each position,",
        "and any practical things to watch. Keep it under 400 words, plain text.",
    ]
    return "\n".join(lines)


def format_html(payload, positions, log_lines):
    """Wrap the report in a clean HTML page for browser viewing."""

    # Build positions table rows
    rows = ""
    for p in positions:
        gl_val = p.get("gain_loss")
        gl_pct = p.get("gain_loss_pct")
        if gl_val is not None:
            color = "green" if gl_val >= 0 else "red"
            gl_str = f'<span style="color:{color}">{gl_val:+.2f} ({gl_pct:+.1f}%)</span>'
        else:
            gl_str = "N/A"
            color = "gray"

        mv = p.get("market_value")
        rows += f"""
        <tr>
            <td><strong>{p['ticker']}</strong></td>
            <td>{p.get('account', '—')}</td>
            <td>{p['quantity']:.4f}</td>
            <td>${p['avg_buy_price']:.2f}</td>
            <td>${p['current_price']:.2f if p['current_price'] else '—'}</td>
            <td>{gl_str}</td>
            <td>${mv:.2f if mv else '—'}</td>
        </tr>"""

    log_html = "\n".join(log_lines)

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stock Briefing — {date.today().strftime("%B %d, %Y")}</title>
    <style>
        body {{ font-family: monospace; background: #0f0f0f; color: #e0e0e0; padding: 2rem; max-width: 1100px; margin: auto; }}
        h1 {{ color: #00ff88; }}
        h2 {{ color: #888; border-bottom: 1px solid #333; padding-bottom: 4px; }}
        table {{ width: 100%; border-collapse: collapse; margin-bottom: 2rem; }}
        th {{ background: #1a1a1a; color: #aaa; padding: 8px 12px; text-align: left; }}
        td {{ padding: 8px 12px; border-bottom: 1px solid #222; }}
        tr:hover td {{ background: #1a1a1a; }}
        pre {{ background: #1a1a1a; padding: 1.5rem; border-radius: 6px; white-space: pre-wrap; word-break: break-word; font-size: 0.85rem; line-height: 1.5; }}
        .payload {{ background: #0a1a0a; border: 1px solid #00ff8844; color: #00ff88; }}
        .log {{ background: #1a0a0a; border: 1px solid #ff444444; color: #ff8888; font-size: 0.78rem; }}
        .copy-btn {{ background: #00ff88; color: #000; border: none; padding: 8px 16px; cursor: pointer; border-radius: 4px; font-family: monospace; font-weight: bold; margin-bottom: 1rem; }}
        .copy-btn:hover {{ background: #00cc66; }}
    </style>
</head>
<body>
    <h1>📈 Stock Briefing — {date.today().strftime("%B %d, %Y")}</h1>

    <h2>Positions ({len(positions)} total)</h2>
    <table>
        <tr>
            <th>Ticker</th><th>Account</th><th>Quantity</th>
            <th>Avg Cost</th><th>Current</th><th>G/L</th><th>Market Value</th>
        </tr>
        {rows}
    </table>

    <h2>Full Claude Prompt</h2>
    <button class="copy-btn" onclick="navigator.clipboard.writeText(document.getElementById('payload').innerText)">
        Copy to Clipboard
    </button>
    <pre id="payload" class="payload">{payload}</pre>

    <h2>Run Log</h2>
    <pre class="log">{log_html}</pre>
</body>
</html>"""


def run():
    log = []

    def log_print(msg):
        print(msg, flush=True)
        log.append(msg)

    log_print("=== RUN STARTED ===")

    try:
        from robinhood import get_positions
        log_print("robinhood imported OK")
    except Exception as e:
        log_print(f"IMPORT ERROR robinhood: {e}\n{traceback.format_exc()}")
        return None, [], log

    try:
        from news import get_news_for_tickers
        log_print("news imported OK")
    except Exception as e:
        log_print(f"IMPORT ERROR news: {e}\n{traceback.format_exc()}")
        return None, [], log

    try:
        import config
        log_print("config imported OK")
        log_print(f"ROBINHOOD_USERNAME set: {bool(config.ROBINHOOD_USERNAME)}")
        log_print(f"ROBINHOOD_PASSWORD set: {bool(config.ROBINHOOD_PASSWORD)}")
        log_print(f"NEWS_API_KEY set: {bool(config.NEWS_API_KEY)}")
    except Exception as e:
        log_print(f"IMPORT ERROR config: {e}\n{traceback.format_exc()}")
        return None, [], log

    log_print("Fetching Robinhood positions...")
    try:
        positions = get_positions()
        log_print(f"get_positions returned {len(positions)} positions")
    except Exception as e:
        log_print(f"ERROR in get_positions: {e}\n{traceback.format_exc()}")
        return None, [], log

    if not positions:
        log_print("No positions found or login failed.")
        return None, [], log

    tickers = list(dict.fromkeys(p["ticker"] for p in positions))
    log_print(f"Tickers: {tickers}")

    log_print("Fetching news...")
    try:
        news = get_news_for_tickers(tickers)
        log_print(f"News fetched for {len(news)} tickers")
    except Exception as e:
        log_print(f"ERROR in get_news_for_tickers: {e}\n{traceback.format_exc()}")
        return None, positions, log

    log_print("Formatting payload...")
    try:
        payload = format_payload(positions, news)
        log_print(f"Payload length: {len(payload)} chars")
    except Exception as e:
        log_print(f"ERROR in format_payload: {e}\n{traceback.format_exc()}")
        return None, positions, log

    log_print("=== RUN COMPLETE ===")
    return payload, positions, log


@app.route("/run")
def trigger():
    token = request.args.get("token")
    if token != os.environ.get("RUN_TOKEN"):
        abort(403)

    print("=== /run ENDPOINT HIT ===", flush=True)
    payload, positions, log = run()

    if payload is None:
        error_log = "\n".join(log)
        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Stock Briefing — ERROR</title>
<style>body{{font-family:monospace;background:#0f0f0f;color:#ff8888;padding:2rem;}}
pre{{background:#1a0a0a;padding:1rem;border-radius:6px;white-space:pre-wrap;}}</style>
</head>
<body><h1>Run Failed</h1><pre>{error_log}</pre></body>
</html>""", 500

    return format_html(payload, positions, log), 200


@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
