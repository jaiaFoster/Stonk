"""
app/services/personalization.py — Per-user Daily Opportunity personalization (28B).

Orchestrates:
  1. Load latest core run signals.
  2. Fetch user Robinhood positions via serialized lock.
  3. Enrich actions with already_held / position_size_context / debit_sizing_context.
  4. Persist results to users.db.
  5. Return summary dict to the API layer.

SECURITY: decrypted Robinhood password lives only in a local variable inside
run_personalization(). It is deleted immediately after the fetch call.
Never logged. Never returned.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any

from app import config


def generate_run_id() -> str:
    return "usr_" + secrets.token_hex(16)


# ---------------------------------------------------------------------------
# Core run helpers
# ---------------------------------------------------------------------------

def _load_latest_core_run() -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return (snapshot_row, report_data) from the latest successful core run."""
    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository(log_print=lambda msg: None)
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return None, None
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        return snapshot, report
    except Exception:
        return None, None


def _core_run_freshness_hours(snapshot: dict[str, Any]) -> float:
    """Hours elapsed since the core run completed. Returns 999.0 on parse failure."""
    completed_at = str(snapshot.get("completed_at") or "")
    if not completed_at:
        return 999.0
    try:
        ts = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return delta.total_seconds() / 3600.0
    except Exception:
        return 999.0


# ---------------------------------------------------------------------------
# Personalization logic (Steps 2–5 from spec)
# ---------------------------------------------------------------------------

def build_user_daily_opportunity(
    positions: list[dict[str, Any]],
    report: dict[str, Any],
    open_calendars: list[dict[str, Any]] | None = None,
    open_verticals: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build personalized Daily Opportunity from core run signals + user positions.

    Steps:
      2. Flag already-held tickers.
      3. Compute account value + debit sizing context.
      4. Add position context where user holds the ticker.

    Forward-factor actions are excluded (dry-run, never in Daily Opportunity).
    """
    # Build ticker → position map and total account value
    # 28D: only sum positions with valid (> 0) market_value — skip null-price positions
    held: dict[str, dict[str, Any]] = {}
    total_account_value = 0.0
    for pos in positions:
        ticker = str(pos.get("ticker") or "").upper()
        mv_raw = pos.get("market_value")
        mv = float(mv_raw) if mv_raw is not None else 0.0
        if mv > 0:
            total_account_value += mv
        if ticker:
            # Keep the position with the largest market value if ticker appears
            # in multiple accounts (unlikely but safe)
            if ticker not in held or mv > float(held[ticker].get("market_value") or 0):
                held[ticker] = pos

    # TKT-035: Build sets of tickers with open spreads for conflict detection
    open_calendar_tickers: dict[str, dict[str, Any]] = {}
    for cal in (open_calendars or []):
        ticker = str(cal.get("underlying") or cal.get("ticker") or "").upper().strip()
        if ticker:
            open_calendar_tickers[ticker] = cal

    open_vertical_tickers: dict[str, dict[str, Any]] = {}
    for v in (open_verticals or []):
        ticker = str(v.get("underlying") or v.get("ticker") or "").upper().strip()
        if ticker:
            open_vertical_tickers[ticker] = v

    # Pull core signals from shared report
    tradier = report.get("tradier_snapshot", {}) or {}
    daily_opp = tradier.get("_daily_opportunity_engine") or {}
    core_actions: list[dict[str, Any]] = list(daily_opp.get("actions") or [])

    max_debit_pct = float(getattr(config, "CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT", 0.02))
    result: list[dict[str, Any]] = []

    for action in core_actions:
        # Hard constraint: FF never in any user's Daily Opportunity
        strategy_raw = str(
            action.get("source") or action.get("source_strategy") or ""
        ).lower()
        if "forward_factor" in strategy_raw or strategy_raw.startswith("ff_"):
            continue

        ticker = str(action.get("ticker") or "").upper()
        action_type = str(action.get("type") or "")
        already_held = ticker in held

        # TKT-035: conflict detection — open vertical spread on this ticker
        already_has_vertical = ticker in open_vertical_tickers
        existing_vertical_pnl_pct = None
        existing_vertical_exit_signal = None
        if already_has_vertical:
            v = open_vertical_tickers[ticker]
            existing_vertical_pnl_pct = v.get("unrealized_pnl_pct")
            from app.services.skew_momentum_vertical_service import _compute_exit_signal
            existing_vertical_exit_signal, _ = _compute_exit_signal(v)

        # TKT-035: conflict detection — user already has open calendar on this ticker
        options_conflict = None
        if ticker in open_calendar_tickers:
            cal = open_calendar_tickers[ticker]
            exit_note = ""
            front_dte = cal.get("front_dte")
            if isinstance(front_dte, int):
                if front_dte <= 3:
                    exit_note = f" Short leg expires in {front_dte}d — assignment risk."
                elif front_dte <= 7:
                    exit_note = f" Short leg expires in {front_dte}d — review soon."
            options_conflict = {
                "has_open_calendar": True,
                "strategy": cal.get("strategy") or "Long Calendar Spread",
                "front_expiration": cal.get("front_expiration"),
                "back_expiration": cal.get("back_expiration"),
                "front_dte": front_dte,
                "action": cal.get("action") or "MONITOR",
                "note": f"Open {cal.get('option_type', '')} calendar detected.{exit_note}",
            }

        # Step 4 — position context
        pos_ctx: dict[str, Any] | None = None
        if already_held and total_account_value > 0:
            pos = held[ticker]
            mv = float(pos.get("market_value") or 0)
            pos_ctx = {
                "quantity": pos.get("quantity"),
                "market_value": round(mv, 2),
                "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
                "portfolio_pct": round(mv / total_account_value * 100, 2),
            }

        # Step 3 — debit sizing context for options candidates
        debit_ctx: dict[str, Any] | None = None
        if action_type in {
            "calendar", "active_calendar",
            "skew_vertical", "active_skew_vertical",
        } and total_account_value > 0:
            # Best-effort: debit may not be present in every action shape
            debit = float(
                action.get("debit")
                or action.get("net_debit")
                or action.get("debit_per_spread")
                or 0
            )
            max_recommended = total_account_value * max_debit_pct
            debit_ctx = {
                "account_value": round(total_account_value, 2),
                "max_recommended_debit": round(max_recommended, 2),
                "debit_pct_of_account": round(debit / total_account_value * 100, 3) if debit else None,
            }

        result.append({
            "ticker": ticker,
            "action": action.get("action"),
            "type": action_type,
            "strategy": action.get("source") or action.get("source_strategy"),
            "signal_score": (
                action.get("priority_score")
                or action.get("signal_score")
                or action.get("actionability_score")
            ),
            "verdict": action.get("verdict") or action.get("action"),
            "notes": (
                action.get("why")
                or action.get("why_combined")
                or action.get("primary_reason")
            ),
            "already_held": already_held,
            "already_has_vertical": already_has_vertical,
            "existing_position_pnl_pct": existing_vertical_pnl_pct,
            "existing_exit_signal": existing_vertical_exit_signal,
            "note": (
                f"Existing {ticker} vertical open. Review before adding."
                if already_has_vertical else None
            ),
            "position_size_context": json.dumps(pos_ctx) if pos_ctx else None,
            "debit_sizing_context": json.dumps(debit_ctx) if debit_ctx else None,
            "options_conflict": json.dumps(options_conflict) if options_conflict else None,
        })

    return result


def _normalize_options_positions_for_storage(
    calendars: list[dict[str, Any]],
    verticals: list[dict[str, Any]],
    single_legs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Build a combined normalized options positions list for user_positions storage.
    Verticals and calendars take priority; remaining single legs are added as unknown strategy.
    """
    result = []
    covered_tickers: dict[str, int] = {}

    for v in verticals:
        ticker = str(v.get("underlying") or v.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        covered_tickers[ticker] = covered_tickers.get(ticker, 0) + 1
        result.append({
            "ticker": ticker,
            "strategy_type": "skew_vertical",
            "option_type": v.get("option_type"),
            "strike": None,
            "expiration": v.get("expiration"),
            "dte": v.get("dte"),
            "quantity": v.get("quantity"),
            "legs": v.get("legs") or [],
            "net_debit": v.get("net_debit"),
            "current_value": v.get("current_value"),
            "unrealized_pnl_pct": _compute_pnl_pct(v.get("net_debit"), v.get("current_value")),
            "max_profit": v.get("max_profit"),
            "max_loss": v.get("max_loss"),
            "pct_of_max_profit": v.get("pct_of_max_profit"),
            "account_type": "options",
        })

    for cal in calendars:
        ticker = str(cal.get("underlying") or cal.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        result.append({
            "ticker": ticker,
            "strategy_type": "earnings_calendar",
            "option_type": cal.get("option_type"),
            "strike": cal.get("strike"),
            "expiration": cal.get("back_expiration"),
            "dte": cal.get("back_dte"),
            "quantity": cal.get("quantity"),
            "legs": [],
            "net_debit": cal.get("entry_mid_debit_estimate"),
            "current_value": cal.get("current_mid_debit"),
            "unrealized_pnl_pct": None,
            "max_profit": None,
            "max_loss": None,
            "pct_of_max_profit": None,
            "account_type": "options",
        })

    return result


def _compute_pnl_pct(net_debit: float | None, current_value: float | None) -> float | None:
    if net_debit is not None and current_value is not None and net_debit != 0:
        return round((current_value - net_debit) / abs(net_debit) * 100.0, 2)
    return None


# ---------------------------------------------------------------------------
# Full personalization run
# ---------------------------------------------------------------------------

def run_personalization(user_id: int, user: dict[str, Any]) -> dict[str, Any]:
    """
    Orchestrate a full per-user personalization run.

    Returns a response dict suitable for direct JSON serialization.
    Never returns or logs the decrypted Robinhood password.
    """
    from app.db.users import (
        create_user_run,
        complete_user_run,
        fail_user_run,
        save_user_positions,
        save_user_daily_opportunity,
        save_user_option_positions,
        decrypt_robinhood_password,
        get_active_user_run,
    )
    from app.services.robinhood_queue import (
        RobinhoodQueueTimeout,
        RobinhoodDeviceApprovalRequired,
        fetch_all_with_lock,
        session_cache_available,
    )

    rh_username = str(user.get("robinhood_username") or "").strip()
    rh_password_enc = str(user.get("robinhood_password_encrypted") or "").strip()

    # Q5: no creds — write a run row first so it counts toward the rate limit window,
    # then return the shared-signal response.
    if not rh_username or not rh_password_enc:
        _no_creds_run_id = generate_run_id()
        try:
            create_user_run(user_id, _no_creds_run_id)
            fail_user_run(_no_creds_run_id, "no_robinhood_credentials")
        except Exception:
            pass
        return {
            "status": "ok",
            "personalized": False,
            "reason": "no_robinhood_credentials",
            "message": "No Robinhood credentials on file. Showing shared market signals.",
            "provider_calls_triggered": False,
        }

    # 28D: Run deduplication — return active run if already in progress
    stale_secs = int(getattr(config, "USER_RUN_STALE_RUNNING_SECONDS", 180))
    active = get_active_user_run(user_id, stale_seconds=stale_secs)
    if active:
        return {
            "status": "already_running",
            "run_id": active.get("run_id"),
            "started_at": active.get("started_at"),
            "message": "A personalization run is already in progress. Wait for it to complete.",
            "provider_calls_triggered": False,
        }

    # 28D: Session cache check
    cache_available = session_cache_available(user_id)

    # Load latest core run (non-blocking — proceed even if stale)
    snapshot, report = _load_latest_core_run()
    core_run_id: str | None = None
    freshness_hours = 999.0
    if snapshot:
        core_run_id = snapshot.get("run_id")
        freshness_hours = _core_run_freshness_hours(snapshot)
    stale_threshold = float(getattr(config, "CORE_RUN_STALE_THRESHOLD_HOURS", 4.0))
    core_run_stale = freshness_hours > stale_threshold

    run_id = generate_run_id()
    create_user_run(
        user_id, run_id,
        core_run_id=core_run_id,
        core_run_freshness_hours=freshness_hours,
    )

    # Decrypt credentials immediately before fetch, delete immediately after
    rh_password: str | None = None
    try:
        try:
            rh_password = decrypt_robinhood_password(rh_password_enc)
        except Exception as exc:
            fail_user_run(run_id, f"credential_decrypt_failed: {type(exc).__name__}")
            return {
                "status": "error",
                "error": "credential_decrypt_failed",
                "message": "Could not decrypt stored Robinhood credentials.",
                "provider_calls_triggered": False,
            }

        # Serialized Robinhood fetch — stock + options in one session
        try:
            positions, raw_option_positions = fetch_all_with_lock(user_id, rh_username, rh_password)
            # Mark creds validated on successful fetch
            try:
                from app.db.users import set_credentials_validated
                set_credentials_validated(user_id)
            except Exception:
                pass
        except RobinhoodQueueTimeout:
            fail_user_run(run_id, "queue_timeout", timed_out=True)
            return {
                "status": "error",
                "error": "queue_timeout",
                "message": "Robinhood fetch queue busy. Try again in 60 seconds.",
                "retry_after_seconds": 60,
                "provider_calls_triggered": True,
            }
        except RobinhoodDeviceApprovalRequired as exc:
            err = str(exc).replace(rh_password, "[REDACTED]")
            fail_user_run(run_id, "device_approval_required")
            try:
                from app.db.users import set_credentials_error
                set_credentials_error(user_id, "device_approval_required")
            except Exception:
                pass
            return {
                "status": "error",
                "error": "device_approval_required",
                "message": (
                    "Robinhood requires device approval. "
                    "Check your Robinhood app/email and approve, then retry."
                ),
                "session_cache_available": cache_available,
                "provider_calls_triggered": True,
            }
        except Exception as exc:
            err = str(exc).replace(rh_password, "[REDACTED]")
            fail_user_run(run_id, err[:500])
            try:
                from app.db.users import set_credentials_error
                set_credentials_error(user_id, err[:200])
            except Exception:
                pass
            return {
                "status": "error",
                "error": "fetch_failed",
                "message": f"Robinhood fetch failed: {type(exc).__name__}",
                "provider_calls_triggered": True,
            }

    finally:
        # Ensure password not held beyond this scope
        if rh_password is not None:
            del rh_password

    # TKT-035: Detect option spreads from raw positions (same session reuse — no second login)
    option_detection: dict[str, Any] = {}
    detected_calendars: list[dict[str, Any]] = []
    detected_verticals: list[dict[str, Any]] = []
    normalized_options_positions: list[dict[str, Any]] = []
    try:
        from app.services.open_options_service import detect_from_robinhood_raw_positions
        option_detection = detect_from_robinhood_raw_positions(
            raw_option_positions,
            log_print=lambda msg: print(f"[personalization] {msg}", flush=True),
        )
        detected_calendars = option_detection.get("calendars") or []
        detected_verticals = option_detection.get("verticals") or []
        # Build full options_positions list in spec format for storage + response
        normalized_options_positions = _normalize_options_positions_for_storage(
            detected_calendars, detected_verticals, option_detection.get("option_legs") or []
        )
        print(
            f"[personalization] user_id={user_id} run_id={run_id}: "
            f"{len(detected_calendars)} calendar(s), {len(detected_verticals)} vertical(s) detected.",
            flush=True,
        )
    except Exception as exc:
        print(f"[personalization] option detection failed (non-fatal): {exc}", flush=True)

    # 28D: Position fetch validation
    # Warn on zero positions (not a failure — might be empty account)
    if not positions:
        print(
            f"[personalization] user_id={user_id} run_id={run_id}: "
            "0 positions fetched — empty account or fetch returned no data.",
            flush=True,
        )

    # Filter out positions with null price for account value computation
    null_price_count = sum(1 for p in positions if p.get("market_value") is None)
    if null_price_count:
        print(
            f"[personalization] user_id={user_id}: "
            f"{null_price_count} position(s) have null market_value — "
            "excluded from account value sum.",
            flush=True,
        )

    # Persist positions (all — including null-price ones; they're still valid holdings)
    save_user_positions(user_id, run_id, positions)

    # Persist detected calendar spreads to user_option_positions (advisory data)
    try:
        save_user_option_positions(user_id, run_id, detected_calendars)
    except Exception as exc:
        print(f"[personalization] save_user_option_positions failed (non-fatal): {exc}", flush=True)

    # TKT-035 3g: Persist options positions to user_positions table (position_type='options')
    try:
        from app.db.users import save_user_option_positions_to_positions
        save_user_option_positions_to_positions(user_id, run_id, normalized_options_positions)
    except Exception as exc:
        print(f"[personalization] save_user_option_positions_to_positions failed (non-fatal): {exc}", flush=True)

    # Build personalized Daily Opportunity (pass calendars for conflict detection)
    daily_opp: list[dict[str, Any]] = []
    if snapshot and report:
        daily_opp = build_user_daily_opportunity(
            positions, report,
            open_calendars=detected_calendars,
            open_verticals=detected_verticals,
        )

    # Persist Daily Opportunity
    save_user_daily_opportunity(user_id, run_id, daily_opp)

    # Mark run complete
    complete_user_run(
        run_id,
        positions_fetched=len(positions),
        daily_opportunity_count=len(daily_opp),
    )

    return {
        "status": "ok",
        "run_id": run_id,
        "user_id": user_id,
        "positions_fetched": len(positions),
        "option_calendars_detected": len(detected_calendars),
        "option_verticals_detected": len(detected_verticals),
        "daily_opportunity_count": len(daily_opp),
        "core_run_id_used": core_run_id,
        "core_run_freshness_hours": round(freshness_hours, 2),
        "core_run_stale": core_run_stale,
        "session_cache_available": cache_available,
        "personalized": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_triggered": True,
    }
