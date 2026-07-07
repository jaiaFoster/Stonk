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
    """Read daily opportunity actions from the latest stored snapshot."""
    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=False)
        if not snapshot:
            return {
                **_READ_ONLY_BASE,
                "empty_state": "no_snapshot",
                "enabled": True,
                "has_data": False,
                "action_count": 0,
                "actions": [],
            }
        summary = repo.load_summary(snapshot, full=False)
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
            "source": do_engine.get("source", "daily_opportunity_engine_v1"),
            "enabled": bool(do_engine.get("enabled", True)),
            "has_data": bool(do_engine.get("has_data") or actions),
            "action_count": len(actions),
            "actions": actions,
            "summary": do_engine.get("summary") or {},
        }
    except Exception as exc:
        return {**_READ_ONLY_BASE, "error": str(exc), "actions": [], "action_count": 0}
