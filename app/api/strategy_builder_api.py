"""Read-only API helpers for the custom-strategy data catalog."""

from __future__ import annotations

from typing import Any

from app.services.strategy_data_catalog_service import (
    build_catalog_response,
    get_field,
    operator_catalog,
    requirements_for_fields,
    validate_rule_definition,
)


def catalog(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_catalog_response(filters)


def fields(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_catalog_response(filters)


def field(field_id: str) -> tuple[dict[str, Any], int]:
    return get_field(field_id)


def operators() -> dict[str, Any]:
    return operator_catalog()


def requirements(field_ids: list[str]) -> dict[str, Any]:
    return requirements_for_fields(field_ids)


def validate_rule(body: dict[str, Any]) -> dict[str, Any]:
    return validate_rule_definition(body or {})
