"""Custom strategy compiler — non-executing preview and data requirement mapping.

ASA Patch 31B.
Maps strategy conditions to data requirements and cost classes.
Never triggers provider calls or broker writes.
"""
from __future__ import annotations

from typing import Any

# Cost class ordering (cheapest → most expensive).
_COST_CLASS_RANK = {"cheap": 0, "moderate": 1, "expensive": 2, "unsupported": 3}

# Fields that cost nothing (always in-memory from row store).
_CHEAP_FIELDS: frozenset[str] = frozenset({
    "ticker", "score", "verdict", "action", "row_type", "strategy_id",
    "daily_opportunity_eligible", "eligibility_status", "decision_class",
    "action_type", "data_quality", "strategy_family", "strategy_goal",
})


def compile_preview(definition: dict[str, Any]) -> dict[str, Any]:
    """Return a non-executing preview of data requirements and cost class.

    This function is read-only and makes no provider calls.
    """
    conditions = definition.get("conditions", [])
    field_ids: set[str] = set()
    for group in conditions:
        if not isinstance(group, dict):
            continue
        for cond in (group.get("conditions") or []):
            if isinstance(cond, dict) and cond.get("field_id"):
                field_ids.add(str(cond["field_id"]))

    requirements = _map_field_requirements(field_ids)
    cost_class = _aggregate_cost_class(requirements)

    return {
        "preview_type": "compile_preview",
        "schema_version": "31B.v1",
        "field_ids_referenced": sorted(field_ids),
        "data_requirements": requirements,
        "cost_class": cost_class,
        "cost_class_rank": _COST_CLASS_RANK.get(cost_class, 99),
        "provider_calls_triggered": False,
        "broker_calls_triggered": False,
        "executable": False,
        "preview_notes": (
            "Custom strategies are signal-only. No trades or orders are placed. "
            "Provider data is read from the row store; no live API calls are made during preview."
        ),
    }


def _map_field_requirements(field_ids: set[str]) -> list[dict[str, Any]]:
    try:
        from app.services.strategy_data_catalog_service import field_catalog
        catalog = field_catalog()
    except Exception:
        catalog = {}

    requirements: list[dict[str, Any]] = []
    for field_id in sorted(field_ids):
        field_def = catalog.get(field_id)
        if field_def is None:
            requirements.append({
                "field_id": field_id,
                "cost_class": "unsupported",
                "requires_market_data": False,
                "requires_options_data": False,
                "requires_earnings_data": False,
                "requires_broker_data": False,
                "availability_stage": "unknown",
                "note": "Field not found in catalog — will fail at runtime.",
            })
            continue
        cost_class = _field_cost_class(field_def)
        requirements.append({
            "field_id": field_id,
            "display_name": field_def.display_name,
            "cost_class": cost_class,
            "requires_market_data": field_def.requires_market_data,
            "requires_options_data": field_def.requires_options_data,
            "requires_earnings_data": field_def.requires_earnings_data,
            "requires_broker_data": field_def.requires_broker_data,
            "availability_stage": field_def.availability_stage,
            "provider_cost_class": field_def.provider_cost_class,
        })
    return requirements


def _field_cost_class(field_def: Any) -> str:
    if field_def.field_id in _CHEAP_FIELDS:
        return "cheap"
    provider_class = str(field_def.provider_cost_class or "none").lower()
    if field_def.requires_broker_data:
        return "unsupported"
    if provider_class in ("expensive", "chain_set"):
        return "expensive"
    if field_def.requires_options_data or field_def.requires_earnings_data:
        return "moderate"
    if field_def.requires_market_data:
        return "moderate"
    if provider_class == "none":
        return "cheap"
    return "moderate"


def _aggregate_cost_class(requirements: list[dict[str, Any]]) -> str:
    if not requirements:
        return "cheap"
    max_rank = max(
        _COST_CLASS_RANK.get(str(req.get("cost_class") or "cheap"), 0)
        for req in requirements
    )
    for name, rank in _COST_CLASS_RANK.items():
        if rank == max_rank:
            return name
    return "cheap"
