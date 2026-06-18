"""
app/api/user.py — User endpoints (28A + 28B).

GET  /api/user/status         — return user info (no credentials).
POST /api/user/rotate-key     — generate new API key, invalidate old.
POST /api/user/run            — trigger personalization run (28B).
GET  /api/user/run/status     — return latest run status (28B).
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify

from app.auth import require_auth

user_bp = Blueprint("user", __name__, url_prefix="/api/user")


@user_bp.route("/status")
@require_auth
def status():
    user = g.current_user or {}
    api_key = user.get("api_key") or ""
    # Show prefix only: "asa_XXXX..." — first 8 chars after "asa_"
    prefix = api_key[:12] + "..." if len(api_key) > 12 else api_key
    return jsonify({
        "status": "ok",
        "username": user.get("username"),
        "is_admin": bool(user.get("is_admin")),
        "api_key_prefix": prefix,
        "account_active": bool(user.get("is_active")),
        "last_login_at": user.get("last_login_at"),
        "created_at": user.get("created_at"),
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
    user_id = user.get("id")
    if not user_id:
        return jsonify({
            "status": "error",
            "error": "Cannot run personalization for legacy token.",
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
    if result.get("status") == "error":
        if result.get("error") == "queue_timeout":
            status_code = 503
        else:
            status_code = 500
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
