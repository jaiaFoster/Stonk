"""Advisor API — read-only endpoints for external consumers (iOS Shortcuts, Stonk Reporter).

All endpoints serve from latest cached run data only. No provider calls triggered.
Auth: Authorization: Bearer <token> header OR ?token=<token> query param.
Token validated against RUN_TOKEN (same as existing app pattern).
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from app import config

advisor_bp = Blueprint("advisor", __name__, url_prefix="/api/advisor")


def _token_from_request() -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.args.get("token")


def _valid_token(token: str | None) -> bool:
    """Accept RUN_TOKEN (legacy), legacy dev token, or any active user key/session."""
    if not token:
        return False
    # Legacy: existing RUN_TOKEN (advisor callers)
    if config.RUN_TOKEN and token == config.RUN_TOKEN:
        return True
    # Legacy: DEV_API_TOKEN bypass
    try:
        from app.auth import _is_legacy_token
        if _is_legacy_token(token):
            return True
    except Exception:
        pass
    # 28A: user API key or session token
    try:
        from app.auth import _resolve_user
        user = _resolve_user(token)
        return bool(user and user.get("is_active"))
    except Exception:
        return False


def _require_auth():
    """Returns a 401 response if token invalid, else None."""
    if not _valid_token(_token_from_request()):
        return jsonify({"status": "error", "error": "Unauthorized.", "provider_calls_triggered": False}), 401
    return None


def _load_snapshot():
    from app.services.report_snapshot_service import ReportSnapshotRepository
    repo = ReportSnapshotRepository(log_print=lambda msg: None)
    snapshot = repo.latest_success(include_full=True)
    if not snapshot:
        return None, None, None
    summary = repo.load_summary(snapshot, full=True)
    report = summary.get("report_data", {}) or {}
    return snapshot, summary, report


def _strategy_summary(strategies: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for sid, result in (strategies or {}).items():
        out[sid] = {
            "pass": result.get("pass_count", 0),
            "watch": result.get("watch_count", 0),
            "fail": result.get("fail_count", 0),
            "skipped": result.get("skipped_count", 0),
        }
    return out


def _action_shape(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": action.get("ticker"),
        "action": action.get("action"),
        "type": action.get("type"),
        "strategy": action.get("source", action.get("source_strategy")),
        "signal_score": action.get("priority_score") or action.get("signal_score") or action.get("actionability_score"),
        "verdict": action.get("verdict") or action.get("action"),
        "notes": action.get("why") or action.get("why_combined") or action.get("primary_reason"),
    }


def _log_event(endpoint: str, token: str | None, run_id: str | None) -> None:
    """Fire-and-forget telemetry write. Never raises."""
    try:
        from app.db.telemetry import log_event
        log_event(endpoint, token, run_id)
    except Exception:
        pass


@advisor_bp.route("/snapshot")
def snapshot():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    snapshot_row, summary, report = _load_snapshot()
    if snapshot_row is None:
        return jsonify({"status": "no_data", "error": "No completed run available.", "provider_calls_triggered": False}), 404

    from app.services.advisor_data_service import build_advisor_snapshot_payload
    result = build_advisor_snapshot_payload(snapshot_row, summary, report)

    _log_event("/api/advisor/snapshot", _token_from_request(), result.get("run_id"))

    return jsonify({"status": "ok", **result}), 200


@advisor_bp.route("/daily")
def daily():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    snapshot, summary, report = _load_snapshot()
    if snapshot is None:
        return jsonify({"status": "no_data", "error": "No completed run available.", "provider_calls_triggered": False}), 404

    tradier = report.get("tradier_snapshot", {}) or {}
    daily_opp = tradier.get("_daily_opportunity_engine") or {}
    strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}
    pipeline = tradier.get("_pipeline_status", {}) or {}

    actions = [_action_shape(a) for a in (daily_opp.get("actions") or [])]
    run_date = str(snapshot.get("completed_at") or "")[:10]
    run_id = snapshot.get("run_id")

    _log_event("/api/advisor/daily", _token_from_request(), run_id)

    return jsonify({
        "status": "ok",
        "provider_calls_triggered": False,
        "run_id": run_id,
        "run_date": run_date,
        "run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
        "generated_at": snapshot.get("completed_at"),
        "actions": actions,
        "strategy_summary": _strategy_summary(strategies),
        "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
    }), 200


@advisor_bp.route("/positions")
def positions():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    snapshot, summary, report = _load_snapshot()
    if snapshot is None:
        return jsonify({"status": "no_data", "error": "No completed run available.", "provider_calls_triggered": False}), 404

    raw_positions = report.get("positions", []) or []
    by_account: dict[str, list[dict[str, Any]]] = {}
    for pos in raw_positions:
        account = str(pos.get("account") or "unknown")
        by_account.setdefault(account, []).append({
            "ticker": pos.get("ticker"),
            "quantity": pos.get("quantity"),
            "avg_cost": pos.get("avg_buy_price"),
            "current_price": pos.get("current_price"),
            "unrealized_pnl_pct": pos.get("gain_loss_pct"),
            "market_value": pos.get("market_value"),
            "asset_type": pos.get("asset_type", "stock"),
        })

    accounts = [{"account_type": acct, "positions": rows} for acct, rows in by_account.items()]
    run_id = snapshot.get("run_id")

    _log_event("/api/advisor/positions", _token_from_request(), run_id)

    return jsonify({
        "status": "ok",
        "provider_calls_triggered": False,
        "as_of": snapshot.get("completed_at"),
        "accounts": accounts,
    }), 200


@advisor_bp.route("/status")
def status():
    # No auth required — lightweight health check. Not logged (too noisy).
    try:
        snapshot, summary, report = _load_snapshot()
    except Exception:
        return jsonify({"status": "ok", "last_run_quality": None, "last_run_date": None,
                        "daily_opportunity_count": 0, "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN)}), 200

    if snapshot is None:
        return jsonify({"status": "ok", "last_run_quality": None, "last_run_date": None,
                        "daily_opportunity_count": 0, "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN)}), 200

    tradier = report.get("tradier_snapshot", {}) or {}
    daily_opp = tradier.get("_daily_opportunity_engine") or {}
    pipeline = tradier.get("_pipeline_status", {}) or {}

    return jsonify({
        "status": "ok",
        "last_run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
        "last_run_date": str(snapshot.get("completed_at") or "")[:10],
        "daily_opportunity_count": len(daily_opp.get("actions") or []),
        "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
    }), 200


@advisor_bp.route("/vault/status")
def vault_status():
    auth_error = _require_auth()
    if auth_error:
        return auth_error

    from app.db.vault import vault_status as _vault_status
    result = _vault_status()
    return jsonify({"status": "ok", **result}), 200


_VALID_ACTIONS = {"bought", "watched", "ignored", "rejected"}
_VALID_OUTCOMES = {"positive", "negative", "neutral", "pending", "null"}


@advisor_bp.route("/feedback", methods=["POST"])
def feedback():
    if not config.TELEMETRY_ENABLED:
        return jsonify({"status": "disabled"}), 200

    auth_error = _require_auth()
    if auth_error:
        return auth_error

    body = request.get_json(silent=True) or {}
    ticker = str(body.get("ticker") or "").strip().upper()
    if not ticker:
        return jsonify({"status": "error", "error": "ticker is required."}), 400

    run_id = str(body.get("run_id") or "").strip() or None
    action_taken = str(body.get("action_taken") or "").strip().lower() or None
    outcome = str(body.get("outcome") or "").strip().lower() or None
    notes = str(body.get("notes") or "").strip() or None

    if action_taken and action_taken not in _VALID_ACTIONS:
        return jsonify({"status": "error", "error": f"Invalid action_taken. Valid: {sorted(_VALID_ACTIONS)}"}), 400
    if outcome and outcome not in _VALID_OUTCOMES:
        return jsonify({"status": "error", "error": f"Invalid outcome. Valid: {sorted(_VALID_OUTCOMES)}"}), 400

    try:
        from app.db.telemetry import record_feedback
        record_feedback(ticker, run_id, action_taken, outcome, notes)
    except Exception:
        pass

    return jsonify({"status": "ok", "message": "Feedback recorded"}), 200
