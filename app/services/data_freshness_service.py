"""Provider-free freshness and run-quality labels from stored report metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import config
from app.services.degraded_reason_service import classify_degraded_reason


def build_data_freshness_summary(
    snapshot: dict[str, Any] | None,
    summary: dict[str, Any] | None = None,
    manifest: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    snapshot = snapshot or {}
    summary = summary or {}
    manifest = manifest or {}
    current = now or datetime.now(timezone.utc)
    generated_at = snapshot.get("completed_at") or snapshot.get("created_at")
    age_seconds = _age_seconds(generated_at, current)
    freshness_state = _freshness_state(age_seconds)
    report = summary.get("report_data", {}) or {}
    tradier = report.get("tradier_snapshot", {}) or {}
    pipeline = tradier.get("_pipeline_status", {}) or summary.get("pipeline_status", {}) or {}
    provider_status = tradier.get("_provider_status", {}) or {}
    robinhood = provider_status.get("robinhood", {}) or {}
    report_quality = str(
        summary.get("report_quality")
        or pipeline.get("report_quality")
        or ("SUCCESS_COMPLETE" if snapshot.get("status") == "complete" else snapshot.get("status") or "UNKNOWN")
    ).upper()
    latest_quality = str(manifest.get("report_quality") or manifest.get("status") or report_quality).upper()
    canonical_run_id = snapshot.get("run_id")
    latest_run_id = manifest.get("run_id") or canonical_run_id
    latest_status = manifest.get("status") or snapshot.get("status")
    latest_is_degraded = latest_quality in {"SUCCESS_DEGRADED", "DEGRADED", "FAILED", "ERROR"}
    using_latest_run = bool(latest_run_id and canonical_run_id and str(latest_run_id) == str(canonical_run_id) and not latest_is_degraded)
    dashboard_data_source = "latest_complete_run" if using_latest_run else (
        "canonical_complete_snapshot_preserved" if latest_is_degraded else "canonical_complete_snapshot"
    )
    broker_state, broker_at = _broker_state(report.get("positions", []) or [], robinhood, generated_at)
    if manifest.get("has_broker_data") is False:
        broker_state, broker_at = "UNAVAILABLE", None
    has_market = manifest.get("has_market_data")
    has_options = manifest.get("has_options_data")
    degraded_reason = classify_degraded_reason(manifest, pipeline, provider_status)
    warnings: list[str] = []
    quality_label = report_quality
    if freshness_state == "STALE":
        quality_label = "STALE_CACHED_REPORT"
        warnings.append("Cached report exceeds the configured stale-age threshold.")
    if latest_is_degraded:
        quality_label = "LATEST_RUN_DEGRADED"
        warnings.append("Latest attempted run was degraded or failed; showing the latest usable complete report.")
    if broker_state == "STALE_FALLBACK":
        warnings.append("Broker positions use a stale fallback snapshot.")
    elif broker_state == "UNAVAILABLE":
        warnings.append("Broker position data is unavailable.")
    if has_market is False:
        warnings.append("Latest run manifest reports missing market data.")
    if has_options is False:
        warnings.append("Latest run manifest reports missing options data.")
    return {
        "quality_label": quality_label,
        "report_quality": report_quality,
        "latest_run_quality": latest_quality,
        "latest_run_report_quality": latest_quality,
        "freshness_state": freshness_state,
        "generated_at": generated_at,
        "report_age_seconds": age_seconds,
        "canonical_run_id": canonical_run_id,
        "canonical_snapshot_run_id": canonical_run_id,
        "canonical_snapshot_status": snapshot.get("status"),
        "canonical_snapshot_quality": report_quality,
        "latest_run_id": latest_run_id,
        "latest_run_status": latest_status,
        "latest_run_is_degraded": latest_is_degraded,
        "latest_run_degraded": latest_is_degraded,
        "latest_run_degraded_reason": "unknown" if degraded_reason["degraded_reason_code"] == "UNKNOWN" else degraded_reason["degraded_reason_label"],
        **degraded_reason,
        "dashboard_data_source": dashboard_data_source,
        "dashboard_using_latest_run": using_latest_run,
        "dashboard_using_canonical_snapshot": not using_latest_run,
        "canonical_snapshot_preserved": bool(latest_is_degraded and latest_run_id and canonical_run_id and str(latest_run_id) != str(canonical_run_id)),
        "broker_data": _fact(broker_state, broker_at, current, "broker_snapshot" if broker_at != generated_at else "report_snapshot_proxy"),
        "market_data": _fact("AVAILABLE" if has_market is not False else "UNAVAILABLE", generated_at, current, "report_snapshot_proxy"),
        "options_data": _fact("AVAILABLE" if has_options is not False else "UNAVAILABLE", generated_at, current, "report_snapshot_proxy"),
        "earnings_data": _fact("UNKNOWN", None, current, "timestamp_unavailable"),
        "warnings": warnings,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def _broker_state(positions: list[dict[str, Any]], robinhood: dict[str, Any], generated_at: Any) -> tuple[str, Any]:
    stale_times = [
        row.get("broker_snapshot_fetched_at")
        for row in positions
        if row.get("broker_data_state") == "STALE_FALLBACK" and row.get("broker_snapshot_fetched_at")
    ]
    if stale_times or robinhood.get("stale_fallback"):
        return "STALE_FALLBACK", min(stale_times) if stale_times else generated_at
    status = str(robinhood.get("status") or "").lower()
    if status in {"positions_failed", "auth_failed", "auth_required", "rate_limited"} or robinhood.get("positions_available") is False:
        return "UNAVAILABLE", None
    return "CURRENT", generated_at


def _fact(state: str, timestamp: Any, now: datetime, source: str) -> dict[str, Any]:
    return {"state": state, "as_of": timestamp, "age_seconds": _age_seconds(timestamp, now), "source": source}


def _freshness_state(age_seconds: int | None) -> str:
    if age_seconds is None:
        return "UNKNOWN"
    if age_seconds >= int(getattr(config, "REPORT_FRESHNESS_STALE_SECONDS", 86400) or 86400):
        return "STALE"
    if age_seconds >= int(getattr(config, "REPORT_FRESHNESS_WARN_SECONDS", 21600) or 21600):
        return "AGING"
    return "FRESH"


def _age_seconds(value: Any, now: datetime) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0, int((now - parsed.astimezone(timezone.utc)).total_seconds()))
    except (TypeError, ValueError):
        return None
