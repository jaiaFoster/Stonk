"""Approximate serialized sizes for major report sections."""

from __future__ import annotations

import json
from typing import Any

from app.services.provider_payload_compaction_service import build_provider_payload_budget


def json_bytes(value: Any) -> int:
    try:
        return len(json.dumps(value, default=str, separators=(",", ":")).encode("utf-8"))
    except Exception:
        return 0


def build_payload_size_profile(
    payload: str, positions: Any, news: Any, recommendations: Any,
    snapshot: dict[str, Any], log: list[str], report_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = snapshot.get("_strategy_results", {}) or {}
    sections = {
        "payload_text": len((payload or "").encode("utf-8")),
        "report_summary_json": json_bytes(report_summary or {}),
        "tradier_snapshot": json_bytes(snapshot),
        "positions": json_bytes(positions),
        "news": json_bytes(news),
        "recommendations": json_bytes(recommendations),
        "calendar": json_bytes(snapshot.get("_unified_calendar_engine") or snapshot.get("_calendar_ranking")),
        "skew": json_bytes(strategy.get("skew_momentum_vertical") or snapshot.get("_skew_momentum_vertical_strategy")),
        "forward_factor": json_bytes(strategy.get("forward_factor_calendar") or snapshot.get("_forward_factor_strategy")),
        "stock_momentum": json_bytes(strategy.get("stock_momentum") or snapshot.get("_stock_momentum_strategy")),
        "portfolio_gap": json_bytes(snapshot.get("_portfolio_gap")),
        "daily_opportunity": json_bytes(snapshot.get("_daily_opportunity_engine")),
        "pipeline_status": json_bytes(snapshot.get("_pipeline_status")),
        "data_coverage": json_bytes(snapshot.get("_data_coverage")),
        "log": json_bytes(log),
    }
    provider_budget = build_provider_payload_budget(snapshot)
    sections["tradier_snapshot_compact"] = provider_budget["compact_tradier_snapshot_bytes"]
    return {
        "total_profiled_bytes": sum(sections.values()),
        "sections_bytes": sections,
        "provider_payload_budget": provider_budget,
    }


def compact_payload_log(profile: dict[str, Any]) -> str:
    sections = profile.get("sections_bytes", {}) or {}
    largest = sorted(sections.items(), key=lambda item: item[1], reverse=True)[:5]
    return "PayloadProfile: " + ", ".join(f"{key}={value}B" for key, value in largest)
