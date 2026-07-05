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

from app.auth import require_admin

from app.api.user import user_bp
app.register_blueprint(user_bp)

from app.api.knowledge import knowledge_bp
app.register_blueprint(knowledge_bp)

from app.api.plaid import plaid_bp
app.register_blueprint(plaid_bp)

from app.api.auth import auth_bp
app.register_blueprint(auth_bp)

from app.api.telemetry import telemetry_bp
app.register_blueprint(telemetry_bp)

# 28A: set secret key for signed cookies and seed admin user on first boot
app.secret_key = config.SESSION_SECRET_KEY or os.urandom(32)
try:
    from app.db.users import seed_admin_if_needed
    seed_admin_if_needed()
except Exception as _seed_exc:
    print(f"28A seed: {_seed_exc}", flush=True)

# 29A: TKT-036 sysadmin seed + jaia demotion
try:
    from app.db.users import seed_sysadmin
    seed_sysadmin()
except Exception as _seed29_exc:
    print(f"29A seed_sysadmin: {_seed29_exc}", flush=True)

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
    """Allow legacy token, any active admin, or any is_dev=1 user (TKT-036)."""
    if not token:
        return False
    # Legacy: DEV_API_TOKEN / RUN_TOKEN
    expected = config.DEV_API_TOKEN or config.RUN_TOKEN
    if expected and token == expected:
        return True
    # TKT-036: is_admin=1 OR is_dev=1 grants dev endpoint access
    try:
        from app.auth import _resolve_user, _is_legacy_token
        if _is_legacy_token(token):
            return True
        user = _resolve_user(token)
        return bool(
            user and user.get("is_active")
            and (user.get("is_admin") or user.get("is_dev"))
        )
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
<style>{css}
.broker-choice{{margin:1rem 0;display:flex;gap:.5rem}}
.broker-choice label{{flex:1;padding:.6rem;border:1px solid #333;border-radius:4px;
  text-align:center;cursor:pointer}}
.broker-choice input[type=radio]{{display:none}}
.broker-choice input:checked+span{{color:#00ff88;border-color:#00ff88}}
.broker-choice label:has(input:checked){{border-color:#00ff88}}
#rh-fields,#plaid-fields{{display:none}}
</style></head><body><div class="card">
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
  <p style="margin-top:1rem;font-weight:bold">Connect Your Broker</p>
  <div class="broker-choice">
    <label><input type="radio" name="broker_path" value="robinhood" checked onchange="toggleBroker()"><span>Robinhood Direct</span></label>
    <label><input type="radio" name="broker_path" value="plaid" onchange="toggleBroker()"><span>Connect via Plaid</span></label>
    <label><input type="radio" name="broker_path" value="moomoo" onchange="toggleBroker()"><span>Moomoo</span></label>
  </div>
  <div id="rh-fields">
    <label>Robinhood Username</label>
    <input name="robinhood_username" value="{robinhood_username}">
    <label>Robinhood Password</label>
    <input type="password" name="robinhood_password">
  </div>
  <div id="plaid-fields">
    <p class="muted">After creating your account, you'll connect your brokerage via Plaid's secure widget.</p>
    <input type="hidden" name="broker_path_value" value="robinhood">
  </div>
  <div id="moomoo-fields">
    <p class="muted">Moomoo uses an OpenD gateway. After creating your account, your admin will configure your OpenD connection.</p>
  </div>
  <button type="submit">Create Account</button>
</form>
{error}
<p class="muted"><a href="/login">Already have an account? Log in</a></p>
</div>
<script>
function toggleBroker(){{
  var sel=document.querySelector('input[name=broker_path]:checked').value;
  document.getElementById('rh-fields').style.display=sel==='robinhood'?'block':'none';
  document.getElementById('plaid-fields').style.display=sel==='plaid'?'block':'none';
  document.getElementById('moomoo-fields').style.display=sel==='moomoo'?'block':'none';
  var rhU=document.querySelector('[name=robinhood_username]');
  var rhP=document.querySelector('[name=robinhood_password]');
  rhU.required=sel==='robinhood'; rhP.required=sel==='robinhood';
  document.querySelector('[name=broker_path_value]').value=sel;
}}
toggleBroker();
</script>
</body></html>"""

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
:root {{
  --bg:#0f172a; --surface:#1e293b; --border:#334155; --text:#f1f5f9; --text-muted:#94a3b8;
  --pass:#22c55e; --watch:#f59e0b; --near-miss:#8b5cf6; --fail:#ef4444; --exit-stop:#dc2626; --monitor:#6b7280;
}}
.dashboard-card{{background:rgba(15,23,42,.94);border:1px solid var(--border);border-radius:16px;padding:1.25rem;
  box-shadow:0 24px 80px rgba(0,0,0,.35);max-width:1120px;margin:1.5rem auto}}
.muted{{color:var(--text-muted)}} .ok{{color:var(--pass)}} .warn{{color:var(--watch)}} .err{{color:var(--fail)}}
.section{{margin-top:1.2rem;border-top:1px solid var(--border);padding-top:1rem}}
.meta-row,.signal-row,.position-row{{display:flex;flex-wrap:wrap;gap:.55rem;align-items:center}}
.meta-row{{background:#0b1220;border:1px solid var(--border);border-radius:10px;padding:.75rem}}
.strategy-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:.9rem}}
.strategy-section{{background:#0b1220;border:1px solid var(--border);border-radius:10px;padding:.85rem}}
.signal-row,.position-row{{background:#111827;border:1px solid #1f2937;border-radius:8px;padding:.7rem;margin-top:.55rem;cursor:pointer}}
.signal-row:hover,.position-row:hover{{border-color:#475569}}
.ticker{{font-weight:700;color:var(--text)}} .section-title{{display:flex;justify-content:space-between;gap:.6rem;align-items:center;flex-wrap:wrap}}
.badge{{display:inline-flex;align-items:center;gap:.3rem;border:1px solid var(--border);border-radius:999px;padding:.2rem .55rem;font-size:.78rem;background:#0b1220}}
.badge-pass{{color:var(--pass);border-color:rgba(34,197,94,.4)}} .badge-watch{{color:var(--watch);border-color:rgba(245,158,11,.4)}}
.badge-fail{{color:var(--fail);border-color:rgba(239,68,68,.4)}} .badge-muted{{color:var(--text-muted)}}
.age.fresh{{color:var(--pass)}} .age.recent{{color:var(--watch)}} .age.stale,.stale-note{{color:var(--fail)}}
.meta-link{{color:#cbd5e1;text-decoration:none}} .meta-link:hover{{text-decoration:underline}}
.empty{{color:var(--text-muted);margin:.6rem 0 0}} .dry-note{{color:#cbd5e1;font-size:.8rem}}
#run-btn{{background:#00ff8833;border:1px solid #00ff8866;color:#00ff88;padding:.5rem 1.2rem;border-radius:4px;cursor:pointer;font-size:1rem}}
#run-btn:disabled{{opacity:.4;cursor:not-allowed}} #run-result{{margin-top:.8rem;font-size:.9rem;white-space:pre-wrap}}
</style></head><body><div class="dashboard-card">
<h1>Welcome, {username}</h1>
<p><span class="badge">{role}</span></p>
<div class="meta-row">{run_meta_html}</div>
<div class="section">
<h2>Personalization Run</h2>
<p>{run_status_html}</p>
<p>{core_freshness_html}</p>
<button id="run-btn" onclick="triggerRun()">Run Personalization</button>
<div id="run-result"></div>
<script>
var userToken = {user_token_json};
var currentCoreRunId = {current_run_id_json};
var currentRunAgeSeconds = {current_run_age_seconds};
function getOrCreateSessionId(){{
  if(!sessionStorage.getItem('asa_sid')) sessionStorage.setItem('asa_sid', Math.random().toString(36).substr(2,16));
  return sessionStorage.getItem('asa_sid');
}}
function trackSignalView(ticker, strategyId, verdict){{
  fetch('/api/telemetry/signal-engagement?token=' + encodeURIComponent(userToken), {{
    method:'POST',
    headers:{{'Content-Type':'application/json'}},
    body:JSON.stringify({{
      ticker:ticker, strategy_id:strategyId, verdict:verdict,
      action:'expand_detail', session_id:getOrCreateSessionId()
    }})
  }}).catch(function(){{}});
}}
function triggerRun(){{
  var btn=document.getElementById('run-btn');
  var out=document.getElementById('run-result');
  btn.disabled=true; btn.textContent='Running…';
  out.textContent='Fetching signals…';
  fetch('/api/user/run?token=' + encodeURIComponent(userToken),{{method:'POST'}})
    .then(function(r){{return r.json();}})
    .then(function(d){{
      btn.disabled=false; btn.textContent='Run Personalization';
      if(d.status==='ok'){{
        var pos=d.positions_fetched||0;
        var opp=d.daily_opportunity_count||0;
        var mode=d.broker_mode==='signals_only'?' (signals only)':'';
        out.textContent='Done - '+pos+' positions, '+opp+' opportunities'+mode+(d.core_run_stale?' (core run stale)':'');
      }} else if(d.status==='already_running'){{
        out.textContent='Already running since '+d.started_at;
      }} else {{
        out.textContent='Error: '+(d.error||'')+' '+(d.message||'');
      }}
    }})
    .catch(function(e){{
      btn.disabled=false; btn.textContent='Run Personalization';
      out.textContent='Network error: '+e;
    }});
}}
(function(){{
  var POLL_INTERVAL_MS = 5 * 60 * 1000;
  function checkForNewRun(){{
    fetch('/api/advisor/status', {{cache:'no-store'}})
      .then(function(r){{return r.json();}})
      .then(function(data){{
        if(data && data.run_id && data.run_id !== currentCoreRunId){{
          window.location.reload();
        }}
      }})
      .catch(function(){{}});
  }}
  window.setInterval(checkForNewRun, POLL_INTERVAL_MS);
  if(currentRunAgeSeconds > 86400){{
    var meta = document.querySelector('.meta-row');
    if(meta){{
      var staleNote = document.createElement('span');
      staleNote.className = 'stale-note';
      staleNote.textContent = 'Data is over 24h old';
      meta.appendChild(staleNote);
    }}
  }}
}})();
</script>
</div>
{positions_html}
<div class="section">
<h2>Broker Connection</h2>
<p>{cred_status_html}</p>
{cred_update_msg}
</div>
{signals_html}
<div class="section">
<p class="muted"><a class="meta-link" href="/personalize?token={api_key}">Preferences</a> · <a class="meta-link" href="/api/user/status?token={api_key}">View full status (JSON)</a></p>
</div>
<form method="POST" action="/logout" style="margin-top:1.5rem">
  <button type="submit" style="background:#ff4444">Log Out</button>
</form>
</div></body></html>"""


def _verdict_cls(verdict: str) -> str:
    v = str(verdict or "").upper()
    if v.startswith("PASS"):
        return "ok"
    if v.startswith("WATCH"):
        return "warn"
    return "err"


ACCOUNT_LABELS = {
    "individual": "Individual",
    "ira_roth": "Roth IRA",
    "joint_tenancy_with_ros": "Joint",
    "rollover_ira": "Rollover IRA",
}


def _dashboard_tier(row: dict[str, Any]) -> float:
    tier = row.get("verdict_tier")
    if isinstance(tier, (int, float)):
        return float(tier)
    verdict = str(row.get("verdict") or row.get("action") or "").upper()
    if verdict.startswith("PASS"):
        return 100.0
    if verdict.startswith("WATCH"):
        return 80.0
    if verdict.startswith("NEAR"):
        return 60.0
    return 35.0


def _dashboard_score(row: dict[str, Any]) -> float:
    for key in ("score", "signal_score", "priority_score", "actionability_score"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _dashboard_resolved_verdict(row: dict[str, Any]) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    candidates = (
        row.get("verdict"),
        row.get("action"),
        row.get("final_verdict") if isinstance(row.get("final_verdict"), str) else None,
        raw.get("verdict"),
        raw.get("action"),
        raw.get("final_verdict") if isinstance(raw.get("final_verdict"), str) else None,
    )
    for value in candidates:
        text = str(value or "").strip()
        if text and text.upper() != "UNKNOWN":
            return text
    return "UNKNOWN"


def _dashboard_resolved_ticker(row: dict[str, Any]) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    candidates = (row.get("ticker"), raw.get("ticker"), raw.get("symbol"))
    for value in candidates:
        text = str(value or "").strip()
        if text and text.upper() != "UNKNOWN":
            return text.upper()
    return "UNKNOWN"


def _dashboard_badge(text: str, tone: str = "muted") -> str:
    safe = escape(text)
    return f'<span class="badge badge-{tone}">{safe}</span>'


def _format_currency(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unavailable"
    return f"${value:,.2f}"


def _format_pct(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "unavailable"
    return f"{value:+.2f}%"


def _run_freshness_meta(completed_at: Any, *, now: datetime | None = None) -> dict[str, Any]:
    text = str(completed_at or "").strip()
    if not text:
        return {
            "timestamp_human": "unavailable",
            "age_seconds": 0,
            "age_label": "age unavailable",
            "age_class": "stale",
        }
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = now or datetime.now(timezone.utc)
        age_seconds = max(0, int((current.astimezone(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return {
            "timestamp_human": text[:16].replace("T", " ") + " UTC",
            "age_seconds": 0,
            "age_label": "age unavailable",
            "age_class": "stale",
        }
    if age_seconds < 3600:
        age_label = f"{age_seconds // 60}m ago"
    elif age_seconds < 86400:
        age_label = f"{age_seconds // 3600}h ago"
    else:
        age_label = f"{age_seconds // 86400}d ago"
    age_class = "fresh" if age_seconds < 7200 else "recent" if age_seconds < 86400 else "stale"
    timestamp_human = parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {
        "timestamp_human": timestamp_human,
        "age_seconds": age_seconds,
        "age_label": age_label,
        "age_class": age_class,
    }


def _account_label(account_type: Any, nickname: Any = None) -> str:
    if nickname:
        return str(nickname)
    normalized = str(account_type or "").strip().lower()
    if normalized in ACCOUNT_LABELS:
        return ACCOUNT_LABELS[normalized]
    if not normalized:
        return "Account"
    return normalized.replace("_", " ").title()


def _resolve_dashboard_user():
    user = _get_session_user()
    if user:
        return user
    token = request.args.get("token")
    if not token:
        return None
    try:
        from app.auth import _resolve_user
        user = _resolve_user(token)
        if user and user.get("is_active"):
            return user
    except Exception:
        return None
    return None


def _load_dashboard_core_report() -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    try:
        from app.services.personalization import _load_latest_core_run
        snapshot, report = _load_latest_core_run()
        tradier = (report or {}).get("tradier_snapshot", {}) or {}
        return snapshot, report, tradier
    except Exception:
        return None, None, None


def _build_dashboard_positions_html(user: dict[str, Any]) -> tuple[str, float | None]:
    user_id = user.get("id")
    if not user_id:
        return "", None
    broker_connection_optional = bool(user.get("broker_connection_optional"))
    broker_connected_flag = bool(user.get("broker_connected"))
    if broker_connection_optional and not broker_connected_flag:
        return (
            '<div class="section"><h2>Track Your Portfolio</h2>'
            '<p>Connect your brokerage to see open positions, P&amp;L, and exit signals alongside market signals.</p>'
            '<p><a class="meta-link" href="/connect-broker">Connect Robinhood</a></p>'
            '<p class="muted">All strategy signals remain visible without broker connection.</p></div>',
            None,
        )
    try:
        import json as _json
        from app.db.users import (
            get_latest_complete_user_run,
            get_user_broker_accounts,
            get_user_option_positions,
            get_user_positions,
        )

        latest_run = get_latest_complete_user_run(user_id)
        if not latest_run:
            return '<div class="section"><h2>Open Positions</h2><p class="empty">No personalization run yet.</p></div>', None

        broker_accounts = get_user_broker_accounts(user_id)
        nickname_map = {
            str(row.get("account_number") or ""): _account_label(row.get("account_type"), row.get("nickname"))
            for row in broker_accounts
        }
        user_positions = get_user_positions(user_id, run_id=latest_run.get("run_id"))
        account_value = sum(float(p.get("market_value") or 0) for p in user_positions if p.get("position_type") != "options")
        account_value = round(account_value, 2) if account_value else None

        calendars = []
        for row in get_user_option_positions(user_id, run_id=latest_run.get("run_id")):
            payload = {}
            try:
                payload = _json.loads(row.get("calendar_json") or "{}")
            except Exception:
                payload = {}
            account_name = _account_label(
                payload.get("account_label") or row.get("account_type"),
                None,
            )
            calendars.append({
                "ticker": str(row.get("underlying") or "").upper(),
                "option_type": str(row.get("option_type") or "").upper(),
                "front_expiration": row.get("front_expiration"),
                "back_expiration": row.get("back_expiration"),
                "action": str(row.get("action") or payload.get("action") or "MONITOR").upper(),
                "pnl_pct_estimate": payload.get("pnl_pct_estimate") or payload.get("gain_pct_estimate"),
                "account_name": account_name,
            })

        verticals = []
        for row in user_positions:
            if row.get("position_type") != "options":
                continue
            details = {}
            try:
                details = _json.loads(row.get("option_details") or "{}")
            except Exception:
                details = {}
            if str(details.get("strategy_type") or "") != "skew_vertical":
                continue
            verticals.append({
                "ticker": str(row.get("ticker") or "").upper(),
                "option_type": str(details.get("option_type") or "").upper(),
                "long_strike": details.get("legs", [{}])[0].get("strike") if details.get("legs") else None,
                "short_strike": details.get("legs", [{}, {}])[1].get("strike") if len(details.get("legs") or []) > 1 else None,
                "expiration": details.get("expiration"),
                "dte": next((leg.get("dte") for leg in (details.get("legs") or []) if leg.get("dte") is not None), details.get("dte")),
                "exit_signal": str(details.get("exit_signal") or "HOLD").upper(),
                "unrealized_pnl_pct": row.get("unrealized_pnl_pct"),
                "current_value": details.get("current_value"),
                "account_name": nickname_map.get(str(row.get("account_number") or ""), _account_label(row.get("account_type"))),
            })

        if not calendars and not verticals:
            return '<div class="section"><h2>Open Positions</h2><p class="empty">No open option structures detected in latest personalization run.</p></div>', account_value

        cards = []
        for cal in calendars:
            tone = "watch" if cal["action"].startswith("MONITOR") else "fail" if cal["action"].startswith("EXIT") else "muted"
            cards.append(
                '<div class="position-row">'
                f'<span class="ticker">{escape(cal["ticker"])}</span>'
                f'{_dashboard_badge("Calendar", "muted")}'
                f'<span>{escape(cal["option_type"] or "OPTION")} Calendar</span>'
                f'<span>{escape(str(cal["front_expiration"] or "—"))} / {escape(str(cal["back_expiration"] or "—"))}</span>'
                f'{_dashboard_badge(cal["action"], tone)}'
                f'<span>{escape(cal["account_name"])}</span>'
                f'<span>{escape(_format_pct(cal["pnl_pct_estimate"]))}</span>'
                '</div>'
            )
        for vertical in verticals:
            tone = "watch" if vertical["exit_signal"].startswith("MONITOR") or vertical["exit_signal"] == "HOLD" else "fail"
            cards.append(
                '<div class="position-row">'
                f'<span class="ticker">{escape(vertical["ticker"])}</span>'
                f'{_dashboard_badge("Vertical", "muted")}'
                f'<span>{escape(str(vertical["long_strike"] or "—"))} / {escape(str(vertical["short_strike"] or "—"))} {escape(vertical["option_type"] or "OPTION")}</span>'
                f'<span>{escape(str(vertical["expiration"] or "—"))}</span>'
                f'<span>{escape(str(vertical["dte"] or "—"))} DTE</span>'
                f'{_dashboard_badge(vertical["exit_signal"], tone)}'
                f'<span>{escape(vertical["account_name"])}</span>'
                f'<span>{escape(_format_pct(vertical["unrealized_pnl_pct"]))}</span>'
                '</div>'
            )
        return '<div class="section"><h2>Open Positions</h2>' + "".join(cards) + "</div>", account_value
    except Exception:
        return '<div class="section"><h2>Open Positions</h2><p class="empty">Open positions unavailable.</p></div>', None


def _build_strategy_section(title: str, strategy_id: str, result: dict[str, Any], note: str | None = None) -> str:
    rows = list(result.get("canonical_opportunities") or result.get("rows") or [])
    rows.sort(key=lambda item: (_dashboard_tier(item), _dashboard_score(item)), reverse=True)
    counts = (
        f'{int(result.get("pass_count", 0))} PASS · '
        f'{int(result.get("watch_count", 0))} WATCH · '
        f'{int(result.get("fail_count", 0))} FAIL'
    )
    body = []
    for row in rows[:5]:
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else row
        ticker = _dashboard_resolved_ticker(row)
        verdict = _dashboard_resolved_verdict(row)
        detail_bits = []
        if strategy_id == "forward_factor_calendar":
            ff_value = raw.get("source_forward_factor")
            if ff_value is None:
                ff_value = raw.get("diagnostic_raw_iv_forward_factor") or raw.get("forward_factor")
            if isinstance(ff_value, (int, float)):
                detail_bits.append(f"FF {ff_value:.3f}")
        if strategy_id == "earnings_calendar":
            if raw.get("date_confidence"):
                detail_bits.append(str(raw.get("date_confidence")))
            if raw.get("days_until_earnings") is not None:
                detail_bits.append(f"{raw.get('days_until_earnings')}d")
        if strategy_id in {"stock_momentum", "skew_momentum_vertical"} and isinstance(_dashboard_score(row), (int, float)):
            detail_bits.append(f"Score {_dashboard_score(row):.1f}")
        if strategy_id == "skew_momentum_vertical" and raw.get("direction"):
            detail_bits.append(str(raw.get("direction")))
        detail_html = " · ".join(escape(str(bit)) for bit in detail_bits if bit)
        onclick_js = (
            f"trackSignalView({json.dumps(ticker)}, {json.dumps(strategy_id)}, {json.dumps(verdict)})"
        )
        verdict_upper = verdict.upper()
        badge_tone = "pass" if verdict_upper.startswith("PASS") else "watch" if verdict_upper.startswith("WATCH") else "fail"
        body.append(
            f'<div class="signal-row {_verdict_cls(verdict)}" '
            f'onclick="{escape(onclick_js, quote=True)}">'
            f'<span class="ticker">{escape(ticker)}</span>'
            f'{_dashboard_badge(verdict, badge_tone)}'
            f'<span>{detail_html or escape("Signal ready")}</span>'
            '</div>'
        )
    if not body:
        empty = '<p class="empty">No rows this run.</p>'
    else:
        empty = "".join(body)
    note_html = f'<span class="dry-note">{escape(note)}</span>' if note else ""
    return (
        '<div class="strategy-section">'
        f'<div class="section-title"><h3>{escape(title)}</h3><span>{_dashboard_badge(counts, "muted")} {note_html}</span></div>'
        f'{empty}</div>'
    )


def _build_signals_html(report: dict) -> str:
    try:
        tradier = (report or {}).get("tradier_snapshot", {}) or {}
        strategy_results = tradier.get("_strategy_results", {}) or {}
        stock = strategy_results.get("stock_momentum", {})
        ff = strategy_results.get("forward_factor_calendar", {})
        calendar = strategy_results.get("earnings_calendar", {})
        skew = strategy_results.get("skew_momentum_vertical", {})
        return (
            '<div class="section"><h2>Today\'s Signals</h2><div class="strategy-grid">'
            + _build_strategy_section("Stock Momentum", "stock_momentum", stock)
            + _build_strategy_section("Forward Factor Calendar", "forward_factor_calendar", ff, "dry-run: signal live, execution gated")
            + _build_strategy_section("Earnings Calendar", "earnings_calendar", calendar)
            + _build_strategy_section("Skew Momentum Verticals", "skew_momentum_vertical", skew)
            + '</div></div>'
        )
    except Exception:
        return '<div class="section"><h2>Today\'s Signals</h2><p class="empty">Signals unavailable.</p></div>'


SCREENER_VERDICT_DISPLAY = {
    "PASS": ("PASS", "pass", "Tradeable signal"),
    "WATCH": ("WATCH", "watch", "Worth monitoring"),
    "NEAR_MISS": ("NEAR MISS", "near", "Close — check manually"),
    "FAIL": ("FILTERED", "fail", "Filtered by risk gates"),
    "SKIP": ("SKIPPED", "muted", "Outside scan window"),
    "DIAGNOSTIC": ("RESEARCH", "info", "Signal tracked, not live"),
}


def _public_screener_verdict_meta(verdict: str, raw: dict[str, Any] | None = None) -> tuple[str, str, str]:
    verdict_text = str(verdict or "UNKNOWN").strip() or "UNKNOWN"
    verdict_upper = verdict_text.upper()
    raw = raw or {}
    if "DRY RUN" in verdict_upper or bool(raw.get("dry_run")):
        return SCREENER_VERDICT_DISPLAY["DIAGNOSTIC"]
    if verdict_upper.startswith(("PASS", "CONSIDER ADDING", "ADD ON", "HIGH-PRIORITY CONSIDER ADDING")):
        return SCREENER_VERDICT_DISPLAY["PASS"]
    if verdict_upper.startswith("WATCH"):
        return SCREENER_VERDICT_DISPLAY["WATCH"]
    if verdict_upper.startswith(("HIGH_SIGNAL_UNTRADEABLE", "NEAR")):
        return SCREENER_VERDICT_DISPLAY["NEAR_MISS"]
    if verdict_upper.startswith(("SKIP", "NOT EVALUATED")):
        return SCREENER_VERDICT_DISPLAY["SKIP"]
    return SCREENER_VERDICT_DISPLAY["FAIL"]


def _public_signal_tone(row: dict[str, Any]) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    _, tone, _ = _public_screener_verdict_meta(_dashboard_resolved_verdict(row), raw)
    return tone


def _public_signal_confidence(row: dict[str, Any]) -> int:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    text = str(row.get("data_confidence") or row.get("confidence") or raw.get("data_confidence") or raw.get("confidence") or "").strip().lower()
    if text == "high":
        return 3
    if text == "medium":
        return 2
    if text == "low":
        return 1
    return 0


def _public_liquidity_rank(row: dict[str, Any]) -> int:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    status = str(
        row.get("liquidity_status")
        or ((row.get("liquidity_result") or {}).get("status") if isinstance(row.get("liquidity_result"), dict) else None)
        or raw.get("liquidity_status")
        or ((raw.get("liquidity_result") or {}).get("status") if isinstance(raw.get("liquidity_result"), dict) else None)
        or ""
    ).upper()
    if status == "PASS":
        return 3
    if status == "WATCH":
        return 2
    if status == "FAIL":
        return 1
    return 0


def _public_reason_fragments(row: dict[str, Any]) -> list[str]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    candidates: list[Any] = [
        row.get("display_reason"),
        row.get("primary_reason"),
        row.get("why"),
        row.get("why_combined"),
        row.get("notes"),
        row.get("blocking_reason"),
        row.get("blocking_reasons"),
        row.get("gate_failures"),
        row.get("failed_gates"),
        row.get("diagnostics"),
        raw.get("primary_reason"),
        raw.get("why"),
        raw.get("blocking_reasons"),
    ]
    bits: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        if isinstance(value, (list, tuple, set)):
            for item in value:
                add(item)
            return
        if isinstance(value, dict):
            for key in ("reason", "message", "summary", "detail"):
                if value.get(key):
                    add(value.get(key))
            return
        text = str(value or "").strip()
        if not text:
            return
        normalized = " ".join(text.split())
        if normalized not in seen:
            seen.add(normalized)
            bits.append(normalized)

    for item in candidates:
        add(item)

    fields = [
        ("Date confidence", row.get("date_confidence") or raw.get("date_confidence")),
        ("Days to earnings", row.get("days_until_earnings") if row.get("days_until_earnings") is not None else raw.get("days_until_earnings")),
        ("Front DTE", row.get("front_dte") if row.get("front_dte") is not None else raw.get("front_dte")),
        ("Back DTE", row.get("back_dte") if row.get("back_dte") is not None else raw.get("back_dte")),
        ("IV relationship", row.get("iv_relationship") or raw.get("iv_relationship")),
        ("Forward Factor", row.get("source_forward_factor") if row.get("source_forward_factor") is not None else raw.get("source_forward_factor")),
        ("Diagnostic FF", row.get("diagnostic_raw_iv_forward_factor") if row.get("diagnostic_raw_iv_forward_factor") is not None else raw.get("diagnostic_raw_iv_forward_factor")),
        ("Spread width", row.get("spread_width") if row.get("spread_width") is not None else raw.get("spread_width")),
        ("Bid/ask spread", row.get("bid_ask_spread") if row.get("bid_ask_spread") is not None else raw.get("bid_ask_spread")),
        ("Open interest", row.get("open_interest") if row.get("open_interest") is not None else raw.get("open_interest")),
        ("Volume", row.get("volume") if row.get("volume") is not None else raw.get("volume")),
        ("Avg volume", row.get("avg_volume") if row.get("avg_volume") is not None else raw.get("avg_volume")),
    ]
    for label, value in fields:
        if value is None or value == "":
            continue
        if isinstance(value, float):
            if "Factor" in label:
                add(f"{label}: {value:.3f}")
            else:
                add(f"{label}: {value:.2f}")
        else:
            add(f"{label}: {value}")

    if row.get("stale_structure") or raw.get("stale_structure"):
        add("Structure flagged stale.")
    if row.get("near_miss") or raw.get("near_miss"):
        add("Near miss setup.")
    if row.get("can_enter_daily_opportunity") is False or raw.get("can_enter_daily_opportunity") is False:
        add("Not eligible for Daily Opportunity.")
    if row.get("can_trade_live") is False or raw.get("can_trade_live") is False:
        add("Not live-tradable.")

    return bits or ["No detailed reason available."]


def _public_reason_summary(row: dict[str, Any]) -> str:
    bits = _public_reason_fragments(row)
    return bits[0] if bits else "No detailed reason available."


def _public_detail_pairs(row: dict[str, Any]) -> list[tuple[str, str]]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    details: list[tuple[str, str]] = []

    def grab(*keys: str):
        for key in keys:
            if row.get(key) not in (None, ""):
                return row.get(key)
            if raw.get(key) not in (None, ""):
                return raw.get(key)
        return None

    score = _dashboard_score(row)
    if score:
        details.append(("Score", f"{score:.1f}"))
    confidence = grab("data_confidence", "confidence")
    if confidence:
        details.append(("Confidence", str(confidence)))
    earnings_conf = grab("earnings_confidence", "date_confidence", "earnings_date_confidence")
    if earnings_conf:
        details.append(("Date trust", str(earnings_conf)))
    earnings_date = grab("earnings_date", "date")
    earnings_time = grab("earnings_time", "session_label")
    if earnings_date:
        details.append(("Earnings", f"{earnings_date}{(' ' + str(earnings_time)) if earnings_time else ''}"))
    sources = grab("date_sources", "sources_seen")
    if isinstance(sources, (list, tuple)) and sources:
        details.append(("Sources", f"{len(sources)}: {', '.join(str(item) for item in sources[:2])}"))
    liquidity = grab("liquidity_status")
    if not liquidity and isinstance(grab("liquidity_result"), dict):
        liquidity = grab("liquidity_result").get("status")
    if liquidity:
        details.append(("Liquidity", str(liquidity)))
    front_dte = grab("front_dte")
    back_dte = grab("back_dte")
    if front_dte is not None or back_dte is not None:
        if front_dte is not None and back_dte is not None:
            details.append(("DTE", f"{front_dte} / {back_dte}"))
        else:
            details.append(("DTE", str(front_dte if front_dte is not None else back_dte)))
    expiration_pair = grab("expiration_pair")
    if isinstance(expiration_pair, dict):
        front = expiration_pair.get("front") or expiration_pair.get("front_expiration")
        back = expiration_pair.get("back") or expiration_pair.get("back_expiration")
        if front or back:
            details.append(("Expirations", f"{front or '—'} / {back or '—'}"))
    forward_factor = grab("source_forward_factor")
    diagnostic_ff = grab("diagnostic_raw_iv_forward_factor", "forward_factor")
    if forward_factor is not None:
        details.append(("Forward Factor", f"{float(forward_factor):.3f}"))
    elif diagnostic_ff is not None:
        details.append(("Diagnostic FF", f"{float(diagnostic_ff):.3f}"))
    front_iv = grab("front_iv", "front_raw_iv")
    back_iv = grab("back_iv", "back_raw_iv")
    if front_iv is not None or back_iv is not None:
        details.append(("IV", f"{front_iv if front_iv is not None else '—'} / {back_iv if back_iv is not None else '—'}"))
    source_iv_status = grab("source_iv_status")
    if source_iv_status:
        details.append(("FF source mode", str(source_iv_status)))
    contamination = grab("earnings_contamination_reason")
    if row.get("earnings_contaminated") or raw.get("earnings_contaminated"):
        details.append(("Earnings risk", str(contamination or "Contaminated")))
    debit = grab("net_debit", "conservative_debit")
    if isinstance(debit, (int, float)):
        details.append(("Debit", _format_currency(float(debit))))
    structure_status = grab("structure_status")
    if structure_status:
        details.append(("Structure", str(structure_status)))
    can_enter = grab("can_enter_daily_opportunity")
    if can_enter is not None:
        details.append(("Daily Opp", "Yes" if bool(can_enter) else "No"))
    can_trade = grab("can_trade_live")
    if can_trade is not None:
        details.append(("Live trade", "Yes" if bool(can_trade) else "No"))
    return details


def _public_fail_priority(row: dict[str, Any]) -> tuple[int, float]:
    verdict = _dashboard_resolved_verdict(row).upper()
    reason_blob = " ".join(_public_reason_fragments(row)).upper()
    score = _dashboard_score(row)
    if score >= 80:
        bucket = 0
    elif "UNTRADEABLE" in verdict or "UNTRADEABLE" in reason_blob:
        bucket = 1
    elif "DATE" in reason_blob or "EARNINGS" in reason_blob:
        bucket = 2
    elif "LIQUID" in reason_blob or "SPREAD" in reason_blob or "VOLUME" in reason_blob or "OPEN INTEREST" in reason_blob:
        bucket = 3
    elif "DTE" in reason_blob or "EXPIRATION" in reason_blob:
        bucket = 4
    elif "IV" in reason_blob:
        bucket = 5
    elif "STALE" in reason_blob:
        bucket = 6
    else:
        bucket = 7
    return bucket, -score


def _sort_public_candidates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda item: (_dashboard_tier(item), _dashboard_score(item), _public_signal_confidence(item), _public_liquidity_rank(item)),
        reverse=True,
    )


def _public_row_card(row: dict[str, Any], strategy_id: str) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    ticker = _dashboard_resolved_ticker(row)
    verdict = _dashboard_resolved_verdict(row)
    label, tone, tooltip = _public_screener_verdict_meta(verdict, raw)
    details = _public_detail_pairs(row)
    detail_html = "".join(
        f'<span class="mini">{escape(label_text)}: {escape(value_text)}</span>'
        for label_text, value_text in details[:6]
    )
    reason_bits = _public_reason_fragments(row)
    primary_reason = reason_bits[0]
    why_html = ""
    if len(reason_bits) > 1:
        why_html = f'<div class="row-why"><strong>More context:</strong> {escape(reason_bits[1])}</div>'
    extra_badges = []
    if strategy_id == "forward_factor_calendar" and bool(config.FORWARD_FACTOR_DRY_RUN):
        extra_badges.append(_dashboard_badge("DRY RUN", "info"))
    if raw.get("is_diagnostic_only") or row.get("is_diagnostic_only"):
        extra_badges.append(_dashboard_badge("Research Only", "info"))
    if raw.get("can_enter_daily_opportunity") is False or row.get("can_enter_daily_opportunity") is False:
        extra_badges.append(_dashboard_badge("Not in Daily Opportunity", "muted"))
    if (row.get("date_confidence") or raw.get("date_confidence")) == "single_source":
        extra_badges.append(_dashboard_badge("Single source — verify", "watch"))
    if row.get("date_conflict") or raw.get("date_conflict"):
        extra_badges.append(_dashboard_badge("Date conflict", "fail"))
    card_payload = {
        "strategy_id": strategy_id,
        "ticker": ticker,
        "verdict": label,
    }
    return (
        f'<div class="demo-row demo-row-{tone}" data-demo-card="1" data-demo="{escape(json.dumps(card_payload), quote=True)}">'
        f'<div class="demo-row-head"><div><h4>{escape(ticker)}</h4><div class="demo-badges"><span class="badge badge-{escape(tone)}" title="{escape(tooltip, quote=True)}">{escape(label)}</span>{"".join(extra_badges)}</div></div></div>'
        f'<div class="row-summary">{escape(primary_reason)}</div>'
        f'<div class="mini-grid">{detail_html}</div>'
        f'{why_html}'
        '</div>'
    )


def _build_public_strategy_section(title: str, strategy_id: str, result: dict[str, Any], explainer: dict[str, str], dry_run: bool = False) -> str:
    rows = list(result.get("canonical_opportunities") or result.get("rows") or result.get("items") or [])
    ordered = _sort_public_candidates(rows)
    top_rows = [row for row in ordered if not _dashboard_resolved_verdict(row).upper().startswith("FAIL")][:5]
    fail_rows = sorted(
        [row for row in ordered if _dashboard_resolved_verdict(row).upper().startswith("FAIL")],
        key=_public_fail_priority,
    )[:4]
    counts_html = (
        _dashboard_badge(f'PASS {int(result.get("pass_count", 0))}', "pass")
        + _dashboard_badge(f'WATCH {int(result.get("watch_count", 0))}', "watch")
        + _dashboard_badge(f'FAIL {int(result.get("fail_count", 0))}', "fail")
    )
    anchors = {
        "stock_momentum": "stock-momentum",
        "forward_factor_calendar": "forward-factor",
        "earnings_calendar": "earnings-calendar",
        "skew_momentum_vertical": "skew-verticals",
    }
    anchor = anchors.get(strategy_id, strategy_id.replace("_", "-"))
    dry_html = _dashboard_badge("DRY RUN", "info") if dry_run else ""
    candidate_html = "".join(_public_row_card(row, strategy_id) for row in top_rows) or '<p class="empty">No top candidates this run.</p>'
    rejected_html = "".join(_public_row_card(row, strategy_id) for row in fail_rows) or '<p class="empty">No rejected examples this run.</p>'
    return (
        f'<section class="demo-section" id="{escape(anchor)}">'
        f'<div class="section-title"><div><h2>{escape(title)}</h2><p class="muted">{escape(explainer["short"])}</p></div><div class="demo-badges">{counts_html}{dry_html}<a class="mini anchor-link" href="#{escape(anchor)}" data-demo-copy-link="1" data-anchor="{escape(anchor)}">Copy link</a></div></div>'
        f'<div class="strategy-copy"><div><strong>Why it exists</strong><p>{escape(explainer["why"])}</p></div>'
        f'<div><strong>What blocks a trade</strong><p>{escape(explainer["blocks"])}</p></div>'
        f'<div><strong>Current status</strong><p>{escape(explainer["status"])}</p></div></div>'
        f'<div class="section-subcopy">{escape(explainer["matters"])}</div>'
        f'{f"<div class=\"note\">Forward Factor is being observed in dry-run mode. PASS means volatility relationship looked attractive, not that ASA is ready to recommend live trade.</div>" if strategy_id == "forward_factor_calendar" else ""}'
        f'<div class="demo-group"><h3>Top candidates</h3>{candidate_html}</div>'
        f'<div class="demo-group"><h3>Rejected by Risk Filters</h3><p class="muted">Rejected trades are part of edge. System shows what looked interesting, what failed, why trade stayed blocked.</p>{rejected_html}</div>'
        '</section>'
    )


def _build_public_screener_context() -> dict[str, Any] | None:
    snapshot, report, tradier = _load_dashboard_core_report()
    if not snapshot or not report:
        return None
    strategies = (tradier or {}).get("_strategy_results", {}) or {}
    pipeline = (tradier or {}).get("_pipeline_status", {}) or {}
    count_total = 0
    for sid in ("stock_momentum", "forward_factor_calendar", "earnings_calendar", "skew_momentum_vertical"):
        result = strategies.get(sid, {}) or {}
        count_total += int(result.get("pass_count", 0) or 0) + int(result.get("watch_count", 0) or 0) + int(result.get("fail_count", 0) or 0)
    ff = (tradier.get("_forward_factor_strategy") or {}) if isinstance(tradier, dict) else {}
    ff_stage = (ff.get("stage_counts") or ((ff.get("summary") or {}).get("stage_counts")) or {}) if isinstance(ff, dict) else {}
    earnings_quality = (tradier.get("_earnings_discovery_quality") or {}) if isinstance(tradier, dict) else {}
    earnings_rows = list((tradier.get("_earnings_calendar_strategy") or {}).get("items") or [])
    single_source_count = sum(1 for row in earnings_rows if str(row.get("date_confidence") or row.get("earnings_date_confidence") or "").lower() == "single_source")
    conflict_count = sum(1 for row in earnings_rows if bool(row.get("date_conflict")))
    coverage = {
        "run_mode": snapshot.get("mode") or ((tradier.get("_pipeline_status") or {}).get("run_mode") if isinstance(tradier, dict) else None) or "unknown",
        "ff_universe": int(ff_stage.get("universe", 0) or len(ff.get("scanned_tickers") or []) or 0),
        "ff_evaluated": int(ff_stage.get("cheap_evaluated", 0) or 0),
        "ff_skipped_dev_cap": int(ff_stage.get("skipped_dev_cap", 0) or 0),
        "ff_skipped_provider_budget": int(ff_stage.get("skipped_provider_budget", 0) or 0),
        "ff_chain_sets": int(ff_stage.get("chain_sets", 0) or 0),
        "earnings_candidates_returned": int(earnings_quality.get("passed_count", 0) or len(earnings_quality.get("items") or []) or 0),
        "skew_universe_cap": int(getattr(config, "SKEW_UNIVERSE_MAX_CANDIDATES", 50) or 50),
        "warnings": [],
    }
    if coverage["ff_skipped_dev_cap"] > 0:
        coverage["warnings"].append("Forward Factor demo looks capped by dev limits.")
    if coverage["ff_skipped_provider_budget"] > 0:
        coverage["warnings"].append("Forward Factor demo looks capped by provider budget.")
    if str(coverage["run_mode"]).lower() == "dev":
        coverage["warnings"].append("Run mode is dev; demo may show fewer opportunities than production.")
    if coverage["earnings_candidates_returned"] <= 6 and str(coverage["run_mode"]).lower() == "dev":
        coverage["warnings"].append("Earnings discovery appears narrow in this dev run.")
    earnings_trust = {
        "single_source_count": single_source_count,
        "conflict_count": conflict_count,
        "provider_order": list(getattr(config, "EARNINGS_PROVIDER_ORDER", ["finnhub", "alphavantage"]) or []),
    }
    freshness = _run_freshness_meta(snapshot.get("completed_at"))
    return {
        "snapshot": snapshot,
        "report": report,
        "tradier": tradier,
        "run_id": snapshot.get("run_id"),
        "generated_at": snapshot.get("completed_at"),
        "run_timestamp_human": freshness["timestamp_human"],
        "run_age_seconds": freshness["age_seconds"],
        "run_age_label": freshness["age_label"],
        "run_age_class": freshness["age_class"],
        "run_quality": pipeline.get("report_quality") or pipeline.get("overall_status") or "UNKNOWN",
        "signals_found": count_total,
        "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
        "strategy_results": strategies,
        "coverage": coverage,
        "earnings_trust": earnings_trust,
    }


_PUBLIC_SCREENER_CSS = """
body{background:#050816;color:#e5e7eb;font-family:Inter,ui-sans-serif,system-ui,sans-serif;margin:0;line-height:1.45}
.wrap{max-width:1180px;margin:0 auto;padding:1.2rem}
.topnav{position:sticky;top:0;z-index:20;display:flex;gap:.55rem;flex-wrap:nowrap;overflow:auto;padding:.7rem 1.2rem;background:rgba(5,8,22,.93);backdrop-filter:blur(8px);border-bottom:1px solid rgba(148,163,184,.18)}
.topnav a{color:#e5e7eb;text-decoration:none;white-space:nowrap;padding:.35rem .6rem;border-radius:999px;border:1px solid rgba(148,163,184,.16);background:#09101c;font-size:.84rem}
.topnav a:hover{text-decoration:none;border-color:rgba(96,165,250,.32)}
.hero,.demo-section,.copy-band,.cta-band{background:#0b1220;border:1px solid rgba(148,163,184,.18);border-radius:10px;padding:1rem 1.1rem;margin:0 0 1rem}
.hero h1,.demo-section h2,.copy-band h2{margin:.1rem 0 .45rem}
.hero p,.muted,.copy-band p,.strategy-copy p,.section-subcopy{color:#cbd5e1}
.run-freshness{display:flex;align-items:center;gap:.5rem;font-size:.82rem;color:#94a3b8;margin:.35rem 0 1rem}
.run-freshness .age.fresh{color:#22c55e}.run-freshness .age.recent{color:#f59e0b}.run-freshness .age.stale{color:#ef4444}
.demo-badges,.mini-grid,.summary-badges{display:flex;gap:.45rem;flex-wrap:wrap;align-items:center}
.badge{display:inline-flex;align-items:center;gap:.3rem;border:1px solid rgba(148,163,184,.28);border-radius:999px;padding:.2rem .55rem;font-size:.78rem;background:#09101c}
.badge-pass{color:#22c55e;border-color:rgba(34,197,94,.4)}
.badge-watch{color:#f59e0b;border-color:rgba(245,158,11,.4)}
.badge-fail{color:#ef4444;border-color:rgba(239,68,68,.4)}
.badge-info{color:#60a5fa;border-color:rgba(96,165,250,.4)}
.badge-near{color:#c084fc;border-color:rgba(192,132,252,.4)}
.badge-muted{color:#cbd5e1}
.hero-grid,.strategy-copy,.guide-grid,.cta-grid{display:grid;gap:.8rem}
.hero-grid{grid-template-columns:2fr 1fr}
.guide-grid,.strategy-copy{grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.section-title{display:flex;justify-content:space-between;align-items:flex-start;gap:1rem;flex-wrap:wrap}
.demo-group{margin-top:1rem}
.demo-group h3{margin:.2rem 0 .5rem}
.demo-row{border:1px solid rgba(148,163,184,.18);border-radius:8px;padding:.8rem;margin:.55rem 0;background:#07101b}
.demo-row-pass{border-color:rgba(34,197,94,.32)}
.demo-row-watch{border-color:rgba(245,158,11,.32)}
.demo-row-fail{border-color:rgba(239,68,68,.32)}
.demo-row-near{border-color:rgba(192,132,252,.32)}
.demo-row-info{border-color:rgba(96,165,250,.32)}
.demo-row-head{display:flex;justify-content:space-between;gap:.8rem;align-items:flex-start}
.demo-row h4{margin:0 0 .25rem}
.row-summary{margin:.55rem 0;color:#f8fafc}
.row-why{margin-top:.45rem;color:#cbd5e1;font-size:.92rem}
.mini{font-size:.8rem;color:#cbd5e1;background:#0a1322;border-radius:999px;padding:.18rem .45rem;border:1px solid rgba(148,163,184,.18)}
.copy-band ul{margin:.5rem 0 0 1rem;padding:0}
.empty{color:#94a3b8}
.cta-grid{grid-template-columns:repeat(auto-fit,minmax(220px,1fr));align-items:center}
.cta-buttons{display:flex;gap:.7rem;flex-wrap:wrap}
.btn{display:inline-flex;align-items:center;justify-content:center;padding:.7rem 1rem;border-radius:8px;text-decoration:none;font-weight:600}
.btn-primary{background:#22c55e;color:#04110a}
.btn-secondary{border:1px solid rgba(148,163,184,.28);color:#e5e7eb;background:#09101c}
.note{font-size:.84rem;color:#94a3b8}
.coverage-grid{display:grid;gap:.55rem;grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
.anchor-link{text-decoration:none}
html{scroll-behavior:smooth}
@media (max-width:800px){.hero-grid{grid-template-columns:1fr}.wrap{padding:.9rem}.hero,.demo-section,.copy-band,.cta-band{padding:.9rem}}
"""


_PUBLIC_SCREENER_HTML = """<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>ASA Screener Demo</title><style>{css}</style></head><body><div class="wrap">{body}</div></body></html>"""


def _render_public_screener(context: dict[str, Any]) -> str:
    strategies = context.get("strategy_results", {}) or {}
    run_quality = str(context.get("run_quality") or "UNKNOWN")
    quality_tone = "pass" if run_quality.upper() == "SUCCESS_COMPLETE" else "watch"
    generated_at = str(context.get("generated_at") or "—")
    run_timestamp_human = str(context.get("run_timestamp_human") or generated_at[:16].replace("T", " ") + " UTC")
    run_age_label = str(context.get("run_age_label") or "age unavailable")
    run_age_class = str(context.get("run_age_class") or "stale")
    coverage = context.get("coverage") or {}
    earnings_trust = context.get("earnings_trust") or {}
    explainers = {
        "stock_momentum": {
            "short": "Looks for stocks showing strong trend or relative-strength behavior worth attention before options structure selection.",
            "why": "Momentum can identify where institutional demand is already showing up.",
            "blocks": "Weak trend, overextension, lack of confirmation, or poor risk/reward.",
            "status": "Live signal module",
            "matters": "Stock momentum often answers whether market already agrees with idea before structure complexity begins.",
        },
        "earnings_calendar": {
            "short": "Looks for earnings-driven calendar spread candidates where event volatility and expiration spacing can create usable structure.",
            "why": "Structure can benefit from volatility differences, time decay behavior, and post-event repricing when setup is liquid and correctly timed.",
            "blocks": "Unverified earnings date, bad expiration pair, adverse IV relationship, low liquidity, too little DTE, wide spreads, or stale structure.",
            "status": "Live strategy with safety gates",
            "matters": "Calendars can look exciting fast. Filters matter more than excitement.",
        },
        "skew_momentum_vertical": {
            "short": "Looks for directional debit spreads where momentum agrees with options skew and overpriced wings can help finance entry.",
            "why": "Trade can use inflated wing pricing to reduce debit while staying aligned with trend.",
            "blocks": "No momentum confirmation, weak skew edge, poor reward/risk, low open interest, poor volume, or wide bid/ask spreads.",
            "status": "Live strategy with safety gates",
            "matters": "Skew without trend, or trend without fair pricing, is usually not enough.",
        },
        "forward_factor_calendar": {
            "short": "Looks for calendar or double-calendar setups where forward volatility appears cheap relative to front volatility.",
            "why": "If near-term volatility is overpriced compared with forward volatility, structure may capture favorable volatility relationship.",
            "blocks": "Forward factor below threshold, poor liquidity, bad expiration spacing, unstable pricing, or structure not yet validated.",
            "status": "Dry-run only — visible for research, not promoted as a live trade recommendation.",
            "matters": "Dry-run research can teach where signal exists before system is trusted to recommend live action.",
        },
    }
    sections_html = (
        _build_public_strategy_section("Stock Momentum", "stock_momentum", strategies.get("stock_momentum", {}) or {}, explainers["stock_momentum"])
        + _build_public_strategy_section("Forward Factor Calendar", "forward_factor_calendar", strategies.get("forward_factor_calendar", {}) or {}, explainers["forward_factor_calendar"], dry_run=True)
        + _build_public_strategy_section("Earnings Calendar", "earnings_calendar", strategies.get("earnings_calendar", {}) or {}, explainers["earnings_calendar"])
        + _build_public_strategy_section("Skew Momentum Verticals", "skew_momentum_vertical", strategies.get("skew_momentum_vertical", {}) or {}, explainers["skew_momentum_vertical"])
    )
    nav_html = """
<nav class="topnav">
  <a href="#stock-momentum" data-demo-nav="stock_momentum">Stock Momentum</a>
  <a href="#forward-factor" data-demo-nav="forward_factor_calendar">Forward Factor</a>
  <a href="#earnings-calendar" data-demo-nav="earnings_calendar">Earnings Calendar</a>
  <a href="#skew-verticals" data-demo-nav="skew_momentum_vertical">Skew Verticals</a>
  <a href="#why-rejects" data-demo-nav="why_rejects">Why ASA Rejects Trades</a>
  <a href="#cta" data-demo-nav="cta">Create Account</a>
</nav>
"""
    coverage_html = f"""
<section class="copy-band">
  <h2>Scan Coverage</h2>
  <div class="coverage-grid">
    <div><strong>Universe Discovery</strong><p>enabled</p></div>
    <div><strong>Core universe source</strong><p>S&amp;P 500 + Russell supplement</p></div>
    <div><strong>FF universe</strong><p>{int(coverage.get('ff_universe', 0))}</p></div>
    <div><strong>FF evaluated</strong><p>{int(coverage.get('ff_evaluated', 0))}</p></div>
    <div><strong>FF skipped by dev cap</strong><p>{int(coverage.get('ff_skipped_dev_cap', 0))}</p></div>
    <div><strong>FF skipped by provider budget</strong><p>{int(coverage.get('ff_skipped_provider_budget', 0))}</p></div>
    <div><strong>Earnings candidates returned</strong><p>{int(coverage.get('earnings_candidates_returned', 0))}</p></div>
    <div><strong>Skew universe cap</strong><p>{int(coverage.get('skew_universe_cap', 0))}</p></div>
  </div>
  {''.join(f'<p class="note">{escape(str(w))}</p>' for w in (coverage.get('warnings') or []))}
</section>
"""
    demo_js = f"""
<script>
(function(){{
  const runId = {json.dumps(str(context.get("run_id") or ""))};
  const page = "/screener";
  let sid = null;
  try {{
    sid = localStorage.getItem("asa_demo_sid");
    if (!sid) {{
      sid = "demo_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem("asa_demo_sid", sid);
    }}
  }} catch (_err) {{
    sid = "demo_fallback";
  }}
  function send(ev) {{
    try {{
      fetch("/api/telemetry/public-demo", {{
        method: "POST",
        headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify(Object.assign({{ session_id: sid, page: page, run_id: runId }}, ev))
      }});
    }} catch (_err) {{}}
  }}
  send({{ event_type: "page_view", action: "load" }});
  document.querySelectorAll("[data-demo-nav]").forEach((el) => {{
    el.addEventListener("click", () => send({{ event_type: "strategy_nav_click", strategy_id: el.getAttribute("data-demo-nav"), action: "nav_click" }}));
  }});
  document.querySelectorAll("[data-demo-card]").forEach((el) => {{
    el.addEventListener("click", () => {{
      try {{
        const payload = JSON.parse(el.getAttribute("data-demo") || "{{}}");
        send({{ event_type: "signal_card_click", strategy_id: payload.strategy_id || null, ticker: payload.ticker || null, verdict: payload.verdict || null, action: "card_click" }});
      }} catch (_err) {{}}
    }});
  }});
  document.querySelectorAll("[data-demo-cta]").forEach((el) => {{
    el.addEventListener("click", () => send({{ event_type: "cta_click", action: el.getAttribute("data-demo-cta") || "cta_click" }}));
  }});
  document.querySelectorAll("[data-demo-copy-link]").forEach((el) => {{
    el.addEventListener("click", (evt) => {{
      const anchor = el.getAttribute("data-anchor");
      const url = window.location.origin + window.location.pathname + "#" + anchor;
      if (navigator.clipboard && anchor) {{
        evt.preventDefault();
        navigator.clipboard.writeText(url).catch(() => null);
      }}
      send({{ event_type: "copy_link_click", strategy_id: anchor || null, action: "copy_link" }});
    }});
  }});
}})();
</script>
"""
    body = f"""
{nav_html}
<section class="hero">
  <div class="hero-grid">
    <div>
      <h1>Today&apos;s Options &amp; Stock Screener</h1>
      <p>ASA scans momentum, earnings calendars, volatility skew, and forward volatility to find high-quality setups — and reject ones that do not meet risk rules.</p>
      <div class="run-freshness"><span class="as-of">Signals as of {escape(run_timestamp_human)}</span><span class="age {escape(run_age_class)}">{escape(run_age_label)}</span></div>
      <div class="summary-badges">
        {_dashboard_badge(f'Latest scan: {generated_at}', 'muted')}
        {_dashboard_badge(f'Run quality: {run_quality}', quality_tone)}
        {_dashboard_badge(f'Signals found: {int(context.get("signals_found", 0))}', 'muted')}
        {_dashboard_badge('Forward Factor: DRY RUN', 'info') if context.get("ff_dry_run") else ''}
        {_dashboard_badge('Read-only cached scan', 'info')}
        {_dashboard_badge('Provider calls: none', 'info')}
        {_dashboard_badge('No broker data shown', 'muted')}
      </div>
    </div>
    <div class="copy-band" style="margin:0">
      <h2>How to read this page</h2>
      <div class="guide-grid">
        <div><strong>PASS</strong><p>Setup meets strategy&apos;s current rules.</p></div>
        <div><strong>WATCH</strong><p>Interesting, but needs confirmation or better pricing.</p></div>
        <div><strong>FAIL</strong><p>Blocked by risk, liquidity, timing, or data-quality rules.</p></div>
        <div><strong>DRY RUN</strong><p>Visible for research, not allowed into live trade recommendations yet.</p></div>
      </div>
      <p><strong>A failed setup is not wasted.</strong> It shows risk filter working.</p>
    </div>
  </div>
</section>
{coverage_html}
<section class="copy-band">
  <h2>Earnings date trust</h2>
  <p>Earnings dates can move or differ between providers. ASA marks whether earnings signal is single-source, multi-source, or conflicting. Single-source event trades should be reviewed before any real order.</p>
  <div class="summary-badges">
    {_dashboard_badge(f"Provider order: {', '.join(str(x) for x in (earnings_trust.get('provider_order') or []))}", 'muted')}
    {_dashboard_badge(f"Single-source rows: {int(earnings_trust.get('single_source_count', 0))}", 'watch')}
    {_dashboard_badge(f"Conflicts: {int(earnings_trust.get('conflict_count', 0))}", 'fail' if int(earnings_trust.get('conflict_count', 0)) else 'muted')}
  </div>
</section>
<section class="copy-band" id="why-rejects">
  <h2>Why ASA rejects trades</h2>
  <p>Most screeners only show what passed. ASA also shows what failed and why. That matters because edge is not just finding ideas — it is avoiding bad entries, bad liquidity, bad dates, and bad structures.</p>
</section>
{sections_html}
<section class="copy-band">
  <div class="guide-grid">
    <div>
      <h2>Why there are dry-run signals</h2>
      <p>Some strategies are visible before they are allowed into live recommendations. Dry-run signals help validate model without pretending system is ready to trade them automatically.</p>
    </div>
    <div>
      <h2>Why connect broker later</h2>
      <p>This public page shows market scan. Private account can later personalize same scan against actual holdings, account size, open options positions, and risk limits.</p>
    </div>
  </div>
</section>
<section class="cta-band" id="cta">
  <div class="cta-grid">
    <div>
      <h2>See full system later</h2>
      <p>ASA is risk-aware options decision system. Public screener shows market ideas. Private account layers in holdings, open structures, and account guardrails.</p>
      <p class="note">ASA is research and decision-support tool. It is not financial advice, not broker, and does not place trades. All signals require independent review.</p>
    </div>
    <div class="cta-buttons">
      <a class="btn btn-primary" href="/signup" data-demo-cta="create_account">Create a free screener account</a>
      <a class="btn btn-secondary" href="/login" data-demo-cta="personalization_info">See how broker personalization works</a>
    </div>
  </div>
</section>
{demo_js}
"""
    return render_template_string(_PUBLIC_SCREENER_HTML.format(css=_PUBLIC_SCREENER_CSS, body=body))


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
        broker_path = request.form.get("broker_path", "robinhood").strip()
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
        elif broker_path == "robinhood" and (not robinhood_username or not robinhood_password):
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
                elif broker_path == "plaid":
                    user = create_user(username, password)
                    user_id = user.get("id")
                    consume_invite_code(invite_code, user_id)
                    token = create_session(user_id)
                    update_last_login(user_id)
                    from flask import session as flask_session
                    flask_session["session_token"] = token
                    return redirect("/dashboard?connect_plaid=1")
                elif broker_path == "moomoo":
                    user = create_user(username, password)
                    user_id = user.get("id")
                    import sqlite3 as _sq
                    with _sq.connect(config.USERS_DB_PATH) as _conn:
                        _conn.execute("UPDATE users SET broker_type='moomoo' WHERE id=?", (user_id,))
                    consume_invite_code(invite_code, user_id)
                    token = create_session(user_id)
                    update_last_login(user_id)
                    from flask import session as flask_session
                    flask_session["session_token"] = token
                    return redirect("/dashboard")
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


@app.route("/demo/screener")
def public_screener_alias():
    return redirect("/screener", code=302)


@app.route("/screener")
def public_screener():
    if not getattr(config, "PUBLIC_SCREENER_ENABLED", True):
        abort(404)
    context = _build_public_screener_context()
    if not context:
        return render_template_string(
            _PUBLIC_SCREENER_HTML.format(
                css=_PUBLIC_SCREENER_CSS,
                body=(
                    '<section class="hero"><h1>Today&apos;s Options &amp; Stock Screener</h1>'
                    '<p>Demo temporarily unavailable.</p>'
                    '<p class="note">Latest successful cached run was not available.</p></section>'
                ),
            )
        ), 200
    return _render_public_screener(context)


@app.route("/dashboard")
def dashboard():
    user = _resolve_dashboard_user()
    if not user:
        return redirect("/login")
    api_key = user.get("api_key", "")
    user_token = request.args.get("token") or api_key
    key_prefix = (api_key[:12] + "...") if len(api_key) > 12 else api_key
    is_admin = bool(user.get("is_admin"))
    last_login = user.get("last_login_at") or "—"
    snapshot, report, tradier = _load_dashboard_core_report()
    from app.services.run_manifest_repository import RunManifestRepository
    manifest = RunManifestRepository().latest() or {}

    # 28C: credential status display
    validated_at = user.get("credentials_validated_at")
    last_error = user.get("credentials_last_error")
    rh_username_disp = user.get("robinhood_username") or ""
    if validated_at:
        cred_status_html = (
            f'<span class="ok">✓ Validated</span> — {escape(str(validated_at)[:10])}'
            + (f' ({escape(rh_username_disp)})' if rh_username_disp else "")
        )
    elif last_error:
        cred_status_html = f'<span class="err">Last error:</span> {escape(last_error[:120])}'
    elif rh_username_disp:
        cred_status_html = f'<span class="warn">Not yet validated</span> — {escape(rh_username_disp)}'
    else:
        cred_status_html = '<span class="warn">No Robinhood credentials stored.</span>'

    cred_update_msg = request.args.get("cred_msg", "")
    cred_update_html = (
        f'<p class="{"ok" if "success" in cred_update_msg.lower() else "err"}">{escape(cred_update_msg)}</p>'
        if cred_update_msg else ""
    )

    # 28D: last run status + core freshness for dashboard
    run_status_html = '<span class="muted">No personalization run yet.</span>'
    core_freshness_html = ""
    try:
        user_id_dash = user.get("id")
        if user_id_dash and not is_admin:
            from app.db.users import get_latest_user_run
            last_run = get_latest_user_run(user_id_dash)
            if last_run:
                st = escape(str(last_run.get("status") or ""))
                ts = escape(str(last_run.get("completed_at") or last_run.get("started_at") or "")[:16])
                pos = last_run.get("positions_fetched") or 0
                opp = last_run.get("daily_opportunity_count") or 0
                cls = "ok" if st == "complete" else ("err" if st == "failed" else "warn")
                run_status_html = (
                    f'Last run: <span class="{cls}">{st}</span> — {ts} '
                    f'({pos} positions, {opp} opportunities)'
                )
        from app.services.personalization import _core_run_freshness_hours
        from app import config as _cfg
        if snapshot:
            fh = _core_run_freshness_hours(snapshot)
            stale = fh > float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
            cls = "err" if stale else "ok"
            stale_txt = ' <span class="warn">(STALE)</span>' if stale else ""
            core_freshness_html = f'Core run: <span class="{cls}">{fh:.1f}h old</span>{stale_txt}'
    except Exception:
        pass

    positions_html, account_value = _build_dashboard_positions_html(user)
    last_run_at = (snapshot or {}).get("completed_at") or manifest.get("completed_at") or "—"
    current_run_id = (snapshot or {}).get("run_id") or manifest.get("run_id") or ""
    freshness = _run_freshness_meta(last_run_at)
    report_quality = manifest.get("report_quality") or ((tradier or {}).get("_pipeline_status", {}) or {}).get("report_quality") or "UNKNOWN"
    quality_tone = "pass" if str(report_quality).upper() == "SUCCESS_COMPLETE" else "watch"
    provider_fetch_count = manifest.get("provider_fetch_count", 0)
    broker_mode = manifest.get("broker_mode") or ("signals_only" if (user.get("broker_connection_optional") and not user.get("broker_connected")) else "connected")
    account_html = f'<span>Account: {_format_currency(account_value)}</span>' if account_value is not None and broker_mode == "connected" else ""
    run_meta_html = (
        f'<span>Last run: {escape(freshness["timestamp_human"])}</span>'
        f'<span class="age {escape(freshness["age_class"])}">{escape(freshness["age_label"])}</span>'
        f'{_dashboard_badge(str(report_quality), quality_tone)}'
        f'<span>{escape(str(provider_fetch_count))} API calls</span>'
        f'{account_html}'
        f'<span>{_dashboard_badge("signals only", "watch") if broker_mode == "signals_only" else ""}</span>'
        f'<a class="meta-link" href="/personalize?token={escape(api_key)}">Preferences</a>'
    )

    html = _DASHBOARD_HTML.format(
        css=_AUTH_CSS,
        username=escape(str(user.get("username", ""))),
        role="Admin" if is_admin else "User",
        key_prefix=escape(key_prefix),
        api_key=escape(api_key),
        user_token_json=json.dumps(str(user_token or "")),
        current_run_id_json=json.dumps(str(current_run_id)),
        current_run_age_seconds=int(freshness["age_seconds"]),
        last_login=escape(str(last_login)),
        run_meta_html=run_meta_html,
        run_status_html=run_status_html,
        core_freshness_html=core_freshness_html,
        cred_status_html=cred_status_html,
        cred_update_msg=cred_update_html,
        positions_html=positions_html,
        signals_html=_build_signals_html(report or {}),
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


@app.route("/connect-broker")
def connect_broker_page():
    """TKT-FEAT-001: Simple page for broker-optional users to connect Robinhood."""
    user = _get_session_user()
    if not user:
        return redirect("/login")
    api_key = user.get("api_key", "")
    msg = request.args.get("msg", "")
    msg_html = (
        f'<p class="{"ok" if "success" in msg.lower() else "err"}">{escape(msg)}</p>'
        if msg else ""
    )
    return render_template_string(
        """<!DOCTYPE html><html><head><title>ASA — Connect Broker</title>
<style>{css}</style></head><body><div class="card">
<h1>Connect Your Brokerage</h1>
<p>Validate and store your Robinhood credentials to enable position tracking and personalized exit signals.</p>
{msg_html}
<form method="POST" action="/user/update-credentials">
  <label>Robinhood Username (email)</label>
  <input name="robinhood_username" type="email" required autofocus>
  <label>Robinhood Password</label>
  <input type="password" name="robinhood_password" required>
  <button type="submit">Validate &amp; Connect</button>
</form>
<p class="muted" style="margin-top:1rem"><a href="/dashboard">← Back to dashboard</a></p>
</div></body></html>""".format(css=_AUTH_CSS, msg_html=msg_html)
    )


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


@app.route("/api/dev/skew-threshold-analysis")
@require_admin
def dev_skew_threshold_analysis():
    from app.services.skew_threshold_analysis_service import build_skew_threshold_analysis
    return jsonify(build_skew_threshold_analysis()), 200


@app.route("/api/dev/ff-graduation-analysis")
@require_admin
def dev_ff_graduation_analysis():
    from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
    return jsonify(build_ff_graduation_analysis()), 200


@app.route("/api/dev/trigger-run", methods=["POST"])
def dev_trigger_run():
    """Kick off a pipeline run and return immediately with a run_id for polling.

    Uses the same lock/job mechanism as /run so it cannot double-fire a concurrent run.
    Poll GET /api/dev/status to watch the run complete.
    """
    _require_dev_diagnostics_token()
    mode = str(request.args.get("mode") or "dev").strip().lower()
    if mode not in {"dev", "prod"}:
        return jsonify({"error": "mode must be 'dev' or 'prod'"}), 400

    global ACTIVE_JOB_ID
    _recover_stale_run_if_needed()
    _cleanup_old_jobs()
    with RUN_STATE_LOCK:
        if not RUN_LOCK.acquire(blocking=False):
            return jsonify({
                "status": "already_running",
                "run_id": ACTIVE_JOB_ID,
                "mode": mode,
                "poll": f"/api/dev/status?token={request.args.get('token')}",
                "note": "A run is already in progress. Poll /api/dev/status for completion.",
            }), 202

        job_id = uuid.uuid4().hex
        now = time.time()
        ACTIVE_JOB_ID = job_id
        RUN_JOBS[job_id] = {
            "status": "running",
            "message": _initial_job_message(mode),
            "mode": mode,
            "created_at": now,
            "started_at": now,
            "heartbeat_at": now,
            "updated_at": now,
            "timeout_reason": None,
            "failed_stage": None,
            "retry_safe": False,
            "result": None,
        }

    worker = threading.Thread(target=_run_job, args=(job_id, mode, RUN_LOCK), daemon=True)
    worker.start()
    print(f"=== /api/dev/trigger-run: async job {job_id} started; mode={mode} ===", flush=True)
    return jsonify({
        "status": "triggered",
        "run_id": job_id,
        "mode": mode,
        "poll": f"/api/dev/status?token={request.args.get('token')}",
        "note": "Poll /api/dev/status until latest_run.run_id matches and quality=SUCCESS_COMPLETE",
        "provider_calls_triggered": True,
        "read_only": False,
    }), 202


@app.route("/api/dev/calendar-pipeline-trace")
def dev_calendar_pipeline_trace():
    """Post-hoc reconstruction of the calendar pipeline lifecycle from the latest snapshot.

    Reads from the existing stored snapshot — no new provider calls. Shows per-ticker
    stages (prescreen → quality precheck → expiration pair → scanner → trade engine)
    so pipeline drop-offs are immediately visible without parsing Railway logs.
    """
    _require_dev_diagnostics_token()
    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return jsonify({"status": "unavailable", "error": "No completed run snapshot found.", "provider_calls_triggered": False}), 404
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        quality = tradier.get("_earnings_discovery_quality") or {}
        engine = tradier.get("_unified_calendar_trade_engine") or {}
        prescreen_stats = quality.get("_prescreen_stats") or {}
        new_trade_rows = (engine.get("new_trade_rows") or [])
        quality_items = quality.get("items") or []
        quality_rejected = quality.get("rejected_items") or []

        # Build lookup maps for quality rows (all checked, incl. rejected).
        quality_by_ticker: dict[str, dict] = {}
        for row in quality_items + quality_rejected:
            if isinstance(row, dict) and row.get("ticker"):
                quality_by_ticker[str(row["ticker"]).upper()] = row

        # Reconstruct lifecycle for each ticker that reached the trade engine.
        lifecycle = []
        for row in new_trade_rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("ticker") or "").upper()
            if not ticker:
                continue
            qp = row.get("quality_precheck") or quality_by_ticker.get(ticker) or {}
            lifecycle.append(_reconstruct_calendar_lifecycle(ticker, row, qp))

        # Tickers rejected at the quality precheck level (never reached engine).
        for qrow in quality_rejected:
            if not isinstance(qrow, dict):
                continue
            ticker = str(qrow.get("ticker") or "").upper()
            if not ticker or any(lc.get("ticker") == ticker for lc in lifecycle):
                continue
            lifecycle.append(_reconstruct_calendar_lifecycle(ticker, {}, qrow))

        # Aggregate summary.
        pair_built = sum(1 for lc in lifecycle if lc["stages"].get("front_back_expirations_found") == "PASS")
        pair_dict_stored = sum(1 for lc in lifecycle if lc["stages"].get("expiration_pair_dict_stored") == "PASS")
        pair_dict_missing = sum(1 for lc in lifecycle if lc["stages"].get("expiration_pair_dict_stored") == "FAIL")
        structures_built = sum(1 for lc in lifecycle if lc["stages"].get("structure_built") == "PASS")
        final_pass = sum(1 for lc in lifecycle if str(lc["stages"].get("final_verdict") or "").startswith("PASS"))
        final_watch = sum(1 for lc in lifecycle if str(lc["stages"].get("final_verdict") or "").startswith("WATCH"))
        final_fail = sum(1 for lc in lifecycle if str(lc["stages"].get("final_verdict") or "").startswith("FAIL"))

        warnings = []
        removed_pct = float(prescreen_stats.get("removed_pct") or 0)
        if removed_pct > 80 and prescreen_stats.get("cache_size"):
            warnings.append(
                f"{removed_pct:.1f}% of events removed by constituent prescreen — "
                f"possible stale cache (size={prescreen_stats['cache_size']}, expected ~664)"
            )

        result = {
            "status": "ok",
            "source_run_id": snapshot.get("run_id"),
            "checked_at": summary.get("created_at") or snapshot.get("run_id"),
            "provider_calls_triggered": False,
            "read_only": True,
            "summary": {
                "constituent_cache_size": prescreen_stats.get("cache_size"),
                "raw_events_from_finnhub": prescreen_stats.get("raw_count"),
                "after_constituent_prescreen": prescreen_stats.get("post_count"),
                "prescreen_removed_count": prescreen_stats.get("removed_count"),
                "prescreen_removed_pct": prescreen_stats.get("removed_pct"),
                "prescreen_removed_tickers": prescreen_stats.get("removed_tickers", []),
                "prescreen_fail_open": prescreen_stats.get("fail_open"),
                "quality_checked_count": len(quality_items) + len(quality_rejected),
                "quality_passed_count": len(quality_items),
                "quality_rejected_count": len(quality_rejected),
                "expiration_pairs_built": pair_built,
                "expiration_pair_dict_stored": pair_dict_stored,
                "expiration_pair_dict_missing": pair_dict_missing,
                "structures_built": structures_built,
                "final_pass": final_pass,
                "final_watch": final_watch,
                "final_fail": final_fail,
                "WARNING": warnings,
            },
            "lifecycle": lifecycle,
        }
        return jsonify(result), 200
    except Exception as exc:
        import traceback
        return jsonify({"status": "error", "error": str(exc), "trace": traceback.format_exc(), "provider_calls_triggered": False}), 500


def _reconstruct_calendar_lifecycle(ticker: str, engine_row: dict, quality_row: dict) -> dict:
    """Reconstruct per-ticker pipeline stages from stored snapshot data."""
    qp = quality_row or {}
    has_exps = bool(qp.get("front_expiration") and qp.get("back_expiration"))
    has_pair_dict = bool(qp.get("expiration_pair"))
    has_spread = bool(engine_row.get("possible_spread") and any(engine_row.get("possible_spread", {}).values()))
    quality_passed = bool(qp.get("passes_precheck"))
    final_verdict = str(engine_row.get("verdict") or qp.get("verdict") or "UNKNOWN")

    stages = {
        "constituent_prescreen": "PASS" if (quality_passed or has_exps) else "UNKNOWN",
        "quality_precheck": "PASS" if quality_passed else ("FAIL" if qp else "SKIPPED"),
        "front_back_expirations_found": "PASS" if has_exps else ("FAIL" if quality_passed else "SKIPPED"),
        "expiration_pair_dict_stored": "PASS" if has_pair_dict else ("FAIL" if has_exps else "SKIPPED"),
        "trade_engine_received_pair": "PASS" if has_pair_dict else ("FAIL" if has_exps else "SKIPPED"),
        "structure_built": "PASS" if has_spread else ("FAIL" if has_pair_dict else "SKIPPED"),
        "final_verdict": final_verdict,
    }
    earnings_date = (qp.get("event") or {}).get("earnings_date") or qp.get("earnings_date")
    front = qp.get("front_expiration")
    front_before_earnings: bool | None = None
    if front and earnings_date:
        try:
            from datetime import datetime as _dt
            front_before_earnings = _dt.strptime(str(front)[:10], "%Y-%m-%d").date() < _dt.strptime(str(earnings_date)[:10], "%Y-%m-%d").date()
        except Exception:
            pass
    return {
        "ticker": ticker,
        "stages": stages,
        "expiration_debug": {
            "front_expiration": front,
            "back_expiration": qp.get("back_expiration"),
            "expiration_pair_dict": qp.get("expiration_pair"),
            "passes_precheck": quality_passed,
            "expiry_near_miss": bool(qp.get("expiry_near_miss")),
            "expiry_exception": qp.get("expiry_exception"),
            "earnings_date": earnings_date,
            "front_before_earnings": front_before_earnings,
        },
    }


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


_SCREENER_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:monospace;background:#111;color:#ccc;padding:1rem;max-width:960px;margin:0 auto}
h1{color:#fff;margin-bottom:.25rem}
h2{color:#ddd;font-size:1rem;margin:1.25rem 0 .5rem}
h3{color:#bbb;font-size:.9rem;margin:.75rem 0 .25rem;border-bottom:1px solid #333;padding-bottom:.2rem}
.muted{color:#666;font-size:.8rem}
.section{border:1px solid #2a2a2a;border-radius:4px;padding:.75rem 1rem;margin-bottom:1rem}
.row{border:1px solid #1e2a1e;border-radius:3px;padding:.5rem .75rem;margin-bottom:.5rem;background:#141a14}
.label-badge{display:inline-block;padding:.1rem .4rem;border-radius:3px;font-size:.75rem;margin-right:.4rem}
.label-pass{background:#1a3a1a;color:#4f4}
.label-watch{background:#3a2a00;color:#fa0}
.label-fail{background:#3a1a1a;color:#f44}
.label-skipped{background:#1e1e1e;color:#666}
.label-dry-run{background:#1a1a3a;color:#88f}
.label-original{background:#222;color:#888;font-size:.7rem}
.do-reason{font-size:.78rem;color:#888;margin-top:.25rem}
.source-label{font-size:.75rem;color:#669;margin-top:.2rem}
details{margin-top:.4rem}
summary{font-size:.75rem;color:#555;cursor:pointer;user-select:none}
summary:hover{color:#999}
.gate-list{margin:.3rem 0 0 1rem;font-size:.75rem}
.gate-pass{color:#4a4}
.gate-watch{color:#a80}
.gate-fail{color:#a44}
.gate-unknown{color:#666}
.gate-na{color:#444}
.gate-dry-run{color:#66a}
.gate-skipped{color:#555}
.group-header{color:#888;font-size:.8rem;font-style:italic;margin:.3rem 0}
.empty-group{color:#444;font-size:.8rem;font-style:italic;margin:.25rem 0}
a.back{color:#555;font-size:.8rem;text-decoration:none}
a.back:hover{color:#999}
.scan-meta{font-size:.8rem;color:#555;margin-bottom:.5rem}
</style>
"""

_GATE_ICON = {
    "pass": "✓",
    "watch": "~",
    "fail": "✗",
    "unknown": "?",
    "not_applicable": "—",
    "dry_run": "⊘",
    "skipped": "·",
}


def _screener_gate_html(checklist: list[dict]) -> str:
    if not checklist:
        return ""
    items = ""
    for g in checklist:
        status = str(g.get("status") or "unknown")
        name = escape(str(g.get("name") or ""))
        detail = str(g.get("detail") or "")
        icon = _GATE_ICON.get(status, "?")
        detail_txt = f" — {escape(detail)}" if detail else ""
        css = f"gate-{status.replace('_', '-')}"
        items += f'<li class="{css}">{icon} {name}{detail_txt}</li>'
    return f'<details><summary>Gate checklist ({len(checklist)})</summary><ul class="gate-list">{items}</ul></details>'


def _screener_label_badge(public_label: str, original: str) -> str:
    lc = public_label.lower()
    if "pass" in lc or "candidate" in lc or "eligible" in lc:
        cls = "label-pass"
    elif "watch" in lc or "near" in lc:
        cls = "label-watch"
    elif "skipped" in lc or "limited" in lc:
        cls = "label-skipped"
    elif "dry" in lc or "gated" in lc:
        cls = "label-dry-run"
    else:
        cls = "label-fail"
    badge = f'<span class="label-badge {cls}">{escape(public_label)}</span>'
    if original and original != public_label:
        badge += f'<span class="label-badge label-original">{escape(original)}</span>'
    return badge


def _build_screener_ff_html(rows: list[dict], svc: Any) -> str:
    groups = svc.ff_grouping(rows)
    html = ""

    def _ff_row_html(row: dict) -> str:
        ticker = escape(str(row.get("ticker") or ""))
        pub_label, _orig = svc.public_verdict_label(row, "forward_factor")
        badge = _screener_label_badge(pub_label, "")
        src_label = escape(svc.public_ff_source_label(row))
        do_reason = escape(svc.public_daily_opportunity_reason(row, "forward_factor"))
        gates = svc.build_public_gate_checklist(row, "forward_factor")
        gate_html = _screener_gate_html(gates)
        tier = str(row.get("signal_tier") or "")
        tier_map = {
            "SOURCE_QUALIFIED_POSITIVE": "Source-qualified positive",
            "DIAGNOSTIC_POSITIVE": "Diagnostic positive",
            "WATCH_NEAR_POSITIVE": "Near positive",
            "NEGATIVE_OR_BLOCKED": "Negative / blocked",
            "NOT_EVALUATED": "Not evaluated",
        }
        tier_txt = escape(tier_map.get(tier, tier)) if tier else ""
        return (
            f'<div class="row"><strong>{ticker}</strong> {badge}'
            + (f'<br><span class="source-label">IV: {src_label}</span>' if src_label else "")
            + (f'<br><span class="source-label">Signal tier: {tier_txt}</span>' if tier_txt else "")
            + f'<div class="do-reason">{do_reason}</div>'
            + gate_html
            + "</div>"
        )

    html += "<h3>Evaluated Candidates</h3>"
    if groups["evaluated"]:
        for row in groups["evaluated"]:
            html += _ff_row_html(row)
    else:
        html += '<p class="empty-group">No tickers reached the evaluation stage this scan.</p>'

    html += "<h3>Skipped by Coverage</h3>"
    if groups["skipped"]:
        for row in groups["skipped"]:
            html += _ff_row_html(row)
    else:
        html += '<p class="empty-group">No tickers were skipped by coverage limits this scan.</p>'

    html += "<h3>Rejected by Risk Filters</h3>"
    if groups["rejected"]:
        for row in groups["rejected"]:
            html += _ff_row_html(row)
    else:
        html += '<p class="empty-group">No tickers were rejected by risk filters this scan.</p>'

    return html


def _build_screener_cal_html(items: list[dict], svc: Any) -> str:
    if not items:
        return '<p class="empty-group">No calendar candidates in current scan.</p>'
    sorted_items = sorted(
        items,
        key=lambda r: (0 if "PASS" in str(r.get("action") or "").upper() else
                       1 if "WATCH" in str(r.get("action") or "").upper() else 2),
    )
    html = ""
    for row in sorted_items[:15]:
        ticker = escape(str(row.get("ticker") or ""))
        pub_label, orig = svc.public_verdict_label(row, "calendar")
        badge = _screener_label_badge(pub_label, orig)
        dte = row.get("days_until_earnings")
        dte_txt = f" | {dte}d to earnings" if dte is not None else ""
        timing = str(row.get("entry_timing") or "")
        timing_txt = f" | {escape(timing)}" if timing and timing != "UNKNOWN" else ""
        do_reason = escape(svc.public_daily_opportunity_reason(row, "calendar"))
        gates = svc.build_public_gate_checklist(row, "calendar")
        gate_html = _screener_gate_html(gates)
        html += (
            f'<div class="row"><strong>{ticker}</strong> {badge}'
            + (f'<span class="muted">{escape(dte_txt)}{timing_txt}</span>' if (dte_txt or timing_txt) else "")
            + f'<div class="do-reason">{do_reason}</div>'
            + gate_html
            + "</div>"
        )
    return html


def _build_screener_skew_html(items: list[dict], svc: Any) -> str:
    if not items:
        return '<p class="empty-group">No skew vertical signals in current scan.</p>'
    html = ""
    for row in items[:12]:
        ticker = escape(str(row.get("ticker") or ""))
        direction = escape(str(row.get("direction") or ""))
        pub_label, orig = svc.public_verdict_label(row, "skew")
        badge = _screener_label_badge(pub_label, orig)
        dir_txt = f" ({direction})" if direction else ""
        do_reason = escape(svc.public_daily_opportunity_reason(row, "skew"))
        gates = svc.build_public_gate_checklist(row, "skew")
        gate_html = _screener_gate_html(gates)
        html += (
            f'<div class="row"><strong>{ticker}</strong>{escape(dir_txt)} {badge}'
            + f'<div class="do-reason">{do_reason}</div>'
            + gate_html
            + "</div>"
        )
    return html


def _build_screener_stock_html(items: list[dict], svc: Any) -> str:
    if not items:
        return '<p class="empty-group">No stock momentum signals in current scan.</p>'
    sorted_items = sorted(
        items,
        key=lambda r: -(float(r.get("score") or 0)),
    )
    html = ""
    for row in sorted_items[:15]:
        ticker = escape(str(row.get("ticker") or ""))
        pub_label, orig = svc.public_verdict_label(row, "stock_momentum")
        badge = _screener_label_badge(pub_label, orig)
        do_reason = escape(svc.public_daily_opportunity_reason(row, "stock_momentum"))
        gates = svc.build_public_gate_checklist(row, "stock_momentum")
        gate_html = _screener_gate_html(gates)
        html += (
            f'<div class="row"><strong>{ticker}</strong> {badge}'
            + f'<div class="do-reason">{do_reason}</div>'
            + gate_html
            + "</div>"
        )
    return html


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
