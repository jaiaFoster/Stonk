"""Build redacted pull-on-demand developer snapshots from stored reports."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

from app import config
from app.services.redaction_service import redact
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_manifest_repository import RunManifestRepository
from app.services.data_freshness_service import build_data_freshness_summary


def build_developer_snapshot(mode: str = "latest", report_repository: ReportSnapshotRepository | None = None, manifest_repository: RunManifestRepository | None = None) -> dict[str, Any]:
    report_repository = report_repository or ReportSnapshotRepository()
    manifest_repository = manifest_repository or RunManifestRepository()
    manifest = manifest_repository.latest()
    if mode == "manifest_only":
        return redact(_read_only({"snapshot_version": 1, "snapshot_mode": mode, "created_at": _now(), "run_manifest": manifest}))
    snapshot = report_repository.latest_success(include_full=mode == "full")
    if not snapshot:
        return redact(_read_only({"snapshot_version": 1, "snapshot_mode": mode, "created_at": _now(), "source_status": "unavailable", "run_manifest": manifest}))
    summary = report_repository.load_summary(snapshot, full=mode == "full")
    report = summary.get("report_data", {}) or {}
    tradier = report.get("tradier_snapshot", {}) or {}
    strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}
    compact_strategies = {
        key: _strategy_summary(value, include_rows=mode == "full" and config.DEV_SNAPSHOT_INCLUDE_FULL_STRATEGY_ROWS)
        for key, value in strategies.items()
    }
    from app.services.testing_packet_service import build_strategy_catalog
    result = {
        "snapshot_version": 1, "snapshot_mode": mode, "created_at": _now(),
        "source_run_id": snapshot.get("run_id"), "source_status": snapshot.get("status"), "app_mode": snapshot.get("mode"),
        "available_detail_sections": ["daily_opportunity", "data_coverage", "lifecycle", "pipeline", "portfolio", "providers", "provider_raw", "strategies", "strategy"],
        "git_commit": os.environ.get("RAILWAY_GIT_COMMIT_SHA") or os.environ.get("GIT_COMMIT"),
        "git_branch": os.environ.get("RAILWAY_GIT_BRANCH") or os.environ.get("GIT_BRANCH"),
        "deploy_label": os.environ.get("RAILWAY_DEPLOYMENT_ID"),
        "report_snapshot_profile": report_repository.snapshot_profile(snapshot),
        "run_manifest": manifest, "runtime_profile": tradier.get("_runtime_profile"),
        "payload_size_profile": tradier.get("_payload_size_profile"), "storage_profile": tradier.get("_storage_profile"),
        "provider_payload_budget": (tradier.get("_payload_size_profile") or {}).get("provider_payload_budget"),
        "data_freshness": build_data_freshness_summary(snapshot, summary, manifest),
        "provider_status": tradier.get("_provider_status"), "data_coverage": tradier.get("_data_coverage"),
        "portfolio_summary": {"position_count": len(report.get("positions", []) or []), "recommendation_count": len(report.get("recommendations", []) or [])},
        "positions_summary": report.get("positions", []), "open_options_summary": _compact(tradier.get("_open_options_positions")),
        "calendar_lifecycle_summary": _compact(tradier.get("_calendar_lifecycle_checks")),
        "daily_opportunity": _compact(tradier.get("_daily_opportunity_engine")),
        "strategy_summaries": compact_strategies, "strategy_ids": build_strategy_catalog({
            "strategy_summaries": compact_strategies,
            "source_run_id": snapshot.get("run_id"),
        }), "portfolio_gap": _compact(tradier.get("_portfolio_gap")),
        "logs": list(report.get("log", []) or [])[-config.REPORT_SNAPSHOT_MAX_LOG_LINES:] if config.DEV_SNAPSHOT_INCLUDE_FULL_LOG else list(report.get("log", []) or [])[-25:],
        "errors": (tradier.get("_pipeline_status", {}) or {}).get("errors", []),
        "warnings": (tradier.get("_pipeline_status", {}) or {}).get("warnings", []),
    }
    return redact(_read_only(result))


def build_snapshot_detail(
    section: str,
    *,
    strategy_id: str | None = None,
    report_repository: ReportSnapshotRepository | None = None,
) -> dict[str, Any]:
    """Load one explicit detail section from dormant full snapshot state."""
    report_repository = report_repository or ReportSnapshotRepository()
    snapshot = report_repository.latest_success(include_full=True)
    base = {
        "snapshot_version": 1,
        "snapshot_mode": "detail",
        "detail_section": section,
        "created_at": _now(),
        "source_run_id": (snapshot or {}).get("run_id"),
        "source_status": (snapshot or {}).get("status"),
    }
    if not snapshot:
        return redact(_read_only({**base, "status": "unavailable", "detail": None}))
    summary = report_repository.load_summary(snapshot, full=True)
    report = summary.get("report_data", {}) or {}
    tradier = report.get("tradier_snapshot", {}) or {}
    strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}
    details = {
        "daily_opportunity": tradier.get("_daily_opportunity_engine"),
        "data_coverage": tradier.get("_data_coverage"),
        "lifecycle": {
            "open_options": tradier.get("_open_options_positions"),
            "calendar_lifecycle": tradier.get("_calendar_lifecycle_checks"),
            "calendar_engine": tradier.get("_unified_calendar_trade_engine"),
        },
        "pipeline": tradier.get("_pipeline_status"),
        "portfolio": {
            "positions": report.get("positions", []),
            "recommendations": report.get("recommendations", []),
            "portfolio_gap": tradier.get("_portfolio_gap"),
        },
        "providers": tradier.get("_provider_status"),
        "strategies": strategies,
    }
    if section == "strategy":
        detail = (strategies or {}).get(str(strategy_id or ""))
        base["strategy_id"] = strategy_id
        if detail is None:
            from app.services.testing_packet_service import valid_strategy_ids
            base["error"] = "Unknown strategy_id."
            base["valid_strategy_ids"] = valid_strategy_ids()
    elif section == "provider_raw":
        detail = report_repository.load_raw_provider_snapshot(snapshot)
        base["raw_provider_payload"] = True
    else:
        detail = details.get(section)
    status = "ok" if detail is not None else "not_found"
    return redact(_read_only({**base, "status": status, "detail": detail}))


def _strategy_summary(result: dict[str, Any], include_rows: bool) -> dict[str, Any]:
    output = {key: result.get(key) for key in ("strategy_id", "strategy_label", "enabled", "ran", "pass_count", "watch_count", "fail_count", "skipped_count", "summary")}
    if include_rows:
        output["rows"] = list(result.get("rows", []) or [])[:50]
    return output


def _compact(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    output = {}
    for key, item in value.items():
        if key not in {"summary", "items", "actions", "calendars", "provider_status", "errors"}:
            continue
        output[key] = item[:50] if isinstance(item, list) else item
    return output


def _json(raw: Any, fallback: Any) -> Any:
    try:
        return json.loads(raw) if isinstance(raw, str) else raw or fallback
    except json.JSONDecodeError:
        return fallback


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_only(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "provider_calls_triggered": False, "read_only": True}
