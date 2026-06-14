"""Provider-free, redacted deploy diagnostics built from stored/in-memory state."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from app import config
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.redaction_service import redact
from app.services.run_manifest_repository import RunManifestRepository


def build_dev_status(
    jobs: dict[str, dict[str, Any]] | None = None,
    active_job_id: str | None = None,
    booted_at: str | None = None,
) -> dict[str, Any]:
    jobs = jobs or {}
    active = jobs.get(active_job_id or "", {}) if active_job_id else {}
    latest = RunManifestRepository().latest()
    return redact({
        "status": "ok",
        "app_booted": True,
        "booted_at": booted_at,
        "checked_at": _now(),
        "app_mode": config.APP_MODE,
        "git_commit": _git_commit(),
        "git_branch": _git_branch(),
        "deploy_label": os.environ.get("RAILWAY_DEPLOYMENT_ID"),
        "active_run": _job_summary(active_job_id, active) if active else None,
        "tracked_job_count": len(jobs),
        "latest_run": latest,
        "provider_calls_triggered": False,
    })


def build_latest_run_manifest() -> dict[str, Any]:
    return redact({
        "status": "ok",
        "checked_at": _now(),
        "run_manifest": RunManifestRepository().latest(),
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
    return redact({
        "status": "ok" if all(checks[key] for key in required) else "warning",
        "checked_at": _now(),
        "source_run_id": snapshot.get("source_run_id"),
        "checks": checks,
        "provider_calls_triggered": False,
        "trade_execution_enabled": False,
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


def _git_commit() -> str | None:
    return os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT")


def _git_branch() -> str | None:
    return os.environ.get("RAILWAY_GIT_BRANCH") or os.environ.get("GIT_BRANCH")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
