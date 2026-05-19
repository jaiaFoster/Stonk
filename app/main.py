"""
app/main.py — Flask application routes for Algo Stock Advisor.

This file owns the web layer only:
- /run token validation
- loading screen and async run lifecycle
- run locking
- converting pipeline results into HTTP responses
- /health endpoint

The heavy Robinhood/news/scoring pipeline imports are intentionally lazy where
possible so the web server can boot and serve /health even if a provider has a
runtime issue.
"""

from __future__ import annotations

import os
import json
import threading
import time
import traceback
import uuid
from html import escape
from typing import Any

from flask import Flask, abort, jsonify, request

from app import config
from app.utils.log_safety import install_werkzeug_redaction_filter

app = Flask(__name__)
install_werkzeug_redaction_filter()

# Prevent overlapping /run calls from colliding with Robinhood login/session state.
RUN_LOCK = threading.Lock()
RUN_JOBS: dict[str, dict[str, Any]] = {}
ACTIVE_JOB_ID: str | None = None
MAX_JOB_AGE_SECONDS = 60 * 60

print("Algo Stock Advisor Flask app loaded.", flush=True)


PipelineResult = tuple[
    str | None,
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[str],
]


def _requested_run_mode() -> str:
    """Return prod/dev for the current request, defaulting to APP_MODE."""
    requested = (request.args.get("mode") or config.APP_MODE or "prod").strip().lower()
    if requested in {"dev", "development", "test", "testing"}:
        return "dev"
    return "prod"


def run(run_mode: str = "prod") -> PipelineResult:
    """
    Backward-compatible run function.

    Returns:
        tuple: payload, positions, structured news map, recommendations, log lines.
    """
    try:
        from app.services.analysis_service import run_portfolio_pipeline

        return run_portfolio_pipeline(run_mode=run_mode)
    except Exception as e:
        error_log = [
            "=== RUN STARTED ===",
            f"FATAL ERROR before pipeline could run: {e}",
            traceback.format_exc(),
        ]
        return None, [], {}, [], error_log


@app.route("/run")
def trigger():
    token = request.args.get("token")
    if token != config.RUN_TOKEN:
        abort(403)

    run_mode = _requested_run_mode()

    # Escape hatch for old blocking behavior, useful for debugging.
    if request.args.get("sync") == "1":
        return run_sync_response(run_mode=run_mode)

    _cleanup_old_jobs()

    global ACTIVE_JOB_ID

    if not RUN_LOCK.acquire(blocking=False):
        if ACTIVE_JOB_ID and ACTIVE_JOB_ID in RUN_JOBS:
            active_mode = str(RUN_JOBS.get(ACTIVE_JOB_ID, {}).get("mode", "prod"))
            return loading_page(ACTIVE_JOB_ID, token, already_running=True, run_mode=active_mode), 202
        return run_already_active_page(), 409

    job_id = uuid.uuid4().hex
    ACTIVE_JOB_ID = job_id
    RUN_JOBS[job_id] = {
        "status": "running",
        "message": _initial_job_message(run_mode),
        "mode": run_mode,
        "created_at": time.time(),
        "updated_at": time.time(),
        "result": None,
    }

    worker = threading.Thread(target=_run_job, args=(job_id, run_mode), daemon=True)
    worker.start()

    print(f"=== /run ENDPOINT HIT; async job {job_id} started; mode={run_mode} ===", flush=True)
    return loading_page(job_id, token, run_mode=run_mode), 202


@app.route("/run/status/<job_id>")
def run_status(job_id: str):
    token = request.args.get("token")
    if token != config.RUN_TOKEN:
        abort(403)

    job = RUN_JOBS.get(job_id)
    if not job:
        return jsonify(
            {
                "status": "missing",
                "message": "Run not found. Start a new /run request.",
                "redirect_url": None,
                "log_tail": [],
            }
        ), 404

    log_tail: list[str] = []
    result = job.get("result")
    if result:
        try:
            log_tail = list(result[4])[-10:]
        except Exception:
            log_tail = []

    return jsonify(
        {
            "status": job.get("status", "unknown"),
            "message": job.get("message", "Working..."),
            "redirect_url": f"/run/result/{job_id}?token={token}",
            "log_tail": log_tail,
            "mode": job.get("mode", "prod"),
            "updated_at": job.get("updated_at"),
        }
    )


@app.route("/run/result/<job_id>")
def run_result(job_id: str):
    token = request.args.get("token")
    if token != config.RUN_TOKEN:
        abort(403)

    job = RUN_JOBS.get(job_id)
    if not job:
        return missing_run_page(), 404

    status = job.get("status")
    if status == "running":
        return loading_page(job_id, token, already_running=True, run_mode=str(job.get("mode", "prod"))), 202

    result = job.get("result")
    if not result:
        error_log = escape(str(job.get("message", "Run failed without a result.")))
        return error_page("Run Failed", error_log), 500

    payload, positions, news, recommendations, log = result

    if payload is None or status == "error":
        error_log = escape("\n".join(log))
        return error_page("Run Failed", error_log), 500

    try:
        from app.services.report_service import format_html

        return format_html(payload, positions, news, recommendations, log), 200
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
        return error_page("Report Render Failed", error_log), 500


@app.route("/health")
def health():
    return "OK", 200


def _run_job(job_id: str, run_mode: str = "prod") -> None:
    global ACTIVE_JOB_ID

    try:
        RUN_JOBS[job_id]["message"] = _running_job_message(run_mode)
        RUN_JOBS[job_id]["updated_at"] = time.time()

        print(f"=== BACKGROUND RUN {job_id} STARTED; mode={run_mode} ===", flush=True)
        result = run(run_mode=run_mode)
        payload, positions, news, recommendations, log = result

        if payload is None:
            RUN_JOBS[job_id]["status"] = "error"
            RUN_JOBS[job_id]["message"] = "Run failed. Open result page for logs."
        else:
            RUN_JOBS[job_id]["status"] = "complete"
            RUN_JOBS[job_id]["message"] = "Run complete. Loading report."

        RUN_JOBS[job_id]["result"] = result
        RUN_JOBS[job_id]["updated_at"] = time.time()
        print(f"=== BACKGROUND RUN {job_id} FINISHED ===", flush=True)

    except Exception as e:
        RUN_JOBS[job_id]["status"] = "error"
        RUN_JOBS[job_id]["message"] = f"Unexpected run error: {e}"
        RUN_JOBS[job_id]["result"] = (
            None,
            [],
            {},
            [],
            [
                "=== RUN STARTED ===",
                f"UNEXPECTED BACKGROUND ERROR: {e}",
                traceback.format_exc(),
            ],
        )
        RUN_JOBS[job_id]["updated_at"] = time.time()
        print(f"=== BACKGROUND RUN {job_id} ERRORED: {e} ===", flush=True)

    finally:
        ACTIVE_JOB_ID = None
        RUN_LOCK.release()


def run_sync_response(run_mode: str = "prod"):
    if not RUN_LOCK.acquire(blocking=False):
        return run_already_active_page(), 409

    try:
        print(f"=== /run ENDPOINT HIT; sync mode; mode={run_mode} ===", flush=True)
        payload, positions, news, recommendations, log = run(run_mode=run_mode)

        if payload is None:
            error_log = escape("\n".join(log))
            return error_page("Run Failed", error_log), 500

        try:
            from app.services.report_service import format_html

            return format_html(payload, positions, news, recommendations, log), 200
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
            return error_page("Report Render Failed", error_log), 500

    finally:
        RUN_LOCK.release()


def loading_page(
    job_id: str,
    token: str | None,
    already_running: bool = False,
    run_mode: str = "prod",
) -> str:
    safe_job_id = escape(job_id)
    safe_token = token or ""
    clean_mode = "dev" if str(run_mode).lower() == "dev" else "prod"
    mode_badge = "DEV MODE" if clean_mode == "dev" else "PROD MODE"
    title = "Run Already Active" if already_running else "Portfolio Run Started"
    subtitle = (
        "A portfolio run is already in progress. This page will load the result when it finishes."
        if already_running
        else "Your portfolio run has started. This page will load the report automatically."
    )

    # Build JavaScript strings with json.dumps so special characters in the token
    # cannot break the polling script. This also avoids the previous issue where
    # an escaped newline became a real newline inside a JS string literal.
    status_url = f"/run/status/{job_id}?token={safe_token}"
    result_url = f"/run/result/{job_id}?token={safe_token}"
    status_url_js = json.dumps(status_url)
    result_url_js = json.dumps(result_url)

    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Stock Advisor — Loading</title>
    <style>
        body {{
            font-family: monospace;
            background: #0f0f0f;
            color: #e0e0e0;
            padding: 2rem;
            max-width: 900px;
            margin: auto;
        }}
        h1 {{ color: #00ff88; }}
        .card {{
            background: #1a1a1a;
            border: 1px solid #00ff8844;
            border-radius: 10px;
            padding: 1.5rem;
            margin-top: 1rem;
        }}
        .spinner {{
            width: 36px;
            height: 36px;
            border: 4px solid #333;
            border-top-color: #00ff88;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-bottom: 1rem;
        }}
        @keyframes spin {{ to {{ transform: rotate(360deg); }} }}
        .muted {{ color: #999; }}
        .small {{ font-size: 0.85rem; }}
        pre {{
            background: #0f0f0f;
            color: #aaa;
            padding: 1rem;
            border-radius: 6px;
            white-space: pre-wrap;
            min-height: 80px;
        }}
        a {{ color: #00ff88; }}
    </style>
</head>
<body>
    <h1>📈 Stock Advisor — {escape(title)}</h1>
    <div class="card">
        <div class="spinner"></div>
        <p><strong>{escape(mode_badge)}</strong></p>
        <p>{escape(subtitle)}</p>
        <p><strong>Waiting for Robinhood approval if prompted.</strong></p>
        <p class="muted">Check your phone and approve the Robinhood login/device request if one appears.</p>
        <p id="status">Starting...</p>
        <pre id="log">Job: {safe_job_id}</pre>
        <p class="muted small">
            If this page does not move after the logs say the run completed, open the result directly:
            <a id="resultLink" href="#">result page</a>
        </p>
    </div>

    <script>
        const statusUrl = {status_url_js};
        const resultUrl = {result_url_js};
        document.getElementById("resultLink").href = resultUrl;

        async function pollStatus() {{
            try {{
                const response = await fetch(statusUrl, {{ cache: "no-store" }});
                const data = await response.json();
                document.getElementById("status").innerText = data.message || data.status || "Working...";

                if (data.log_tail && data.log_tail.length) {{
                    document.getElementById("log").innerText = data.log_tail.join("\\n");
                }}

                if (data.status === "complete" || data.status === "error") {{
                    window.location.assign(resultUrl);
                    return;
                }}
            }} catch (err) {{
                document.getElementById("status").innerText = "Still running. Waiting for status update...";
            }}
            setTimeout(pollStatus, 3000);
        }}

        setTimeout(pollStatus, 1000);
    </script>
</body>
</html>"""


def _initial_job_message(run_mode: str) -> str:
    if str(run_mode).lower() == "dev":
        return (
            "Starting DEV portfolio run. Robinhood still fetches the portfolio; "
            "external provider calls are limited."
        )
    return "Starting portfolio run. Waiting for Robinhood approval if prompted."


def _running_job_message(run_mode: str) -> str:
    if str(run_mode).lower() == "dev":
        return (
            "Running DEV mode. Waiting for Robinhood approval if prompted — "
            "external API calls are limited."
        )
    return "Running. Waiting for Robinhood approval if prompted — check your phone."


def run_already_active_page() -> str:
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
</html>"""


def missing_run_page() -> str:
    return """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Stock Advisor — Run Missing</title></head>
<body style="font-family:monospace;background:#0f0f0f;color:#ff8888;padding:2rem;">
    <h1>Run Not Found</h1>
    <p>This run result is no longer available. Start a new /run request.</p>
</body>
</html>"""


def error_page(title: str, error_log: str) -> str:
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
    <h1>{escape(title)}</h1>
    <pre>{error_log}</pre>
</body>
</html>"""


def _cleanup_old_jobs() -> None:
    now = time.time()
    expired = [
        job_id
        for job_id, job in RUN_JOBS.items()
        if now - float(job.get("created_at", now)) > MAX_JOB_AGE_SECONDS
    ]
    for job_id in expired:
        RUN_JOBS.pop(job_id, None)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Algo Stock Advisor on 0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port)
