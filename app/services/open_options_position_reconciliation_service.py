"""Canonical open-options position reconciliation.

Patch 33C: this service owns row-store lifecycle row -> child calendar ->
double-calendar parent grouping. APIs may project this output but must not
rebuild grouping independently.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


def reconcile_open_calendar_positions(lifecycle_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [row for row in lifecycle_rows if isinstance(row, dict)]
    child_calendars = [_calendar_structure_from_row(row) for row in rows]
    parent_double_calendars, unmatched = _build_double_calendar_parents(child_calendars)
    dedup = _structure_dedup_summary(child_calendars)
    reconciliation = _lifecycle_row_reconciliation(rows, child_calendars, dedup)
    reconciliation["parent_double_calendar_count"] = len(parent_double_calendars)
    reconciliation["unmatched_child_calendar_count"] = len(unmatched)
    reconciliation["cardinality_ok"] = (
        len(rows) == len(child_calendars)
        and (not child_calendars or len(parent_double_calendars) > 0 or len(unmatched) == len(child_calendars))
    )
    return {
        "source": "open_options_position_reconciliation_service",
        "lifecycle_rows": rows,
        "child_calendars": child_calendars,
        "double_calendar_parents": parent_double_calendars,
        "unmatched_child_calendars": unmatched,
        "dedup_summary": dedup,
        "reconciliation": reconciliation,
        "open_option_leg_count": sum(len(item.get("legs") or []) for item in child_calendars),
    }


def _calendar_structure_from_row(row: dict[str, Any]) -> dict[str, Any]:
    details = (row.get("details") or {}).get("earnings_calendar") or {}
    structure = _coerce_structure(row.get("structure_summary") or details.get("structure") or {})
    value_summary = _coerce_value(details.get("value") or {})
    ticker = row.get("ticker") or row.get("symbol")
    structure_id = (
        row.get("current_structure_id")
        or row.get("structure_id")
        or row.get("row_id")
        or _child_structure_id(ticker, structure)
    )
    return {
        "structure_id": structure_id,
        "position_structure_id": structure_id,
        "account_id_masked": _masked_account(row),
        "underlying": ticker,
        "ticker": ticker,
        "structure_type": structure.get("structure_type") or structure.get("type") or "calendar",
        "front_expiration": structure.get("front_expiration"),
        "back_expiration": structure.get("back_expiration"),
        "strike": structure.get("strike"),
        "option_type": structure.get("option_type"),
        "legs": structure.get("legs") or [],
        "current_debit": value_summary.get("current_debit") or value_summary.get("current_mid_debit") or structure.get("current_debit"),
        "lifecycle_action": row.get("verdict") or row.get("trade_verdict"),
        "lifecycle_reason": row.get("primary_reason") or row.get("disposition_reason"),
        "source_row_id": row.get("row_id"),
        "source_table": "strategy_rows",
        "matching_status": "matched_child_calendar",
    }


def _coerce_structure(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not isinstance(value, str):
        return {}
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


def _build_double_calendar_parents(
    structures: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    non_calendars: list[dict[str, Any]] = []
    for structure in structures:
        stype = str(structure.get("structure_type") or "calendar").lower()
        if "calendar" not in stype or stype == "double_calendar":
            non_calendars.append(structure)
            continue
        key = (
            str(structure.get("underlying") or structure.get("ticker") or "").upper(),
            str(structure.get("front_expiration") or ""),
            str(structure.get("back_expiration") or ""),
        )
        groups.setdefault(key, []).append(structure)

    parents: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for (ticker, front, back), children in groups.items():
        calls = [item for item in children if str(item.get("option_type") or "").lower() == "call"]
        puts = [item for item in children if str(item.get("option_type") or "").lower() == "put"]
        if not calls or not puts:
            unmatched.extend(children)
            continue
        call_leg = calls[0]
        put_leg = puts[0]
        child_ids = [str(call_leg.get("structure_id") or ""), str(put_leg.get("structure_id") or "")]
        parent_id = hashlib.sha256(f"dc:{ticker}:{front}:{back}".encode()).hexdigest()[:16]
        call_leg["parent_structure_id"] = parent_id
        put_leg["parent_structure_id"] = parent_id
        call_debit = _num(call_leg.get("current_debit"))
        put_debit = _num(put_leg.get("current_debit"))
        combined_debit = round(call_debit + put_debit, 2) if call_debit is not None and put_debit is not None else None
        strikes = sorted([v for v in (call_leg.get("strike"), put_leg.get("strike")) if v is not None], key=lambda value: float(value))
        parents.append({
            "parent_structure_id": parent_id,
            "position_parent_id": parent_id,
            "position_structure_type": "double_calendar",
            "account_id_masked": call_leg.get("account_id_masked") or put_leg.get("account_id_masked"),
            "structure_type": "double_calendar",
            "ticker": ticker,
            "front_expiration": front,
            "back_expiration": back,
            "lower_strike": strikes[0] if strikes else None,
            "upper_strike": strikes[-1] if len(strikes) > 1 else None,
            "current_total_debit": combined_debit,
            "child_structure_ids": child_ids,
            "child_count": 2,
            "call_calendar": call_leg,
            "put_calendar": put_leg,
            "lifecycle_action": call_leg.get("lifecycle_action") or put_leg.get("lifecycle_action"),
            "lifecycle_reason": call_leg.get("lifecycle_reason") or put_leg.get("lifecycle_reason"),
            "assignment_risk_summary": {"status": "not_evaluated", "source": "child_calendar_grouping"},
            "matching_status": "matched_double_calendar_parent",
            "unmatched_leg_count": 0,
            "source_table": "strategy_rows",
        })
        unmatched.extend(calls[1:])
        unmatched.extend(puts[1:])
    unmatched.extend(non_calendars)
    return parents, unmatched


def _lifecycle_row_reconciliation(
    lifecycle_rows: list[dict[str, Any]],
    structures: list[dict[str, Any]],
    dedup: dict[str, Any],
) -> dict[str, Any]:
    unique_keys = {
        (
            item.get("underlying"),
            item.get("structure_type"),
            item.get("option_type"),
            item.get("strike"),
            item.get("front_expiration"),
            item.get("back_expiration"),
        )
        for item in structures
    }
    return {
        "lifecycle_row_count": len(lifecycle_rows),
        "child_calendar_count": len(structures),
        "structure_count": len(structures),
        "unique_structure_keys": len(unique_keys),
        "duplicate_group_count": dedup.get("duplicate_group_count", 0),
        "key_fields": ["underlying", "structure_type", "option_type", "strike", "front_expiration", "back_expiration"],
    }


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


def _child_structure_id(ticker: Any, structure: dict[str, Any]) -> str:
    raw = ":".join(str(part or "") for part in (
        ticker,
        structure.get("structure_type") or "calendar",
        structure.get("option_type"),
        structure.get("strike"),
        structure.get("front_expiration"),
        structure.get("back_expiration"),
    ))
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _masked_account(row: dict[str, Any]) -> str | None:
    for key in ("account_id_masked", "account_number_masked", "account_label_masked"):
        value = row.get(key)
        if value:
            return str(value)
    details = (row.get("details") or {}).get("earnings_calendar") if isinstance(row.get("details"), dict) else {}
    raw = details.get("raw") if isinstance(details, dict) else {}
    for key in ("account_id", "account_number", "account"):
        value = row.get(key) or (raw.get(key) if isinstance(raw, dict) else None)
        if value:
            text = str(value)
            if len(text) <= 4:
                return "***"
            return f"***{text[-4:]}"
    return None


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
