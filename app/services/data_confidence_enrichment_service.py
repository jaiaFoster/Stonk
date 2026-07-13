"""
ASA Patch 32A — Data Confidence Enrichment Service

Adds Patch 32A provenance fields to strategy rows in-place:
  - data_confidence          overall confidence level (HIGH/MEDIUM/LOW/CONFLICT/UNKNOWN)
  - data_confidence_summary  {field_id: confidence_level} mapping
  - provenance_refs          {field_id: compact_provenance} dict
  - freshness_summary        {field_id: {age_seconds, label}}
  - conflict_count           int
  - has_data_conflict        bool

This service is read-only: no provider calls, no broker writes.
All functions are additive — no existing row fields are removed or altered.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.patch32a_provenance import (
    CONFIDENCE_UNKNOWN,
    PATCH32A_SCHEMA_VERSION,
    FieldProvenanceRecord,
)
from app.services.earnings_selection_service import (
    build_earnings_field_provenance,
    row_confidence_summary,
)
from app.services.data_provenance_service import freshness_age_seconds, freshness_label

_ENRICHMENT_SCHEMA_VERSION = "32A.v1"


def enrich_row_with_data_confidence(
    row: dict[str, Any],
    strategy_id: str = "unknown",
    provider_results: dict[str, dict[str, Any]] | None = None,
) -> None:
    """Attach Patch 32A data confidence fields to a strategy row in-place.

    Parameters
    ----------
    row:
        A normalized strategy row dict (modified in-place).
    strategy_id:
        The strategy this row belongs to (for logging / field routing).
    provider_results:
        Optional dict of raw per-provider results (e.g. {"finnhub": {...}}).
        When provided, earnings provenance is derived from it.
        When absent, any existing Sprint 28 provenance on the row is used.
    """
    provenance_refs: dict[str, Any] = {}

    # Build earnings provenance
    if strategy_id in ("earnings_calendar",) or row.get("earnings_date"):
        try:
            prov_map = build_earnings_field_provenance(
                event=row,
                provider_results=provider_results,
                observed_at=row.get("retrieved_at") or row.get("observed_at"),
            )
            for fid, rec in prov_map.items():
                provenance_refs[fid] = rec.compact()
        except Exception:
            pass

    # Carry forward any Sprint 28 _ff_provenance into provenance_refs
    ff_prov = row.get("_ff_provenance") or {}
    if ff_prov:
        provenance_refs.setdefault("forward_factor", {
            "field_id": "forward_factor",
            "confidence_level": "LOW",
            "confidence_color": "orange",
            "selected_provider": "calculated",
            "selected_source_type": "CALCULATED",
            "provider_count": 1,
            "has_conflict": False,
            "schema_version": PATCH32A_SCHEMA_VERSION,
        })

    # Carry forward IV provenance from Sprint 28 if available
    for iv_field in ("front_iv", "back_iv"):
        existing = (row.get("_ff_provenance") or {}).get(iv_field)
        if existing and iv_field not in provenance_refs:
            src = (existing.get("source") or "tradier") if isinstance(existing, dict) else "tradier"
            provenance_refs.setdefault(iv_field, {
                "field_id": iv_field,
                "confidence_level": "LOW",
                "confidence_color": "orange",
                "selected_provider": src,
                "selected_source_type": "PROVIDER",
                "provider_count": 1,
                "has_conflict": False,
                "schema_version": PATCH32A_SCHEMA_VERSION,
            })

    # Quote provenance — use retrieved_at from chain_provider if available
    chain_src = row.get("chain_provider") or "tradier"
    if row.get("last_price") is not None or row.get("stock_price") is not None:
        provenance_refs.setdefault("quote", {
            "field_id": "quote",
            "confidence_level": "LOW",
            "confidence_color": "orange",
            "selected_provider": chain_src,
            "selected_source_type": "PROVIDER",
            "provider_count": 1,
            "has_conflict": False,
            "schema_version": PATCH32A_SCHEMA_VERSION,
        })

    # Compute roll-up
    conf_summary = row_confidence_summary(provenance_refs)

    # Freshness summary
    freshness_summary: dict[str, Any] = {}
    retrieved_at = row.get("retrieved_at") or row.get("observed_at")
    if retrieved_at:
        age = freshness_age_seconds({"retrieved_at": retrieved_at})
        label = freshness_label(age)
        freshness_summary["data"] = {
            "retrieved_at": retrieved_at,
            "age_seconds": age,
            "label": label,
        }

    row["data_confidence"] = conf_summary["data_confidence"]
    row["data_confidence_summary"] = conf_summary["data_confidence_summary"]
    row["provenance_refs"] = provenance_refs
    row["freshness_summary"] = freshness_summary
    row["conflict_count"] = conf_summary["conflict_count"]
    row["has_data_conflict"] = conf_summary["has_data_conflict"]
    row["data_confidence_schema_version"] = _ENRICHMENT_SCHEMA_VERSION


def enrich_rows_batch(
    rows: list[dict[str, Any]],
    strategy_id: str = "unknown",
    provider_results_by_ticker: dict[str, dict[str, dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    """Enrich a list of rows with data confidence fields. Returns new list."""
    enriched = []
    for row in rows:
        r = dict(row)
        ticker = str(r.get("ticker") or "").upper()
        pr = (provider_results_by_ticker or {}).get(ticker)
        enrich_row_with_data_confidence(r, strategy_id=strategy_id, provider_results=pr)
        enriched.append(r)
    return enriched


def data_confidence_compact(row: dict[str, Any]) -> dict[str, Any]:
    """Return a minimal data confidence summary for embedding in opportunity cards."""
    return {
        "data_confidence": row.get("data_confidence") or CONFIDENCE_UNKNOWN,
        "has_data_conflict": bool(row.get("has_data_conflict")),
        "conflict_count": int(row.get("conflict_count") or 0),
        "freshness_label": (
            (row.get("freshness_summary") or {}).get("data", {}).get("label") or "unknown"
        ),
        "confidence_color": _confidence_color(row.get("data_confidence") or CONFIDENCE_UNKNOWN),
        "schema_version": _ENRICHMENT_SCHEMA_VERSION,
    }


def build_rejection_explainability(row: dict[str, Any], strategy_id: str = "unknown") -> dict[str, Any]:
    """Build a rejection explainability summary for calendar rejected rows.

    Returns gate_name, blocking_gate_count, data used for evaluation,
    and data confidence at rejection time.
    """
    gates = row.get("gates") or []
    blocking_gates: list[dict[str, Any]] = []
    if isinstance(gates, list):
        for g in gates:
            if isinstance(g, dict) and (g.get("blocking") or g.get("result") in {"fail", "FAIL"}):
                blocking_gates.append({
                    "gate": g.get("gate") or g.get("name") or "unknown",
                    "result": g.get("result") or "fail",
                    "reason": g.get("reason") or g.get("primary_reason") or "",
                })

    data_used: dict[str, Any] = {}
    for field in ("earnings_date", "earnings_time", "strike", "front_dte", "back_dte",
                  "front_iv", "back_iv", "debit", "delta", "score"):
        if row.get(field) is not None:
            data_used[field] = row[field]

    return {
        "ticker": row.get("ticker"),
        "strategy_id": strategy_id,
        "verdict": row.get("verdict"),
        "decision_class": row.get("decision_class"),
        "primary_reason": row.get("primary_reason"),
        "blocking_gates": blocking_gates,
        "blocking_gate_count": len(blocking_gates),
        "data_used": data_used,
        "data_confidence": row.get("data_confidence") or CONFIDENCE_UNKNOWN,
        "has_data_conflict": bool(row.get("has_data_conflict")),
        "schema_version": _ENRICHMENT_SCHEMA_VERSION,
    }


def _confidence_color(level: str) -> str:
    return {
        "HIGH": "green",
        "MEDIUM": "yellow-green",
        "LOW": "orange",
        "CONFLICT": "red",
        "UNKNOWN": "gray",
    }.get(str(level).upper(), "gray")
