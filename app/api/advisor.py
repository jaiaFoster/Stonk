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


def _valid_token(token: str | None) -> "tuple[bool, dict | None]":
    """Accept RUN_TOKEN (legacy), legacy dev token, or any active user key/session.
    Returns (is_valid, user_dict_or_none)."""
    if not token:
        return False, None
    # Legacy: existing RUN_TOKEN (advisor callers)
    if config.RUN_TOKEN and token == config.RUN_TOKEN:
        return True, None
    # Legacy: DEV_API_TOKEN bypass
    try:
        from app.auth import _is_legacy_token, _synthetic_admin_user
        if _is_legacy_token(token):
            return True, _synthetic_admin_user()
    except Exception:
        pass
    # 28A: user API key or session token
    try:
        from app.auth import _resolve_user
        user = _resolve_user(token)
        if user and user.get("is_active"):
            return True, user
    except Exception:
        pass
    return False, None


def _require_auth():
    """Returns a 401 response if token invalid, else None. Sets g.current_user on success."""
    from flask import g
    valid, user = _valid_token(_token_from_request())
    if not valid:
        return jsonify({"status": "error", "error": "Unauthorized.", "provider_calls_triggered": False}), 401
    g.current_user = user or {}
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


def _personalized_action_shape(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a user_daily_opportunity DB row for API output."""
    import json as _json
    pos_ctx = None
    debit_ctx = None
    try:
        raw_pos = row.get("position_size_context")
        if raw_pos:
            pos_ctx = _json.loads(raw_pos)
    except Exception:
        pass
    try:
        raw_debit = row.get("debit_sizing_context")
        if raw_debit:
            debit_ctx = _json.loads(raw_debit)
    except Exception:
        pass
    return {
        "ticker": row.get("ticker"),
        "action": row.get("action"),
        "type": row.get("type"),
        "strategy": row.get("strategy"),
        "signal_score": row.get("signal_score"),
        "verdict": row.get("verdict"),
        "notes": row.get("notes"),
        "already_held": bool(row.get("already_held")),
        "position_size_context": pos_ctx,
        "debit_sizing_context": debit_ctx,
    }


def _is_personal_user() -> bool:
    """True when caller is a real non-admin user (not legacy token, not admin)."""
    try:
        from flask import g
        user = getattr(g, "current_user", None) or {}
        user_id = user.get("id")
        is_admin = bool(user.get("is_admin"))
        # Legacy synthetic user has id=0
        return bool(user_id and user_id != 0 and not is_admin)
    except Exception:
        return False


def _get_personal_user_id() -> int | None:
    try:
        from flask import g
        user = getattr(g, "current_user", None) or {}
        uid = user.get("id")
        return int(uid) if uid and uid != 0 else None
    except Exception:
        return None


def _pnl_dollars(net_debit: float | None, current_value: float | None, qty: float | None) -> float | None:
    if net_debit is not None and current_value is not None:
        per_spread = (current_value - net_debit) * 100.0
        contracts = float(qty or 1)
        return round(per_spread * contracts, 2)
    return None


def _overlay_enriched_marks(options_positions: list[dict], report: dict | None) -> None:
    """Overlay live marks from core-run enriched verticals onto DB-sourced positions."""
    if not report or not options_positions:
        return
    tradier = (report.get("tradier_snapshot") or {})
    enriched = (tradier.get("_open_options_positions") or {}).get("verticals") or []
    if not enriched:
        return
    for op in options_positions:
        if op.get("strategy_type") != "skew_vertical":
            continue
        op_strikes = sorted(
            float(l.get("strike") or 0) for l in (op.get("legs") or []) if l.get("strike")
        )
        op_ticker = str(op.get("ticker") or "").upper()
        op_exp = str(op.get("expiration") or "")
        for ev in enriched:
            ev_strikes = sorted([
                float(ev.get("long_strike") or 0),
                float(ev.get("short_strike") or 0),
            ])
            if (op_ticker == str(ev.get("ticker") or "").upper()
                    and op_exp == str(ev.get("expiration") or "")
                    and op_strikes == ev_strikes):
                op["current_value"] = ev.get("current_value")
                op["unrealized_pnl"] = ev.get("unrealized_pnl")
                op["unrealized_pnl_pct"] = ev.get("unrealized_pnl_pct")
                op["pct_of_max_profit"] = ev.get("pct_of_max_profit")
                op["exit_signal"] = ev.get("exit_signal", "HOLD")
                for leg in (op.get("legs") or []):
                    for ev_leg in (ev.get("legs") or []):
                        if (leg.get("strike") == ev_leg.get("strike")
                                and leg.get("position") == ev_leg.get("position")):
                            leg["current_price"] = ev_leg.get("current_price")
                break


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

    run_date = str(snapshot.get("completed_at") or "")[:10]
    run_id = snapshot.get("run_id")

    _log_event("/api/advisor/daily", _token_from_request(), run_id)

    # 28B: serve personalized output for non-admin users
    if _is_personal_user():
        user_id = _get_personal_user_id()
        try:
            from app.db.users import get_latest_complete_user_run, get_user_daily_opportunity
            user_run = get_latest_complete_user_run(user_id) if user_id else None
            if user_run:
                rows = get_user_daily_opportunity(user_id, run_id=user_run.get("run_id"))
                personalized_actions = [_personalized_action_shape(r) for r in rows]
                total_account_value: float | None = None
                positions_count = user_run.get("positions_fetched") or 0
                # Compute account value from stored positions
                try:
                    from app.db.users import get_user_positions
                    positions = get_user_positions(user_id, run_id=user_run.get("run_id"))
                    total_account_value = sum(float(p.get("market_value") or 0) for p in positions)
                    if total_account_value == 0:
                        total_account_value = None
                except Exception:
                    pass
                freshness = user_run.get("core_run_freshness_hours")
                stale_threshold = float(getattr(config, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
                return jsonify({
                    "status": "ok",
                    "provider_calls_triggered": False,
                    "personalized": True,
                    "user_run_id": user_run.get("run_id"),
                    "core_run_id": user_run.get("core_run_id_used"),
                    "core_run_freshness_hours": round(freshness, 2) if freshness is not None else None,
                    "core_run_stale": (freshness > stale_threshold) if freshness is not None else None,
                    "total_account_value": round(total_account_value, 2) if total_account_value else None,
                    "positions_count": positions_count,
                    "run_id": run_id,
                    "run_date": run_date,
                    "run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
                    "generated_at": snapshot.get("completed_at"),
                    "actions": personalized_actions,
                    "strategy_summary": _strategy_summary(strategies),
                    "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
                }), 200
        except Exception as exc:
            import traceback
            print(f"[advisor.snapshot] personal block failed: {type(exc).__name__}: {exc}", flush=True)
            print(traceback.format_exc(), flush=True)
            from app.db.users import log_user_error
            log_user_error(user_id, "advisor.daily", type(exc).__name__, str(exc))

        # No completed run yet — shared output with personalized: false
        actions = [_action_shape(a) for a in (daily_opp.get("actions") or [])]
        return jsonify({
            "status": "ok",
            "provider_calls_triggered": False,
            "personalized": False,
            "reason": "no_run_yet",
            "message": "No personalization run yet. POST /api/user/run to personalize.",
            "run_id": run_id,
            "run_date": run_date,
            "run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
            "generated_at": snapshot.get("completed_at"),
            "actions": actions,
            "strategy_summary": _strategy_summary(strategies),
            "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
        }), 200

    # Admin / legacy token: shared output, unchanged behavior
    actions = [_action_shape(a) for a in (daily_opp.get("actions") or [])]
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
    run_id = snapshot.get("run_id") if snapshot else None
    _log_event("/api/advisor/positions", _token_from_request(), run_id)

    # 28B: serve per-user positions for non-admin users
    if _is_personal_user():
        user_id = _get_personal_user_id()
        try:
            from app.db.users import get_latest_complete_user_run, get_user_positions
            user_run = get_latest_complete_user_run(user_id) if user_id else None
            if user_run:
                user_positions = get_user_positions(user_id, run_id=user_run.get("run_id"))
                by_account: dict[str, dict[str, Any]] = {}
                for pos in user_positions:
                    acct_num = str(pos.get("account_number") or pos.get("account_type") or "default")
                    acct_type = str(pos.get("account_type") or "default")
                    if acct_num not in by_account:
                        by_account[acct_num] = {
                            "account_number": pos.get("account_number"),
                            "account_type": acct_type,
                            "positions": [],
                        }
                    by_account[acct_num]["positions"].append({
                        "ticker": pos.get("ticker"),
                        "quantity": pos.get("quantity"),
                        "avg_cost": pos.get("avg_cost"),
                        "current_price": pos.get("current_price"),
                        "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
                        "market_value": pos.get("market_value"),
                        "asset_type": "stock" if pos.get("position_type") != "options" else "options",
                    })
                accounts_list = list(by_account.values())

                # TKT-035: options positions in spec format
                options_positions = []
                try:
                    from app.db.users import get_user_positions as _get_all_positions
                    import json as _json
                    from app import config as _cfg
                    exit_target = float(getattr(_cfg, "SKEW_PROFIT_TARGET_PCT", 50.0))
                    all_positions = _get_all_positions(user_id, run_id=user_run.get("run_id"))
                    for p in all_positions:
                        if p.get("position_type") != "options":
                            continue
                        details = {}
                        try:
                            details = _json.loads(p.get("option_details") or "{}")
                        except Exception:
                            pass
                        options_positions.append({
                            "ticker": p.get("ticker"),
                            "strategy_type": details.get("strategy_type") or "unknown",
                            "option_type": details.get("option_type"),
                            "legs": details.get("legs") or [],
                            "net_debit": details.get("net_debit"),
                            "current_value": details.get("current_value"),
                            "unrealized_pnl": _pnl_dollars(details.get("net_debit"), details.get("current_value"), p.get("quantity")),
                            "unrealized_pnl_pct": p.get("unrealized_pnl_pct"),
                            "max_profit": details.get("max_profit"),
                            "max_loss": details.get("max_loss"),
                            "pct_of_max_profit": details.get("pct_of_max_profit"),
                            "exit_target_pct": exit_target,
                            "exit_signal": details.get("exit_signal"),
                            "exit_reason": details.get("exit_reason"),
                        })
                except Exception as exc:
                    import traceback
                    print(f"[advisor.positions] options block failed for user_id={user_id} "
                          f"run_id={user_run.get('run_id') if user_run else None}: "
                          f"{type(exc).__name__}: {exc}", flush=True)
                    print(traceback.format_exc(), flush=True)
                    from app.db.users import log_user_error
                    log_user_error(user_id, "advisor.positions.options", type(exc).__name__, str(exc),
                                   run_id=user_run.get("run_id") if user_run else None)

                _overlay_enriched_marks(options_positions, report)

                has_open_verticals = any(p.get("strategy_type") == "skew_vertical" for p in options_positions)
                has_open_calendars = any(p.get("strategy_type") == "earnings_calendar" for p in options_positions)

                # TKT-043: surface discovered broker accounts
                broker_accounts = []
                try:
                    from app.db.users import get_user_broker_accounts
                    broker_accounts = [
                        {"account_number": a.get("account_number"), "account_type": a.get("account_type"),
                         "broker_type": a.get("broker_type"), "discovered_at": a.get("discovered_at"),
                         "account_nickname": a.get("nickname")}
                        for a in get_user_broker_accounts(user_id)
                    ]
                except Exception:
                    pass

                return jsonify({
                    "status": "ok",
                    "provider_calls_triggered": False,
                    "personalized": True,
                    "as_of": user_run.get("completed_at"),
                    "user_run_id": user_run.get("run_id"),
                    "accounts": accounts_list,
                    "broker_accounts": broker_accounts,
                    "options_positions": options_positions,
                    "options_count": len(options_positions),
                    "has_open_verticals": has_open_verticals,
                    "has_open_calendars": has_open_calendars,
                }), 200
        except Exception as exc:
            import traceback
            print(f"[advisor.positions] outer block failed for user_id={user_id}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            print(traceback.format_exc(), flush=True)
            from app.db.users import log_user_error
            log_user_error(user_id, "advisor.positions", type(exc).__name__, str(exc))

        # No run yet — include empty options fields so callers don't need to guard on MISSING keys
        return jsonify({
            "status": "ok",
            "provider_calls_triggered": False,
            "personalized": False,
            "reason": "no_run_yet",
            "message": "No personalization run yet. POST /api/user/run to fetch your positions.",
            "accounts": [],
            "broker_accounts": [],
            "options_positions": [],
            "options_count": 0,
            "has_open_verticals": False,
            "has_open_calendars": False,
        }), 200

    # Admin / legacy token: shared positions from core run snapshot
    if snapshot is None:
        return jsonify({"status": "no_data", "error": "No completed run available.", "provider_calls_triggered": False}), 404

    raw_positions = report.get("positions", []) or []
    by_account_shared: dict[str, list[dict[str, Any]]] = {}
    for pos in raw_positions:
        account = str(pos.get("account") or "unknown")
        by_account_shared.setdefault(account, []).append({
            "ticker": pos.get("ticker"),
            "quantity": pos.get("quantity"),
            "avg_cost": pos.get("avg_buy_price"),
            "current_price": pos.get("current_price"),
            "unrealized_pnl_pct": pos.get("gain_loss_pct"),
            "market_value": pos.get("market_value"),
            "asset_type": pos.get("asset_type", "stock"),
        })

    accounts_shared = [{"account_type": acct, "positions": rows} for acct, rows in by_account_shared.items()]

    return jsonify({
        "status": "ok",
        "provider_calls_triggered": False,
        "as_of": snapshot.get("completed_at"),
        "accounts": accounts_shared,
        # Options fields always present so callers never guard on MISSING keys.
        # Admin/shared path has no per-user options context; return empty.
        "options_positions": [],
        "options_count": 0,
        "has_open_verticals": False,
        "has_open_calendars": False,
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
