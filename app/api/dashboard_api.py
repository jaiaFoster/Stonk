"""Compact dashboard summary — read-only, no provider calls.

ASA Patch 30D.1 Lane 6 — GET /api/dashboard/summary
Returns the latest run manifest plus strategy counts, DO count, and API links.
"""
from __future__ import annotations

from typing import Any

_READ_ONLY_BASE: dict[str, Any] = {"provider_calls_triggered": False, "read_only": True}


def build_dashboard_summary() -> dict[str, Any]:
    """Return compact run manifest shape for the dashboard summary endpoint."""
    try:
        from app.services.run_manifest_repository import RunManifestRepository
        manifest = RunManifestRepository().latest()
    except Exception:
        manifest = None

    if not manifest:
        return {
            **_READ_ONLY_BASE,
            "empty_state": "no_run_manifest",
            "note": "No completed run found. Trigger a run with POST /api/run/refresh.",
            "api_links": _api_links(),
        }

    return {
        **_READ_ONLY_BASE,
        "run_id": manifest.get("run_id"),
        "status": manifest.get("status"),
        "report_quality": manifest.get("report_quality"),
        "generated_at": manifest.get("completed_at"),
        "mode": manifest.get("mode"),
        "runtime_total_ms": manifest.get("runtime_total_ms"),
        "strategy_counts": manifest.get("strategy_counts") or {},
        "daily_opportunity_count": manifest.get("daily_opportunity_count", 0),
        "has_broker_data": manifest.get("has_broker_data"),
        "has_market_data": manifest.get("has_market_data"),
        "has_options_data": manifest.get("has_options_data"),
        "has_errors": manifest.get("has_errors", False),
        "error_count": manifest.get("error_count", 0),
        "broker_mode": manifest.get("broker_mode"),
        "broker_auth_status": manifest.get("broker_auth_status"),
        "summary_json_bytes": manifest.get("summary_json_bytes", 0),
        "git_commit": manifest.get("git_commit"),
        "deploy_label": manifest.get("deploy_label"),
        "degraded_reason": manifest.get("degraded_reason"),
        "api_links": _api_links(),
    }


def _api_links() -> dict[str, str]:
    return {
        "daily_opportunity": "/api/daily-opportunity",
        "open_positions": "/api/open-positions",
        "strategy_rows_template": "/api/strategies/{strategy_id}/rows",
        "run_latest": "/api/runs/latest",
        "run_refresh": "/api/run/refresh",
    }
