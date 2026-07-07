"""Open options positions and lifecycle read-only API — no provider calls.

ASA Patch 30D.1 Lane 8 — GET /api/open-positions
Serves compact positions list and lifecycle summary from the latest stored snapshot.
"""
from __future__ import annotations

from typing import Any

_READ_ONLY_BASE: dict[str, Any] = {"provider_calls_triggered": False, "read_only": True}


def _compact_position(pos: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": pos.get("ticker"),
        "strategy_type": pos.get("strategy_type"),
        "expiration": pos.get("expiration"),
        "option_type": pos.get("option_type"),
        "qty": pos.get("qty"),
        "net_debit": pos.get("net_debit"),
        "current_value": pos.get("current_value"),
        "unrealized_pnl": pos.get("unrealized_pnl"),
        "unrealized_pnl_pct": pos.get("unrealized_pnl_pct"),
        "exit_signal": pos.get("exit_signal"),
        "broker": pos.get("broker"),
    }


def build_open_positions_response() -> dict[str, Any]:
    """Read open positions and lifecycle from the latest stored snapshot."""
    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return {
                **_READ_ONLY_BASE,
                "empty_state": "no_snapshot",
                "options_positions": [],
                "options_count": 0,
                "has_open_verticals": False,
                "has_open_calendars": False,
                "active_calendar_count": 0,
            }
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        open_opts = tradier.get("_open_options_positions") or {}
        lifecycle = tradier.get("_calendar_lifecycle_checks") or {}

        positions_raw = (
            open_opts.get("options_positions")
            or open_opts.get("positions")
            or []
        )
        positions = [_compact_position(p) for p in positions_raw if isinstance(p, dict)]

        lifecycle_summary = {
            "checked_count": len(lifecycle.get("checks") or []),
            "status": lifecycle.get("status"),
        }

        return {
            **_READ_ONLY_BASE,
            "source_run_id": snapshot.get("run_id"),
            "generated_at": snapshot.get("completed_at"),
            "options_positions": positions,
            "options_count": len(positions),
            "has_open_verticals": bool(open_opts.get("has_open_verticals")),
            "has_open_calendars": bool(open_opts.get("has_open_calendars")),
            "active_calendar_count": int(open_opts.get("active_calendar_count") or 0),
            "lifecycle_summary": lifecycle_summary,
        }
    except Exception as exc:
        return {**_READ_ONLY_BASE, "error": str(exc), "options_positions": [], "options_count": 0}
