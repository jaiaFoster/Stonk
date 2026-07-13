"""
Sprint 28 — Epic L: Provenance API

Read-only endpoints for accessing provenance, confidence, and data quality
information without breaking existing API clients.

Versioning strategy
-------------------
- Existing API responses are UNCHANGED — no fields removed, no formats altered.
- Provenance data is available via new endpoints and optional query parameters.
- Response headers: `X-ASA-Data-Version` and `X-ASA-Provenance-Schema` are
  added to all API responses that go through `add_provenance_headers()`.
- `?include_provenance=true` on strategy rows endpoints adds `_provenance`
  and `_data_details` sub-objects to each row.

All functions are read-only (provider_calls_triggered=False, read_only=True).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.data_provenance import PROVENANCE_SCHEMA_VERSION
from app.services.data_provenance_service import (
    compact_provenance,
    freshness_label,
    freshness_age_seconds,
)
from app.services.earnings_confidence_service import (
    build_earnings_confidence_report,
    public_confidence_label,
)
from app.services.provider_health_service import (
    build_provider_health_from_manifest,
    build_health_summary,
)

_API_DATA_VERSION = "28.L.v1"
_READ_ONLY = {"provider_calls_triggered": False, "read_only": True}


# ─── Response header helpers ───────────────────────────────────────────────────

def provenance_response_headers() -> dict[str, str]:
    """Return HTTP response headers to attach to all API responses."""
    return {
        "X-ASA-Data-Version": _API_DATA_VERSION,
        "X-ASA-Provenance-Schema": PROVENANCE_SCHEMA_VERSION,
        "X-ASA-Read-Only": "true",
    }


def add_provenance_headers(response: Any) -> Any:
    """Add provenance headers to a Flask response object in-place."""
    try:
        for key, val in provenance_response_headers().items():
            response.headers[key] = val
    except Exception:
        pass
    return response


# ─── Strategy row enrichment ───────────────────────────────────────────────────

def enrich_rows_with_provenance(
    rows: list[dict[str, Any]],
    strategy_id: str,
    include_data_details: bool = False,
) -> list[dict[str, Any]]:
    """Add `_provenance_compact` (and optionally `_data_details`) to each row.

    This is additive — no existing fields are altered.
    """
    enriched = []
    for row in rows:
        r = dict(row)
        r["_provenance_compact"] = _row_provenance_compact(r, strategy_id)
        if include_data_details:
            try:
                from app.services.data_details_builder_service import build_data_details
                r["_data_details"] = build_data_details(r, strategy_id)
            except Exception:
                pass
        enriched.append(r)
    return enriched


def _row_provenance_compact(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Build a compact provenance summary for one strategy row."""
    sources: list[str] = []
    conf_fields: dict[str, str] = {}

    # Earnings confidence
    if row.get("earnings_sources_seen") or row.get("date_sources"):
        srcs = row.get("earnings_sources_seen") or row.get("date_sources") or []
        sources.extend(srcs[:4])
        conf_fields["earnings_date"] = row.get("earnings_date_confidence") or row.get("date_confidence") or "unknown"

    # Quote / options
    chain_src = row.get("chain_provider") or "tradier"
    if row.get("front_iv") is not None or row.get("back_iv") is not None:
        if chain_src not in sources:
            sources.append(chain_src)
        conf_fields["iv"] = "single_source"

    # FF provenance
    ff_prov = row.get("_ff_provenance") or {}
    if ff_prov:
        conf_fields["forward_factor"] = "single_source"

    retrieved_at = row.get("retrieved_at") or row.get("observed_at")
    age = freshness_age_seconds({"retrieved_at": retrieved_at}) if retrieved_at else None

    return {
        "sources": list(dict.fromkeys(sources)),
        "confidence_fields": conf_fields,
        "has_conflict": bool(row.get("date_conflict") or row.get("earnings_source_conflict")),
        "data_freshness": freshness_label(age),
        "retrieved_at": retrieved_at,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
    }


# ─── Provenance summary endpoint builders ──────────────────────────────────────

def build_provenance_summary(
    ticker: str,
    earnings_event: dict[str, Any] | None = None,
    provider_manifest: dict[str, Any] | None = None,
    strategy_rows: list[dict[str, Any]] | None = None,
    strategy_id: str = "unknown",
) -> dict[str, Any]:
    """Build a full provenance summary for a ticker across all data types.

    Suitable for the `/api/data/provenance/<ticker>` endpoint.
    """
    sections: dict[str, Any] = {}

    if earnings_event:
        conf_report = build_earnings_confidence_report(earnings_event)
        sections["earnings"] = {
            "date": earnings_event.get("earnings_date"),
            "confidence_label": public_confidence_label(conf_report),
            "date_confidence": conf_report.get("date_confidence"),
            "conflict_detected": conf_report.get("conflict_detected"),
            "sources": conf_report.get("sources_returned_data") or [],
            "trade_allowed": conf_report.get("trade_allowed"),
            "full_report": conf_report,
        }

    if provider_manifest:
        health_records = build_provider_health_from_manifest(provider_manifest)
        sections["provider_health"] = build_health_summary(health_records)

    if strategy_rows:
        row_summaries = []
        for r in strategy_rows[:10]:
            row_summaries.append({
                "ticker": r.get("ticker"),
                "verdict": r.get("verdict"),
                "score": r.get("score"),
                "provenance": _row_provenance_compact(r, strategy_id),
            })
        sections["strategy_rows"] = {
            "count": len(strategy_rows),
            "summaries": row_summaries,
        }

    return {
        "ticker": ticker,
        "api_data_version": _API_DATA_VERSION,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "sections": sections,
        "checked_at": _utcnow(),
        **_READ_ONLY,
    }


def build_data_version_info() -> dict[str, Any]:
    """Return version/schema info for the `/api/data/version` endpoint."""
    return {
        "api_data_version": _API_DATA_VERSION,
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "response_headers": provenance_response_headers(),
        "optional_query_params": {
            "include_provenance": "Add _provenance_compact to each strategy row.",
            "include_data_details": "Add _data_details panel data to each row.",
        },
        "endpoints": {
            "/api/data/version": "Data version and schema info (this response).",
            "/api/data/provenance/<ticker>": "Full provenance for one ticker.",
        },
        "checked_at": _utcnow(),
        **_READ_ONLY,
    }


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
