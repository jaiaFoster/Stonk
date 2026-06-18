"""
app/api/user.py — User endpoints (28A).

GET  /api/user/status     — return user info (no credentials).
POST /api/user/rotate-key — generate new API key, invalidate old.
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
