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
from datetime import datetime, timezone
from html import escape
from typing import Any

from flask import Flask, abort, g, jsonify, redirect, render_template_string, request, url_for

from app import config
from app.utils.log_safety import install_werkzeug_redaction_filter

app = Flask(__name__)
install_werkzeug_redaction_filter()

from app.api.advisor import advisor_bp
app.register_blueprint(advisor_bp)

from app.api.admin import admin_bp
app.register_blueprint(admin_bp)

from app.api.user import user_bp
app.register_blueprint(user_bp)

# 28A: set secret key for signed cookies and seed admin user on first boot
app.secret_key = config.SESSION_SECRET_KEY or os.urandom(32)
try:
    from app.db.users import seed_admin_if_needed
    seed_admin_if_needed()
except Exception as _seed_exc:
    print(f"28A seed: {_seed_exc}", flush=True)

# Prevent overlapping /run calls from colliding with Robinhood login/session state.
RUN_LOCK = threading.Lock()
RUN_STATE_LOCK = threading.Lock()
ACTIVE_TRADE_REFRESH_LOCK = threading.Lock()
RUN_JOBS: dict[str, dict[str, Any]] = {}
ACTIVE_JOB_ID: str | None = None
MAX_JOB_AGE_SECONDS = 60 * 60
APP_BOOTED_AT = datetime.now(timezone.utc).isoformat()

print("Algo Stock Advisor Flask app loaded.", flush=True)


PipelineResult = tuple[
    str | None,
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[str],
]


def _requested_run_mode() -> str:
    """Return prod/dev for the current request, defaulting to APP_MODE."""
    requested = (request.args.get("mode") or config.APP_MODE or "prod").strip().lower()
    if requested in {"dev", "development", "test", "testing"}:
        return "dev"
    return "prod"


def _valid_run_token(token: str | None) -> bool:
    """Require RUN_TOKEN to be configured and matched by the request."""
    return bool(config.RUN_TOKEN) and token == config.RUN_TOKEN


def _valid_dev_token(token: str | None) -> bool:
    """Allow legacy DEV_API_TOKEN/RUN_TOKEN or any active admin user key/session (28A)."""
    if not token:
        return False
    # Legacy: DEV_API_TOKEN / RUN_TOKEN
    expected = config.DEV_API_TOKEN or config.RUN_TOKEN
    if expected and token == expected:
        return True
    # 28A: user must be admin
    try:
        from app.auth import _resolve_user, _is_legacy_token
        if _is_legacy_token(token):
            return True
        user = _resolve_user(token)
        return bool(user and user.get("is_active") and user.get("is_admin"))
    except Exception:
        return False


def _requested_dashboard_view() -> str:
    """Return shell/full without changing the underlying stored report."""
    requested = str(request.args.get("view") or request.args.get("detail") or config.DASHBOARD_DEFAULT_VIEW).strip().lower()
    return "full" if requested in {"full", "detail"} else "shell"


def _record_usage(event_type: str, **kwargs: Any) -> None:
    try:
        from app.services.usage_telemetry_service import record_usage_event

        record_usage_event(event_type, **kwargs)
    except Exception as exc:
        print(f"UsageTelemetry route warning: {exc}", flush=True)


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
        return None, [], {}, [], {}, error_log




@app.route("/")
def home():
    token = request.args.get("token")
    if _valid_run_token(token):
        try:
            from app.services.report_snapshot_service import ReportSnapshotRepository
            from app.services.report_service import format_html
            from app.services.data_freshness_service import build_data_freshness_summary
            from app.services.run_manifest_repository import RunManifestRepository

            repository = ReportSnapshotRepository(log_print=lambda message: print(message, flush=True))
            dashboard_view = _requested_dashboard_view()
            snapshot = repository.latest_success(include_full=dashboard_view == "full")
            if snapshot:
                summary = repository.load_summary(snapshot, full=dashboard_view == "full")
                report = summary.get("report_data") or {}
                payload = repository.load_payload(snapshot, full=dashboard_view == "full")
                report_snapshot = report.get("tradier_snapshot", {}) or {}
                report_snapshot["_report_snapshot"] = {
                    "run_id": snapshot.get("run_id"),
                    "generated_at": snapshot.get("completed_at"),
                    "market_data_refreshed_at": snapshot.get("completed_at"),
                    "active_trades_refreshed_at": (summary.get("active_trades_refreshed_at") or "not separately refreshed"),
                    "source": "cached server snapshot",
                    "freshness": build_data_freshness_summary(snapshot, summary, RunManifestRepository().latest()),
                }
                print("Dashboard: rendered persistent snapshot without provider calls", flush=True)
                _record_usage(
                    "dashboard_load",
                    source="cached_dashboard",
                    run_id=snapshot.get("run_id"),
                    metadata={"dashboard_view": dashboard_view, "route_name": "home"},
                )
                return format_html(
                    payload,
                    report.get("positions", []),
                    report.get("news", {}),
                    report.get("recommendations", []),
                    report_snapshot,
                    report.get("log", []),
                    view=dashboard_view,
                ), 200
        except Exception as exc:
            print(f"Latest report snapshot unavailable: {exc}", flush=True)
    return _render_home_page(), 200


@app.route("/refresh-market-data", methods=["GET", "POST"])
def refresh_market_data():
    token = request.values.get("token") or request.args.get("token")
    if not _valid_run_token(token):
        abort(403)
    _recover_stale_run_if_needed()
    if RUN_LOCK.locked():
        return jsonify({"status": "already_running", "message": "Refresh already in progress."}), 409
    mode = _requested_run_mode()
    return jsonify({
        "status": "ready",
        "scope": "merged_strategy_requirements",
        "message": "Start refresh using redirect_url. Existing successful snapshot remains visible until completion.",
        "redirect_url": f"/run?token={token}&mode={mode}",
    }), 202

@app.route("/run")
def trigger():
    token = request.args.get("token")
    if not _valid_run_token(token):
        abort(403)

    run_mode = _requested_run_mode()

    # Escape hatch for old blocking behavior, useful for debugging.
    if request.args.get("sync") == "1":
        return run_sync_response(run_mode=run_mode)

    _cleanup_old_jobs()

    global ACTIVE_JOB_ID
    _recover_stale_run_if_needed()
    with RUN_STATE_LOCK:
        job_lock = RUN_LOCK
        if not job_lock.acquire(blocking=False):
            if ACTIVE_JOB_ID and ACTIVE_JOB_ID in RUN_JOBS:
                active_mode = str(RUN_JOBS.get(ACTIVE_JOB_ID, {}).get("mode", "prod"))
                return loading_page(ACTIVE_JOB_ID, token, already_running=True, run_mode=active_mode), 202
            return run_already_active_page(), 409

        job_id = uuid.uuid4().hex
        now = time.time()
        ACTIVE_JOB_ID = job_id
        RUN_JOBS[job_id] = {
            "status": "running",
            "message": _initial_job_message(run_mode),
            "mode": run_mode,
            "created_at": now,
            "started_at": now,
            "heartbeat_at": now,
            "updated_at": now,
            "timeout_reason": None,
            "failed_stage": None,
            "retry_safe": False,
            "result": None,
        }

    worker = threading.Thread(target=_run_job, args=(job_id, run_mode, job_lock), daemon=True)
    worker.start()

    print(f"=== /run ENDPOINT HIT; async job {job_id} started; mode={run_mode} ===", flush=True)
    return loading_page(job_id, token, run_mode=run_mode), 202


@app.route("/run/status/<job_id>")
def run_status(job_id: str):
    token = request.args.get("token")
    if not _valid_run_token(token):
        abort(403)

    _recover_stale_run_if_needed()
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
            log_tail = list(result[5])[-10:]
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
            "started_at": job.get("started_at"),
            "heartbeat_at": job.get("heartbeat_at"),
            "timeout_reason": job.get("timeout_reason"),
            "failed_stage": job.get("failed_stage"),
            "retry_safe": bool(job.get("retry_safe")),
            "run_lock": _run_lock_status(),
        }
    )


@app.route("/run/result/<job_id>")
def run_result(job_id: str):
    token = request.args.get("token")
    if not _valid_run_token(token):
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

    payload, positions, news, recommendations, tradier_snapshot, log = result

    if payload is None or status == "error":
        error_log = escape("\n".join(log))
        return error_page("Run Failed", error_log), 500

    try:
        from app.services.report_service import format_html

        return format_html(
            payload, positions, news, recommendations, tradier_snapshot, log,
            view=_requested_dashboard_view(),
        ), 200
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




@app.route("/trades")
def trades_page():
    token = request.args.get("token")
    if not _valid_run_token(token):
        abort(403)
    return _render_manual_trade_deprecated_page(token or ""), 410


@app.route("/trades/add", methods=["GET", "POST"])
def trades_add():
    token = request.values.get("token") or request.args.get("token")
    if not _valid_run_token(token):
        abort(403)
    return jsonify(
        {
            "status": "disabled",
            "error": "Manual trade entry is disabled. Algo Stock Advisor is a read-only viewing tool; open calendars must be auto-detected from broker option positions.",
        }
    ), 410


@app.route("/trades/close", methods=["GET", "POST"])
def trades_close():
    token = request.values.get("token") or request.args.get("token")
    if not _valid_run_token(token):
        abort(403)
    return jsonify(
        {
            "status": "disabled",
            "error": "Manual trade closing is disabled. Lifecycle actions are advisory and based on auto-detected broker positions.",
        }
    ), 410


@app.route("/trades/delete", methods=["GET", "POST"])
def trades_delete():
    token = request.values.get("token") or request.args.get("token")
    if not _valid_run_token(token):
        abort(403)
    return jsonify(
        {
            "status": "disabled",
            "error": "Manual trade deletion is disabled because manual trade tracking is out of scope.",
        }
    ), 410

@app.route("/config-check")
def config_check():
    token = request.args.get("token")
    if not _valid_run_token(token):
        abort(403)

    try:
        from app.services.config_check_service import build_config_check

        data = build_config_check(run_mode=_requested_run_mode())
        return jsonify(data), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/research/calendar-backtest")
def research_calendar_backtest():
    token = request.args.get("token")
    if not _valid_run_token(token):
        abort(403)

    try:
        from app.services.calendar_research_service import (
            render_calendar_backtest_research_html,
            run_calendar_backtest_research,
        )

        params = dict(request.args.items())
        report = run_calendar_backtest_research(params, log_print=lambda msg: print(msg, flush=True))
        if str(request.args.get("format") or "").lower() == "json":
            return jsonify(report), 200
        return render_calendar_backtest_research_html(report), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "traceback": traceback.format_exc()}), 500


@app.route("/refresh-active-trades", methods=["GET", "POST"])
def refresh_active_trades():
    token = request.values.get("token") or request.args.get("token")
    if not _valid_run_token(token):
        abort(403)

    if not ACTIVE_TRADE_REFRESH_LOCK.acquire(blocking=False):
        return jsonify({"status": "already_running", "scope": "active_trades_only", "message": "Active trade refresh already in progress."}), 409
    try:
        from app.services.calendar_lifecycle_service import evaluate_calendar_lifecycle
        from app.services.open_options_service import detect_open_options_positions

        log: list[str] = []

        def logger(message: str) -> None:
            safe = str(message)
            log.append(safe)
            print(safe, flush=True)

        open_options = detect_open_options_positions(log_print=logger)
        provider_status = (open_options or {}).get("provider_status", {}) or {}
        rh_status = (provider_status.get("robinhood") or {}) if isinstance(provider_status, dict) else {}
        rh_state = str(rh_status.get("status") or "").lower()
        rh_unavailable = bool(
            rh_status.get("rate_limited")
            or rh_status.get("auth_required")
            or rh_state in {"rate_limited", "auth_required", "auth_failed"}
        )
        lifecycle = evaluate_calendar_lifecycle(
            open_options=open_options,
            tradier_snapshot={},
            earnings_events={},
            trade_memory=None,
            log_print=logger,
        )
        summary = {
            "option_position_count": ((open_options or {}).get("summary", {}) or {}).get("option_leg_count", 0),
            "calendar_count": ((lifecycle or {}).get("summary", {}) or {}).get("calendar_count", 0),
            "urgent_count": ((lifecycle or {}).get("summary", {}) or {}).get("urgent_count", 0),
            "exit_review_count": ((lifecycle or {}).get("summary", {}) or {}).get("exit_review_count", 0),
        }
        response = {
                "status": "ok",
                "scope": "active_trades_only",
                "provider_status": provider_status,
                "provider_unavailable": rh_unavailable,
                "message": (
                    "Robinhood unavailable during this run; active trades were not interpreted as empty."
                    if rh_unavailable
                    else "Active trades refresh complete."
                ),
                "skipped": [
                    "broad earnings discovery",
                    "news fetch",
                    "watchlist scan",
                    "sector suggestions",
                    "stock momentum scan",
                    "full portfolio scoring",
                ],
                "summary": summary,
                "log_tail": log[-20:],
            }
        detail = str(request.values.get("detail") or request.args.get("detail") or config.ACTIVE_TRADES_DEFAULT_DETAIL).lower()
        response["detail"] = detail
        if detail == "full":
            response["open_options"] = open_options
            response["lifecycle"] = lifecycle
        return jsonify(response), 200
    except Exception as e:
        return jsonify({"status": "error", "error": str(e), "traceback": traceback.format_exc()}), 500
    finally:
        ACTIVE_TRADE_REFRESH_LOCK.release()


@app.route("/health")
def health():
    return "OK", 200


# ---------------------------------------------------------------------------
# 28A: Signup / Login / Dashboard / Logout
# ---------------------------------------------------------------------------

_AUTH_CSS = """
body{font-family:monospace;background:#0f0f0f;color:#e0e0e0;display:flex;
  align-items:center;justify-content:center;min-height:100vh;margin:0}
.card{background:#1a1a1a;border:1px solid #00ff8844;border-radius:10px;
  padding:2rem;width:100%;max-width:440px}
h1{color:#00ff88;margin-top:0;font-size:1.3rem}
label{display:block;margin:.8rem 0 .25rem;color:#aaa;font-size:.85rem}
input{width:100%;box-sizing:border-box;background:#111;border:1px solid #333;
  border-radius:4px;color:#e0e0e0;padding:.55rem .7rem;font-family:monospace}
input:focus{outline:none;border-color:#00ff88}
button,input[type=submit]{background:#00ff88;color:#000;border:none;
  border-radius:4px;padding:.6rem 1.2rem;font-weight:bold;cursor:pointer;
  margin-top:1rem;font-family:monospace}
.err{color:#ff6b6b;margin-top:.7rem;font-size:.88rem}
.muted{color:#666;font-size:.82rem;margin-top:.6rem}
a{color:#00ff88}
.key-box{background:#111;border:1px solid #00ff8855;border-radius:4px;
  padding:.6rem;word-break:break-all;margin:.5rem 0;font-size:.9rem}
"""

_SIGNUP_HTML = """<!DOCTYPE html><html><head><title>ASA — Sign Up</title>
<style>{css}</style></head><body><div class="card">
<h1>Sign Up — Algo Stock Advisor</h1>
<form method="POST">
  <label>Username (3–20 chars, a-z A-Z 0-9 _)</label>
  <input name="username" value="{username}" required autofocus>
  <label>Password (8+ chars)</label>
  <input type="password" name="password" required>
  <label>Confirm Password</label>
  <input type="password" name="confirm_password" required>
  <label>Invite Code</label>
  <input name="invite_code" value="{invite_code}" required>
  <label>Robinhood Username</label>
  <input name="robinhood_username" value="{robinhood_username}" required>
  <label>Robinhood Password</label>
  <input type="password" name="robinhood_password" required>
  <button type="submit">Create Account</button>
</form>
{error}
<p class="muted"><a href="/login">Already have an account? Log in</a></p>
</div></body></html>"""

_LOGIN_HTML = """<!DOCTYPE html><html><head><title>ASA — Login</title>
<style>{css}</style></head><body><div class="card">
<h1>Log In — Algo Stock Advisor</h1>
<form method="POST">
  <label>Username</label>
  <input name="username" autofocus required>
  <label>Password</label>
  <input type="password" name="password" required>
  <button type="submit">Log In</button>
</form>
{error}
<p class="muted"><a href="/signup">Need an account? Sign up</a></p>
</div></body></html>"""

_DASHBOARD_HTML = """<!DOCTYPE html><html><head><title>ASA — Dashboard</title>
<style>{css}
.pill{{background:#00ff8822;border:1px solid #00ff8844;border-radius:4px;
  padding:.3rem .6rem;font-size:.82rem;display:inline-block;margin:.2rem}}
.ok{{color:#00ff88}}.warn{{color:#ffcc44}}.err{{color:#ff4444}}
.section{{margin-top:1.5rem;border-top:1px solid #333;padding-top:1rem}}
</style></head><body><div class="card">
<h1>Welcome, {username}</h1>
<p><span class="pill">{role}</span></p>
<p>Your API key (first 12 chars shown):</p>
<div class="key-box">{key_prefix}</div>
<p class="muted">Use full key in Authorization: Bearer header or ?token= param.</p>
<p class="muted">Last login: {last_login}</p>
<p><a href="/api/user/status?token={api_key}">View full status (JSON)</a></p>
<div class="section">
<h2>Robinhood Credentials</h2>
<p>{cred_status_html}</p>
<details style="margin-top:.8rem">
<summary style="cursor:pointer;color:#aaa">Update Robinhood Credentials</summary>
<form method="POST" action="/user/update-credentials" style="margin-top:.8rem">
  <label>Robinhood Username (email)</label>
  <input name="robinhood_username" type="email" required>
  <label>Robinhood Password</label>
  <input name="robinhood_password" type="password" required>
  <button type="submit">Validate &amp; Save</button>
</form>
{cred_update_msg}
</details>
</div>
<form method="POST" action="/logout" style="margin-top:1.5rem">
  <button type="submit" style="background:#ff4444">Log Out</button>
</form>
</div></body></html>"""


def _get_session_user():
    """Return user dict from session cookie, or None."""
    from flask import session
    token = session.get("session_token")
    if not token:
        return None
    try:
        from app.db.users import get_user_by_session_token
        return get_user_by_session_token(token)
    except Exception:
        return None


@app.route("/signup", methods=["GET", "POST"])
def signup():
    import re
    error = ""
    username = ""
    invite_code = ""
    robinhood_username = ""

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm_password", "")
        invite_code = request.form.get("invite_code", "").strip()
        robinhood_username = request.form.get("robinhood_username", "").strip()
        robinhood_password = request.form.get("robinhood_password", "")

        # TKT-031: Hard error if credential encryption unavailable — never silently drop creds.
        from app.db.users import get_encryption_key_status
        if not get_encryption_key_status():
            error = (
                "Service configuration error: credential storage unavailable. "
                "Contact the administrator."
            )
        # Validation
        elif not re.match(r'^[a-zA-Z0-9_]{3,20}$', username):
            error = "Username must be 3–20 chars, letters/numbers/underscore only."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif password != confirm:
            error = "Passwords do not match."
        elif not invite_code:
            error = "Invite code required."
        elif not robinhood_username or not robinhood_password:
            error = "Robinhood credentials required."
        else:
            try:
                from app.db.users import (
                    create_user, create_session, get_invite_code,
                    consume_invite_code, get_user_by_username, update_last_login,
                    set_credentials_validated,
                )
                invite = get_invite_code(invite_code)
                if not invite or invite.get("is_used"):
                    error = "Invalid or already-used invite code."
                elif get_user_by_username(username):
                    error = "Username already taken."
                else:
                    # 28C: Validate Robinhood credentials before creating account
                    from app.services.broker_provider import BrokerCredentialProvider
                    provider = BrokerCredentialProvider.get_provider("robinhood")
                    valid, err_key = provider.validate_credentials(rh_username=robinhood_username, password=robinhood_password)
                    if not valid:
                        _signup_err_msgs = {
                            "validation_timeout": "Robinhood validation timed out. Please try again.",
                            "device_approval_required": (
                                "Robinhood requires device approval for this login. "
                                "Check your email/SMS, approve, then retry signup."
                            ),
                            "rate_limited": "Robinhood rate limit reached. Try again in a few minutes.",
                            "login_failed": "Robinhood login failed. Check username and password.",
                        }
                        error = _signup_err_msgs.get(err_key, "Robinhood credential validation failed.")
                    else:
                        user = create_user(
                            username, password,
                            robinhood_username=robinhood_username,
                            robinhood_password_plain=robinhood_password,
                        )
                        user_id = user.get("id")
                        set_credentials_validated(user_id)
                        consume_invite_code(invite_code, user_id)
                        token = create_session(user_id)
                        update_last_login(user_id)
                        from flask import session as flask_session
                        flask_session["session_token"] = token
                        return redirect("/dashboard")
            except Exception as exc:
                error = f"Signup failed: {exc}"

    err_html = f'<p class="err">{escape(error)}</p>' if error else ""
    return render_template_string(
        _SIGNUP_HTML.format(css=_AUTH_CSS, username=escape(username),
                            invite_code=escape(invite_code),
                            robinhood_username=escape(robinhood_username),
                            error=err_html)
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    error = ""
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        try:
            from app.db.users import (
                get_user_by_username, check_password, create_session, update_last_login
            )
            user = get_user_by_username(username)
            if user and user.get("is_active") and check_password(password, user.get("password_hash", "")):
                token = create_session(user["id"])
                update_last_login(user["id"])
                from flask import session as flask_session
                flask_session["session_token"] = token
                return redirect("/dashboard")
            else:
                error = "Invalid username or password."
        except Exception:
            error = "Invalid username or password."

    err_html = f'<p class="err">{escape(error)}</p>' if error else ""
    return render_template_string(_LOGIN_HTML.format(css=_AUTH_CSS, error=err_html))


@app.route("/dashboard")
def dashboard():
    user = _get_session_user()
    if not user:
        return redirect("/login")
    api_key = user.get("api_key", "")
    key_prefix = (api_key[:12] + "...") if len(api_key) > 12 else api_key
    is_admin = bool(user.get("is_admin"))
    last_login = user.get("last_login_at") or "—"

    # 28C: credential status display
    validated_at = user.get("credentials_validated_at")
    last_error = user.get("credentials_last_error")
    rh_username = user.get("robinhood_username") or ""
    if validated_at:
        cred_status_html = (
            f'<span class="ok">✓ Validated</span> — {escape(str(validated_at)[:10])}'
            + (f' ({escape(rh_username)})' if rh_username else "")
        )
    elif last_error:
        cred_status_html = f'<span class="err">Last error:</span> {escape(last_error[:120])}'
    elif rh_username:
        cred_status_html = f'<span class="warn">Not yet validated</span> — {escape(rh_username)}'
    else:
        cred_status_html = '<span class="warn">No Robinhood credentials stored.</span>'

    cred_update_msg = request.args.get("cred_msg", "")
    cred_update_html = (
        f'<p class="{"ok" if "success" in cred_update_msg.lower() else "err"}">{escape(cred_update_msg)}</p>'
        if cred_update_msg else ""
    )

    html = _DASHBOARD_HTML.format(
        css=_AUTH_CSS,
        username=escape(str(user.get("username", ""))),
        role="Admin" if is_admin else "User",
        key_prefix=escape(key_prefix),
        api_key=escape(api_key),
        last_login=escape(str(last_login)),
        cred_status_html=cred_status_html,
        cred_update_msg=cred_update_html,
    )
    return html


@app.route("/user/update-credentials", methods=["POST"])
def update_credentials_form():
    """
    28C: Form-friendly POST alias for PUT /api/user/credentials.
    Session-authenticated. Redirects to /dashboard with status message.
    NEVER logs passwords.
    """
    user = _get_session_user()
    if not user:
        return redirect("/login")
    user_id = user.get("id")

    from app.db.users import get_encryption_key_status
    if not get_encryption_key_status():
        return redirect("/dashboard?cred_msg=Service+configuration+error%3A+contact+admin")

    rh_username = request.form.get("robinhood_username", "").strip()
    rh_password = request.form.get("robinhood_password", "")
    if not rh_username or not rh_password:
        return redirect("/dashboard?cred_msg=Username+and+password+required")

    from app.services.broker_provider import BrokerCredentialProvider
    provider = BrokerCredentialProvider.get_provider("robinhood")
    valid, err_key = provider.validate_credentials(rh_username, rh_password)

    if not valid:
        _msgs = {
            "validation_timeout": "Validation+timed+out.+Try+again.",
            "device_approval_required": "Device+approval+required.+Check+email%2FSMS+then+retry.",
            "rate_limited": "Robinhood+rate+limit+hit.+Try+again+in+a+few+minutes.",
            "login_failed": "Robinhood+login+failed.+Check+credentials.",
        }
        msg = _msgs.get(err_key, "Validation+failed.")
        return redirect(f"/dashboard?cred_msg={msg}")

    from app.db.users import update_broker_credentials
    update_broker_credentials(user_id, rh_username, rh_password)
    return redirect("/dashboard?cred_msg=Success%3A+credentials+updated+and+validated.")


@app.route("/logout", methods=["POST"])
def logout():
    from flask import session as flask_session
    token = flask_session.pop("session_token", None)
    if token:
        try:
            from app.db.users import delete_session
            delete_session(token)
        except Exception:
            pass
    return redirect("/login")


@app.route("/api/dev/snapshot")
@app.route("/dev/snapshot")
def developer_snapshot():
    if not config.ENABLE_DEV_SNAPSHOT_ENDPOINT:
        abort(404)
    if config.DEV_SNAPSHOT_REQUIRE_TOKEN and not _valid_dev_token(request.args.get("token")):
        abort(403)
    mode = str(request.args.get("mode") or config.DEV_SNAPSHOT_DEFAULT_MODE).strip().lower()
    if mode == "fresh":
        if not config.DEV_SNAPSHOT_ALLOW_FRESH:
            return jsonify({"status": "disabled", "error": "Fresh developer snapshots are disabled."}), 403
        return jsonify({"status": "ready", "redirect_url": f"/run?token={request.args.get('token')}&mode={_requested_run_mode()}"}), 202
    if mode not in {"latest", "manifest_only", "summary", "full"}:
        return jsonify({"status": "error", "error": "Unsupported snapshot mode."}), 400
    from app.services.developer_snapshot_service import build_developer_snapshot
    result = build_developer_snapshot(mode)
    _record_usage(
        "snapshot_request",
        source="developer_snapshot",
        run_id=result.get("source_run_id"),
        metadata={"request_mode": mode, "route_name": "developer_snapshot"},
    )
    return jsonify(result), 200


@app.route("/api/dev/snapshot/detail/<section>")
def developer_snapshot_detail(section: str):
    if not config.ENABLE_DEV_SNAPSHOT_ENDPOINT:
        abort(404)
    if config.DEV_SNAPSHOT_REQUIRE_TOKEN and not _valid_dev_token(request.args.get("token")):
        abort(403)
    allowed = {"daily_opportunity", "data_coverage", "lifecycle", "pipeline", "portfolio", "providers", "provider_raw", "strategies", "strategy"}
    if section not in allowed:
        return jsonify({"status": "error", "error": "Unsupported detail section.", "provider_calls_triggered": False, "read_only": True}), 400
    from app.services.developer_snapshot_service import build_snapshot_detail
    result = build_snapshot_detail(section, strategy_id=request.args.get("strategy_id"))
    _record_usage(
        "detail_request",
        section=section,
        source="developer_snapshot_detail",
        run_id=result.get("source_run_id"),
        metadata={"detail_section": section, "strategy_id": request.args.get("strategy_id"), "route_name": "developer_snapshot_detail"},
    )
    return jsonify(result), 200 if result.get("status") != "not_found" else 404


@app.route("/api/usage/event", methods=["POST"])
def usage_event():
    token = request.args.get("token")
    if not (_valid_run_token(token) or _valid_dev_token(token)):
        abort(403)
    data = request.get_json(silent=True) or {}
    _record_usage(
        str(data.get("event_type") or ""),
        section=data.get("section"),
        source="dashboard",
        run_id=data.get("run_id"),
        metadata=data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    )
    return jsonify({"status": "accepted", "read_only": True, "provider_calls_triggered": False}), 202


def _require_dev_diagnostics_token() -> None:
    if not config.ENABLE_DEV_DIAGNOSTICS_ENDPOINTS:
        abort(404)
    if not _valid_dev_token(request.args.get("token")):
        abort(403)


@app.route("/api/dev/status")
def dev_status():
    _require_dev_diagnostics_token()
    from app.services.app_diagnostics_service import build_dev_status
    _recover_stale_run_if_needed()
    return jsonify(build_dev_status(RUN_JOBS, ACTIVE_JOB_ID, APP_BOOTED_AT, _run_lock_status())), 200


@app.route("/api/dev/latest-run-manifest")
def dev_latest_run_manifest():
    _require_dev_diagnostics_token()
    from app.services.app_diagnostics_service import build_latest_run_manifest
    return jsonify(build_latest_run_manifest()), 200


@app.route("/api/dev/latest-profiles")
def dev_latest_profiles():
    _require_dev_diagnostics_token()
    from app.services.app_diagnostics_service import build_latest_profiles
    return jsonify(build_latest_profiles()), 200


@app.route("/api/dev/feature-health")
def dev_feature_health():
    _require_dev_diagnostics_token()
    from app.services.app_diagnostics_service import build_feature_health
    return jsonify(build_feature_health()), 200


@app.route("/api/dev/strategy-ids")
def dev_strategy_ids():
    _require_dev_diagnostics_token()
    from app.services.testing_packet_service import build_strategy_catalog
    return jsonify({
        "status": "ok",
        "strategies": build_strategy_catalog(),
        "provider_calls_triggered": False,
        "read_only": True,
    }), 200


@app.route("/api/dev/testing-packet")
def dev_testing_packet():
    _require_dev_diagnostics_token()
    from app.services.testing_packet_service import build_testing_packet
    return jsonify(build_testing_packet()), 200


@app.route("/api/dev/usage-telemetry")
def dev_usage_telemetry():
    _require_dev_diagnostics_token()
    from app.services.usage_telemetry_service import build_usage_telemetry_diagnostics
    return jsonify(build_usage_telemetry_diagnostics()), 200


def _run_job(job_id: str, run_mode: str = "prod", job_lock: threading.Lock | None = None) -> None:
    global ACTIVE_JOB_ID

    try:
        RUN_JOBS[job_id]["message"] = _running_job_message(run_mode)
        RUN_JOBS[job_id]["updated_at"] = time.time()
        RUN_JOBS[job_id]["heartbeat_at"] = time.time()

        print(f"=== BACKGROUND RUN {job_id} STARTED; mode={run_mode} ===", flush=True)
        result = run(run_mode=run_mode)
        if RUN_JOBS[job_id].get("status") == "timeout":
            print(f"=== BACKGROUND RUN {job_id} RETURNED AFTER TIMEOUT; RESULT DISCARDED ===", flush=True)
            return
        payload, positions, news, recommendations, tradier_snapshot, log = result

        if payload is None:
            RUN_JOBS[job_id]["status"] = "error"
            RUN_JOBS[job_id]["message"] = "Run failed. Open result page for logs."
        else:
            RUN_JOBS[job_id]["status"] = "complete"
            RUN_JOBS[job_id]["message"] = "Run complete. Loading report."

        RUN_JOBS[job_id]["result"] = result
        RUN_JOBS[job_id]["updated_at"] = time.time()
        RUN_JOBS[job_id]["heartbeat_at"] = time.time()
        RUN_JOBS[job_id]["retry_safe"] = True
        print(f"=== BACKGROUND RUN {job_id} FINISHED ===", flush=True)

    except Exception as e:
        if RUN_JOBS.get(job_id, {}).get("status") == "timeout":
            print(f"=== BACKGROUND RUN {job_id} ERRORED AFTER TIMEOUT; ERROR DISCARDED ===", flush=True)
            return
        RUN_JOBS[job_id]["status"] = "error"
        RUN_JOBS[job_id]["message"] = f"Unexpected run error: {e}"
        RUN_JOBS[job_id]["result"] = (
            None,
            [],
            {},
            [],
            {},
            [
                "=== RUN STARTED ===",
                f"UNEXPECTED BACKGROUND ERROR: {e}",
                traceback.format_exc(),
            ],
        )
        RUN_JOBS[job_id]["updated_at"] = time.time()
        RUN_JOBS[job_id]["heartbeat_at"] = time.time()
        RUN_JOBS[job_id]["retry_safe"] = True
        print(f"=== BACKGROUND RUN {job_id} ERRORED: {e} ===", flush=True)

    finally:
        with RUN_STATE_LOCK:
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None
        _safe_release_lock(job_lock)


def run_sync_response(run_mode: str = "prod"):
    _recover_stale_run_if_needed()
    sync_lock = RUN_LOCK
    if not sync_lock.acquire(blocking=False):
        return run_already_active_page(), 409

    try:
        print(f"=== /run ENDPOINT HIT; sync mode; mode={run_mode} ===", flush=True)
        payload, positions, news, recommendations, tradier_snapshot, log = run(run_mode=run_mode)

        if payload is None:
            error_log = escape("\n".join(log))
            return error_page("Run Failed", error_log), 500

        try:
            from app.services.report_service import format_html

            return format_html(
                payload, positions, news, recommendations, tradier_snapshot, log,
                view=_requested_dashboard_view(),
            ), 200
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
        _safe_release_lock(sync_lock)




def _render_home_page() -> str:
    """Small mobile-friendly endpoint menu for the Railway base URL."""
    return """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Algo Stock Advisor</title>
    <style>
        :root { color-scheme: dark; }
        * { box-sizing: border-box; }
        body {
            font-family: monospace;
            background: #0f0f0f;
            color: #e0e0e0;
            padding: 1.25rem;
            max-width: 900px;
            margin: auto;
            line-height: 1.45;
        }
        h1 { color: #00ff88; font-size: clamp(1.5rem, 5vw, 2.2rem); }
        .card {
            background: #141414;
            border: 1px solid #333;
            border-radius: 14px;
            padding: 1rem;
            margin: 1rem 0;
        }
        label { display: block; color: #aaa; margin-bottom: 0.35rem; }
        input {
            width: 100%;
            background: #0b0b0b;
            color: #e0e0e0;
            border: 1px solid #333;
            border-radius: 8px;
            padding: 0.7rem;
            font-family: monospace;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 0.75rem;
            margin-top: 1rem;
        }
        button, a.button {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            min-height: 44px;
            background: #00ff88;
            color: #00140a;
            border: none;
            border-radius: 10px;
            padding: 0.7rem 0.9rem;
            font-family: monospace;
            font-weight: bold;
            text-decoration: none;
            cursor: pointer;
            text-align: center;
        }
        a.secondary, button.secondary { background: #1f2937; color: #e5e7eb; border: 1px solid #333; }
        .muted { color: #999; font-size: 0.9rem; }
        .tiny { color: #777; font-size: 0.8rem; }
        @media (max-width: 620px) {
            body { padding: 0.85rem; }
            .grid { grid-template-columns: 1fr; }
        }
    </style>
</head>
<body>
    <h1>📈 Algo Stock Advisor</h1>
    <p class="muted">Mobile-friendly endpoint menu. Your token is stored only in this browser's localStorage.</p>

    <div class="card">
        <label for="token">RUN_TOKEN</label>
        <input id="token" type="password" placeholder="Paste your RUN_TOKEN here" autocomplete="off">
        <div class="grid">
            <button onclick="saveToken()">Save Token</button>
            <button class="secondary" onclick="clearToken()">Clear Token</button>
        </div>
        <p id="tokenStatus" class="tiny">Token not loaded yet.</p>
    </div>

    <div class="card">
        <h2>Run / Review</h2>
        <div class="grid">
            <a class="button" href="#" onclick="go('/run?mode=dev'); return false;">Run DEV Report</a>
            <a class="button" href="#" onclick="go('/run'); return false;">Run PROD Report</a>
            <a class="button secondary" href="#" onclick="go('/refresh-active-trades'); return false;">Refresh Active Trades</a>
            
            <a class="button secondary" href="#" onclick="go('/config-check'); return false;">Config Check</a>
            <a class="button secondary" href="/health">Health</a>
        </div>
    </div>

    <div class="card">
        <h2>Notes</h2>
        <p class="muted">Use DEV for normal testing. Use PROD only when you want the wider configured scan and are comfortable spending more API calls.</p>
    </div>

    <script>
        const input = document.getElementById('token');
        const status = document.getElementById('tokenStatus');
        const saved = localStorage.getItem('runToken') || '';
        input.value = saved;
        status.innerText = saved ? 'Token loaded from this browser.' : 'Paste your token and save it.';

        function saveToken() {
            localStorage.setItem('runToken', input.value.trim());
            status.innerText = input.value.trim() ? 'Token saved in this browser.' : 'No token saved.';
        }
        function clearToken() {
            localStorage.removeItem('runToken');
            input.value = '';
            status.innerText = 'Token cleared.';
        }
        function go(path) {
            const token = (input.value || localStorage.getItem('runToken') || '').trim();
            if (!token) {
                alert('Paste and save your RUN_TOKEN first.');
                return;
            }
            const sep = path.includes('?') ? '&' : '?';
            window.location.href = path + sep + 'token=' + encodeURIComponent(token);
        }
    </script>
</body>
</html>"""


def _render_manual_trade_deprecated_page(token: str) -> str:
    run_url = f"/run?mode=dev&token={token}"
    return f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Manual Trade Entry Disabled</title>
    <style>
        :root {{ color-scheme: dark; }}
        body {{ font-family: monospace; background: #0f0f0f; color: #e0e0e0; padding: 1rem; max-width: 760px; margin: auto; }}
        .card {{ background: #141414; border: 1px solid #333; border-radius: 14px; padding: 1rem; }}
        a.button {{ display: inline-flex; min-height: 44px; align-items: center; justify-content: center; background: #00ff88; color: #00140a; border-radius: 10px; padding: 0.7rem 0.9rem; text-decoration: none; font-weight: bold; }}
        .muted {{ color: #aaa; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Manual Trade Entry Disabled</h1>
        <p class="muted">This app is now intentionally read-only. Manual trade tracking/input is out of scope.</p>
        <p>Open calendars should appear only when they are automatically detected from broker option positions, especially Robinhood options.</p>
        <p><a class="button" href="{run_url}">Run DEV Report</a></p>
    </div>
</body>
</html>"""


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
    <meta name="viewport" content="width=device-width, initial-scale=1">
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
        @media (max-width: 700px) {{
            body {{ padding: 1rem; }}
            .card {{ padding: 1rem; }}
            pre {{ font-size: 0.78rem; max-height: 45vh; overflow: auto; }}
        }}
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



def _redirect_html(message: str, url: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta http-equiv="refresh" content="0;url={escape(url)}"></head>
<body style="font-family:monospace;background:#0f0f0f;color:#e0e0e0;padding:2rem;">
<p>{escape(message)}</p><p><a href="{escape(url)}">Return</a></p>
</body></html>"""

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
        if job.get("status") != "running" and now - float(job.get("created_at", now)) > MAX_JOB_AGE_SECONDS
    ]
    for job_id in expired:
        RUN_JOBS.pop(job_id, None)


def _recover_stale_run_if_needed(now: float | None = None) -> bool:
    """Mark an overlong run timed out and rotate the lock so retry can proceed."""
    global ACTIVE_JOB_ID, RUN_LOCK
    current_time = float(now if now is not None else time.time())
    with RUN_STATE_LOCK:
        job_id = ACTIVE_JOB_ID
        job = RUN_JOBS.get(job_id or "") if job_id else None
        if not RUN_LOCK.locked() or not job or job.get("status") != "running":
            return False
        started_at = float(job.get("started_at") or job.get("created_at") or current_time)
        age_seconds = max(0.0, current_time - started_at)
        timeout_seconds = max(1, int(config.RUN_STALE_TIMEOUT_SECONDS))
        if age_seconds <= timeout_seconds:
            return False
        job.update({
            "status": "timeout",
            "message": f"Run timed out after {int(age_seconds)}s. Safe retry is available.",
            "updated_at": current_time,
            "heartbeat_at": current_time,
            "timeout_reason": "run_stale_timeout",
            "failed_stage": "background_run",
            "retry_safe": True,
        })
        print(
            f"RunWatchdog: job={job_id} timed out age={int(age_seconds)}s "
            f"limit={timeout_seconds}s; rotating run lock for safe retry.",
            flush=True,
        )
        ACTIVE_JOB_ID = None
        RUN_LOCK = threading.Lock()
        return True


def _run_lock_status() -> dict[str, Any]:
    job = RUN_JOBS.get(ACTIVE_JOB_ID or "") if ACTIVE_JOB_ID else None
    now = time.time()
    started_at = float((job or {}).get("started_at") or (job or {}).get("created_at") or now)
    return {
        "held": bool(RUN_LOCK.locked()),
        "active_job_id": ACTIVE_JOB_ID,
        "active_run_age_seconds": round(max(0.0, now - started_at), 1) if job else None,
        "stale_timeout_seconds": int(config.RUN_STALE_TIMEOUT_SECONDS),
        "timeout_reason": (job or {}).get("timeout_reason"),
        "retry_safe": not RUN_LOCK.locked() or bool((job or {}).get("retry_safe")),
    }


def _safe_release_lock(lock: Any) -> None:
    if lock is None:
        return
    try:
        if lock.locked():
            lock.release()
    except RuntimeError:
        pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting Algo Stock Advisor on 0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port)
