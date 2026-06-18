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
    held: dict[str, dict[str, Any]] = {}
    total_account_value = 0.0
    for pos in positions:
        ticker = str(pos.get("ticker") or "").upper()
        mv = float(pos.get("market_value") or 0)
        total_account_value += mv
        if ticker:
            # Keep the position with the largest market value if ticker appears
            # in multiple accounts (unlikely but safe)
            if ticker not in held or mv > float(held[ticker].get("market_value") or 0):
                held[ticker] = pos

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
            "position_size_context": json.dumps(pos_ctx) if pos_ctx else None,
            "debit_sizing_context": json.dumps(debit_ctx) if debit_ctx else None,
        })

    return result


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
        decrypt_robinhood_password,
    )
    from app.services.robinhood_queue import RobinhoodQueueTimeout, fetch_with_lock

    rh_username = str(user.get("robinhood_username") or "").strip()
    rh_password_enc = str(user.get("robinhood_password_encrypted") or "").strip()

    # Q5: no creds → shared output, no error
    if not rh_username or not rh_password_enc:
        return {
            "status": "ok",
            "personalized": False,
            "reason": "no_robinhood_credentials",
            "message": "No Robinhood credentials on file. Showing shared market signals.",
            "provider_calls_triggered": False,
        }

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

        # Serialized Robinhood fetch
        try:
            positions = fetch_with_lock(user_id, rh_username, rh_password)
        except RobinhoodQueueTimeout:
            fail_user_run(run_id, "queue_timeout", timed_out=True)
            return {
                "status": "error",
                "error": "queue_timeout",
                "message": "Robinhood fetch queue busy. Try again in 60 seconds.",
                "provider_calls_triggered": True,
            }
        except Exception as exc:
            err = str(exc).replace(rh_password, "[REDACTED]")
            fail_user_run(run_id, err[:500])
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

    # Persist positions
    save_user_positions(user_id, run_id, positions)

    # Build personalized Daily Opportunity
    daily_opp: list[dict[str, Any]] = []
    if snapshot and report:
        daily_opp = build_user_daily_opportunity(positions, report)

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
        "daily_opportunity_count": len(daily_opp),
        "core_run_id_used": core_run_id,
        "core_run_freshness_hours": round(freshness_hours, 2),
        "core_run_stale": core_run_stale,
        "personalized": True,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "provider_calls_triggered": True,
    }
