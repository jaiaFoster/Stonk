"""
Sprint 28 — Epic A: Data Provenance Service

Provides helpers for building, attaching, and querying DataProvenanceRecord
objects across all data types. The service is purely in-process — it never
calls providers, writes to storage, or modifies strategy behavior.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.data_provenance import (
    CALC_DIRECT,
    CALC_DERIVED,
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_DISPUTED,
    CONFIDENCE_NO_DATA,
    CONFIDENCE_SINGLE_SOURCE,
    PROVENANCE_SCHEMA_VERSION,
    SOURCE_CALCULATED,
    SOURCE_UNKNOWN,
    DataProvenanceRecord,
    EarningsProvenance,
)

_READ_ONLY = {"provider_calls_triggered": False, "read_only": True}


# ─── Field-level provenance attachment ────────────────────────────────────────

def attach_provenance(data: dict[str, Any], field: str, record: DataProvenanceRecord) -> None:
    """Attach a provenance record to *data* for *field* in-place.

    The annotation is stored under `_provenance.<field>` so it does not
    conflict with the field itself and can be stripped in one operation.
    """
    prov = data.setdefault("_provenance", {})
    prov[field] = record.to_dict()


def get_provenance(data: dict[str, Any], field: str) -> DataProvenanceRecord | None:
    prov = (data or {}).get("_provenance") or {}
    raw = prov.get(field)
    if not isinstance(raw, dict):
        return None
    return DataProvenanceRecord.from_dict(raw)


def strip_provenance(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *data* without any `_provenance` keys (for compact payloads)."""
    result = {k: v for k, v in data.items() if k != "_provenance"}
    return result


def provenance_summary(data: dict[str, Any]) -> dict[str, Any]:
    """Return a compact summary of all provenance records attached to *data*."""
    prov = (data or {}).get("_provenance") or {}
    sources: set[str] = set()
    confidence_map: dict[str, int] = {}
    conflicts: list[str] = []
    for field_name, raw in prov.items():
        if not isinstance(raw, dict):
            continue
        src = str(raw.get("source") or SOURCE_UNKNOWN)
        sources.add(src)
        conf = str(raw.get("confidence") or CONFIDENCE_NO_DATA)
        confidence_map[conf] = confidence_map.get(conf, 0) + 1
        if raw.get("conflict_detected"):
            conflicts.append(field_name)
    return {
        "provenance_schema_version": PROVENANCE_SCHEMA_VERSION,
        "field_count": len(prov),
        "sources": sorted(sources),
        "confidence_distribution": confidence_map,
        "conflicts": conflicts,
        "has_conflicts": bool(conflicts),
    }


# ─── Object-level provenance builders ─────────────────────────────────────────

def build_earnings_provenance(
    earnings_event: dict[str, Any],
    provider_results: dict[str, dict[str, Any]] | None = None,
    retrieved_at: str | None = None,
) -> EarningsProvenance:
    """Build a full EarningsProvenance from a merged earnings event.

    Parameters
    ----------
    earnings_event : dict
        The normalized, merged earnings event produced by the provider layer.
    provider_results : dict
        Raw per-provider results keyed by provider slug before merging.
    retrieved_at : str | None
        ISO timestamp when the data was fetched.
    """
    event = earnings_event or {}
    pr = provider_results or {}
    ts = retrieved_at or _utcnow()

    sources_seen = list(event.get("sources_seen") or event.get("date_sources") or [])
    sources_failed: list[str] = list(event.get("provider_errors") or [])
    sources_checked = list(set(sources_seen + [s.split(":")[0] for s in sources_failed]))

    conflict = bool(event.get("earnings_source_conflict") or event.get("date_conflict"))
    conflict_details = list(event.get("earnings_conflict_details") or [])

    # Date provenance
    if conflict:
        date_prov = DataProvenanceRecord.disputed(conflict_details, ts)
    elif len(sources_seen) >= 2:
        date_prov = DataProvenanceRecord.multi_source_confirmed(
            primary_source=sources_seen[0],
            all_sources=sources_seen,
            retrieved_at=ts,
            selection_reason=f"Date agreed by {len(sources_seen)} providers.",
        )
    elif len(sources_seen) == 1:
        date_prov = DataProvenanceRecord.single_source(
            source=sources_seen[0],
            retrieved_at=ts,
            selection_reason=f"Only {sources_seen[0]} reported this date.",
        )
    else:
        date_prov = DataProvenanceRecord.unavailable("No provider reported an earnings date.")

    # Session provenance: session confirmation is harder — derive from source data
    is_confirmed = bool(event.get("is_timestamp_confirmed"))
    if is_confirmed and len(sources_seen) >= 2:
        sess_prov = DataProvenanceRecord.multi_source_confirmed(
            primary_source=sources_seen[0],
            all_sources=sources_seen,
            retrieved_at=ts,
            selection_reason="Session (before/after market) confirmed by multiple sources.",
        )
    elif event.get("earnings_time") or event.get("session_label"):
        sess_prov = DataProvenanceRecord.single_source(
            source=sources_seen[0] if sources_seen else SOURCE_UNKNOWN,
            retrieved_at=ts,
            selection_reason="Session inferred from single provider.",
        )
    else:
        sess_prov = DataProvenanceRecord.unavailable("Session not reported.")

    # Per-provider detail
    provider_detail: dict[str, dict[str, Any]] = {}
    for src in sources_seen:
        raw = pr.get(src) or {}
        provider_detail[src] = {
            "date": raw.get("earnings_date") or raw.get("date") or event.get("earnings_date"),
            "session": raw.get("session_label") or raw.get("earnings_time"),
            "is_confirmed": raw.get("is_timestamp_confirmed", False),
            "eps_estimate": raw.get("eps_estimate"),
            "retrieved_at": ts,
        }

    conflict_summary = ""
    if conflict and conflict_details:
        parts = [f"{d.get('date')} ({', '.join(d.get('sources', []))})" for d in conflict_details]
        conflict_summary = "Date conflict: " + " vs ".join(parts)

    return EarningsProvenance(
        date_provenance=date_prov,
        session_provenance=sess_prov,
        provider_detail=provider_detail,
        conflict_summary=conflict_summary,
        date_agreement=not conflict and len(sources_seen) >= 2,
        session_agreement=is_confirmed,
        sources_checked=sources_checked,
        sources_returned_data=sources_seen,
        sources_failed=sources_failed,
    )


def build_quote_provenance(
    provider: str,
    retrieved_at: str | None = None,
    cache_hit: bool = False,
    cache_age_seconds: int | None = None,
) -> DataProvenanceRecord:
    method = CALC_DIRECT
    reason = f"Real-time quote from {provider}"
    if cache_hit:
        reason = f"Cached quote from {provider}"
        if cache_age_seconds is not None:
            reason += f" ({cache_age_seconds}s ago)"
    return DataProvenanceRecord(
        source=provider,
        retrieved_at=retrieved_at or _utcnow(),
        confidence=CONFIDENCE_SINGLE_SOURCE,
        calculation_method=method,
        approximation=cache_hit,
        selection_reason=reason,
    )


def build_iv_provenance(
    provider: str,
    calculation_method: str = "provider_native",
    retrieved_at: str | None = None,
    approximation: bool = False,
) -> DataProvenanceRecord:
    return DataProvenanceRecord(
        source=provider,
        retrieved_at=retrieved_at or _utcnow(),
        confidence=CONFIDENCE_SINGLE_SOURCE,
        calculation_method=calculation_method,
        approximation=approximation,
        selection_reason=f"IV from {provider} via {calculation_method}.",
    )


def build_forward_factor_provenance(
    front_iv_source: str,
    back_iv_source: str,
    front_dte: int,
    back_dte: int,
    retrieved_at: str | None = None,
) -> DataProvenanceRecord:
    return DataProvenanceRecord(
        source=SOURCE_CALCULATED,
        retrieved_at=retrieved_at or _utcnow(),
        confidence=CONFIDENCE_SINGLE_SOURCE,
        calculation_method=CALC_DERIVED,
        selection_reason=(
            f"Forward Factor derived from {front_iv_source} IV at {front_dte}d "
            f"and {back_iv_source} IV at {back_dte}d using variance term structure."
        ),
    )


# ─── Conflict detection ────────────────────────────────────────────────────────

def detect_value_conflict(
    field_name: str,
    values_by_source: dict[str, Any],
    tolerance: float = 0.0,
) -> dict[str, Any]:
    """Detect whether multiple sources disagree on a field's value.

    Returns a conflict report dict with `has_conflict`, `conflict_details`,
    and `agreed_value` (the most common value, or None when all differ).
    """
    if len(values_by_source) < 2:
        sources = list(values_by_source.keys())
        vals = list(values_by_source.values())
        return {
            "has_conflict": False,
            "field": field_name,
            "agreed_value": vals[0] if vals else None,
            "conflict_details": [],
            "sources_checked": sources,
            "tolerance_applied": tolerance,
        }

    items = [(src, val) for src, val in values_by_source.items() if val is not None]
    if not items:
        return {
            "has_conflict": False,
            "field": field_name,
            "agreed_value": None,
            "conflict_details": [],
            "sources_checked": list(values_by_source.keys()),
            "tolerance_applied": tolerance,
        }

    # Numeric comparison with tolerance
    try:
        nums = [(src, float(val)) for src, val in items]
        baseline_src, baseline_val = nums[0]
        conflict_details = []
        for src, val in nums[1:]:
            diff = abs(val - baseline_val)
            if diff > tolerance:
                conflict_details.append({
                    "source": src,
                    "value": val,
                    "baseline_source": baseline_src,
                    "baseline_value": baseline_val,
                    "diff": diff,
                })
        has_conflict = bool(conflict_details)
        return {
            "has_conflict": has_conflict,
            "field": field_name,
            "agreed_value": baseline_val if not has_conflict else None,
            "conflict_details": conflict_details,
            "sources_checked": [src for src, _ in items],
            "tolerance_applied": tolerance,
        }
    except (TypeError, ValueError):
        pass

    # String comparison
    unique_vals = set(str(v) for _, v in items)
    has_conflict = len(unique_vals) > 1
    conflict_details = []
    if has_conflict:
        for src, val in items:
            conflict_details.append({"source": src, "value": val})
    return {
        "has_conflict": has_conflict,
        "field": field_name,
        "agreed_value": items[0][1] if not has_conflict else None,
        "conflict_details": conflict_details,
        "sources_checked": [src for src, _ in items],
        "tolerance_applied": tolerance,
    }


# ─── Freshness annotation ──────────────────────────────────────────────────────

def annotate_freshness(data: dict[str, Any], retrieved_at: str | None = None) -> None:
    """Stamp `retrieved_at` on *data* in-place if not already present."""
    if "retrieved_at" not in data or not data["retrieved_at"]:
        data["retrieved_at"] = retrieved_at or _utcnow()


def freshness_age_seconds(data: dict[str, Any], now: datetime | None = None) -> int | None:
    """Return seconds since `retrieved_at` in *data*, or None if unavailable."""
    ts = (data or {}).get("retrieved_at")
    if not ts:
        return None
    try:
        from datetime import timezone as _tz
        parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_tz.utc)
        ref = (now or datetime.now(_tz.utc)).astimezone(_tz.utc)
        return max(0, int((ref - parsed).total_seconds()))
    except (TypeError, ValueError):
        return None


def freshness_label(age_seconds: int | None, warn_threshold: int = 21600, stale_threshold: int = 86400) -> str:
    if age_seconds is None:
        return "unknown"
    if age_seconds < warn_threshold:
        return "fresh"
    if age_seconds < stale_threshold:
        return "aging"
    return "stale"


# ─── Compact provenance serialization for API ──────────────────────────────────

def compact_provenance(record: DataProvenanceRecord | dict[str, Any] | None) -> dict[str, Any]:
    """Serialize a provenance record to a compact form safe for API responses."""
    if record is None:
        return {"source": SOURCE_UNKNOWN, "confidence": CONFIDENCE_NO_DATA}
    raw = record.to_dict() if isinstance(record, DataProvenanceRecord) else (record or {})
    out: dict[str, Any] = {
        "source": raw.get("source") or SOURCE_UNKNOWN,
        "confidence": raw.get("confidence") or CONFIDENCE_NO_DATA,
    }
    if raw.get("retrieved_at"):
        out["retrieved_at"] = raw["retrieved_at"]
    if raw.get("conflict_detected"):
        out["conflict_detected"] = True
        out["conflict_count"] = len(raw.get("conflict_details") or [])
    if raw.get("approximation"):
        out["approximation"] = True
    if raw.get("calculation_method") and raw["calculation_method"] not in (CALC_DIRECT, "unknown"):
        out["calculation_method"] = raw["calculation_method"]
    if raw.get("selection_reason"):
        out["selection_reason"] = raw["selection_reason"]
    return out


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
