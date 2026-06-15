"""Compact provider-free QA packet and strategy ID discovery."""

from __future__ import annotations

from typing import Any

from app import config
from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.redaction_service import redact
from app.strategies.registry import STRATEGY_REGISTRY


STRATEGY_ALIASES = {
    "earnings_calendar": ["calendar", "earnings"],
    "skew_momentum_vertical": ["skew", "skew_vertical"],
    "forward_factor_calendar": ["forward_factor", "ff"],
    "stock_momentum": ["momentum", "stock_add"],
}


def build_strategy_catalog(snapshot: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    snapshot = snapshot or build_developer_snapshot("summary")
    summaries = snapshot.get("strategy_summaries") or {}
    output = []
    for strategy in STRATEGY_REGISTRY:
        result = summaries.get(strategy.strategy_id) or {}
        output.append({
            "strategy_id": strategy.strategy_id,
            "display_name": strategy.strategy_label,
            "aliases": STRATEGY_ALIASES.get(strategy.strategy_id, []),
            "enabled": strategy.is_enabled(),
            "dry_run": strategy.strategy_id == "forward_factor_calendar" and bool(config.FORWARD_FACTOR_DRY_RUN),
            "supported_detail_endpoint": f"/api/dev/snapshot/detail/strategy?strategy_id={strategy.strategy_id}",
            "pass_count": int(result.get("pass_count") or 0),
            "watch_count": int(result.get("watch_count") or 0),
            "fail_count": int(result.get("fail_count") or 0),
            "skipped_count": int(result.get("skipped_count") or 0),
            "last_run_id": snapshot.get("source_run_id"),
            "available_detail_modes": ["summary", "detail"],
        })
    return output


def valid_strategy_ids() -> list[str]:
    return [strategy.strategy_id for strategy in STRATEGY_REGISTRY]


def build_testing_packet() -> dict[str, Any]:
    snapshot = build_developer_snapshot("full")
    strategies = snapshot.get("strategy_summaries") or {}
    daily = snapshot.get("daily_opportunity") or {}
    actions = daily.get("actions", []) if isinstance(daily, dict) else []
    ff_actions = [
        row for row in actions
        if isinstance(row, dict) and str(row.get("strategy_id") or row.get("source_strategy") or "").lower() == "forward_factor_calendar"
    ]
    return redact({
        "status": "ok" if snapshot.get("source_run_id") else "unavailable",
        "source_run_id": snapshot.get("source_run_id"),
        "source_status": snapshot.get("source_status"),
        "app_deploy": {
            "git_commit": snapshot.get("git_commit"),
            "git_branch": snapshot.get("git_branch"),
            "deploy_label": snapshot.get("deploy_label"),
        },
        "latest_run_manifest": snapshot.get("run_manifest"),
        "runtime_profile": snapshot.get("runtime_profile"),
        "payload_profile": snapshot.get("payload_size_profile"),
        "storage_profile": snapshot.get("storage_profile"),
        "data_freshness": snapshot.get("data_freshness"),
        "active_lifecycle_summary": snapshot.get("calendar_lifecycle_summary"),
        "daily_opportunity_summary": {
            "summary": daily.get("summary") if isinstance(daily, dict) else {},
            "top_actions": actions[:5],
        },
        "strategy_ids": build_strategy_catalog(snapshot),
        "strategy_results": {
            strategy_id: _compact_strategy_result(result)
            for strategy_id, result in strategies.items()
            if isinstance(result, dict)
        },
        "provider_caveats": _provider_caveats(snapshot),
        "portfolio_gap_summary": snapshot.get("portfolio_gap"),
        "risk_review_summary": {"status": "available_in_portfolio_detail"},
        "endpoint_health": {
            "snapshot_available": bool(snapshot.get("source_run_id")),
            "manifest_available": bool(snapshot.get("run_manifest")),
            "profiles_available": bool(snapshot.get("runtime_profile") or snapshot.get("payload_size_profile")),
            "provider_calls_triggered": False,
        },
        "forward_factor_dry_run_excluded": bool(config.FORWARD_FACTOR_DRY_RUN) and not ff_actions,
        "trade_execution_enabled": False,
        "limitations": [
            "Stored-state testing packet; no provider refresh.",
            "Rows are bounded samples, not full holdings, chains, logs, or provider payloads.",
        ],
        "provider_calls_triggered": False,
        "read_only": True,
    })


def _compact_strategy_result(result: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in (result.get("rows") or []) if isinstance(row, dict)]
    return {
        "strategy_id": result.get("strategy_id"),
        "display_name": result.get("strategy_label"),
        "enabled": result.get("enabled"),
        "pass_count": int(result.get("pass_count") or 0),
        "watch_count": int(result.get("watch_count") or 0),
        "fail_count": int(result.get("fail_count") or 0),
        "skipped_count": int(result.get("skipped_count") or 0),
        "top_pass": _top_rows(rows, ("PASS", "CONSIDER ADDING", "ADD ON")),
        "top_watch": _top_rows(rows, ("WATCH", "RESEARCH")),
        "top_fail": _top_rows(rows, ("FAIL", "BLOCKED", "AVOID")),
    }


def _top_rows(rows: list[dict[str, Any]], prefixes: tuple[str, ...]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        verdict = str(row.get("final_verdict") or row.get("verdict") or row.get("action") or "").upper()
        if any(prefix in verdict for prefix in prefixes):
            output.append({
                key: row.get(key)
                for key in ("ticker", "verdict", "final_verdict", "action", "score", "primary_reason", "primary_blocker")
                if row.get(key) is not None
            })
        if len(output) >= 3:
            break
    return output


def _provider_caveats(snapshot: dict[str, Any]) -> dict[str, Any]:
    coverage = snapshot.get("data_coverage") or {}
    return {
        "provider_status": snapshot.get("provider_status"),
        "coverage_counters": coverage.get("counters") if isinstance(coverage, dict) else None,
        "provider_payload_budget": snapshot.get("provider_payload_budget"),
    }
