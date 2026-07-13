"""Open options positions and lifecycle read-only API — no provider calls.

ASA Patch 30D.1 Lane 8 — GET /api/open-positions
Serves compact positions list and lifecycle summary from the latest stored snapshot.
"""
from __future__ import annotations

import re
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

        active_calendar_count = max(int(open_opts.get("active_calendar_count") or (open_opts.get("summary") or {}).get("calendar_count") or 0), lifecycle_summary["checked_count"])
        calendar_structures = _legacy_calendar_structures(lifecycle)
        return _mask_account_fields({
            **_READ_ONLY_BASE,
            "source": "legacy_snapshot_fallback",
            "fallback_used": True,
            "source_run_id": snapshot.get("run_id"),
            "generated_at": snapshot.get("completed_at"),
            "options_positions": positions,
            "options_count": len(positions),
            "has_open_verticals": bool(open_opts.get("has_open_verticals")),
            "has_open_calendars": bool(open_opts.get("has_open_calendars")) or active_calendar_count > 0 or bool(calendar_structures),
            "active_calendar_count": active_calendar_count,
            "calendar_structures": calendar_structures,
            "lifecycle_summary": lifecycle_summary,
            "lifecycle_rows": lifecycle.get("checks") or [],
            "dedup_summary": _dedup_summary(positions),
        })
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
    # 33A: Build parent double-calendar structures (e.g. SBUX call+put calendar → one parent)
    double_calendar_parents, unmatched_child_calendars = _build_double_calendar_parents(structures)
    all_child_calendars = structures  # keep original list for counting
    warnings: list[str] = []
    dedup = _structure_dedup_summary(structures)
    if dedup.get("duplicate_group_count"):
        warnings.append("Potential duplicate option structures detected; preserved separately pending account-alias confirmation.")
    lifecycle_reconciliation = _lifecycle_row_reconciliation(lifecycle_rows, structures, dedup)
    # Compute leg count across all structures
    leg_count = sum(len(s.get("legs") or []) for s in structures)
    # Counts: double calendars count as 1 parent with 2 child calendars
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
        "has_open_calendars": bool(structures),
        "active_calendar_count": active_calendar_count,
        "active_double_calendar_count": active_double_calendar_count,
        "active_vertical_count": 0,
        "unmatched_leg_count": len(unmatched_child_calendars),
        "calendar_structures": all_child_calendars,
        "double_calendar_structures": double_calendar_parents,
        "unmatched_calendars": unmatched_child_calendars,
        "lifecycle_rows": lifecycle_rows,
        "lifecycle_summary": {"checked_count": len(lifecycle_rows), "status": "row_store"},
        "lifecycle_reconciliation": lifecycle_reconciliation,
        "warnings": warnings,
        "dedup_summary": dedup,
    })


def _calendar_structure_from_row(row: dict[str, Any]) -> dict[str, Any]:
    details = (row.get("details") or {}).get("earnings_calendar") or {}
    structure = _coerce_structure(row.get("structure_summary") or details.get("structure") or {})
    value = details.get("value") or {}
    value_summary = _coerce_value(value)
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
        "current_debit": value_summary.get("current_debit") or value_summary.get("current_mid_debit") or structure.get("current_debit"),
        "lifecycle_action": row.get("verdict"),
        "lifecycle_reason": row.get("primary_reason"),
        "source_row_id": row.get("row_id"),
        "source_table": "strategy_rows",
    }


def _coerce_structure(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    # Example row-store lifecycle value:
    # "110.0 CALL | short 2026-08-21 / long 2026-09-18"
    match = re.search(
        r"(?P<strike>\d+(?:\.\d+)?)\s+(?P<option_type>CALL|PUT)\s+\|\s+short\s+"
        r"(?P<front>\d{4}-\d{2}-\d{2})\s*/\s*long\s+(?P<back>\d{4}-\d{2}-\d{2})",
        value,
        flags=re.IGNORECASE,
    )
    if not match:
        return {"structure_type": "calendar", "description": value}
    return {
        "structure_type": "calendar",
        "strike": float(match.group("strike")),
        "option_type": match.group("option_type").lower(),
        "front_expiration": match.group("front"),
        "back_expiration": match.group("back"),
        "description": value,
    }


def _coerce_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
    match = re.search(r"current debit\s+(?P<debit>-?\d+(?:\.\d+)?)", value, flags=re.IGNORECASE)
    if not match:
        return {"description": value}
    return {"current_debit": float(match.group("debit")), "description": value}


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


def _lifecycle_row_reconciliation(
    lifecycle_rows: list[dict[str, Any]],
    structures: list[dict[str, Any]],
    dedup: dict[str, Any],
) -> dict[str, Any]:
    """LifecycleRowReconciliation: audit cardinality between raw rows and built structures.

    Structure keys include underlying, structure_type, option_type, strike,
    front_expiration, back_expiration so call vs put calendars on the same
    ticker are never collapsed into a single structure.
    """
    unique_keys: set[tuple[Any, ...]] = set()
    for s in structures:
        key = (
            s.get("underlying"),
            s.get("structure_type"),
            s.get("option_type"),
            s.get("strike"),
            s.get("front_expiration"),
            s.get("back_expiration"),
        )
        unique_keys.add(key)
    return {
        "lifecycle_row_count": len(lifecycle_rows),
        "structure_count": len(structures),
        "unique_structure_keys": len(unique_keys),
        "duplicate_group_count": dedup.get("duplicate_group_count", 0),
        "cardinality_ok": len(lifecycle_rows) == len(structures),
        "key_fields": ["underlying", "structure_type", "option_type", "strike", "front_expiration", "back_expiration"],
    }


def _build_double_calendar_parents(
    structures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Group call and put calendars on the same ticker/front/back into parent double-calendar structures.

    Returns: (parent_double_calendars, unmatched_child_calendars)
    Each parent has child_structure_ids, lower_strike, upper_strike, combined_pnl, etc.
    Children that cannot be paired remain in unmatched_child_calendars.
    """
    import hashlib as _hashlib

    # Group by (ticker, front_expiration, back_expiration) — the double-calendar identity
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    non_calendars: list[dict[str, Any]] = []

    for s in structures:
        stype = str(s.get("structure_type") or "calendar").lower()
        if "calendar" not in stype or stype == "double_calendar":
            non_calendars.append(s)
            continue
        key = (
            str(s.get("underlying") or s.get("ticker") or "").upper(),
            str(s.get("front_expiration") or ""),
            str(s.get("back_expiration") or ""),
        )
        groups.setdefault(key, []).append(s)

    parents: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []

    for (ticker, front, back), children in groups.items():
        calls = [c for c in children if str(c.get("option_type") or "").lower() == "call"]
        puts = [c for c in children if str(c.get("option_type") or "").lower() == "put"]

        if calls and puts:
            # Pair greedily: first call with first put
            call_leg = calls[0]
            put_leg = puts[0]
            child_ids = [
                str(call_leg.get("structure_id") or ""),
                str(put_leg.get("structure_id") or ""),
            ]
            call_strike = call_leg.get("strike")
            put_strike = put_leg.get("strike")
            strikes_sorted = sorted(
                [v for v in [call_strike, put_strike] if v is not None],
                key=lambda x: float(x) if x is not None else 0,
            )
            lower_strike = strikes_sorted[0] if strikes_sorted else None
            upper_strike = strikes_sorted[-1] if len(strikes_sorted) > 1 else None

            call_debit = call_leg.get("current_debit")
            put_debit = put_leg.get("current_debit")
            combined_debit: float | None = None
            if call_debit is not None and put_debit is not None:
                try:
                    combined_debit = round(float(call_debit) + float(put_debit), 2)
                except (TypeError, ValueError):
                    combined_debit = None

            parent_id_raw = f"dc:{ticker}:{front}:{back}"
            parent_id = _hashlib.sha256(parent_id_raw.encode()).hexdigest()[:16]

            lifecycle_action = call_leg.get("lifecycle_action") or put_leg.get("lifecycle_action")
            assignment_risk = _assignment_risk_summary(call_leg, put_leg)

            parent: dict[str, Any] = {
                "parent_structure_id": parent_id,
                "structure_type": "double_calendar",
                "ticker": ticker,
                "front_expiration": front,
                "back_expiration": back,
                "lower_strike": lower_strike,
                "upper_strike": upper_strike,
                "current_total_debit": combined_debit,
                "combined_pnl": None,
                "child_structure_ids": child_ids,
                "call_calendar": call_leg,
                "put_calendar": put_leg,
                "lifecycle_action": lifecycle_action,
                "assignment_risk_summary": assignment_risk,
                "source_table": "strategy_rows",
            }
            # Tag children with parent reference
            call_leg["parent_structure_id"] = parent_id
            put_leg["parent_structure_id"] = parent_id
            parents.append(parent)
            # Any extra legs beyond the matched pair are unmatched
            unmatched.extend(calls[1:])
            unmatched.extend(puts[1:])
        else:
            unmatched.extend(children)

    unmatched.extend(non_calendars)
    return parents, unmatched


def _assignment_risk_summary(call_leg: dict[str, Any], put_leg: dict[str, Any]) -> str:
    """Build a brief assignment-risk summary for a double-calendar parent."""
    parts: list[str] = []
    for leg, side in ((call_leg, "call"), (put_leg, "put")):
        action = str(leg.get("lifecycle_action") or "").upper()
        if "ASSIGNMENT" in action or "URGENT" in action:
            parts.append(f"{side.upper()} leg: {action}")
    return "; ".join(parts) if parts else "No assignment risk flagged."


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
