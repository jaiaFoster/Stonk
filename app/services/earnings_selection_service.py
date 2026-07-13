"""
ASA Patch 32A — Earnings Selection Service

Deterministic provider selection for earnings date / session data.

Selection priority: Robinhood > Finnhub > Alpha Vantage

Confidence rules
----------------
HIGH    — ≥2 providers agree on normalised date AND session (BMO/AMC/unknown)
MEDIUM  — ≥2 providers agree on date; sessions differ or one is unknown
LOW     — exactly 1 provider returned data
CONFLICT — ≥2 providers returned different dates (calendar day mismatch)
UNKNOWN — no provider returned usable data

This service is purely in-process: no provider calls, no DB writes, read-only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.patch32a_provenance import (
    CONFIDENCE_CONFLICT,
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    CONFIDENCE_UNKNOWN,
    EARNINGS_PROVIDER_PRIORITY,
    SOURCE_TYPE_MISSING,
    SOURCE_TYPE_PROVIDER,
    STATUS_AVAILABLE,
    STATUS_ERROR,
    STATUS_MISSING,
    STATUS_NOT_REQUESTED,
    PATCH32A_SCHEMA_VERSION,
    FieldProvenanceRecord,
    ProviderValueRecord,
)

_SELECTION_SCHEMA_VERSION = "32A.v1"

# Session normalisation: map provider session strings to canonical form
_SESSION_CANONICAL = {
    "pre": "BMO",
    "pre market": "BMO",
    "before market open": "BMO",
    "bmo": "BMO",
    "before open": "BMO",
    "post": "AMC",
    "post market": "AMC",
    "after market close": "AMC",
    "after market": "AMC",
    "amc": "AMC",
    "after close": "AMC",
    "after hours": "AMC",
    "unk": "UNKNOWN",
    "unknown": "UNKNOWN",
    "": "UNKNOWN",
}


def _normalise_session(raw: str | None) -> str:
    if raw is None:
        return "UNKNOWN"
    return _SESSION_CANONICAL.get(str(raw).strip().lower(), "UNKNOWN")


def _normalise_date(raw: str | None) -> str | None:
    """Return YYYY-MM-DD or None."""
    if not raw:
        return None
    try:
        s = str(raw).strip()
        if len(s) >= 10:
            return s[:10]
        return None
    except Exception:
        return None


def select_earnings_provenance(
    provider_results: dict[str, dict[str, Any]],
    observed_at: str | None = None,
) -> FieldProvenanceRecord:
    """Build a FieldProvenanceRecord for the earnings_date field.

    Parameters
    ----------
    provider_results:
        Keyed by provider slug ("robinhood", "finnhub", "alpha_vantage", …).
        Each value is a dict with keys like earnings_date, session_label,
        is_timestamp_confirmed, error, error_message. Missing / error providers
        may be absent from the dict or present with null date.
    observed_at:
        ISO timestamp for when this selection was made.
    """
    ts = observed_at or _utcnow()

    # Build per-provider records, retaining every attempted provider
    pv_records: list[ProviderValueRecord] = []
    available_by_provider: dict[str, str] = {}  # provider → normalised_date

    # Ensure all known providers appear (even if not in provider_results)
    seen_providers: set[str] = set(provider_results.keys()) | set(EARNINGS_PROVIDER_PRIORITY)

    for provider in sorted(seen_providers):
        raw = provider_results.get(provider)
        if raw is None:
            pv_records.append(ProviderValueRecord.not_requested(provider))
            continue
        err = raw.get("error") or raw.get("error_message")
        raw_date = raw.get("earnings_date") or raw.get("date")
        norm_date = _normalise_date(raw_date)
        if err and not norm_date:
            pv_records.append(ProviderValueRecord.error(
                provider=provider,
                error_code=str(raw.get("error_code") or "PROVIDER_ERROR"),
                error_message=str(err)[:200],
                observed_at=ts,
            ))
        elif norm_date:
            pv_records.append(ProviderValueRecord.available(
                provider=provider,
                value=norm_date,
                observed_at=ts,
            ))
            available_by_provider[provider] = norm_date
        else:
            pv_records.append(ProviderValueRecord.missing(provider, observed_at=ts))

    # Determine confidence and select winner
    available_dates = list(available_by_provider.values())
    unique_dates = set(available_dates)

    if not available_by_provider:
        return _build_record(
            field_id="earnings_date",
            selected_value=None,
            selected_provider="unknown",
            source_type=SOURCE_TYPE_MISSING,
            confidence_level=CONFIDENCE_UNKNOWN,
            confidence_reason="No provider returned earnings date data.",
            selection_reason="No data available.",
            pv_records=pv_records,
            conflicts=[],
            ts=ts,
        )

    if len(unique_dates) > 1:
        # Conflict — providers disagree on date
        conflict_list = [
            {"provider": p, "value": d}
            for p, d in available_by_provider.items()
        ]
        selected_provider, selected_date = _pick_by_priority(available_by_provider)
        return _build_record(
            field_id="earnings_date",
            selected_value=selected_date,
            selected_provider=selected_provider,
            source_type=SOURCE_TYPE_PROVIDER,
            confidence_level=CONFIDENCE_CONFLICT,
            confidence_reason=(
                f"Providers disagree on earnings date: "
                + ", ".join(f"{p}={d}" for p, d in sorted(available_by_provider.items()))
            ),
            selection_reason=f"Selected highest-priority provider ({selected_provider}) despite conflict.",
            pv_records=pv_records,
            conflicts=conflict_list,
            ts=ts,
        )

    # All available providers agree on date
    agreed_date = available_dates[0]
    selected_provider, _ = _pick_by_priority(available_by_provider)
    provider_count = len(available_by_provider)

    # Check session agreement for HIGH vs MEDIUM
    sessions = {
        p: _normalise_session(
            (provider_results.get(p) or {}).get("session_label")
            or (provider_results.get(p) or {}).get("earnings_time")
        )
        for p in available_by_provider
    }
    known_sessions = {s for s in sessions.values() if s != "UNKNOWN"}

    if provider_count >= 2 and len(known_sessions) <= 1:
        # ≥2 agree on date; sessions agree (or all unknown → HIGH)
        confidence = CONFIDENCE_HIGH
        reason = (
            f"{provider_count} providers agree on {agreed_date}"
            + (f" and session ({next(iter(known_sessions))})" if known_sessions else " (session unknown)")
        )
    elif provider_count >= 2:
        # ≥2 agree on date but sessions differ
        confidence = CONFIDENCE_MEDIUM
        reason = (
            f"{provider_count} providers agree on date {agreed_date} "
            f"but sessions differ: {sessions}"
        )
    else:
        confidence = CONFIDENCE_LOW
        reason = f"Only {selected_provider} reported earnings date {agreed_date}."

    return _build_record(
        field_id="earnings_date",
        selected_value=agreed_date,
        selected_provider=selected_provider,
        source_type=SOURCE_TYPE_PROVIDER,
        confidence_level=confidence,
        confidence_reason=reason,
        selection_reason=_selection_reason(selected_provider, available_by_provider),
        pv_records=pv_records,
        conflicts=[],
        ts=ts,
    )


def select_earnings_session_provenance(
    provider_results: dict[str, dict[str, Any]],
    observed_at: str | None = None,
) -> FieldProvenanceRecord:
    """Build a FieldProvenanceRecord for earnings_session (BMO/AMC/UNKNOWN)."""
    ts = observed_at or _utcnow()

    pv_records: list[ProviderValueRecord] = []
    available_by_provider: dict[str, str] = {}

    seen_providers: set[str] = set(provider_results.keys()) | set(EARNINGS_PROVIDER_PRIORITY)
    for provider in sorted(seen_providers):
        raw = provider_results.get(provider)
        if raw is None:
            pv_records.append(ProviderValueRecord.not_requested(provider))
            continue
        session_raw = raw.get("session_label") or raw.get("earnings_time")
        norm = _normalise_session(session_raw)
        if norm != "UNKNOWN":
            pv_records.append(ProviderValueRecord.available(provider, norm, ts))
            available_by_provider[provider] = norm
        elif raw.get("error") or raw.get("error_message"):
            pv_records.append(ProviderValueRecord.error(
                provider, str(raw.get("error_code") or "PROVIDER_ERROR"),
                str(raw.get("error") or raw.get("error_message") or "")[:200], ts,
            ))
        else:
            pv_records.append(ProviderValueRecord.missing(provider, ts))

    if not available_by_provider:
        return _build_record(
            field_id="earnings_session",
            selected_value="UNKNOWN",
            selected_provider="unknown",
            source_type=SOURCE_TYPE_MISSING,
            confidence_level=CONFIDENCE_UNKNOWN,
            confidence_reason="No provider reported a session (BMO/AMC).",
            selection_reason="No session data.",
            pv_records=pv_records,
            conflicts=[],
            ts=ts,
        )

    unique_sessions = set(available_by_provider.values())
    selected_provider, selected_session = _pick_by_priority(available_by_provider)

    if len(unique_sessions) > 1:
        conflicts = [{"provider": p, "value": s} for p, s in available_by_provider.items()]
        return _build_record(
            field_id="earnings_session",
            selected_value=selected_session,
            selected_provider=selected_provider,
            source_type=SOURCE_TYPE_PROVIDER,
            confidence_level=CONFIDENCE_CONFLICT,
            confidence_reason=f"Session conflict: {available_by_provider}",
            selection_reason=f"Highest-priority provider selected ({selected_provider}).",
            pv_records=pv_records,
            conflicts=conflicts,
            ts=ts,
        )

    count = len(available_by_provider)
    confidence = CONFIDENCE_HIGH if count >= 2 else CONFIDENCE_LOW
    reason = (
        f"{count} provider{'s' if count > 1 else ''} report session={selected_session}."
    )
    return _build_record(
        field_id="earnings_session",
        selected_value=selected_session,
        selected_provider=selected_provider,
        source_type=SOURCE_TYPE_PROVIDER,
        confidence_level=confidence,
        confidence_reason=reason,
        selection_reason=_selection_reason(selected_provider, available_by_provider),
        pv_records=pv_records,
        conflicts=[],
        ts=ts,
    )


def build_earnings_field_provenance(
    event: dict[str, Any],
    provider_results: dict[str, dict[str, Any]] | None = None,
    observed_at: str | None = None,
) -> dict[str, FieldProvenanceRecord]:
    """Return {field_id: FieldProvenanceRecord} for earnings_date and earnings_session.

    Suitable for attaching to strategy rows as provenance_refs.
    """
    pr = provider_results or {}
    ts = observed_at or _utcnow()

    # Merge event-level data into per-provider dict if provider_results is sparse
    if not pr and event:
        sources = list(event.get("date_sources") or event.get("sources_seen") or [])
        for src in sources:
            pr.setdefault(src, {
                "earnings_date": event.get("earnings_date"),
                "session_label": event.get("session_label") or event.get("earnings_time"),
            })

    return {
        "earnings_date": select_earnings_provenance(pr, ts),
        "earnings_session": select_earnings_session_provenance(pr, ts),
    }


def row_confidence_summary(
    provenance_refs: dict[str, FieldProvenanceRecord | dict[str, Any]],
) -> dict[str, Any]:
    """Derive data_confidence, conflict_count, has_data_conflict from provenance_refs."""
    if not provenance_refs:
        return {
            "data_confidence": CONFIDENCE_UNKNOWN,
            "conflict_count": 0,
            "has_data_conflict": False,
            "data_confidence_summary": {},
        }

    levels = []
    conflicts = 0
    summary: dict[str, Any] = {}

    for fid, rec in provenance_refs.items():
        if isinstance(rec, dict):
            level = str(rec.get("confidence_level") or CONFIDENCE_UNKNOWN)
            has_c = bool(rec.get("has_conflict") or level == CONFIDENCE_CONFLICT)
        elif isinstance(rec, FieldProvenanceRecord):
            level = rec.confidence_level
            has_c = rec.has_conflict
        else:
            level = CONFIDENCE_UNKNOWN
            has_c = False
        levels.append(level)
        if has_c:
            conflicts += 1
        summary[fid] = level

    # Roll up: worst wins
    def _rank(lvl: str) -> int:
        return {CONFIDENCE_CONFLICT: 0, CONFIDENCE_UNKNOWN: 1, CONFIDENCE_LOW: 2,
                CONFIDENCE_MEDIUM: 3, CONFIDENCE_HIGH: 4}.get(lvl, 1)

    overall = min(levels, key=_rank) if levels else CONFIDENCE_UNKNOWN
    return {
        "data_confidence": overall,
        "conflict_count": conflicts,
        "has_data_conflict": conflicts > 0,
        "data_confidence_summary": summary,
    }


# ─── Private helpers ───────────────────────────────────────────────────────────

def _pick_by_priority(
    available_by_provider: dict[str, Any],
) -> tuple[str, Any]:
    """Return (provider, value) for the highest-priority provider."""
    for p in EARNINGS_PROVIDER_PRIORITY:
        if p in available_by_provider:
            return p, available_by_provider[p]
    # Fallback: first alphabetically
    p = sorted(available_by_provider.keys())[0]
    return p, available_by_provider[p]


def _selection_reason(selected: str, available: dict[str, Any]) -> str:
    others = [p for p in EARNINGS_PROVIDER_PRIORITY if p in available and p != selected]
    if others:
        return f"{selected} selected (priority); also available: {', '.join(others)}."
    return f"{selected} selected (only available provider)."


def _build_record(
    *,
    field_id: str,
    selected_value: Any,
    selected_provider: str,
    source_type: str,
    confidence_level: str,
    confidence_reason: str,
    selection_reason: str,
    pv_records: list[ProviderValueRecord],
    conflicts: list[dict[str, Any]],
    ts: str,
) -> FieldProvenanceRecord:
    # Mark selected provider in pv_records
    for pv in pv_records:
        if pv.provider == selected_provider and pv.status == STATUS_AVAILABLE:
            pv.is_selected = True
    return FieldProvenanceRecord(
        field_id=field_id,
        selected_value=selected_value,
        selected_provider=selected_provider,
        selected_source_type=source_type,
        selected_at=ts,
        observed_at=ts,
        freshness_timestamp=ts,
        confidence_level=confidence_level,
        confidence_reason=confidence_reason,
        selection_reason=selection_reason,
        is_calculated=False,
        is_approximation=False,
        provider_values=pv_records,
        conflicts=conflicts,
        schema_version=PATCH32A_SCHEMA_VERSION,
    )


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
