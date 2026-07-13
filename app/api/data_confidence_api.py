"""
ASA Patch 32A — Data Confidence API

Provides the generic field-level provenance endpoint:

  GET /api/data-confidence/field
      ?run_id=<run_id>
      &strategy_id=<strategy_id>
      &row_id=<row_id>
      &field_id=<field_id>

All endpoints are read-only (provider_calls_triggered=False, read_only=True).

Confidence color map for UI clients
------------------------------------
HIGH    → green
MEDIUM  → yellow-green
LOW     → orange
CONFLICT → red
UNKNOWN  → gray
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.patch32a_provenance import (
    CONFIDENCE_COLOR,
    CONFIDENCE_LABEL,
    CONFIDENCE_LEVELS,
    PATCH32A_SCHEMA_VERSION,
    PROVIDER_STATUSES,
)
from app.db import data_provenance as _dp_db

_API_VERSION = "32A.v1"
_READ_ONLY = {"provider_calls_triggered": False, "read_only": True}


def get_field_provenance_response(
    run_id: str | None,
    strategy_id: str | None,
    row_id: str | None,
    field_id: str | None,
) -> tuple[dict[str, Any], int]:
    """Handle GET /api/data-confidence/field.

    Returns (response_dict, http_status_code).
    All parameters are optional — missing ones are treated as wildcard.
    field_id is required to return a useful response.
    """
    if not field_id:
        return {
            "error": "field_id is required",
            "example": "/api/data-confidence/field?run_id=&strategy_id=&row_id=&field_id=earnings_date",
            **_READ_ONLY,
        }, 400

    rows = _dp_db.get_field_provenance(
        run_id=run_id or None,
        strategy_id=strategy_id or None,
        row_id=row_id or None,
        field_id=field_id,
        limit=20,
    )

    if not rows:
        return {
            "field_id": field_id,
            "run_id": run_id,
            "strategy_id": strategy_id,
            "row_id": row_id,
            "found": False,
            "message": "No provenance records found for the given parameters.",
            "api_version": _API_VERSION,
            "schema_version": PATCH32A_SCHEMA_VERSION,
            **_READ_ONLY,
        }, 404

    latest = rows[0]
    prov = latest.get("provenance") or {}

    return {
        "field_id": field_id,
        "run_id": latest.get("run_id"),
        "strategy_id": latest.get("strategy_id"),
        "row_id": latest.get("row_id"),
        "ticker": latest.get("ticker"),
        "found": True,
        "provenance": _enrich_provenance(prov),
        "history_count": len(rows),
        "history": [
            {
                "id": r.get("id"),
                "run_id": r.get("run_id"),
                "confidence_level": r.get("confidence_level"),
                "selected_value": r.get("selected_value"),
                "selected_provider": r.get("selected_provider"),
                "created_at": r.get("created_at"),
            }
            for r in rows[:5]
        ],
        "confidence_levels_reference": _confidence_reference(),
        "api_version": _API_VERSION,
        "schema_version": PATCH32A_SCHEMA_VERSION,
        "checked_at": _utcnow(),
        **_READ_ONLY,
    }, 200


def build_data_confidence_reference() -> dict[str, Any]:
    """Return reference data for the data confidence system — for /api/data-confidence/reference."""
    return {
        "confidence_levels": [
            {
                "level": level,
                "label": CONFIDENCE_LABEL.get(level, level),
                "color": CONFIDENCE_COLOR.get(level, "gray"),
            }
            for level in CONFIDENCE_LEVELS
        ],
        "provider_statuses": list(PROVIDER_STATUSES),
        "selection_priority": ["robinhood", "finnhub", "alpha_vantage"],
        "earnings_rules": {
            "HIGH": "≥2 providers agree on date AND session",
            "MEDIUM": "≥2 providers agree on date; session differs or unknown",
            "LOW": "Only 1 provider has data",
            "CONFLICT": "2+ providers report different dates",
            "UNKNOWN": "No provider has data",
        },
        "freshness_thresholds_seconds": {
            "fresh": "< 21600 (6 hours)",
            "aging": "21600–86400 (6–24 hours)",
            "stale": "> 86400 (24 hours)",
        },
        "api_version": _API_VERSION,
        "schema_version": PATCH32A_SCHEMA_VERSION,
        "checked_at": _utcnow(),
        **_READ_ONLY,
    }


def _enrich_provenance(prov: dict[str, Any]) -> dict[str, Any]:
    """Add human-readable labels and colour to a raw provenance dict."""
    out = dict(prov)
    level = str(prov.get("confidence_level") or "UNKNOWN")
    out["confidence_label"] = CONFIDENCE_LABEL.get(level, level)
    out["confidence_color"] = CONFIDENCE_COLOR.get(level, "gray")
    return out


def _confidence_reference() -> list[dict[str, Any]]:
    return [
        {"level": lvl, "label": CONFIDENCE_LABEL.get(lvl, lvl), "color": CONFIDENCE_COLOR.get(lvl, "gray")}
        for lvl in CONFIDENCE_LEVELS
    ]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
