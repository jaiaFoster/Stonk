"""
app/api/admin.py — Admin-only API endpoints (28A).

POST /api/admin/invite — generate single-use invite code.
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
