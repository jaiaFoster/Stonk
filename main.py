"""
main.py — Daily stock data orchestrator + Flask trigger endpoint.
Fetches positions + news, formats a prompt payload, pushes to ntfy.
Shortcuts picks it up and passes it to "Ask Claude".
"""

import os
import sys
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
            f"Value: ${p['market_value']:.2f}"
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


def run():
    print("=== RUN STARTED ===", flush=True)

    print("Importing modules...", flush=True)
    try:
        from robinhood import get_positions
        print("robinhood imported OK", flush=True)
    except Exception as e:
        print(f"IMPORT ERROR robinhood: {e}", flush=True)
        traceback.print_exc()
        return

    try:
        from news import get_news_for_tickers
        print("news imported OK", flush=True)
    except Exception as e:
        print(f"IMPORT ERROR news: {e}", flush=True)
        traceback.print_exc()
        return

    try:
        from notifier import send_to_phone
        print("notifier imported OK", flush=True)
    except Exception as e:
        print(f"IMPORT ERROR notifier: {e}", flush=True)
        traceback.print_exc()
        return

    try:
        import config
        print(f"config imported OK", flush=True)
        print(f"ROBINHOOD_USERNAME set: {bool(config.ROBINHOOD_USERNAME)}", flush=True)
        print(f"ROBINHOOD_PASSWORD set: {bool(config.ROBINHOOD_PASSWORD)}", flush=True)
        print(f"NTFY_TOPIC set: {bool(config.NTFY_TOPIC)}", flush=True)
        print(f"NEWS_API_KEY set: {bool(config.NEWS_API_KEY)}", flush=True)
    except Exception as e:
        print(f"IMPORT ERROR config: {e}", flush=True)
        traceback.print_exc()
        return

    print("Fetching Robinhood positions...", flush=True)
    try:
        positions = get_positions()
        print(f"get_positions returned {len(positions)} positions", flush=True)
    except Exception as e:
        print(f"ERROR in get_positions: {e}", flush=True)
        traceback.print_exc()
        return

    if not positions:
        print("No positions found or login failed.", flush=True)
        return

    tickers = list(dict.fromkeys(p["ticker"] for p in positions))
    print(f"Tickers: {tickers}", flush=True)

    print("Fetching news...", flush=True)
    try:
        news = get_news_for_tickers(tickers)
        print(f"News fetched for {len(news)} tickers", flush=True)
    except Exception as e:
        print(f"ERROR in get_news_for_tickers: {e}", flush=True)
        traceback.print_exc()
        return

    print("Formatting payload...", flush=True)
    try:
        payload = format_payload(positions, news)
        print(f"Payload length: {len(payload)} chars", flush=True)
    except Exception as e:
        print(f"ERROR in format_payload: {e}", flush=True)
        traceback.print_exc()
        return

    print("Sending to phone...", flush=True)
    try:
        send_to_phone(payload)
        print("send_to_phone completed", flush=True)
    except Exception as e:
        print(f"ERROR in send_to_phone: {e}", flush=True)
        traceback.print_exc()
        return

    print("=== RUN COMPLETE ===", flush=True)


@app.route("/run")
def trigger():
    token = request.args.get("token")
    if token != os.environ.get("RUN_TOKEN"):
        abort(403)
    print("=== /run ENDPOINT HIT ===", flush=True)
    try:
        run()
    except Exception as e:
        print(f"UNHANDLED ERROR in run(): {e}", flush=True)
        traceback.print_exc()
        return f"ERROR: {e}", 500
    return "OK", 200


@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
