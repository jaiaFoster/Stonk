"""Run management API helpers — read-only status and manifest functions.

ASA Patch 30D.1 Lane 5 — run status and manifest query functions.
Routes live in main.py to share thread state (RUN_JOBS, RUN_LOCK, ACTIVE_JOB_ID).
POST /api/run/refresh is in main.py (requires RUN_TOKEN; triggers provider calls).
GET /api/run/status/<job_id> and GET /api/runs/latest call into this module.
"""
from __future__ import annotations

from typing import Any

_READ_ONLY_BASE: dict[str, Any] = {"provider_calls_triggered": False, "read_only": True}


def get_run_status(job_id: str, run_jobs: dict[str, Any]) -> dict[str, Any]:
    """Return compact status of a specific async job from RUN_JOBS."""
    if not job_id:
        return {**_READ_ONLY_BASE, "error": "job_id is required.", "status": "error"}
    if not isinstance(run_jobs, dict):
        return {**_READ_ONLY_BASE, "job_id": job_id, "status": "not_found", "error": "No job registry available."}
    job = run_jobs.get(str(job_id))
    if not job:
        return {**_READ_ONLY_BASE, "job_id": job_id, "status": "not_found", "error": "Job ID not found or expired."}
    return {
        **_READ_ONLY_BASE,
        "job_id": job_id,
        "status": job.get("status"),
        "message": job.get("message"),
        "mode": job.get("mode"),
        "created_at": job.get("created_at"),
        "result": _compact_result(job.get("result")),
    }


def get_latest_run() -> dict[str, Any]:
    """Return compact latest RunManifest from persistent storage."""
    try:
        from app.services.run_manifest_repository import RunManifestRepository
        manifest = RunManifestRepository().latest()
    except Exception as exc:
        return {**_READ_ONLY_BASE, "error": str(exc)}
    if not manifest:
        return {**_READ_ONLY_BASE, "empty_state": "no_manifest", "note": "No completed run found."}
    return {
        **_READ_ONLY_BASE,
        "run_id": manifest.get("run_id"),
        "status": manifest.get("status"),
        "report_quality": manifest.get("report_quality"),
        "mode": manifest.get("mode"),
        "completed_at": manifest.get("completed_at"),
        "runtime_total_ms": manifest.get("runtime_total_ms"),
        "strategy_counts": manifest.get("strategy_counts") or {},
        "daily_opportunity_count": manifest.get("daily_opportunity_count", 0),
        "has_broker_data": manifest.get("has_broker_data"),
        "has_errors": manifest.get("has_errors", False),
        "degraded_reason": manifest.get("degraded_reason"),
        "broker_auth_status": manifest.get("broker_auth_status"),
        "summary_json_bytes": manifest.get("summary_json_bytes", 0),
    }


def _compact_result(result: Any) -> dict[str, Any] | None:
    """Strip raw pipeline details from a job result for API output."""
    if not isinstance(result, dict):
        return None
    return {
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "report_quality": result.get("report_quality"),
        "completed_at": result.get("completed_at"),
        "error": result.get("error"),
    }
