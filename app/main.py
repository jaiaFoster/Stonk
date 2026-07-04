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
.pill{{background:#00ff8822;border:1px solid #00ff8844;border-radius:4px;
  padding:.3rem .6rem;font-size:.82rem;display:inline-block;margin:.2rem}}
.ok{{color:#00ff88}}.warn{{color:#ffcc44}}.err{{color:#ff4444}}
.section{{margin-top:1.5rem;border-top:1px solid #333;padding-top:1rem}}
#run-btn{{background:#00ff8833;border:1px solid #00ff8866;color:#00ff88;
  padding:.5rem 1.2rem;border-radius:4px;cursor:pointer;font-size:1rem}}
#run-btn:disabled{{opacity:.4;cursor:not-allowed}}
#run-result{{margin-top:.8rem;font-size:.9rem;white-space:pre-wrap}}
</style></head><body><div class="card">
<h1>Welcome, {username}</h1>
<p><span class="pill">{role}</span></p>
<p>Your API key (first 12 chars shown):</p>
<div class="key-box">{key_prefix}</div>
<p class="muted">Use full key in Authorization: Bearer header or ?token= param.</p>
<p class="muted">Last login: {last_login}</p>
<p><a href="/api/user/status?token={api_key}">View full status (JSON)</a></p>
{broker_prompt_html}
<div class="section">
<h2>Personalization Run</h2>
<p>{run_status_html}</p>
<p>{core_freshness_html}</p>
<button id="run-btn" onclick="triggerRun()">Run Personalization</button>
<div id="run-result"></div>
<script>
function triggerRun(){{
  var btn=document.getElementById('run-btn');
  var out=document.getElementById('run-result');
  btn.disabled=true; btn.textContent='Running…';
  out.textContent='Fetching signals…';
  fetch('/api/user/run?token={api_key}',{{method:'POST'}})
    .then(function(r){{return r.json();}})
    .then(function(d){{
      btn.disabled=false; btn.textContent='Run Personalization';
      if(d.status==='ok'){{
        var pos=d.positions_fetched||0;
        var opp=d.daily_opportunity_count||0;
        var mode=d.broker_mode==='signals_only'?' (signals only)':'';
        out.textContent='✓ Done — '+pos+' positions, '+opp+' opportunities'+mode+(d.core_run_stale?' (core run stale)':'');
      }} else if(d.status==='already_running'){{
        out.textContent='⏳ Already running since '+d.started_at;
      }} else {{
        out.textContent='✗ '+(d.error||'')+': '+(d.message||'');
      }}
    }})
    .catch(function(e){{
      btn.disabled=false; btn.textContent='Run Personalization';
      out.textContent='Network error: '+e;
    }});
}}
</script>
</div>
<div class="section">
<h2>Broker Connection</h2>
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
<details style="margin-top:.8rem">
<summary style="cursor:pointer;color:#aaa">Connect via Plaid</summary>
<p class="muted">Link your brokerage account securely through Plaid — no password stored on our servers.</p>
<button id="plaid-btn" onclick="launchPlaid()" style="margin-top:.5rem">Connect Brokerage via Plaid</button>
<div id="plaid-result" style="margin-top:.5rem;font-size:.9rem"></div>
</details>
<script src="https://cdn.plaid.com/link/v2/stable/link-initialize.js"></script>
<script>
function launchPlaid(){{
  var btn=document.getElementById('plaid-btn');
  var out=document.getElementById('plaid-result');
  btn.disabled=true; btn.textContent='Connecting…';
  fetch('/api/plaid/link-token?token={api_key}',{{method:'POST'}})
    .then(function(r){{return r.json();}})
    .then(function(d){{
      if(d.status!=='ok'){{
        btn.disabled=false; btn.textContent='Connect Brokerage via Plaid';
        out.textContent='✗ '+d.message; return;
      }}
      var handler=Plaid.create({{
        token:d.link_token,
        onSuccess:function(public_token){{
          out.textContent='Exchanging token…';
          fetch('/api/plaid/exchange?token={api_key}',{{
            method:'POST',
            headers:{{'Content-Type':'application/json'}},
            body:JSON.stringify({{public_token:public_token}})
          }}).then(function(r){{return r.json();}}).then(function(ex){{
            btn.disabled=false; btn.textContent='Connect Brokerage via Plaid';
            if(ex.status==='ok'){{out.textContent='✓ Connected! Run Personalization to fetch positions.';}}
            else{{out.textContent='✗ '+ex.message;}}
          }});
        }},
        onExit:function(){{
          btn.disabled=false; btn.textContent='Connect Brokerage via Plaid';
        }}
      }});
      handler.open();
    }})
    .catch(function(e){{
      btn.disabled=false; btn.textContent='Connect Brokerage via Plaid';
      out.textContent='Network error: '+e;
    }});
}}
if(window.location.search.indexOf('connect_plaid=1')!==-1){{setTimeout(launchPlaid,500);}}
</script>
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
        from app.services.personalization import _load_latest_core_run, _core_run_freshness_hours
        from app import config as _cfg
        snap, _ = _load_latest_core_run()
        if snap:
            fh = _core_run_freshness_hours(snap)
            stale = fh > float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
            cls = "err" if stale else "ok"
            stale_txt = ' <span class="warn">(STALE)</span>' if stale else ""
            core_freshness_html = f'Core run: <span class="{cls}">{fh:.1f}h old</span>{stale_txt}'
    except Exception:
        pass

    # TKT-FEAT-001: broker-optional users get a connect prompt instead of credential status
    broker_connection_optional = bool(user.get("broker_connection_optional"))
    broker_connected_flag = bool(user.get("broker_connected"))
    broker_prompt_html = ""
    if broker_connection_optional and not broker_connected_flag:
        broker_prompt_html = (
            '<div class="section" style="border-color:#00ff8844">'
            '<h2 style="color:#00ff88">Connect Your Brokerage</h2>'
            '<p>Connect Robinhood to see your open positions, P&amp;L, and personalized exit signals '
            'alongside these market signals.</p>'
            f'<a href="/connect-broker" style="display:inline-block;margin-top:.5rem;'
            f'padding:.5rem 1.2rem;background:#00ff8833;border:1px solid #00ff8866;'
            f'color:#00ff88;border-radius:4px;text-decoration:none">Connect Robinhood</a>'
            '<p class="muted" style="margin-top:.5rem">All strategy signals are fully visible without a broker connection.</p>'
            '</div>'
        )

    html = _DASHBOARD_HTML.format(
        css=_AUTH_CSS,
        username=escape(str(user.get("username", ""))),
        role="Admin" if is_admin else "User",
        key_prefix=escape(key_prefix),
        api_key=escape(api_key),
        last_login=escape(str(last_login)),
        run_status_html=run_status_html,
        core_freshness_html=core_freshness_html,
        cred_status_html=cred_status_html,
        cred_update_msg=cred_update_html,
        broker_prompt_html=broker_prompt_html,
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
