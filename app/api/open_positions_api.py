"""Open options positions and lifecycle read-only API — no provider calls.

ASA Patch 30D.1 Lane 8 — GET /api/open-positions
Serves compact positions list and lifecycle summary from the latest stored snapshot.
"""
from __future__ import annotations

import re
from typing import Any

from app.services.open_options_position_reconciliation_service import reconcile_open_calendar_positions

_READ_ONLY_BASE: dict[str, Any] = {"provider_calls_triggered": False, "read_only": True}


def build_open_positions_response() -> dict[str, Any]:
    """Read open positions/lifecycle from canonical row-store structures only."""
    try:
        return _open_positions_from_row_store()
    except Exception:
        return {**_READ_ONLY_BASE, "source": "empty", "fallback_used": False, "error": "row_store_unavailable", "options_positions": [], "options_count": 0}


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

    reconciled = reconcile_open_calendar_positions(lifecycle_rows)
    all_child_calendars = reconciled["child_calendars"]
    double_calendar_parents = reconciled["double_calendar_parents"]
    unmatched_child_calendars = reconciled["unmatched_child_calendars"]
    warnings: list[str] = []
    dedup = reconciled["dedup_summary"]
    if dedup.get("duplicate_group_count"):
        warnings.append("Potential duplicate option structures detected; preserved separately pending account-alias confirmation.")
    lifecycle_reconciliation = reconciled["reconciliation"]
    # Compute leg count across all structures
    leg_count = int(reconciled.get("open_option_leg_count") or 0)
    # Compatibility: active_calendar_count remains child calendars. Canonical
    # 33C parent count is exposed separately.
    active_double_calendar_count = len(double_calendar_parents)
    active_calendar_count = len(all_child_calendars)
    # Verification: warn if lifecycle completeness is deferred
    if not warnings and lifecycle_reconciliation.get("cardinality_ok") and not dedup.get("duplicate_group_count"):
        lifecycle_reconciliation["completeness_status"] = "PASS"
    else:
        lifecycle_reconciliation["completeness_status"] = "WARN"
        if not warnings:
            warnings.append("Lifecycle completeness: count mismatch between rows and structures.")
    return _mask_account_fields({
        **_READ_ONLY_BASE,
        "source": "strategy_row_store",
        "fallback_used": False,
        "source_run_id": result.get("run_id"),
        "latest_run_id": result.get("run_id"),
        "options_positions": [],
        "options_count": 0,
        "open_option_leg_count": leg_count,
        "has_open_verticals": False,
        "has_open_calendars": bool(all_child_calendars or double_calendar_parents),
        "active_calendar_count": active_calendar_count,
        "child_calendar_count": len(all_child_calendars),
        "active_parent_calendar_count": active_double_calendar_count or active_calendar_count,
        "parent_double_calendar_count": active_double_calendar_count,
        "active_double_calendar_count": active_double_calendar_count,
        "active_vertical_count": 0,
        "unmatched_leg_count": len(unmatched_child_calendars),
        "calendar_structures": all_child_calendars,
        "parent_calendar_structures": double_calendar_parents or all_child_calendars,
        "child_calendar_structures": all_child_calendars,
        "double_calendar_structures": double_calendar_parents,
        "unmatched_calendars": unmatched_child_calendars,
        "lifecycle_rows": lifecycle_rows,
        "lifecycle_summary": {"checked_count": len(lifecycle_rows), "status": "row_store"},
        "lifecycle_reconciliation": lifecycle_reconciliation,
        "warnings": warnings,
        "dedup_summary": dedup,
    })


def _mask_account_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [_mask_account_fields(item) for item in value]
    if not isinstance(value, dict):
        return value
    output: dict[str, Any] = {}
    for key, item in value.items():
        key_lower = str(key).lower()
        if key_lower in {"account_number", "account_number_rhs", "rhs_account_number", "account_id", "_source_account_number"}:
            output[key] = _mask_account_id(item)
        elif key_lower in {"account", "url"} and isinstance(item, str) and "/accounts/" in item:
            output[key] = re.sub(r"(/accounts/)([^/]+)", lambda m: m.group(1) + _mask_account_id(m.group(2)), item)
        else:
            output[key] = _mask_account_fields(item)
    return output


def _mask_account_id(value: Any) -> Any:
    if value in (None, ""):
        return value
    text = str(value)
    if len(text) <= 4:
        return "***"
    return f"***{text[-4:]}"
