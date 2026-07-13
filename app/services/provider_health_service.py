"""
Sprint 28 — Epic M: Provider Health Service

Tracks provider availability, partial outages, degradation, and auth failures
across ASA's data pipeline. Health events are derived from stored run manifests
and in-memory run context — this service never calls providers directly.

Health states
-------------
- HEALTHY: Provider returned data in the last run.
- DEGRADED: Provider returned partial data or a non-critical error.
- UNAVAILABLE: Provider returned no data (auth failure, rate limit, outage).
- UNKNOWN: No data about this provider in the latest run.
- STALE: Provider last reported HEALTHY but the last successful check is old.

Provider slugs
--------------
- tradier
- robinhood
- finnhub
- alpha_vantage
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.data_provenance import PROVENANCE_SCHEMA_VERSION

HEALTH_HEALTHY = "healthy"
HEALTH_DEGRADED = "degraded"
HEALTH_UNAVAILABLE = "unavailable"
HEALTH_UNKNOWN = "unknown"
HEALTH_STALE = "stale"

KNOWN_PROVIDERS = ("tradier", "robinhood", "finnhub", "alpha_vantage")

# Thresholds
STALE_HEALTH_SECONDS = 86400      # 24h without a check = stale
DEGRADED_ERROR_RATE_PCT = 0.20    # >20% error rate = degraded


class ProviderHealthRecord:
    """In-memory health record for one provider."""

    __slots__ = (
        "provider",
        "status",
        "last_checked_at",
        "last_success_at",
        "error_message",
        "error_code",
        "partial_failure",
        "data_types_available",
        "data_types_failed",
        "fetch_count",
        "error_count",
        "notes",
        "schema_version",
    )

    def __init__(self, provider: str):
        self.provider = provider
        self.status = HEALTH_UNKNOWN
        self.last_checked_at: str | None = None
        self.last_success_at: str | None = None
        self.error_message: str | None = None
        self.error_code: str | None = None
        self.partial_failure: bool = False
        self.data_types_available: list[str] = []
        self.data_types_failed: list[str] = []
        self.fetch_count: int = 0
        self.error_count: int = 0
        self.notes: list[str] = []
        self.schema_version = PROVENANCE_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "last_checked_at": self.last_checked_at,
            "last_success_at": self.last_success_at,
            "error_message": self.error_message,
            "error_code": self.error_code,
            "partial_failure": self.partial_failure,
            "data_types_available": self.data_types_available,
            "data_types_failed": self.data_types_failed,
            "fetch_count": self.fetch_count,
            "error_count": self.error_count,
            "error_rate": self.error_count / self.fetch_count if self.fetch_count else 0.0,
            "notes": self.notes,
            "schema_version": self.schema_version,
        }


def build_provider_health_from_manifest(
    manifest: dict[str, Any] | None,
) -> dict[str, ProviderHealthRecord]:
    """Derive provider health records from the latest run manifest."""
    m = manifest or {}
    pipeline = m.get("pipeline_status") or m.get("_pipeline_status") or {}
    provider_status = m.get("provider_status") or pipeline.get("_provider_status") or {}
    records: dict[str, ProviderHealthRecord] = {}

    for slug in KNOWN_PROVIDERS:
        rec = ProviderHealthRecord(slug)
        raw = provider_status.get(slug) or {}

        status_str = str(raw.get("status") or "").lower()
        if "auth_failed" in status_str or "auth_required" in status_str:
            rec.status = HEALTH_UNAVAILABLE
            rec.error_code = "auth_failed"
            rec.error_message = f"{slug} authentication failed."
        elif "rate_limited" in status_str or "429" in status_str:
            rec.status = HEALTH_UNAVAILABLE
            rec.error_code = "rate_limited"
            rec.error_message = f"{slug} rate-limited."
        elif raw.get("positions_available") is False or raw.get("data_available") is False:
            rec.status = HEALTH_UNAVAILABLE
            rec.error_message = f"{slug} data unavailable."
        elif raw.get("stale_fallback"):
            rec.status = HEALTH_DEGRADED
            rec.partial_failure = True
            rec.notes.append("Using stale fallback data.")
        elif status_str in {"ok", "success", "available", "positions_available"}:
            rec.status = HEALTH_HEALTHY
        elif status_str:
            rec.status = HEALTH_DEGRADED
            rec.error_message = f"Unexpected status: {status_str}"
        else:
            rec.status = HEALTH_UNKNOWN

        run_at = m.get("created_at") or m.get("completed_at")
        rec.last_checked_at = run_at
        if rec.status == HEALTH_HEALTHY:
            rec.last_success_at = run_at

        records[slug] = rec

    # Earnings providers (finnhub/alpha_vantage) — check manifest flags
    has_earnings = m.get("has_earnings_data")
    if has_earnings is False:
        for slug in ("finnhub", "alpha_vantage"):
            if slug in records and records[slug].status == HEALTH_UNKNOWN:
                records[slug].status = HEALTH_UNAVAILABLE
                records[slug].error_message = "Earnings data not available in last run."

    return records


def build_health_summary(records: dict[str, ProviderHealthRecord]) -> dict[str, Any]:
    """Build a health summary dict suitable for API/diagnostics response."""
    statuses = {slug: rec.to_dict() for slug, rec in (records or {}).items()}
    healthy = sum(1 for r in records.values() if r.status == HEALTH_HEALTHY)
    unavailable = sum(1 for r in records.values() if r.status == HEALTH_UNAVAILABLE)
    degraded = sum(1 for r in records.values() if r.status == HEALTH_DEGRADED)
    unknown = sum(1 for r in records.values() if r.status == HEALTH_UNKNOWN)

    overall = HEALTH_HEALTHY
    if unavailable > 0 and unavailable == len(records):
        overall = HEALTH_UNAVAILABLE
    elif unavailable > 0 or degraded > 0:
        overall = HEALTH_DEGRADED

    return {
        "overall_status": overall,
        "healthy_count": healthy,
        "degraded_count": degraded,
        "unavailable_count": unavailable,
        "unknown_count": unknown,
        "provider_count": len(records),
        "providers": statuses,
        "checked_at": _utcnow(),
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "provider_calls_triggered": False,
        "read_only": True,
    }


def classify_degraded_reason_from_health(
    records: dict[str, ProviderHealthRecord],
) -> str:
    """Return a single human-readable degraded reason string."""
    unavail = [slug for slug, r in records.items() if r.status == HEALTH_UNAVAILABLE]
    degraded = [slug for slug, r in records.items() if r.status == HEALTH_DEGRADED]

    if not unavail and not degraded:
        return "all_providers_healthy"
    parts: list[str] = []
    if unavail:
        parts.append(f"unavailable: {', '.join(unavail)}")
    if degraded:
        parts.append(f"degraded: {', '.join(degraded)}")
    return "; ".join(parts)


def provider_health_from_pipeline_status(
    pipeline_status: dict[str, Any] | None,
) -> dict[str, Any]:
    """Simplified health check derived directly from a pipeline_status dict."""
    ps = pipeline_status or {}
    records = build_provider_health_from_manifest({"pipeline_status": ps})
    return build_health_summary(records)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()
