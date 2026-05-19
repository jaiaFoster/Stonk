"""
app/main.py — Flask application routes for Algo Stock Advisor.

This file owns the web layer only:
- /run token validation
- run locking
- converting pipeline results into HTTP responses
- /health endpoint

Important deployment note:
This file can now be run directly with `python app/main.py` OR imported by
Gunicorn/Railway through `main:app` or `app.main:app`.

The heavy Robinhood/news pipeline imports are intentionally lazy. That keeps the
web server able to boot and serve /health even if a provider has a runtime issue.
"""

from __future__ import annotations

import os
import threading
import traceback
from html import escape
from typing import Any

from flask import Flask, abort, request

from app import config

app = Flask(__name__)

# Prevent overlapping /run calls from colliding with Robinhood login/session state.
RUN_LOCK = threading.Lock()

print("Algo Stock Advisor Flask app loaded.", flush=True)


def run() -> tuple[str | None, list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]:
    """
    Backward-compatible run function.

    Returns:
        tuple: payload, positions, structured news map, log lines.
    """
    try:
        from app.services.analysis_service import run_portfolio_pipeline

        return run_portfolio_pipeline()
    except Exception as e:
        error_log = [
            "=== RUN STARTED ===",
            f"FATAL ERROR before pipeline could run: {e}",
            traceback.format_exc(),
        ]
        return None, [], {}, error_log


@app.route("/run")
def trigger():
    token = request.args.get("token")

    if token != config.RUN_TOKEN:
        abort(403)

    if not RUN_LOCK.acquire(blocking=False):
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stock Advisor — Run Already Active</title>
    <style>
        body {
            font-family: monospace;
            background: #0f0f0f;
            color: #ffcc66;
            padding: 2rem;
        }
    </style>
</head>
<body>
    <h1>Run Already Active</h1>
    <p>A portfolio run is already in progress. Try again after the current run finishes.</p>
</body>
</html>""", 409

    try:
        print("=== /run ENDPOINT HIT ===", flush=True)
        payload, positions, news, log = run()

        if payload is None:
            error_log = escape("\n".join(log))
            return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stock Advisor — ERROR</title>
    <style>
        body {{
            font-family: monospace;
            background: #0f0f0f;
            color: #ff8888;
            padding: 2rem;
        }}
        pre {{
            background: #1a0a0a;
            padding: 1rem;
            border-radius: 6px;
            white-space: pre-wrap;
        }}
    </style>
</head>
<body>
    <h1>Run Failed</h1>
    <pre>{error_log}</pre>
</body>
</html>""", 500

        try:
            from app.services.report_service import format_html

            return format_html(payload, positions, news, log), 200
        except Exception as e:
            error_log = escape(
                "\n".join(
                    [
                        "=== REPORT RENDER FAILED ===",
                        f"ERROR: {e}",
                        traceback.format_exc(),
                        "",
                        "=== PIPELINE LOG BEFORE RENDER FAILURE ===",
                        *log,
                    ]
                )
            )
            return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stock Advisor — Render Error</title>
    <style>
        body {{
            font-family: monospace;
            background: #0f0f0f;
            color: #ff8888;
            padding: 2rem;
        }}
        pre {{
            background: #1a0a0a;
            padding: 1rem;
            border-radius: 6px;
            white-space: pre-wrap;
        }}
    </style>
</head>
<body>
    <h1>Report Render Failed</h1>
    <pre>{error_log}</pre>
</body>
</html>""", 500

    finally:
        RUN_LOCK.release()


@app.route("/health")
def health():
    return "OK", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Algo Stock Advisor on 0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port)
