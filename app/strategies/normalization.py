"""Strategy row normalizers for the strategies package.

Thin wrappers around the existing normalization service that produce dicts
conforming to UniversalStrategyRow and stamp schema_version + row_type.
"""

from __future__ import annotations

from typing import Any

from app.services.strategy_row_normalization_service import (
    normalize_strategy_row as _normalize,
    normalize_strategy_rows as _normalize_rows,
)
from app.strategies.schema import SCHEMA_VERSION, VALID_ROW_TYPES


def normalize_stock_momentum_row(
    row: dict[str, Any],
    strategy_id: str = "stock_momentum",
) -> dict[str, Any]:
    """Normalize a stock_momentum row into the universal schema.

    Works on a shallow copy — original row is not mutated.
    """
    result = _normalize({**row}, strategy_id)
    result.setdefault("row_type", _stock_row_type(result))
    result.setdefault("schema_version", SCHEMA_VERSION)
    return result


def normalize_legacy_row(
    row: dict[str, Any],
    strategy_id: str,
) -> dict[str, Any]:
    """Normalize any legacy strategy row into the universal schema."""
    result = _normalize({**row}, strategy_id)
    result.setdefault("row_type", _infer_row_type(result, strategy_id))
    result.setdefault("schema_version", SCHEMA_VERSION)
    return result


def normalize_rows(
    rows: list[dict[str, Any]],
    strategy_id: str,
) -> list[dict[str, Any]]:
    """Normalize a list of rows. Works on shallow copies."""
    normalized = _normalize_rows(rows, strategy_id)
    for row in normalized:
        row.setdefault("row_type", _infer_row_type(row, strategy_id))
        row.setdefault("schema_version", SCHEMA_VERSION)
    return normalized


def _stock_row_type(row: dict[str, Any]) -> str:
    action = str(row.get("action") or "").upper()
    if "CONSIDER" in action or ("ADD" in action and "AVOID" not in action):
        return "new_candidate"
    if "AVOID" in action or "WEAK" in action or "FAIL" in action:
        return "rejected_candidate"
    return "observation"


def _infer_row_type(row: dict[str, Any], strategy_id: str) -> str:
    if strategy_id == "stock_momentum":
        return _stock_row_type(row)
    verdict = str(row.get("verdict") or "").upper()
    if verdict.startswith("PASS"):
        return "new_candidate"
    if verdict.startswith("FAIL"):
        return "rejected_candidate"
    return "observation"
