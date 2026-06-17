"""Thin stored-state boundary for future Advisor APIs and local vault pulls."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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


def build_advisor_snapshot_payload(snapshot: dict[str, Any], summary: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    """Compact morning-brief payload for /api/advisor/snapshot and local vault writes.

    Reads only from already-loaded cached run data — no provider calls.
    """
    tradier = report.get("tradier_snapshot", {}) or {}
    pipeline = tradier.get("_pipeline_status", {}) or {}
    strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}

    daily_opp = tradier.get("_daily_opportunity_engine") or {}
    actions = [_action_shape(a) for a in (daily_opp.get("actions") or [])]

    ff_strategy = tradier.get("_forward_factor_strategy") or {}
    ff_journal = ff_strategy.get("ff_journal") or {}

    calendar_ranking = tradier.get("_calendar_ranking") or {}
    calendar_items = _compact_calendar_candidates(calendar_ranking)

    skew_strategy = tradier.get("_skew_momentum_vertical_strategy") or {}
    skew_items = _compact_skew_candidates(skew_strategy)

    positions_raw = report.get("positions", []) or []
    positions_summary = [_position_shape(p) for p in positions_raw]

    freshness = _freshness_indicator(snapshot, pipeline)

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run_id": snapshot.get("run_id"),
        "run_date": str(snapshot.get("completed_at") or "")[:10],
        "run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
        "provider_calls_triggered": False,
        "read_only": True,
        "freshness": freshness,
        "daily_opportunity": {
            "action_count": len(actions),
            "actions": actions,
        },
        "strategy_summary": _strategy_summary(strategies),
        "positions_summary": positions_summary,
        "ff_journal_summary": {
            "total_observations": ff_journal.get("total") or 0,
            "distinct_tickers": ff_journal.get("tickers") or 0,
            "distinct_runs": ff_journal.get("runs") or 0,
            "latest_date": ff_journal.get("latest_date"),
        },
        "calendar_candidates": calendar_items,
        "skew_candidates": skew_items,
    }


def _action_shape(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": action.get("ticker"),
        "action": action.get("action"),
        "type": action.get("type"),
        "strategy": action.get("source", action.get("source_strategy")),
        "signal_score": action.get("priority_score") or action.get("signal_score") or action.get("actionability_score"),
        "verdict": action.get("verdict") or action.get("action"),
        "notes": action.get("why") or action.get("why_combined") or action.get("primary_reason"),
    }


def _position_shape(pos: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": pos.get("ticker"),
        "quantity": pos.get("quantity"),
        "avg_cost": pos.get("avg_buy_price"),
        "current_price": pos.get("current_price"),
        "unrealized_pnl_pct": pos.get("gain_loss_pct"),
        "market_value": pos.get("market_value"),
        "asset_type": pos.get("asset_type", "stock"),
    }


def _strategy_summary(strategies: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for sid, result in (strategies or {}).items():
        out[sid] = {
            "pass": result.get("pass_count", 0),
            "watch": result.get("watch_count", 0),
            "fail": result.get("fail_count", 0),
            "skipped": result.get("skipped_count", 0),
        }
    return out


def _compact_calendar_candidates(calendar_ranking: dict[str, Any]) -> list[dict[str, Any]]:
    items = (calendar_ranking.get("items") or [])[:10]
    out = []
    for row in items:
        candidate = row.get("candidate") or {}
        out.append({
            "ticker": row.get("ticker"),
            "rank_score": row.get("rank_score"),
            "action": row.get("action"),
            "entry_timing": row.get("entry_timing"),
            "days_until_earnings": row.get("days_until_earnings"),
            "passes_all_criteria": row.get("passes_all_criteria"),
            "front_expiration": candidate.get("front_expiration"),
            "back_expiration": candidate.get("back_expiration"),
            "debit": candidate.get("debit"),
            "underlying_price": candidate.get("underlying_price"),
        })
    return out


def _compact_skew_candidates(skew_strategy: dict[str, Any]) -> list[dict[str, Any]]:
    items = (skew_strategy.get("pass_items") or [])[:5] + (skew_strategy.get("watch_items") or [])[:5]
    out = []
    for row in items:
        spread = row.get("possible_spread") or {}
        out.append({
            "ticker": row.get("ticker"),
            "verdict": row.get("verdict"),
            "score": row.get("score"),
            "direction": row.get("direction"),
            "momentum_score": row.get("momentum_score"),
            "dte": row.get("dte"),
            "expiration": spread.get("expiration"),
            "conservative_debit": row.get("conservative_debit"),
            "reward_risk": row.get("reward_risk"),
        })
    return out


def _freshness_indicator(snapshot: dict[str, Any], pipeline: dict[str, Any]) -> dict[str, Any]:
    completed_at = snapshot.get("completed_at")
    status = "fresh"
    age_seconds = None
    if completed_at:
        try:
            from app import config
            completed_dt = datetime.fromisoformat(str(completed_at).replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            age_seconds = int((now - completed_dt).total_seconds())
            warn_threshold = int(getattr(config, "REPORT_FRESHNESS_WARN_SECONDS", 21600) or 21600)
            stale_threshold = int(getattr(config, "REPORT_FRESHNESS_STALE_SECONDS", 86400) or 86400)
            if age_seconds >= stale_threshold:
                status = "stale"
            elif age_seconds >= warn_threshold:
                status = "warn"
        except Exception:
            status = "unknown"
    return {
        "status": status,
        "completed_at": completed_at,
        "age_seconds": age_seconds,
        "run_quality": pipeline.get("report_quality") or pipeline.get("overall_status"),
    }
