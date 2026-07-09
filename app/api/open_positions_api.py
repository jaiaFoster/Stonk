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
    """Read open positions/lifecycle from row store first, legacy fallback second."""
    try:
        row_store_response = _open_positions_from_row_store()
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
                "options_positions": [],
                "options_count": 0,
                "has_open_verticals": False,
                "has_open_calendars": False,
                "active_calendar_count": 0,
                "source": "empty",
                "fallback_used": False,
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
            "source": "legacy_snapshot_fallback",
            "fallback_used": True,
            "source_run_id": snapshot.get("run_id"),
            "generated_at": snapshot.get("completed_at"),
            "options_positions": positions,
            "options_count": len(positions),
            "has_open_verticals": bool(open_opts.get("has_open_verticals")),
            "has_open_calendars": bool(open_opts.get("has_open_calendars")),
            "active_calendar_count": max(int(open_opts.get("active_calendar_count") or 0), lifecycle_summary["checked_count"]),
            "calendar_structures": _legacy_calendar_structures(lifecycle),
            "lifecycle_summary": lifecycle_summary,
            "lifecycle_rows": lifecycle.get("checks") or [],
            "dedup_summary": _dedup_summary(positions),
        }
    except Exception as exc:
        return {**_READ_ONLY_BASE, "error": str(exc), "options_positions": [], "options_count": 0}


def _open_positions_from_row_store() -> dict[str, Any]:
    from app.services.strategy_row_repository import StrategyRowRepository

    repo = StrategyRowRepository()
    result = repo.read_latest("earnings_calendar", limit=200)
    rows = [row for row in result.get("rows") or [] if isinstance(row, dict)]
    lifecycle_rows = [
        row for row in rows
        if str(row.get("row_type") or "") == "lifecycle_check"
        or str(row.get("source") or "") == "calendar_lifecycle_v1"
        or str(row.get("verdict") or "").upper().startswith(("HOLD", "EXIT", "CUT", "TAKE PROFIT", "RECHECK"))
    ]
    if not lifecycle_rows:
        return {
            **_READ_ONLY_BASE,
            "source": "empty",
            "fallback_used": False,
            "source_run_id": result.get("run_id"),
            "options_positions": [],
            "options_count": 0,
            "has_open_verticals": False,
            "has_open_calendars": False,
            "active_calendar_count": 0,
            "calendar_structures": [],
            "lifecycle_rows": [],
            "warnings": [],
            "dedup_summary": {},
        }

    structures = [_calendar_structure_from_row(row) for row in lifecycle_rows]
    warnings = []
    dedup = _structure_dedup_summary(structures)
    if dedup.get("duplicate_group_count"):
        warnings.append("Potential duplicate option structures detected; preserved separately pending account-alias confirmation.")
    return {
        **_READ_ONLY_BASE,
        "source": "strategy_row_store",
        "fallback_used": False,
        "source_run_id": result.get("run_id"),
        "latest_run_id": result.get("run_id"),
        "options_positions": [],
        "options_count": 0,
        "open_option_leg_count": sum(len(s.get("legs") or []) for s in structures),
        "has_open_verticals": False,
        "has_open_calendars": bool(structures),
        "active_calendar_count": len(structures),
        "active_vertical_count": 0,
        "unmatched_single_count": 0,
        "calendar_structures": structures,
        "lifecycle_rows": lifecycle_rows,
        "lifecycle_summary": {"checked_count": len(lifecycle_rows), "status": "row_store"},
        "warnings": warnings,
        "dedup_summary": dedup,
    }


def _calendar_structure_from_row(row: dict[str, Any]) -> dict[str, Any]:
    details = (row.get("details") or {}).get("earnings_calendar") or {}
    structure = dict(row.get("structure_summary") or details.get("structure") or {})
    value = details.get("value") or {}
    ticker = row.get("ticker") or row.get("symbol")
    return {
        "structure_id": row.get("row_id"),
        "underlying": ticker,
        "ticker": ticker,
        "structure_type": structure.get("structure_type") or structure.get("type") or "calendar",
        "front_expiration": structure.get("front_expiration"),
        "back_expiration": structure.get("back_expiration"),
        "strike": structure.get("strike"),
        "option_type": structure.get("option_type"),
        "legs": structure.get("legs") or [],
        "current_debit": value.get("current_debit") or value.get("current_mid_debit") or structure.get("current_debit"),
        "lifecycle_action": row.get("verdict"),
        "lifecycle_reason": row.get("primary_reason"),
        "source_row_id": row.get("row_id"),
        "source_table": "strategy_rows",
    }


def _legacy_calendar_structures(lifecycle: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "structure_id": check.get("row_id") or f"{check.get('ticker')}-{index}",
            "underlying": check.get("ticker") or check.get("underlying"),
            "ticker": check.get("ticker") or check.get("underlying"),
            "structure_type": check.get("structure_type") or "calendar",
            "front_expiration": check.get("front_expiration"),
            "back_expiration": check.get("back_expiration"),
            "strike": check.get("strike"),
            "option_type": check.get("option_type"),
            "current_debit": check.get("current_mid_debit") or check.get("current_debit"),
            "lifecycle_action": check.get("action"),
            "lifecycle_reason": check.get("decision_summary") or check.get("next_check"),
            "source": "legacy_snapshot_fallback",
        }
        for index, check in enumerate(lifecycle.get("checks") or [])
        if isinstance(check, dict)
    ]


def _dedup_summary(positions: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], int] = {}
    for pos in positions:
        key = (
            pos.get("ticker"),
            pos.get("option_type"),
            pos.get("expiration"),
            pos.get("strike"),
            pos.get("qty"),
        )
        groups[key] = groups.get(key, 0) + 1
    duplicates = [key for key, count in groups.items() if count > 1]
    return {"duplicate_group_count": len(duplicates), "duplicate_warning": bool(duplicates)}


def _structure_dedup_summary(structures: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[Any, ...], int] = {}
    for structure in structures:
        key = (
            structure.get("underlying"),
            structure.get("structure_type"),
            structure.get("front_expiration"),
            structure.get("back_expiration"),
            structure.get("strike"),
            structure.get("option_type"),
        )
        groups[key] = groups.get(key, 0) + 1
    duplicates = [key for key, count in groups.items() if count > 1]
    return {
        "duplicate_group_count": len(duplicates),
        "duplicate_warning": bool(duplicates),
        "structure_count": len(structures),
    }
