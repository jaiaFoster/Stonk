"""Advisor API — read-only endpoints for external consumers (iOS Shortcuts, Stonk Reporter).

All endpoints serve from latest cached run data only. No provider calls triggered.
Auth: Authorization: Bearer <token> header OR ?token=<token> query param.
Token validated against RUN_TOKEN (same as existing app pattern).
"""

from __future__ import annotations

from datetime import datetime, timezone
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


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _positions_freshness_payload(
    *,
    snapshot: dict[str, Any] | None,
    user_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    core_run_id = (user_run or {}).get("core_run_id_used") or (snapshot or {}).get("run_id")
    core_generated_at = (snapshot or {}).get("completed_at")
    positions_as_of = (user_run or {}).get("completed_at")
    if user_run is None:
        if snapshot is None:
            return {
                "run_id": None,
                "generated_at": None,
                "core_run_id": None,
                "core_generated_at": None,
                "as_of": None,
                "positions_as_of": None,
                "position_data_stale": None,
                "position_data_status": "NO_CORE_RUN",
            }
        return {
            "run_id": snapshot.get("run_id"),
            "generated_at": snapshot.get("completed_at"),
            "core_run_id": core_run_id,
            "core_generated_at": core_generated_at,
            "as_of": snapshot.get("completed_at"),
            "positions_as_of": snapshot.get("completed_at"),
            "position_data_stale": False,
            "position_data_status": "FRESH",
        }

    if snapshot is None:
        return {
            "run_id": None,
            "generated_at": None,
            "core_run_id": core_run_id,
            "core_generated_at": None,
            "as_of": positions_as_of,
            "positions_as_of": positions_as_of,
            "position_data_stale": None,
            "position_data_status": "NO_CORE_RUN",
        }

    positions_dt = _parse_dt(positions_as_of)
    core_dt = _parse_dt(core_generated_at)
    stale = bool(positions_dt and core_dt and positions_dt < core_dt)
    return {
        "run_id": snapshot.get("run_id"),
        "generated_at": snapshot.get("completed_at"),
        "core_run_id": core_run_id,
        "core_generated_at": core_generated_at,
        "as_of": positions_as_of,
        "positions_as_of": positions_as_of,
        "position_data_stale": stale,
        "position_data_status": "STALE_USER_POSITIONS" if stale else "FRESH",
    }


def _empty_positions_payload(
    *,
    snapshot: dict[str, Any] | None,
    personalized: bool,
    user_run: dict[str, Any] | None = None,
    reason: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "ok",
        "provider_calls_triggered": False,
        "personalized": personalized,
        "broker_accounts": [],
        "accounts": [],
        "options_positions": [],
        "options_count": 0,
        "has_open_verticals": False,
        "has_open_calendars": False,
        "active_calendar_count": 0,
        "calendar_structures": [],
        "lifecycle_status": None,
    }
    payload.update(_positions_freshness_payload(snapshot=snapshot, user_run=user_run))
    if user_run is not None:
        payload["user_run_id"] = user_run.get("run_id")
    if reason:
        payload["reason"] = reason
    if message:
        payload["message"] = message
    return payload


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
            ev_exp = str(ev.get("expiration") or "")
            exp_match = (not op_exp or not ev_exp or op_exp == ev_exp)
            if (op_ticker == str(ev.get("ticker") or "").upper()
                    and exp_match
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


def _lifecycle_summary_from_report(report: dict | None) -> dict[str, Any]:
    """Extract calendar lifecycle summary from core run report. 29K: positions/lifecycle unification."""
    tradier = (report.get("tradier_snapshot") or {}) if isinstance(report, dict) else {}
    lc = (tradier.get("_calendar_lifecycle_checks") or {}) if isinstance(tradier, dict) else {}
    if not isinstance(lc, dict) or not lc.get("has_data"):
        return {"has_data": False, "checks": [], "active_calendar_count": 0, "calendar_structures": [], "status": None}
    checks = [c for c in (lc.get("checks") or []) if isinstance(c, dict)]
    active = [c for c in checks if str(c.get("action") or "").upper() not in ("INACTIVE", "CLOSED", "")]
    structures = [_lifecycle_check_to_structure(c) for c in active]
    return {
        "has_data": bool(checks),
        "checks": checks,
        "active_calendar_count": len(active),
        "calendar_structures": structures,
        "status": lc.get("summary", {}).get("overall_action") if isinstance(lc.get("summary"), dict) else None,
    }


def _lifecycle_check_to_structure(check: dict[str, Any]) -> dict[str, Any]:
    """Shape one lifecycle check into a normalized calendar_structure for the positions payload."""
    return {
        "ticker": check.get("ticker"),
        "structure_type": check.get("structure_type") or "calendar",
        "structure_status": check.get("action"),
        "option_type": check.get("option_type"),
        "strike": check.get("strike"),
        "front_expiration": check.get("front_expiration"),
        "back_expiration": check.get("back_expiration"),
        "front_dte": check.get("front_dte"),
        "back_dte": check.get("back_dte"),
        "assignment_risk": check.get("assignment_risk_level"),
        "assignment_risk_reason": (check.get("assignment_risk_reasons") or [None])[0],
        "short_leg_moneyness_pct": check.get("short_leg_moneyness_pct"),
        "short_leg_itm": check.get("short_leg_itm"),
        "short_leg_extrinsic_value": check.get("short_leg_extrinsic_value"),
        "short_leg_extrinsic_value_status": (
            "available" if check.get("short_leg_extrinsic_value") is not None else "unavailable"
        ),
        "current_mid_debit": check.get("current_mid_debit"),
        "entry_debit_estimate": check.get("entry_debit_estimate"),
        "target_debit": check.get("target_debit"),
        "stop_debit": check.get("stop_debit"),
        "estimated_pnl_pct": check.get("estimated_pnl_pct"),
        "lifecycle_status": check.get("action"),
        "recheck_before_close": str(check.get("action") or "").upper() in ("RECHECK BEFORE CLOSE", "URGENT REVIEW / EXIT CHECK"),
        "reasons": check.get("reasons") or [],
        "risks": check.get("risks") or [],
        "legs": [
            {**check.get("short_front_leg", {}), "position": "short"},
            {**check.get("long_back_leg", {}), "position": "long"},
        ] if (check.get("short_front_leg") or check.get("long_back_leg")) else [],
    }


def _overlay_lifecycle(options_positions: list[dict], lifecycle_checks: list[dict]) -> None:
    """Overlay lifecycle fields onto DB-sourced options_positions by ticker+strike match."""
    for lc in lifecycle_checks:
        lc_ticker = str(lc.get("ticker") or "").upper()
        lc_strike = lc.get("strike")
        for op in options_positions:
            if str(op.get("ticker") or "").upper() != lc_ticker:
                continue
            op_strikes = [float(l.get("strike") or 0) for l in (op.get("legs") or [])]
            if lc_strike is not None and op_strikes and float(lc_strike) not in op_strikes:
                continue
            op["lifecycle_status"] = lc.get("action")
            op["assignment_risk"] = lc.get("assignment_risk_level")
            op["recheck_before_close"] = str(lc.get("action") or "").upper() in (
                "RECHECK BEFORE CLOSE", "URGENT REVIEW / EXIT CHECK"
            )
            op["short_leg_moneyness_pct"] = lc.get("short_leg_moneyness_pct")
            op["short_leg_extrinsic_value"] = lc.get("short_leg_extrinsic_value")
            op["short_leg_extrinsic_value_status"] = (
                "available" if lc.get("short_leg_extrinsic_value") is not None else "unavailable"
            )
            op["estimated_pnl_pct"] = op.get("unrealized_pnl_pct") or lc.get("estimated_pnl_pct")
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
                            "expiration": details.get("expiration"),
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

                # 29K: enrich with lifecycle data from snapshot.
                lifecycle_summary = _lifecycle_summary_from_report(report)
                lifecycle_overlay_status = "unavailable"
                lifecycle_reconciliation_notes: list[str] = []
                if lifecycle_summary.get("has_data"):
                    # If lifecycle detects active calendars, surface them even if DB options_positions is empty.
                    if not has_open_calendars and lifecycle_summary.get("active_calendar_count", 0) > 0:
                        has_open_calendars = True
                        # 29.8: DB and lifecycle disagree — flag as reconciled.
                        lifecycle_overlay_status = "reconciled"
                        lifecycle_reconciliation_notes.append(
                            f"DB options_positions reported no open calendars; lifecycle snapshot detected "
                            f"{lifecycle_summary.get('active_calendar_count', 0)} active calendar(s)."
                        )
                    else:
                        lifecycle_overlay_status = "applied"
                    # Overlay lifecycle status onto matching DB options positions.
                    _overlay_lifecycle(options_positions, lifecycle_summary.get("checks") or [])

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

                payload = _empty_positions_payload(snapshot=snapshot, personalized=True, user_run=user_run)
                payload.update({
                    "user_run_id": user_run.get("run_id"),
                    "accounts": accounts_list,
                    "broker_accounts": broker_accounts,
                    "options_positions": options_positions,
                    "options_count": len(options_positions),
                    "has_open_verticals": has_open_verticals,
                    "has_open_calendars": has_open_calendars,
                    "active_calendar_count": lifecycle_summary.get("active_calendar_count", 0),
                    "calendar_structures": lifecycle_summary.get("calendar_structures") or [],
                    "lifecycle_status": lifecycle_summary.get("status"),
                    "lifecycle_overlay_status": lifecycle_overlay_status,
                })
                if lifecycle_reconciliation_notes:
                    payload["positions_lifecycle_reconciliation_notes"] = lifecycle_reconciliation_notes
                return jsonify(payload), 200
        except Exception as exc:
            import traceback
            print(f"[advisor.positions] outer block failed for user_id={user_id}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
            print(traceback.format_exc(), flush=True)
            from app.db.users import log_user_error
            log_user_error(user_id, "advisor.positions", type(exc).__name__, str(exc))

        # No run yet — stable empty shape.
        payload = _empty_positions_payload(
            snapshot=snapshot,
            personalized=False,
            user_run={},
            reason="no_run_yet",
            message="No personalization run yet. POST /api/user/run to fetch your positions.",
        )
        payload["position_data_status"] = "NO_PERSONALIZATION_RUN"
        payload["position_data_stale"] = None
        payload["user_run_id"] = None
        return jsonify(payload), 200

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

    payload = _empty_positions_payload(snapshot=snapshot, personalized=False)
    payload.update({
        "accounts": accounts_shared,
    })
    return jsonify(payload), 200


@advisor_bp.route("/status")
def status():
    # No auth required — lightweight health check. Not logged (too noisy).
    try:
        snapshot, summary, report = _load_snapshot()
    except Exception:
        return jsonify({"status": "ok", "last_run_quality": None, "last_run_date": None,
                        "daily_opportunity_count": 0, "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
                        "provider_calls_triggered": False, "run_id": None, "generated_at": None}), 200

    if snapshot is None:
        return jsonify({"status": "ok", "last_run_quality": None, "last_run_date": None,
                        "daily_opportunity_count": 0, "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
                        "provider_calls_triggered": False, "run_id": None, "generated_at": None}), 200

    tradier = report.get("tradier_snapshot", {}) or {}
    daily_opp = tradier.get("_daily_opportunity_engine") or {}
    pipeline = tradier.get("_pipeline_status", {}) or {}

    return jsonify({
        "status": "ok",
        "last_run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
        "last_run_date": str(snapshot.get("completed_at") or "")[:10],
        "daily_opportunity_count": len(daily_opp.get("actions") or []),
        "ff_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
        "provider_calls_triggered": False,
        "run_id": snapshot.get("run_id"),
        "generated_at": snapshot.get("completed_at"),
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
