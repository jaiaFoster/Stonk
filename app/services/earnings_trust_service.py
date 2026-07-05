"""Read-only earnings-date trust summary from latest cached run."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app import config
from app.services.report_snapshot_service import ReportSnapshotRepository


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
            "provider_errors": [],
            "provider_calls_triggered": False,
        }
    summary = repo.load_summary(snapshot, full=False)
    report = (summary.get("report_data") or {}) if isinstance(summary, dict) else {}
    tradier = (report.get("tradier_snapshot") or {}) if isinstance(report, dict) else {}
    provider_status = _provider_status(snapshot, tradier)
    rows = _earnings_rows(tradier)
    alpha_sources = sum(1 for row in rows if "alphavantage" in ",".join(str(x).lower() for x in row.get("date_sources", [])))
    rows_scored = []
    for row in rows:
        sources = list(row.get("date_sources") or [])
        confidence = str(row.get("date_confidence") or row.get("earnings_date_confidence") or "unknown").lower()
        conflict = bool(row.get("date_conflict"))
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
            "events_returned": sum(1 for row in rows_scored if "finnhub" in ",".join(str(x).lower() for x in row.get("date_sources", []))),
            "last_error": _provider_last_error(provider_status, "finnhub"),
            "last_fetch_status": _provider_fetch_status(provider_status, "finnhub", sum(1 for row in rows_scored if "finnhub" in ",".join(str(x).lower() for x in row.get("date_sources", [])))),
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
    return {
        "ticker": row.get("ticker"),
        "earnings_date": row.get("earnings_date") or row.get("date"),
        "earnings_time": row.get("earnings_time") or row.get("session_label"),
        "date_confidence": row.get("date_confidence") or row.get("earnings_date_confidence"),
        "date_conflict": bool(row.get("date_conflict")),
        "date_sources": list(row.get("date_sources") or row.get("sources_seen") or []),
        "verdict": row.get("verdict"),
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
