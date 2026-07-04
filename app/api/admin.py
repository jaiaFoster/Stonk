"""
app/api/admin.py — Admin-only API endpoints (28A + 28D + 28E).

POST /api/admin/invite                        — generate single-use invite code.
GET  /api/admin/users                         — list all users with run status (28D).
GET  /api/admin/users/<id>/runs               — run history for specific user (28D).
POST /api/admin/users/<id>/deactivate         — deactivate user + invalidate sessions (28E).
POST /api/admin/users/<id>/reactivate         — reactivate user (28E).
POST /api/admin/users/<id>/reset-key          — generate new API key for user (28E).
GET  /api/admin/invites                       — list all invite codes (28E).
POST /api/admin/invites/<code>/revoke         — revoke unused invite code (28E).
GET  /api/admin/summary                       — full admin picture in one call (28E).
GET  /api/admin/errors                        — per-user error log.
"""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request

from app.auth import require_admin

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _is_test_user(username: str) -> bool:
    """Return True if username matches any configured test-user prefix."""
    from app import config as _cfg
    patterns_raw = getattr(_cfg, "ADMIN_TEST_USER_PATTERNS", "testuser,smoke,rh_test,rh28b") or ""
    prefixes = [p.strip().lower() for p in patterns_raw.split(",") if p.strip()]
    lower = (username or "").lower()
    return any(lower.startswith(p) for p in prefixes)


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
    """28D+28E: Return all users with last run status. Never returns credentials."""
    from app.db.users import get_all_users_with_run_status
    users = get_all_users_with_run_status()
    shaped = []
    for u in users:
        shaped.append({
            "user_id": u.get("user_id"),
            "username": u.get("username"),
            "is_active": bool(u.get("is_active")),
            "is_admin": bool(u.get("is_admin")),
            "is_dev": bool(u.get("is_dev")),
            "broker_type": u.get("broker_type") or "robinhood",
            "credentials_validated": bool(u.get("credentials_validated_at")),
            "credentials_validated_at": u.get("credentials_validated_at"),
            "credentials_last_error": u.get("credentials_last_error"),
            "last_run_status": u.get("last_run_status"),
            "last_run_at": u.get("last_run_at"),
            "last_run_positions_fetched": u.get("last_run_positions_fetched"),
            "last_login_at": u.get("last_login_at"),
            "created_at": u.get("created_at"),
            "is_test_user": _is_test_user(u.get("username") or ""),
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


# ---------------------------------------------------------------------------
# 28E: User deactivation / reactivation
# ---------------------------------------------------------------------------

@admin_bp.route("/users/<int:target_user_id>/deactivate", methods=["POST"])
@require_admin
def deactivate_user(target_user_id: int):
    from app.db.users import get_user_by_id, deactivate_user as _deactivate, count_active_admins

    admin = g.current_user or {}
    admin_id = admin.get("id") or 0

    target = get_user_by_id(target_user_id)
    if not target:
        return jsonify({"status": "error", "error": "user_not_found", "provider_calls_triggered": False}), 404

    # Cannot deactivate self
    if admin_id and admin_id == target_user_id:
        return jsonify({
            "status": "error",
            "error": "cannot_deactivate_self",
            "message": "Cannot deactivate your own account.",
            "provider_calls_triggered": False,
        }), 400

    # Cannot deactivate last active admin
    if target.get("is_admin") and count_active_admins() <= 1:
        return jsonify({
            "status": "error",
            "error": "cannot_deactivate_last_admin",
            "message": "Cannot deactivate the last active admin account.",
            "provider_calls_triggered": False,
        }), 400

    sessions_invalidated = _deactivate(target_user_id)

    return jsonify({
        "status": "ok",
        "user_id": target_user_id,
        "username": target.get("username"),
        "is_active": False,
        "sessions_invalidated": sessions_invalidated,
        "provider_calls_triggered": False,
    }), 200


@admin_bp.route("/users/<int:target_user_id>/reactivate", methods=["POST"])
@require_admin
def reactivate_user(target_user_id: int):
    from app.db.users import get_user_by_id, reactivate_user as _reactivate

    target = get_user_by_id(target_user_id)
    if not target:
        return jsonify({"status": "error", "error": "user_not_found", "provider_calls_triggered": False}), 404

    _reactivate(target_user_id)

    return jsonify({
        "status": "ok",
        "user_id": target_user_id,
        "username": target.get("username"),
        "is_active": True,
        "provider_calls_triggered": False,
    }), 200


# ---------------------------------------------------------------------------
# 28E: Admin API key reset
# ---------------------------------------------------------------------------

@admin_bp.route("/users/<int:target_user_id>/reset-key", methods=["POST"])
@require_admin
def reset_api_key(target_user_id: int):
    from app.db.users import get_user_by_id, rotate_api_key

    target = get_user_by_id(target_user_id)
    if not target:
        return jsonify({"status": "error", "error": "user_not_found", "provider_calls_triggered": False}), 404

    new_key = rotate_api_key(target_user_id)

    return jsonify({
        "status": "ok",
        "user_id": target_user_id,
        "username": target.get("username"),
        "api_key": new_key,
        "warning": "Share this with the user immediately. Not shown again.",
        "provider_calls_triggered": False,
    }), 200


# ---------------------------------------------------------------------------
# 28E: Invite code list + revocation
# ---------------------------------------------------------------------------

@admin_bp.route("/invites")
@require_admin
def list_invites():
    from app.db.users import get_invites
    invites = get_invites()
    shaped = []
    for inv in invites:
        shaped.append({
            "code": inv.get("code"),
            "is_used": bool(inv.get("is_used")),
            "used_by_username": inv.get("used_by_username"),
            "used_at": inv.get("used_at"),
            "created_at": inv.get("created_at"),
        })
    unused_count = sum(1 for i in shaped if not i["is_used"])
    return jsonify({
        "status": "ok",
        "invites": shaped,
        "total": len(shaped),
        "unused_count": unused_count,
        "provider_calls_triggered": False,
    }), 200


@admin_bp.route("/invites/<string:code>/revoke", methods=["POST"])
@require_admin
def revoke_invite(code: str):
    from app.db.users import get_invite_code, revoke_invite as _revoke

    inv = get_invite_code(code)
    if not inv:
        return jsonify({"status": "error", "error": "invite_not_found", "provider_calls_triggered": False}), 404

    if inv.get("is_used"):
        return jsonify({
            "status": "error",
            "error": "already_used",
            "message": "Cannot revoke an already-used invite code.",
            "provider_calls_triggered": False,
        }), 400

    ok = _revoke(code)
    if not ok:
        return jsonify({
            "status": "error",
            "error": "revoke_failed",
            "message": "Revocation failed — code may have been consumed concurrently.",
            "provider_calls_triggered": False,
        }), 409

    return jsonify({
        "status": "ok",
        "code": code,
        "revoked": True,
        "provider_calls_triggered": False,
    }), 200


# ---------------------------------------------------------------------------
# 28E: Admin summary
# ---------------------------------------------------------------------------

@admin_bp.route("/summary")
@require_admin
def admin_summary():
    from app.db.users import admin_summary_stats, get_encryption_key_status, count_user_errors_24h
    from app import config as _cfg

    stats = admin_summary_stats()
    errors_24h = 0
    try:
        errors_24h = count_user_errors_24h()
    except Exception:
        pass

    # Core run freshness
    core_run_data = _core_run_info()

    # System flags — never return values, only whether configured
    encryption_key_set = get_encryption_key_status()
    legacy_token_enabled = bool(getattr(_cfg, "LEGACY_DEV_TOKEN_ENABLED", True))
    ff_dry_run = bool(getattr(_cfg, "FORWARD_FACTOR_DRY_RUN", True))
    trade_execution_enabled = bool(getattr(_cfg, "TRADE_EXECUTION_ENABLED", False))

    coverage_issues = _broker_coverage_issues()

    return jsonify({
        "status": "ok",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "users": stats["users"],
        "invites": stats["invites"],
        "runs": stats["runs"],
        "errors": {
            "last_24h": errors_24h,
        },
        "broker_coverage_issues": coverage_issues,
        "core_run": core_run_data,
        "system": {
            "encryption_key_set": encryption_key_set,
            "legacy_token_enabled": legacy_token_enabled,
            "ff_dry_run": ff_dry_run,
            "trade_execution_enabled": trade_execution_enabled,
        },
        "provider_calls_triggered": False,
    }), 200


def _broker_coverage_issues() -> dict:
    """Aggregate recent broker normalization/connection failures for admin visibility."""
    try:
        from app.db.users import get_user_errors
        errors = get_user_errors(limit=200)
        coverage_types = {
            "UnmappedAccountSubtype", "IncompleteOptionData",
            "UnsupportedSecurityType", "PlaidConnectionFailed",
        }
        counts: dict[str, int] = {}
        for e in errors:
            etype = e.get("error_type", "")
            if etype in coverage_types:
                counts[etype] = counts.get(etype, 0) + 1
        return {
            "unmapped_subtypes": counts.get("UnmappedAccountSubtype", 0),
            "unsupported_securities": counts.get("UnsupportedSecurityType", 0),
            "incomplete_options": counts.get("IncompleteOptionData", 0),
            "failed_connections": counts.get("PlaidConnectionFailed", 0),
            "total_issues": sum(counts.values()),
        }
    except Exception:
        return {"total_issues": 0}


def _core_run_info() -> dict:
    """Load latest core run quality + freshness. Never raises."""
    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository(log_print=lambda m: None)
        snapshot = repo.latest_success(include_full=False)
        if not snapshot:
            return {"quality": None, "freshness_hours": None, "stale": None}
        completed_at = snapshot.get("completed_at")
        if not completed_at:
            return {"quality": None, "freshness_hours": None, "stale": None}
        completed_dt = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
        age_hours = round((datetime.now(timezone.utc) - completed_dt).total_seconds() / 3600, 2)
        from app import config as _cfg
        stale_threshold = float(getattr(_cfg, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
        # Quality lives in summary_json → _pipeline_status
        quality = None
        try:
            summary = repo.load_summary(snapshot, full=False)
            report = summary.get("report_data", {}) or {}
            tradier = report.get("tradier_snapshot", {}) or {}
            pipeline = tradier.get("_pipeline_status", {}) or {}
            quality = pipeline.get("report_quality") or pipeline.get("overall_status")
        except Exception:
            pass
        return {
            "quality": quality,
            "freshness_hours": age_hours,
            "stale": age_hours > stale_threshold,
        }
    except Exception:
        return {"quality": None, "freshness_hours": None, "stale": None}


@admin_bp.route("/signal-telemetry")
@require_admin
def signal_telemetry():
    """TKT-FEAT-002: aggregate signal engagement for admin review (last 7 days)."""
    from app.db.telemetry import signal_engagement_summary
    days = min(request.args.get("days", 7, type=int), 90)
    summary = signal_engagement_summary(days=days)
    return jsonify({
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        **summary,
        "provider_calls_triggered": False,
    }), 200


@admin_bp.route("/errors")
@require_admin
def admin_errors():
    """Per-user error log. Optional ?user_id= filter, ?limit=, ?offset=."""
    from app.db.users import get_user_errors

    uid = request.args.get("user_id", type=int)
    limit = min(request.args.get("limit", 50, type=int), 200)
    offset = request.args.get("offset", 0, type=int)

    errors = get_user_errors(user_id=uid, limit=limit, offset=offset)
    return jsonify({
        "status": "ok",
        "errors": errors,
        "count": len(errors),
        "provider_calls_triggered": False,
    }), 200
