"""
app/main.py — Flask application routes for Algo Stock Advisor.

This file owns the web layer only:
- /run token validation
- run locking
- converting pipeline results into HTTP responses
- /health endpoint

Portfolio fetching, news fetching, and report rendering live in services and
providers so the app can grow into a modular advisor system.
"""

import threading
from html import escape

from flask import Flask, abort, request

from app import config
from app.services.analysis_service import run_portfolio_pipeline
from app.services.report_service import format_html

app = Flask(__name__)

# Prevent overlapping /run calls from colliding with Robinhood login/session state.
RUN_LOCK = threading.Lock()


def run():
    """
    Backward-compatible run function.

    Returns:
        tuple[str | None, list[dict], list[str]]: payload, positions, log lines.
    """
    return run_portfolio_pipeline()


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
        payload, positions, log = run_portfolio_pipeline()

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

        return format_html(payload, positions, log), 200

    finally:
        RUN_LOCK.release()


@app.route("/health")
def health():
    return "OK", 200
