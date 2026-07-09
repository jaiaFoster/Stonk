"""Daily Opportunity read-only API — no provider calls.

ASA Patch 30D.1 Lane 7 — GET /api/daily-opportunity
Serves compact action list from the latest stored snapshot.
"""
from __future__ import annotations

from typing import Any

_READ_ONLY_BASE: dict[str, Any] = {"provider_calls_triggered": False, "read_only": True}


def _action_shape(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": action.get("ticker"),
        "action": action.get("action"),
        "type": action.get("type"),
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
    }


def build_daily_opportunity_response(limit: int = 12) -> dict[str, Any]:
    """Read Daily Opportunity from StrategyRowRepository first.

    Legacy snapshot fallback remains for old deployments with no row-store data.
    """
    try:
        row_store_response = _daily_opportunity_from_row_store(limit=limit)
        if row_store_response.get("source") == "strategy_row_store":
            return row_store_response
    except Exception:
        row_store_response = {}

    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return {
                **_READ_ONLY_BASE,
                "empty_state": "no_snapshot",
                "enabled": True,
                "has_data": False,
                "action_count": 0,
                "actions": [],
                "source": "empty",
                "fallback_used": False,
            }
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        do_engine = tradier.get("_daily_opportunity_engine") or {}
        raw_actions = do_engine.get("actions") or []
        if isinstance(raw_actions, dict):
            raw_actions = raw_actions.get("sample") or []
        cap = min(int(limit), 50)
        actions = [_action_shape(a) for a in list(raw_actions)[:cap] if isinstance(a, dict)]
        return {
            **_READ_ONLY_BASE,
            "source_run_id": snapshot.get("run_id"),
            "generated_at": snapshot.get("completed_at"),
            "source": "legacy_snapshot_fallback",
            "engine_source": do_engine.get("source", "daily_opportunity_engine_v1"),
            "fallback_used": True,
            "enabled": bool(do_engine.get("enabled", True)),
            "has_data": bool(do_engine.get("has_data") or actions),
            "action_count": len(actions),
            "actions": actions,
            "summary": do_engine.get("summary") or {},
        }
    except Exception as exc:
        return {**_READ_ONLY_BASE, "error": str(exc), "actions": [], "action_count": 0}


def _daily_opportunity_from_row_store(limit: int = 12) -> dict[str, Any]:
    from app.services.strategy_row_repository import StrategyRowRepository

    repo = StrategyRowRepository()
    strategy_ids = (
        "earnings_calendar",
        "skew_momentum_vertical",
        "stock_momentum",
        "forward_factor_calendar",
    )
    all_rows: list[dict[str, Any]] = []
    latest_run_id = None
    strategy_counts: dict[str, dict[str, int | str]] = {}
    dry_run_exclusions: dict[str, dict[str, Any]] = {}
    for sid in strategy_ids:
        result = repo.read_latest(sid, limit=200)
        run_id = result.get("run_id")
        if run_id and latest_run_id is None:
            latest_run_id = run_id
        rows = [row for row in result.get("rows") or [] if isinstance(row, dict)]
        all_rows.extend(rows)
        strategy_counts[sid] = {"rows_seen": len(rows), "eligible": 0, "excluded": 0}

    if not all_rows:
        return {
            **_READ_ONLY_BASE,
            "source": "empty",
            "fallback_used": False,
            "latest_run_id": None,
            "source_run_id": None,
            "enabled": True,
            "has_data": False,
            "action_count": 0,
            "actions": [],
            "summary": {"row_count_considered": 0, "eligible_count": 0, "excluded_count": 0},
        }

    actions: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for row in all_rows:
        action, exclusion = _action_from_strategy_row(row, latest_run_id)
        sid = str(row.get("strategy_id") or "")
        if action:
            actions.append(action)
            if sid in strategy_counts:
                strategy_counts[sid]["eligible"] = int(strategy_counts[sid].get("eligible", 0)) + 1
        elif exclusion:
            exclusions.append(exclusion)
            if sid in strategy_counts:
                strategy_counts[sid]["excluded"] = int(strategy_counts[sid].get("excluded", 0)) + 1
            if sid == "forward_factor_calendar":
                dry_run_exclusions[sid] = {
                    "rows_seen": int(strategy_counts[sid].get("rows_seen", 0)),
                    "eligible": int(strategy_counts[sid].get("eligible", 0)),
                    "excluded_reason": exclusion.get("exclusion_reason"),
                }

    actions = _dedupe_actions(sorted(actions, key=_daily_sort_key))
    cap = min(int(limit or 12), 50)
    actions = actions[:cap]
    return {
        **_READ_ONLY_BASE,
        "source": "strategy_row_store",
        "fallback_used": False,
        "latest_run_id": latest_run_id,
        "source_run_id": latest_run_id,
        "enabled": True,
        "has_data": bool(actions),
        "row_count_considered": len(all_rows),
        "eligible_count": len(actions),
        "excluded_count": len(exclusions),
        "action_count": len(actions),
        "actions": actions,
        "dry_run_exclusions": dry_run_exclusions,
        "strategy_counts": strategy_counts,
        "summary": {
            "row_count_considered": len(all_rows),
            "eligible_count": len(actions),
            "excluded_count": len(exclusions),
            "calendar_count": sum(1 for action in actions if action.get("type") in {"calendar", "active_calendar"}),
            "stock_count": sum(1 for action in actions if action.get("type") in {"stock", "stock_add", "stock_watch", "tactical_stock_watch"}),
            "stock_watch_count": sum(1 for action in actions if action.get("type") in {"stock_watch", "tactical_stock_watch"}),
            "skew_vertical_count": sum(1 for action in actions if action.get("type") in {"skew_vertical", "active_skew_vertical"}),
            "risk_count": sum(1 for action in actions if action.get("type") in {"risk", "portfolio_risk"}),
        },
    }


def _action_from_strategy_row(row: dict[str, Any], run_id: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    sid = str(row.get("strategy_id") or "")
    verdict = str(row.get("verdict") or "")
    verdict_upper = verdict.upper()
    row_type = str(row.get("row_type") or "")
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
    score = float(row.get("score") or 0)
    if sid == "forward_factor_calendar":
        return None, _exclusion(row, run_id, "dry_run")
    if row_type in {"diagnostic", "rejected_candidate"}:
        return None, _exclusion(row, run_id, "diagnostic_or_rejected")
    if verdict_upper.startswith("FAIL"):
        return None, _exclusion(row, run_id, "fail_verdict")
    eligible = bool(row.get("daily_opportunity_eligible"))
    if sid == "stock_momentum" and not eligible:
        eligible = _stock_row_daily_eligible(verdict_upper, str(row.get("friendly_verdict") or ""))
    if sid == "earnings_calendar" and row_type == "lifecycle_check":
        eligible = True
    if sid == "skew_momentum_vertical" and verdict_upper.startswith("PASS"):
        eligible = True
    if not eligible:
        return None, _exclusion(row, run_id, "not_daily_opportunity_eligible")
    action_type = {
        "earnings_calendar": "active_calendar" if row_type == "lifecycle_check" else "calendar",
        "stock_momentum": _stock_action_type(verdict_upper),
        "skew_momentum_vertical": "skew_vertical",
    }.get(sid, "monitor")
    return {
        "type": action_type,
        "ticker": ticker,
        "priority_score": score,
        "signal_score": score,
        "action": verdict or row.get("friendly_verdict"),
        "verdict": verdict,
        "why": row.get("primary_reason") or (row.get("display") or {}).get("public_reason") or row.get("friendly_verdict"),
        "next_step": (row.get("details") or {}).get("earnings_calendar", {}).get("next_action") or "Review row details and live data before action.",
        "source": "StrategyRowRepository",
        "source_strategy_id": sid,
        "source_row_id": row.get("row_id"),
        "source_run_id": run_id,
        "source_table": "strategy_rows",
        "eligibility_reason": "row marked eligible for Daily Opportunity or active lifecycle review",
        "display": row.get("display") or {},
    }, None


def _exclusion(row: dict[str, Any], run_id: str | None, reason: str) -> dict[str, Any]:
    return {
        "source_strategy_id": row.get("strategy_id"),
        "source_row_id": row.get("row_id"),
        "source_run_id": run_id,
        "ticker": row.get("ticker"),
        "exclusion_reason": reason,
    }


def _daily_sort_key(action: dict[str, Any]) -> tuple[int, float]:
    priority = {
        "active_calendar": 0,
        "active_skew_vertical": 1,
        "calendar": 2,
        "skew_vertical": 3,
        "stock_add": 4,
        "stock": 4,
        "stock_watch": 5,
        "tactical_stock_watch": 6,
        "risk": 7,
        "portfolio_risk": 7,
    }
    return (priority.get(str(action.get("type") or ""), 99), -float(action.get("priority_score") or 0))


def _stock_row_daily_eligible(verdict_upper: str, friendly_verdict: str) -> bool:
    """Preserve legacy Daily Opportunity stock-watch behavior from row-store rows."""
    friendly_upper = friendly_verdict.upper()
    if verdict_upper.startswith(("FAIL", "AVOID")) or "WEAK" in verdict_upper:
        return False
    if verdict_upper.startswith(("CONSIDER ADDING", "ADD ON", "WATCH / CONFIRM TREND")):
        return True
    if verdict_upper.startswith(("TACTICAL ONLY", "STARTER ONLY", "HOLD / DO NOT ADD")):
        return True
    return friendly_upper in {"MOMENTUM PASS", "WATCH", "TACTICAL WATCH"}


def _stock_action_type(verdict_upper: str) -> str:
    if verdict_upper.startswith(("CONSIDER ADDING", "ADD ON")):
        return "stock_add"
    if verdict_upper.startswith(("TACTICAL ONLY", "STARTER ONLY", "HOLD / DO NOT ADD")):
        return "tactical_stock_watch"
    return "stock_watch"


def _dedupe_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for action in actions:
        key = (str(action.get("type") or ""), str(action.get("ticker") or ""), str(action.get("action") or ""))
        if key in seen:
            continue
        seen.add(key)
        output.append(action)
    return output
