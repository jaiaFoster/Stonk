"""
app/api/admin.py — Admin-only API endpoints (28A + 28D).

POST /api/admin/invite              — generate single-use invite code.
GET  /api/admin/users               — list all users with run status (28D).
GET  /api/admin/users/<id>/runs     — run history for specific user (28D).
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.auth import require_admin

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@admin_bp.route("/invite", methods=["POST"])
@require_admin
def create_invite():
    from app.db.users import create_invite_code
    user_id = (g.current_user or {}).get("id") or None
    code = create_invite_code(created_by_user_id=user_id)
    return jsonify({
        "status": "ok",
        "code": code,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": request.get_json(silent=True, force=True) and (request.get_json(silent=True) or {}).get("note") or None,
    }), 201


@admin_bp.route("/users")
@require_admin
def list_users():
    """28D: Return all users with last run status. Never returns credentials."""
    from app.db.users import get_all_users_with_run_status
    users = get_all_users_with_run_status()
    shaped = []
    for u in users:
        shaped.append({
            "user_id": u.get("user_id"),
            "username": u.get("username"),
            "is_active": bool(u.get("is_active")),
            "is_admin": bool(u.get("is_admin")),
            "broker_type": u.get("broker_type") or "robinhood",
            "credentials_validated": bool(u.get("credentials_validated_at")),
            "credentials_validated_at": u.get("credentials_validated_at"),
            "credentials_last_error": u.get("credentials_last_error"),
            "last_run_status": u.get("last_run_status"),
            "last_run_at": u.get("last_run_at"),
            "last_run_positions_fetched": u.get("last_run_positions_fetched"),
            "last_login_at": u.get("last_login_at"),
            "created_at": u.get("created_at"),
            # NEVER: api_key, password_hash, robinhood_password_encrypted
        })
    return jsonify({
        "status": "ok",
        "users": shaped,
        "total": len(shaped),
        "provider_calls_triggered": False,
    }), 200


@admin_bp.route("/users/<int:target_user_id>/runs")
@require_admin
def user_run_history(target_user_id: int):
    """28D: Return run history for a specific user."""
    from app import config as _cfg
    from app.db.users import get_user_run_history, count_user_runs, get_user_by_id

    # Confirm user exists
    if not get_user_by_id(target_user_id):
        return jsonify({
            "status": "error",
            "error": "user_not_found",
            "provider_calls_triggered": False,
        }), 404

    limit = int(getattr(_cfg, "USER_RUN_HISTORY_LIMIT", 10))
    stale_threshold = float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
    runs = get_user_run_history(target_user_id, limit=limit)
    total = count_user_runs(target_user_id)

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
        "status": "ok",
        "user_id": target_user_id,
        "runs": shaped,
        "total_runs": total,
        "provider_calls_triggered": False,
    }), 200
