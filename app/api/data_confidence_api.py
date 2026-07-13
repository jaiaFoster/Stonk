"""
ASA Patch 32A/32B — Data Confidence API

Provides the generic field-level provenance endpoints:

  GET /api/data-confidence/field
      ?run_id=<run_id>
      &strategy_id=<strategy_id>
      &row_id=<row_id>
      &field_id=<field_id>

  GET /api/data-confidence/batch
      ?run_id=<run_id>
      &strategy_id=<strategy_id>
      &field_ids=earnings.date,market.last_price,...  (comma-separated, optional)
      &limit=<1-100>         (default 50)
      &cursor=<id>           (integer cursor for pagination)

  GET /api/data-confidence/reference

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


def get_batch_field_provenance_response(
    run_id: str | None,
    strategy_id: str | None,
    field_ids: list[str] | None,
    limit: int = 50,
    cursor: int | None = None,
) -> tuple[dict[str, Any], int]:
    """Handle GET /api/data-confidence/batch.

    Returns a paginated list of compact FieldProvenanceRecord dicts.
    Bounded at max 100 records per page. No provider calls triggered.

    Cursor is the integer row ID of the last item returned; pass it as
    ?cursor=<id> to fetch the next page.
    """
    clamped_limit = max(1, min(int(limit or 50), 100))

    import json as _json

    try:
        rows = _dp_db.get_field_provenance_batch(
            run_id=run_id or None,
            strategy_id=strategy_id or None,
            field_ids=[f for f in (field_ids or []) if f] or None,
            limit=clamped_limit + 1,  # fetch one extra to detect next page
            cursor=int(cursor) if cursor else None,
        )
    except Exception:
        rows = []

    has_next = len(rows) > clamped_limit
    page_rows = rows[:clamped_limit]
    next_cursor = page_rows[-1].get("id") if has_next and page_rows else None

    items = []
    for r in page_rows:
        prov_raw = r.get("provenance") or {}
        # Build compact representation from stored provenance JSON
        item: dict[str, Any] = {
            "id": r.get("id"),
            "run_id": r.get("run_id"),
            "strategy_id": r.get("strategy_id"),
            "row_id": r.get("row_id"),
            "ticker": r.get("ticker"),
            "field_id": r.get("field_id"),
            "confidence_level": r.get("confidence_level"),
            "confidence_color": CONFIDENCE_COLOR.get(str(r.get("confidence_level") or "UNKNOWN"), "gray"),
            "selected_value": r.get("selected_value"),
            "selected_provider": r.get("selected_provider"),
            "created_at": r.get("created_at"),
        }
        # Include confidence reason and other lightweight fields if available in provenance
        if isinstance(prov_raw, dict):
            for key in ("confidence_reason", "selection_reason", "is_calculated", "has_conflict"):
                if prov_raw.get(key) is not None:
                    item[key] = prov_raw[key]
        items.append(item)

    return {
        "items": items,
        "count": len(items),
        "has_next_page": has_next,
        "next_cursor": next_cursor,
        "limit": clamped_limit,
        "filters": {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "field_ids": field_ids,
        },
        "api_version": _API_VERSION,
        "schema_version": PATCH32A_SCHEMA_VERSION,
        "checked_at": _utcnow(),
        **_READ_ONLY,
    }, 200


def _confidence_reference() -> list[dict[str, Any]]:
    return [
        {"level": lvl, "label": CONFIDENCE_LABEL.get(lvl, lvl), "color": CONFIDENCE_COLOR.get(lvl, "gray")}
        for lvl in CONFIDENCE_LEVELS
    ]


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
