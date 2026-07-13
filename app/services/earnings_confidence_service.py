"""
Sprint 28 — Epic B: Earnings Confidence Service

Provides a structured, per-provider confidence report for any earnings event.
This is designed to be displayed in the UI ("Data Details" panel) and included
in API responses so users know exactly what each data source reported and why
ASA made the decision it did.

Design principles
-----------------
- Every earnings event gets a confidence assessment, even if it's "no_data".
- Provider details are always surfaced — never hidden.
- Conflicts are visible with a human-readable explanation.
- This service is purely read-only: no provider calls, no storage writes.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.data_provenance import (
    CONFIDENCE_CONFIRMED,
    CONFIDENCE_DISPUTED,
    CONFIDENCE_NO_DATA,
    CONFIDENCE_SINGLE_SOURCE,
    PROVENANCE_SCHEMA_VERSION,
    EarningsProvenance,
)
from app.services.data_provenance_service import build_earnings_provenance

# Public confidence tier labels for UI display
CONFIDENCE_LABELS = {
    CONFIDENCE_CONFIRMED: "Confirmed — multiple sources agree",
    CONFIDENCE_SINGLE_SOURCE: "Single source — lower confidence",
    CONFIDENCE_DISPUTED: "Conflict — sources disagree",
    CONFIDENCE_NO_DATA: "No data available",
    "estimated": "Estimated — research only",
}

CONFIDENCE_UI_CLASS = {
    CONFIDENCE_CONFIRMED: "success",
    CONFIDENCE_SINGLE_SOURCE: "warning",
    CONFIDENCE_DISPUTED: "danger",
    CONFIDENCE_NO_DATA: "muted",
    "estimated": "info",
}

PROVIDER_DISPLAY_NAMES = {
    "finnhub": "Finnhub",
    "alpha_vantage": "Alpha Vantage",
    "alphavantage": "Alpha Vantage",
    "av": "Alpha Vantage",
    "tradier": "Tradier",
    "robinhood": "Robinhood",
    "calculated": "ASA (Calculated)",
    "cache": "Cached",
    "unknown": "Unknown",
}


def build_earnings_confidence_report(
    earnings_event: dict[str, Any] | None,
    provider_results: dict[str, dict[str, Any]] | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Build a structured confidence report for a single earnings event.

    Returned dict is suitable for API response and UI "Data Details" panel.
    """
    event = earnings_event or {}
    ts = retrieved_at or _utcnow()

    prov = build_earnings_provenance(event, provider_results, ts)

    date_conf = prov.date_provenance.confidence
    session_conf = prov.session_provenance.confidence

    # Provider detail rows for UI
    provider_rows = _build_provider_rows(event, prov)

    # Actionability gate
    conflict = prov.date_provenance.conflict_detected
    trade_allowed = not conflict and date_conf in {CONFIDENCE_CONFIRMED, CONFIDENCE_SINGLE_SOURCE}

    return {
        "earnings_date": event.get("earnings_date") or event.get("date"),
        "earnings_session": event.get("session_label") or event.get("earnings_time"),
        "date_confidence": date_conf,
        "date_confidence_label": CONFIDENCE_LABELS.get(date_conf, date_conf),
        "date_confidence_ui_class": CONFIDENCE_UI_CLASS.get(date_conf, "muted"),
        "session_confidence": session_conf,
        "session_confidence_label": CONFIDENCE_LABELS.get(session_conf, session_conf),
        "sources_checked": prov.sources_checked,
        "sources_returned_data": prov.sources_returned_data,
        "sources_failed": prov.sources_failed,
        "provider_count": len(prov.sources_returned_data),
        "conflict_detected": conflict,
        "conflict_summary": prov.conflict_summary,
        "date_agreement": prov.date_agreement,
        "session_agreement": prov.session_agreement,
        "trade_allowed": trade_allowed,
        "provider_rows": provider_rows,
        "retrieved_at": ts,
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def build_bulk_earnings_confidence(
    earnings_map: dict[str, dict[str, Any]],
    retrieved_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Build confidence reports for all tickers in an earnings map."""
    ts = retrieved_at or _utcnow()
    return {
        ticker: build_earnings_confidence_report(event, retrieved_at=ts)
        for ticker, event in (earnings_map or {}).items()
    }


def enrich_earnings_event_with_confidence(
    earnings_event: dict[str, Any],
    provider_results: dict[str, dict[str, Any]] | None = None,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    """Return *earnings_event* enriched with a `_confidence` sub-object."""
    event = dict(earnings_event or {})
    event["_confidence"] = build_earnings_confidence_report(event, provider_results, retrieved_at)
    return event


def confidence_gate_passed(report: dict[str, Any]) -> bool:
    """Return True when the confidence report indicates trade is allowed."""
    return bool((report or {}).get("trade_allowed"))


def public_confidence_label(report: dict[str, Any]) -> str:
    """One-line label for display in screener/dashboard tables."""
    r = report or {}
    if r.get("conflict_detected"):
        return "Conflict — do not trade"
    date_conf = r.get("date_confidence") or CONFIDENCE_NO_DATA
    sources = r.get("sources_returned_data") or []
    if date_conf == CONFIDENCE_CONFIRMED and len(sources) >= 2:
        names = " + ".join(PROVIDER_DISPLAY_NAMES.get(s, s.title()) for s in sources[:3])
        return f"{names} — confirmed"
    if date_conf == CONFIDENCE_SINGLE_SOURCE and len(sources) == 1:
        name = PROVIDER_DISPLAY_NAMES.get(sources[0], sources[0].title())
        return f"{name} only — single-source warning"
    if date_conf == CONFIDENCE_NO_DATA:
        return "No earnings data"
    return CONFIDENCE_LABELS.get(date_conf, date_conf)


def _build_provider_rows(
    event: dict[str, Any],
    prov: EarningsProvenance,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for src, detail in (prov.provider_detail or {}).items():
        display_name = PROVIDER_DISPLAY_NAMES.get(src, src.title())
        rows.append({
            "provider": src,
            "provider_display": display_name,
            "date_reported": detail.get("date"),
            "session_reported": detail.get("session"),
            "timestamp_confirmed": bool(detail.get("is_confirmed")),
            "eps_estimate": detail.get("eps_estimate"),
            "retrieved_at": detail.get("retrieved_at"),
        })
    # Also surface any that failed
    for err in prov.sources_failed:
        src = str(err).split(":")[0].strip()
        if src and not any(r["provider"] == src for r in rows):
            rows.append({
                "provider": src,
                "provider_display": PROVIDER_DISPLAY_NAMES.get(src, src.title()),
                "date_reported": None,
                "session_reported": None,
                "timestamp_confirmed": False,
                "eps_estimate": None,
                "retrieved_at": None,
                "error": err,
            })
    return rows


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
