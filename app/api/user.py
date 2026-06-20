"""
app/api/user.py — User endpoints (28A + 28B + 28C + TKT-045).

GET  /api/user/status         — return user info (no credentials).
POST /api/user/rotate-key     — generate new API key, invalidate old.
POST /api/user/run            — trigger personalization run (28B).
GET  /api/user/run/status     — return latest run status (28B).
PUT  /api/user/credentials    — update Robinhood credentials with validation (28C).
PUT  /api/user/accounts/<acct>/nickname — set/clear account nickname (TKT-045).
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.auth import require_auth

user_bp = Blueprint("user", __name__, url_prefix="/api/user")


@user_bp.route("/status")
@require_auth
def status():
    user = g.current_user or {}
    # Reload full row to get 28C credential fields if not already present
    user_id = user.get("id")
    if user_id and user_id != 0:
        try:
            from app.db.users import get_user_by_id
            full = get_user_by_id(user_id)
            if full:
                user = full
        except Exception:
            pass
    api_key = user.get("api_key") or ""
    prefix = api_key[:12] + "..." if len(api_key) > 12 else api_key
    validated_at = user.get("credentials_validated_at")

    # 28D: last run info + session cache availability
    last_run = None
    session_cache = None
    if user_id and user_id != 0:
        try:
            from app.db.users import get_latest_user_run
            last_run = get_latest_user_run(user_id)
        except Exception:
            pass
        try:
            from app.services.robinhood_queue import session_cache_available
            session_cache = session_cache_available(user_id)
        except Exception:
            pass

    return jsonify({
        "status": "ok",
        "username": user.get("username"),
        "is_admin": bool(user.get("is_admin")),
        "is_dev": bool(user.get("is_dev")),
        "api_key_prefix": prefix,
        "account_active": bool(user.get("is_active")),
        "last_login_at": user.get("last_login_at"),
        "created_at": user.get("created_at"),
        "broker_type": user.get("broker_type") or "robinhood",
        "credentials_validated": bool(validated_at),
        "credentials_validated_at": validated_at,
        "credentials_last_error": user.get("credentials_last_error"),
        "last_run_status": last_run.get("status") if last_run else None,
        "last_run_at": last_run.get("completed_at") if last_run else None,
        "last_run_positions_fetched": last_run.get("positions_fetched") if last_run else None,
        "last_run_daily_opportunity_count": last_run.get("daily_opportunity_count") if last_run else None,
        "session_cache_available": session_cache,
        "provider_calls_triggered": False,
    }), 200


@user_bp.route("/rotate-key", methods=["POST"])
@require_auth
def rotate_key():
    user = g.current_user or {}
    user_id = user.get("id")
    if not user_id:
        return jsonify({"status": "error", "error": "Cannot rotate key for legacy token."}), 400
    from app.db.users import rotate_api_key
    new_key = rotate_api_key(user_id)
    return jsonify({
        "status": "ok",
        "api_key": new_key,
        "rotated_at": datetime.now(timezone.utc).isoformat(),
        "warning": "Save this key now. It will not be shown again.",
        "provider_calls_triggered": False,
    }), 200


@user_bp.route("/run", methods=["POST"])
@require_auth
def trigger_run():
    """
    28B: Trigger a per-user personalization run.
    Synchronous — returns when complete or failed (gunicorn timeout 300s, queue 120s).
    """
    user = g.current_user or {}
    user_id = user.get("id") or 0

    # 28E/TKT-033: rate limiting at absolute top — before any other gate
    from app import config as _cfg
    rate_limit = int(getattr(_cfg, "USER_RUN_RATE_LIMIT_PER_HOUR", 3))
    from app.db.users import get_runs_in_last_hour, record_rate_limited_run
    import secrets as _secrets
    recent_runs = get_runs_in_last_hour(user_id) if user_id else []
    if len(recent_runs) >= rate_limit:
        rate_limited_run_id = "rl_" + _secrets.token_hex(8)
        try:
            record_rate_limited_run(user_id, rate_limited_run_id)
        except Exception:
            pass
        # Compute seconds until oldest run in window falls out
        retry_after = 3600
        if recent_runs:
            oldest_started = recent_runs[0].get("started_at") or ""
            try:
                from datetime import datetime, timezone, timedelta
                oldest_dt = datetime.fromisoformat(oldest_started.replace("Z", "+00:00"))
                window_end = oldest_dt + timedelta(hours=1)
                now_dt = datetime.now(timezone.utc)
                retry_after = max(1, int((window_end - now_dt).total_seconds()))
            except Exception:
                pass
        return jsonify({
            "status": "error",
            "error": "rate_limited",
            "message": f"Run limit reached. Max {rate_limit} runs per hour.",
            "retry_after_seconds": retry_after,
            "runs_this_hour": len(recent_runs),
            "limit": rate_limit,
            "provider_calls_triggered": False,
        }), 429

    # Legacy token cannot run personalization
    if not user_id:
        return jsonify({
            "status": "error",
            "error": "Cannot run personalization for legacy token.",
            "provider_calls_triggered": False,
        }), 400

    # TKT-036: all admin accounts are blocked from running personalization
    if user.get("is_admin"):
        return jsonify({
            "status": "error",
            "error": "admin_no_personalization",
            "message": "Admin accounts cannot run personalization. Use a member account.",
            "provider_calls_triggered": False,
        }), 400

    # Reload full user row to get encrypted Robinhood creds
    from app.db.users import get_user_by_id
    full_user = get_user_by_id(user_id) or {}
    if not full_user:
        return jsonify({"status": "error", "error": "User not found.", "provider_calls_triggered": False}), 404

    from app.services.personalization import run_personalization
    result = run_personalization(user_id, full_user)

    status_code = 200
    run_status = result.get("status")
    if run_status == "error":
        if result.get("error") == "queue_timeout":
            status_code = 503
        elif result.get("error") == "device_approval_required":
            status_code = 503
        else:
            status_code = 500
    elif run_status == "already_running":
        status_code = 202
    return jsonify(result), status_code


@user_bp.route("/run/status")
@require_auth
def run_status():
    """28B: Return latest personalization run status for the authenticated user."""
    user = g.current_user or {}
    user_id = user.get("id")
    if not user_id:
        return jsonify({
            "has_run": False,
            "latest_run": None,
            "note": "Legacy token — no personal run history.",
            "provider_calls_triggered": False,
        }), 200

    from app.db.users import get_latest_user_run
    latest = get_latest_user_run(user_id)
    if not latest:
        return jsonify({
            "has_run": False,
            "latest_run": None,
            "provider_calls_triggered": False,
        }), 200

    from app import config as _cfg
    stale_threshold = float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
    freshness = latest.get("core_run_freshness_hours")
    return jsonify({
        "has_run": True,
        "latest_run": {
            "run_id": latest.get("run_id"),
            "status": latest.get("status"),
            "started_at": latest.get("started_at"),
            "completed_at": latest.get("completed_at"),
            "positions_fetched": latest.get("positions_fetched"),
            "daily_opportunity_count": latest.get("daily_opportunity_count"),
            "core_run_freshness_hours": round(freshness, 2) if freshness is not None else None,
            "core_run_stale": (freshness > stale_threshold) if freshness is not None else None,
            "error_message": latest.get("error_message"),
        },
        "provider_calls_triggered": False,
    }), 200


@user_bp.route("/credentials", methods=["PUT"])
@require_auth
def update_credentials():
    """
    28C: Update and validate Robinhood credentials.
    Validates via live login before storing encrypted.
    NEVER returns or logs the password.
    """
    user = g.current_user or {}
    user_id = user.get("id")
    if not user_id or user_id == 0:
        return jsonify({
            "status": "error",
            "error": "not_supported",
            "message": "Cannot update credentials for legacy token.",
            "provider_calls_triggered": False,
        }), 400

    # Check encryption key available
    from app.db.users import get_encryption_key_status
    if not get_encryption_key_status():
        return jsonify({
            "status": "error",
            "error": "service_unavailable",
            "message": "Credential storage unavailable. Contact administrator.",
            "provider_calls_triggered": False,
        }), 503

    body = request.get_json(silent=True) or {}
    rh_username = str(body.get("robinhood_username") or "").strip()
    rh_password = str(body.get("robinhood_password") or "")

    if not rh_username or not rh_password:
        return jsonify({
            "status": "error",
            "error": "missing_fields",
            "message": "robinhood_username and robinhood_password required.",
            "provider_calls_triggered": False,
        }), 400

    # Validate via live login
    from app.services.broker_provider import BrokerCredentialProvider
    provider = BrokerCredentialProvider.get_provider("robinhood")
    valid, err_key = provider.validate_credentials(rh_username, rh_password)

    if not valid:
        _error_messages = {
            "validation_timeout": "Robinhood validation timed out. Try again shortly.",
            "device_approval_required": (
                "Robinhood requires device approval. "
                "Check your email/SMS and approve, then retry."
            ),
            "rate_limited": "Robinhood rate limit hit. Try again in a few minutes.",
            "login_failed": "Robinhood login failed. Check username and password.",
        }
        return jsonify({
            "status": "error",
            "error": err_key,
            "message": _error_messages.get(err_key, "Credential validation failed."),
            "validated": False,
            "provider_calls_triggered": True,
        }), 400

    # Store encrypted credentials + mark validated
    from app.db.users import update_broker_credentials
    update_broker_credentials(user_id, rh_username, rh_password)

    return jsonify({
        "status": "ok",
        "message": "Credentials updated and validated.",
        "robinhood_username": rh_username,
        "validated": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_triggered": True,
    }), 200


@user_bp.route("/runs")
@require_auth
def run_history():
    """28D: Return paginated run history for authenticated user."""
    user = g.current_user or {}
    user_id = user.get("id")
    if not user_id or user_id == 0:
        return jsonify({
            "runs": [],
            "total_runs": 0,
            "note": "No run history for legacy token.",
            "provider_calls_triggered": False,
        }), 200

    from app import config as _cfg
    limit = int(getattr(_cfg, "USER_RUN_HISTORY_LIMIT", 10))
    stale_threshold = float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))

    from app.db.users import get_user_run_history, count_user_runs
    runs = get_user_run_history(user_id, limit=limit)
    total = count_user_runs(user_id)

    shaped = []
    for r in runs:
        freshness = r.get("core_run_freshness_hours")
        shaped.append({
            "run_id": r.get("run_id"),
            "status": r.get("status"),
            "started_at": r.get("started_at"),
            "completed_at": r.get("completed_at"),
            "positions_fetched": r.get("positions_fetched"),
            "daily_opportunity_count": r.get("daily_opportunity_count"),
            "core_run_id_used": r.get("core_run_id_used"),
            "core_run_freshness_hours": round(freshness, 2) if freshness is not None else None,
            "core_run_stale": (freshness > stale_threshold) if freshness is not None else None,
            "error_message": r.get("error_message"),
        })

    return jsonify({
        "runs": shaped,
        "total_runs": total,
        "provider_calls_triggered": False,
    }), 200


@user_bp.route("/core-run-status")
@require_auth
def core_run_status():
    """28D: Return shared core market run freshness. No provider calls."""
    from app.services.personalization import _load_latest_core_run, _core_run_freshness_hours
    from app import config as _cfg

    snapshot, report = _load_latest_core_run()
    if not snapshot:
        return jsonify({
            "status": "ok",
            "core_run_id": None,
            "core_run_quality": None,
            "core_run_completed_at": None,
            "core_run_freshness_hours": None,
            "core_run_stale": None,
            "stale_threshold_hours": float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0)),
            "provider_calls_triggered": False,
        }), 200

    freshness = _core_run_freshness_hours(snapshot)
    stale_threshold = float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
    tradier = (report or {}).get("tradier_snapshot", {}) or {}
    pipeline = tradier.get("_pipeline_status", {}) or {}

    return jsonify({
        "status": "ok",
        "core_run_id": snapshot.get("run_id"),
        "core_run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
        "core_run_completed_at": snapshot.get("completed_at"),
        "core_run_freshness_hours": round(freshness, 2),
        "core_run_stale": freshness > stale_threshold,
        "stale_threshold_hours": stale_threshold,
        "provider_calls_triggered": False,
    }), 200


@user_bp.route("/accounts/<account_number>/nickname", methods=["PUT"])
@require_auth
def set_nickname(account_number):
    """TKT-045: Set or clear a user-defined nickname for a broker account."""
    user = g.current_user or {}
    user_id = user.get("id")
    if not user_id or user_id == 0:
        return jsonify({
            "status": "error",
            "error": "not_supported",
            "message": "Cannot set nickname for legacy token.",
            "provider_calls_triggered": False,
        }), 400

    body = request.get_json(silent=True) or {}
    nickname = body.get("nickname")
    if nickname is not None:
        nickname = str(nickname).strip()
        if len(nickname) > 100:
            return jsonify({
                "status": "error",
                "error": "invalid_nickname",
                "message": "Nickname must be 100 characters or fewer.",
                "provider_calls_triggered": False,
            }), 400
        if not nickname:
            nickname = None

    from app.db.users import set_account_nickname
    updated = set_account_nickname(user_id, account_number, nickname)
    if not updated:
        return jsonify({
            "status": "error",
            "error": "account_not_found",
            "message": "No discovered account with that number for this user.",
            "provider_calls_triggered": False,
        }), 404

    return jsonify({
        "status": "ok",
        "account_number": account_number,
        "nickname": nickname,
        "provider_calls_triggered": False,
    }), 200
