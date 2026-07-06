"""Read-only earnings-date trust summary from latest cached run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import config
from app.services.report_snapshot_service import ReportSnapshotRepository

# TKT-030C (future): Add third earnings-date source fallback.
# Future provider candidates: Financial Modeling Prep, Polygon, Nasdaq earnings
# calendar, company IR parser, manual/reference cache (admin override only).
# Future rule: need 2 agreeing sources; try up to 3 providers; stop when 2
# agree, conflict appears, or all providers fail.

TRUST_PUBLIC_LABELS = {
    "multi_source_confirmed": "Multi-source confirmed",
    "single_source_verify": "Single source — lower confidence",
    "conflict_do_not_trade": "Conflict — do not trade",
    "unknown_research_only": "Unknown — research only",
    "not_applicable": "N/A",
}

# Maps known lowercase provider name fragments to display names.
_PROVIDER_DISPLAY = {
    "finnhub": "Finnhub",
    "alphavantage": "Alpha Vantage",
    "alpha_vantage": "Alpha Vantage",
}


def normalize_earnings_trust(payload: dict[str, Any] | None, *, applicable: bool = True) -> dict[str, Any]:
    """Normalize provider/strategy earnings metadata into one safety contract."""
    root = payload if isinstance(payload, dict) else {}
    nested = root.get("event") if isinstance(root.get("event"), dict) else {}
    earnings = root.get("earnings") if isinstance(root.get("earnings"), dict) else {}
    source = {**nested, **earnings, **root}
    date = source.get("earnings_date") or source.get("date")
    time = source.get("earnings_time") or source.get("session_label")
    sources = source.get("earnings_sources_seen") or source.get("sources_seen") or source.get("date_sources") or []
    if not sources and source.get("source"):
        sources = [source.get("source")]
    if isinstance(sources, str):
        sources = [sources]
    sources = list(dict.fromkeys(str(item) for item in sources if item))
    conflict = bool(source.get("earnings_source_conflict") or source.get("date_conflict"))
    details = list(source.get("earnings_conflict_details") or [])
    if not applicable:
        label, reason = "not_applicable", "Earnings date trust is not applicable to this row."
    elif conflict:
        label, reason = "conflict_do_not_trade", "Conflicting earnings dates were reported. Do not use this row for calendar entry."
    elif not date:
        label, reason = "unknown_research_only", "Earnings date is unavailable. Research only."
    elif len(sources) >= 2:
        label, reason = "multi_source_confirmed", f"Earnings date confirmed by {len(sources)} sources."
    elif len(sources) == 1:
        label, reason = "single_source_verify", "ASA has only one source for this date. Treat as lower confidence."
    else:
        label, reason = "unknown_research_only", "Earnings date has no attributable source. Research only."
    allowed = label == "multi_source_confirmed"
    if label == "single_source_verify" and not config.EARNINGS_TRUST_REQUIRE_MULTI_SOURCE_FOR_CALENDAR_PASS:
        allowed = True
    if label == "conflict_do_not_trade" and config.EARNINGS_TRUST_CONFLICT_CAN_PASS:
        allowed = True
    if label == "unknown_research_only" and config.EARNINGS_TRUST_UNKNOWN_CAN_PASS:
        allowed = True
    confidence = source.get("earnings_date_confidence") or source.get("date_confidence")
    if not confidence:
        confidence = "confirmed" if label == "multi_source_confirmed" else ("single_source" if label == "single_source_verify" else "disputed" if conflict else "no_data")
    return {
        "earnings_date": date,
        "earnings_time": time,
        "earnings_date_confidence": confidence,
        "earnings_source_count": len(sources),
        "earnings_sources_seen": sources,
        "earnings_source_conflict": conflict,
        "earnings_conflict_details": details,
        "earnings_trust_label": label,
        "earnings_trust_reason": reason,
        "calendar_entry_allowed": bool(allowed),
        "provider_date_bleed_suspect": bool(source.get("provider_date_bleed_suspect")),
    }


def public_earnings_trust_label(payload: dict[str, Any] | None) -> str:
    trust = normalize_earnings_trust(payload)
    label = trust["earnings_trust_label"]
    sources = trust.get("earnings_sources_seen") or []
    if label == "multi_source_confirmed" and len(sources) >= 2:
        display = " + ".join(
            _PROVIDER_DISPLAY.get(str(s).lower(), str(s).title()) for s in sources[:3]
        )
        return f"{display} — confirmed"
    if label == "single_source_verify" and len(sources) == 1:
        display = _PROVIDER_DISPLAY.get(str(sources[0]).lower(), str(sources[0]).title())
        return f"{display} only — single-source warning"
    return TRUST_PUBLIC_LABELS[label]


def earnings_trust_caveats(rows: list[dict[str, Any]]) -> list[str]:
    labels = [normalize_earnings_trust(row) for row in rows if isinstance(row, dict)]
    caveats = []
    conflicts = [row for row in labels if row["earnings_trust_label"] == "conflict_do_not_trade"]
    singles = [row for row in labels if row["earnings_trust_label"] == "single_source_verify"]
    unknowns = [row for row in labels if row["earnings_trust_label"] == "unknown_research_only"]
    if conflicts:
        tickers = ", ".join(str(row.get("ticker") or "candidate") for row in rows if normalize_earnings_trust(row)["earnings_trust_label"] == "conflict_do_not_trade")
        caveats.append(f"Blocked: earnings date conflict detected for {tickers}. Do not use these rows for calendar entry.")
    if singles:
        caveats.append("Caution: single-source earnings dates are lower confidence. ASA has only one source for these dates.")
    if unknowns:
        caveats.append("Research only: earnings date confidence unavailable for one or more rows.")
    return caveats


def build_earnings_trust_summary() -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()
    repo = ReportSnapshotRepository(log_print=lambda _m: None)
    snapshot = repo.latest_success(include_full=False)
    if not snapshot:
        return {
            "status": "no_data",
            "checked_at": checked_at,
            "run_id": None,
            "generated_at": None,
            "provider_order": list(config.EARNINGS_PROVIDER_ORDER),
            "merge_provider_events": bool(config.EARNINGS_MERGE_PROVIDER_EVENTS),
            "alpha_vantage_configured": bool(config.ALPHA_VANTAGE_API_KEY),
            "finnhub_configured": bool(config.FINNHUB_API_KEY),
            "alpha_vantage_events_returned": 0,
            "finnhub_events_returned": 0,
            "total_earnings_events": 0,
            "multi_source_count": 0,
            "single_source_count": 0,
            "conflict_count": 0,
            "unknown_time_count": 0,
            "high_confidence_count": 0,
            "medium_confidence_count": 0,
            "low_confidence_count": 0,
            "top_single_source_rows": [],
            "top_conflict_rows": [],
            "calendar_candidates_blocked_by_date_trust": 0,
            "single_source_calendar_candidates": 0,
            "wrong_date_suspects": [],
            "provider_date_bleed_suspects": [],
            "provider_errors": [],
            "provider_calls_triggered": False,
        }
    summary = repo.load_summary(snapshot, full=False)
    report = (summary.get("report_data") or {}) if isinstance(summary, dict) else {}
    tradier = (report.get("tradier_snapshot") or {}) if isinstance(report, dict) else {}
    provider_status = _provider_status(snapshot, tradier)
    rows = _earnings_rows(tradier)
    alpha_sources = sum(1 for row in rows if "alphavantage" in ",".join(str(x).lower() for x in normalize_earnings_trust(row)["earnings_sources_seen"]))
    rows_scored = []
    for row in rows:
        trust = normalize_earnings_trust(row)
        row.update(trust)
        sources = list(row.get("earnings_sources_seen") or row.get("date_sources") or [])
        confidence = str(row.get("date_confidence") or row.get("earnings_date_confidence") or "unknown").lower()
        conflict = bool(row.get("earnings_source_conflict") or row.get("date_conflict"))
        time_unknown = str(row.get("earnings_time") or row.get("session_label") or "unknown").lower() in {"", "unknown", "tbd", "none"}
        row["_source_count"] = len(sources)
        row["_confidence"] = confidence
        row["_conflict"] = conflict
        row["_time_unknown"] = time_unknown
        rows_scored.append(row)
    return {
        "status": "ok",
        "checked_at": checked_at,
        "run_id": snapshot.get("run_id"),
        "generated_at": snapshot.get("completed_at"),
        "provider_order": list(config.EARNINGS_PROVIDER_ORDER),
        "merge_provider_events": bool(config.EARNINGS_MERGE_PROVIDER_EVENTS),
        "alpha_vantage_configured": bool(config.ALPHA_VANTAGE_API_KEY),
        "finnhub_configured": bool(config.FINNHUB_API_KEY),
        "alpha_vantage_events_returned": alpha_sources,
        "finnhub_events_returned": sum(1 for row in rows_scored if "finnhub" in ",".join(str(x).lower() for x in row.get("earnings_sources_seen", []))),
        "total_earnings_events": len(rows_scored),
        "multi_source_count": sum(1 for row in rows_scored if row["_source_count"] >= 2),
        "single_source_count": sum(1 for row in rows_scored if row["_source_count"] == 1),
        "conflict_count": sum(1 for row in rows_scored if row["_conflict"]),
        "unknown_time_count": sum(1 for row in rows_scored if row["_time_unknown"]),
        "high_confidence_count": sum(1 for row in rows_scored if row["_confidence"] == "confirmed"),
        "medium_confidence_count": sum(1 for row in rows_scored if row["_confidence"] in {"single_source", "medium"}),
        "low_confidence_count": sum(1 for row in rows_scored if row["_confidence"] in {"unknown", "disputed", "low", "no_data"}),
        "top_single_source_rows": [_trust_row(row) for row in sorted([r for r in rows_scored if r["_source_count"] == 1], key=lambda r: (_score_row(r), r.get("ticker") or ""))[:10]],
        "top_conflict_rows": [_trust_row(row) for row in sorted([r for r in rows_scored if r["_conflict"]], key=lambda r: (_score_row(r), r.get("ticker") or ""))[:10]],
        "calendar_candidates_blocked_by_date_trust": sum(1 for row in rows_scored if row.get("calendar_entry_allowed") is False),
        "single_source_calendar_candidates": sum(1 for row in rows_scored if row.get("earnings_trust_label") == "single_source_verify"),
        "wrong_date_suspects": [_trust_row(row) for row in rows_scored if row.get("earnings_source_conflict")][:10],
        "provider_date_bleed_suspects": [_trust_row(row) for row in rows_scored if row.get("provider_date_bleed_suspect")][:10],
        "provider_errors": _provider_errors(provider_status),
        "alpha_vantage": {
            "provider_name": "alpha_vantage",
            "configured": bool(config.ALPHA_VANTAGE_API_KEY),
            "events_returned": alpha_sources,
            "last_error": _provider_last_error(provider_status, "alpha_vantage"),
            "last_fetch_status": _provider_fetch_status(provider_status, "alpha_vantage", alpha_sources),
        },
        "finnhub": {
            "provider_name": "finnhub",
            "configured": bool(config.FINNHUB_API_KEY),
            "events_returned": sum(1 for row in rows_scored if "finnhub" in ",".join(str(x).lower() for x in row.get("earnings_sources_seen", []))),
            "last_error": _provider_last_error(provider_status, "finnhub"),
            "last_fetch_status": _provider_fetch_status(provider_status, "finnhub", sum(1 for row in rows_scored if "finnhub" in ",".join(str(x).lower() for x in row.get("earnings_sources_seen", [])))),
        },
        "provider_calls_triggered": False,
    }


def _provider_status(snapshot: dict[str, Any], tradier: dict[str, Any]) -> dict[str, Any]:
    try:
        return json.loads(snapshot.get("provider_status_json") or "{}") or {}
    except Exception:
        return (tradier.get("_provider_status") or {}) if isinstance(tradier, dict) else {}


def _earnings_rows(tradier: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    quality = tradier.get("_earnings_discovery_quality") or {}
    for item in list((quality.get("items") or [])):
        if isinstance(item, dict):
            rows.append(item)
    calendar = tradier.get("_earnings_calendar_strategy") or {}
    for item in list((calendar.get("items") or [])):
        if isinstance(item, dict):
            rows.append(item)
    ff = tradier.get("_forward_factor_strategy") or {}
    for item in list((ff.get("items") or ff.get("rows") or [])):
        if isinstance(item, dict) and (item.get("earnings_date") or item.get("earnings_confidence") or item.get("earnings_contaminated") is not None):
            rows.append(item)
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("ticker") or "").upper(), str(row.get("earnings_date") or row.get("date") or ""))
        if key not in dedup:
            dedup[key] = row
    return list(dedup.values())


def _score_row(row: dict[str, Any]) -> tuple[int, int]:
    confidence = str(row.get("date_confidence") or row.get("earnings_date_confidence") or "").lower()
    sources = len(row.get("date_sources") or [])
    return (0 if confidence == "disputed" or row.get("date_conflict") else 1, sources)


def _trust_row(row: dict[str, Any]) -> dict[str, Any]:
    trust = normalize_earnings_trust(row)
    return {
        "ticker": row.get("ticker"),
        "earnings_date": row.get("earnings_date") or row.get("date"),
        "earnings_time": row.get("earnings_time") or row.get("session_label"),
        "date_confidence": row.get("date_confidence") or row.get("earnings_date_confidence"),
        "date_conflict": bool(row.get("date_conflict")),
        "date_sources": list(row.get("date_sources") or row.get("sources_seen") or []),
        "verdict": row.get("verdict"),
        **trust,
    }


def _provider_last_error(provider_status: dict[str, Any], key: str) -> str | None:
    block = (provider_status.get(key) or {}) if isinstance(provider_status, dict) else {}
    error = block.get("error") or block.get("last_error")
    return str(error) if error else None


def _provider_fetch_status(provider_status: dict[str, Any], key: str, events_returned: int) -> str:
    block = (provider_status.get(key) or {}) if isinstance(provider_status, dict) else {}
    if block.get("error") or block.get("last_error"):
        return "error"
    if events_returned > 0:
        return "ok"
    if key == "alpha_vantage" and bool(config.ALPHA_VANTAGE_API_KEY):
        return "configured_zero_events"
    if key == "finnhub" and bool(config.FINNHUB_API_KEY):
        return "configured_zero_events"
    return "not_configured"


def _provider_errors(provider_status: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key in ("finnhub", "alpha_vantage", "alphavantage"):
        block = (provider_status.get(key) or {}) if isinstance(provider_status, dict) else {}
        error = block.get("error") or block.get("last_error")
        if error:
            rows.append({"provider_name": key, "last_error": str(error)})
    return rows
