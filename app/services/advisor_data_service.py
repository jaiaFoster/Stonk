"""Thin stored-state boundary for future Advisor APIs and local vault pulls."""

from __future__ import annotations

from app.services.developer_snapshot_service import build_developer_snapshot
from app.services.run_manifest_repository import RunManifestRepository


def build_latest_snapshot_manifest():
    return RunManifestRepository().latest()


def build_latest_daily_brief():
    return (build_developer_snapshot("summary").get("daily_opportunity") or {})


def build_latest_active_options_summary():
    return (build_developer_snapshot("summary").get("open_options_summary") or {})


def build_latest_strategy_summary():
    return (build_developer_snapshot("summary").get("strategy_summaries") or {})


def build_latest_risk_summary():
    return (build_developer_snapshot("summary").get("portfolio_gap") or {})


def build_latest_portfolio_gap_summary():
    return build_latest_risk_summary()
