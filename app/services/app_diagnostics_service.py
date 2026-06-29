"""Provider-free, redacted deploy diagnostics built from stored/in-memory state."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app import config
from app.services.commit_identity_service import build_commit_identity
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.redaction_service import redact
from app.services.run_manifest_repository import RunManifestRepository


def build_dev_status(
    jobs: dict[str, dict[str, Any]] | None = None,
    active_job_id: str | None = None,
    booted_at: str | None = None,
    run_lock: dict[str, Any] | None = None,
) -> dict[str, Any]:
    jobs = jobs or {}
    active = jobs.get(active_job_id or "", {}) if active_job_id else {}
    latest = RunManifestRepository().latest()
    commit_identity = build_commit_identity(latest)
    return redact({
        "status": "ok",
        "app_booted": True,
        "booted_at": booted_at,
        "checked_at": _now(),
        "app_mode": config.APP_MODE,
        "git_commit": commit_identity["source_of_truth"],
        "git_branch": commit_identity["git_branch"],
        "deploy_label": commit_identity["deploy_label"],
        "commit_identity": commit_identity,
        "commit_identity_mismatch": commit_identity["commit_identity_mismatch"],
        "active_run": _job_summary(active_job_id, active) if active else None,
        "run_lock": run_lock or {},
        "tracked_job_count": len(jobs),
        "latest_run": latest,
        "provider_fetch_count": (latest or {}).get("provider_fetch_count", 0),
        "provider_calls_triggered": False,
    })


def build_latest_run_manifest() -> dict[str, Any]:
    manifest = RunManifestRepository().latest()
    return redact({
        "status": "ok",
        "checked_at": _now(),
        "run_manifest": manifest,
        "commit_identity": build_commit_identity(manifest),
        "provider_calls_triggered": False,
    })


def build_latest_profiles() -> dict[str, Any]:
    snapshot = build_developer_snapshot("latest")
    runtime = snapshot.get("runtime_profile")
    payload = snapshot.get("payload_size_profile")
    storage = snapshot.get("storage_profile")
    return redact({
        "status": "ok" if snapshot.get("source_run_id") else "unavailable",
        "checked_at": _now(),
        "source_run_id": snapshot.get("source_run_id"),
        "source_status": snapshot.get("source_status"),
        "runtime_profile": runtime,
        "payload_size_profile": payload,
        "storage_profile": storage,
        "data_freshness": snapshot.get("data_freshness"),
        "commit_identity": snapshot.get("commit_identity"),
        "report_snapshot_profile": snapshot.get("report_snapshot_profile"),
        "provider_payload_budget": snapshot.get("provider_payload_budget"),
        "slowest_runtime_phase": _slowest_phase(runtime),
        "largest_payload_section": _largest_section(payload),
        "provider_calls_triggered": False,
    })


def build_feature_health() -> dict[str, Any]:
    snapshot = build_developer_snapshot("summary")
    strategies = snapshot.get("strategy_summaries") or {}
    ff = strategies.get("forward_factor_calendar") or {}
    daily = snapshot.get("daily_opportunity") or {}
    actions = daily.get("actions", []) if isinstance(daily, dict) else []
    ff_daily_rows = [
        row for row in actions
        if isinstance(row, dict) and str(row.get("strategy_id") or row.get("source_strategy") or "").lower() == "forward_factor_calendar"
    ]
    checks = {
        "latest_report_available": bool(snapshot.get("source_run_id")),
        "latest_manifest_available": bool(snapshot.get("run_manifest")),
        "runtime_profile_available": bool(snapshot.get("runtime_profile")),
        "payload_profile_available": bool(snapshot.get("payload_size_profile")),
        "storage_profile_available": bool(snapshot.get("storage_profile")),
        "data_freshness_available": bool(snapshot.get("data_freshness")),
        "daily_opportunity_available": bool(daily),
        "forward_factor_visible": bool(ff),
        "forward_factor_dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
        "forward_factor_daily_opportunity_excluded": not ff_daily_rows,
        "read_only_diagnostics": True,
    }
    required = (
        "latest_report_available",
        "latest_manifest_available",
        "forward_factor_dry_run",
        "forward_factor_daily_opportunity_excluded",
        "read_only_diagnostics",
    )
    from app.db.telemetry import telemetry_summary
    return redact({
        "status": "ok" if all(checks[key] for key in required) else "warning",
        "checked_at": _now(),
        "source_run_id": snapshot.get("source_run_id"),
        "checks": checks,
        "commit_identity": snapshot.get("commit_identity"),
        "provider_calls_triggered": False,
        "trade_execution_enabled": False,
        "telemetry": telemetry_summary(),
    })


def _job_summary(job_id: str | None, job: dict[str, Any]) -> dict[str, Any]:
    result = job.get("result")
    log_tail = []
    if result:
        try:
            log_tail = list(result[5])[-10:]
        except Exception:
            log_tail = []
    return {
        "job_id": job_id,
        "status": job.get("status"),
        "message": job.get("message"),
        "mode": job.get("mode"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "heartbeat_at": job.get("heartbeat_at"),
        "timeout_reason": job.get("timeout_reason"),
        "failed_stage": job.get("failed_stage"),
        "retry_safe": bool(job.get("retry_safe")),
        "log_tail": log_tail,
    }


def _slowest_phase(profile: Any) -> dict[str, Any] | None:
    phases = (profile or {}).get("phases_ms", {}) if isinstance(profile, dict) else {}
    if not phases:
        return None
    key = max(phases, key=lambda item: phases.get(item) or 0)
    return {"phase": key, "duration_ms": phases[key]}


def _largest_section(profile: Any) -> dict[str, Any] | None:
    sections = (profile or {}).get("sections_bytes", {}) if isinstance(profile, dict) else {}
    if not sections:
        return None
    key = max(sections, key=lambda item: sections.get(item) or 0)
    return {"section": key, "bytes": sections[key]}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
