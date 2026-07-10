"""Daily Opportunity read-only API — no provider calls.

ASA Patch 30D.1 Lane 7 — GET /api/daily-opportunity
Serves compact action list from the latest stored snapshot.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

_READ_ONLY_BASE: dict[str, Any] = {"provider_calls_triggered": False, "read_only": True}


def _action_shape(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": action.get("ticker"),
        "action": action.get("action"),
        "type": action.get("type"),
        "strategy": action.get("source") or action.get("source_strategy"),
        "signal_score": (
            action.get("priority_score")
            or action.get("signal_score")
            or action.get("actionability_score")
        ),
        "verdict": action.get("verdict") or action.get("action"),
        "notes": (
            action.get("why")
            or action.get("why_combined")
            or action.get("primary_reason")
        ),
    }


def build_daily_opportunity_response(limit: int = 12, include_exclusions: bool = False) -> dict[str, Any]:
    """Read Daily Opportunity from StrategyRowRepository first.

    Legacy snapshot fallback remains for old deployments with no row-store data.
    """
    try:
        row_store_response = _daily_opportunity_from_row_store(limit=limit, include_exclusions=include_exclusions)
        if row_store_response.get("source") == "strategy_row_store":
            return row_store_response
    except Exception:
        row_store_response = {}

    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return {
                **_READ_ONLY_BASE,
                "empty_state": "no_snapshot",
                "enabled": True,
                "has_data": False,
                "action_count": 0,
                "actions": [],
                "source": "empty",
                "fallback_used": False,
            }
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        do_engine = tradier.get("_daily_opportunity_engine") or {}
        raw_actions = do_engine.get("actions") or []
        if isinstance(raw_actions, dict):
            raw_actions = raw_actions.get("sample") or []
        cap = min(int(limit), 50)
        actions = [_action_shape(a) for a in list(raw_actions)[:cap] if isinstance(a, dict)]
        return {
            **_READ_ONLY_BASE,
            "source_run_id": snapshot.get("run_id"),
            "generated_at": snapshot.get("completed_at"),
            "source": "legacy_snapshot_fallback",
            "engine_source": do_engine.get("source", "daily_opportunity_engine_v1"),
            "fallback_used": True,
            "enabled": bool(do_engine.get("enabled", True)),
            "has_data": bool(do_engine.get("has_data") or actions),
            "action_count": len(actions),
            "actions": actions,
            "summary": do_engine.get("summary") or {},
        }
    except Exception as exc:
        return {**_READ_ONLY_BASE, "error": str(exc), "actions": [], "action_count": 0}


def _daily_opportunity_from_row_store(limit: int = 12, include_exclusions: bool = False) -> dict[str, Any]:
    from app.services.strategy_row_repository import StrategyRowRepository

    repo = StrategyRowRepository()
    strategy_ids = (
        "earnings_calendar",
        "skew_momentum_vertical",
        "stock_momentum",
        "forward_factor_calendar",
    )
    all_rows: list[dict[str, Any]] = []
    latest_run_id = None
    strategy_counts: dict[str, dict[str, int | str]] = {}
    dry_run_exclusions: dict[str, dict[str, Any]] = {}
    for sid in strategy_ids:
        result = repo.read_latest(sid, limit=200)
        run_id = result.get("run_id")
        if run_id and latest_run_id is None:
            latest_run_id = run_id
        rows = [row for row in result.get("rows") or [] if isinstance(row, dict)]
        all_rows.extend(rows)
        strategy_counts[sid] = {"rows_seen": len(rows), "eligible": 0, "excluded": 0}

    if not all_rows:
        return {
            **_READ_ONLY_BASE,
            "source": "empty",
            "fallback_used": False,
            "latest_run_id": None,
            "source_run_id": None,
            "enabled": True,
            "has_data": False,
            "action_count": 0,
            "actions": [],
            "summary": {"row_count_considered": 0, "eligible_count": 0, "excluded_count": 0},
        }

    actions: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    semantic_sources: Counter[str] = Counter()
    for row in all_rows:
        semantic_sources[str(row.get("semantic_source") or "legacy_verdict_inference")] += 1
        action, exclusion = _action_from_strategy_row(row, latest_run_id)
        sid = str(row.get("strategy_id") or "")
        if action:
            actions.append(action)
            if sid in strategy_counts:
                strategy_counts[sid]["eligible"] = int(strategy_counts[sid].get("eligible", 0)) + 1
        elif exclusion:
            exclusions.append(exclusion)
            if sid in strategy_counts:
                strategy_counts[sid]["excluded"] = int(strategy_counts[sid].get("excluded", 0)) + 1
            if sid == "forward_factor_calendar":
                dry_run_exclusions[sid] = {
                    "rows_seen": int(strategy_counts[sid].get("rows_seen", 0)),
                    "eligible": int(strategy_counts[sid].get("eligible", 0)),
                    "excluded_reason": exclusion.get("exclusion_reason"),
                }

    actions, duplicate_exclusions = _dedupe_actions(sorted(actions, key=_daily_sort_key))
    exclusions.extend(duplicate_exclusions)
    eligible_before_limit = len(actions)
    cap = min(int(limit or 12), 50)
    returned_actions = actions[:cap]
    for action in actions[cap:]:
        exclusions.append(_action_limit_exclusion(action))
    exclusion_counts = Counter(str(item.get("exclusion_code") or item.get("exclusion_reason") or "unknown") for item in exclusions)
    action_type_counts = Counter(str(action.get("action_type") or action.get("type") or "unknown") for action in returned_actions)
    truncated_count = max(0, eligible_before_limit - len(returned_actions))
    return {
        **_READ_ONLY_BASE,
        "source": "strategy_row_store",
        "fallback_used": False,
        "latest_run_id": latest_run_id,
        "source_run_id": latest_run_id,
        "enabled": True,
        "has_data": bool(returned_actions),
        "row_count_considered": len(all_rows),
        "eligible_count": eligible_before_limit,
        "eligible_before_limit": eligible_before_limit,
        "excluded_count": len(exclusions),
        "returned_action_count": len(returned_actions),
        "action_count": len(returned_actions),
        "action_limit": cap,
        "truncated": truncated_count > 0,
        "truncated_count": truncated_count,
        "actions": returned_actions,
        "exclusion_counts": dict(exclusion_counts),
        "exclusion_samples": exclusions[:10],
        "exclusions": exclusions[:100] if include_exclusions else None,
        "semantic_source_counts": dict(semantic_sources),
        "inferred_semantics_count": int(semantic_sources.get("legacy_verdict_inference", 0)),
        "dry_run_exclusions": dry_run_exclusions,
        "strategy_counts": strategy_counts,
        "warnings": [],
        "links": {
            "strategy_rows": "/api/strategies/{strategy_id}/rows",
            "open_positions": "/api/open-positions",
            "refresh": "/api/run/refresh",
        },
        "summary": {
            "row_count_considered": len(all_rows),
            "eligible_count": eligible_before_limit,
            "eligible_before_limit": eligible_before_limit,
            "excluded_count": len(exclusions),
            "returned_action_count": len(returned_actions),
            "action_limit": cap,
            "truncated": truncated_count > 0,
            "truncated_count": truncated_count,
            "strategy_counts": strategy_counts,
            "action_type_counts": dict(action_type_counts),
            "exclusion_counts": dict(exclusion_counts),
            "inferred_semantics_count": int(semantic_sources.get("legacy_verdict_inference", 0)),
            "calendar_count": sum(1 for action in returned_actions if action.get("type") in {"calendar", "active_calendar"}),
            "stock_count": sum(1 for action in returned_actions if action.get("type") in {"stock", "stock_add", "stock_watch", "tactical_stock_watch"}),
            "stock_watch_count": sum(1 for action in returned_actions if action.get("type") in {"stock_watch", "tactical_stock_watch"}),
            "skew_vertical_count": sum(1 for action in returned_actions if action.get("type") in {"skew_vertical", "active_skew_vertical"}),
            "risk_count": sum(1 for action in returned_actions if action.get("type") in {"risk", "portfolio_risk"}),
        },
    }


def _action_from_strategy_row(row: dict[str, Any], run_id: str | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    sid = str(row.get("strategy_id") or "")
    verdict = str(row.get("verdict") or "")
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
    score = float(row.get("score") or 0)
    semantics = _semantic_from_row(row)
    action_type = str(semantics.get("action_type") or "none")
    eligibility_status = str(semantics.get("eligibility_status") or "excluded")
    if eligibility_status not in {"eligible", "conditional"} or action_type in {"none", "diagnostic"}:
        return None, _exclusion(row, run_id, str(semantics.get("exclusion_reason") or "not_daily_opportunity_eligible"), semantics)
    if sid == "forward_factor_calendar" or bool(row.get("dry_run")) and action_type == "diagnostic":
        return None, _exclusion(row, run_id, "dry_run", semantics)
    trace = _trace_fields(row)
    display = _display_block(row, semantics)
    return {
        "type": action_type,
        "action_type": action_type,
        "ticker": ticker,
        "priority_score": score,
        "score": score,
        "signal_score": score,
        "action": verdict or row.get("friendly_verdict"),
        "verdict": verdict,
        "friendly_verdict": row.get("friendly_verdict"),
        "primary_reason": row.get("primary_reason"),
        "why": row.get("primary_reason") or (row.get("display") or {}).get("public_reason") or row.get("friendly_verdict"),
        "next_step": (row.get("details") or {}).get("earnings_calendar", {}).get("next_action") or "Review row details and live data before action.",
        "source": "StrategyRowRepository",
        "source_strategy_id": row.get("source_strategy_id") or sid,
        "source_row_id": row.get("source_row_id") or row.get("row_id"),
        "source_run_id": row.get("source_run_id") or run_id,
        "source_table": row.get("source_table") or "strategy_rows",
        "strategy_row_url": f"/api/strategies/{sid}/rows?row_id={row.get('row_id')}",
        "semantic_source": semantics.get("semantic_source"),
        "decision_class": semantics.get("decision_class"),
        "actionability": semantics.get("actionability"),
        "eligibility_status": eligibility_status,
        "eligibility_reason": semantics.get("eligibility_reason") or "Row semantics mark this as eligible.",
        "priority_tier": semantics.get("priority_tier"),
        "review_status": semantics.get("review_status"),
        "display": display,
        **trace,
    }, None


def _exclusion(row: dict[str, Any], run_id: str | None, reason: str, semantics: dict[str, Any] | None = None) -> dict[str, Any]:
    semantics = semantics or _semantic_from_row(row)
    code = _exclusion_code(row, reason, semantics)
    trace = _trace_fields(row)
    return {
        "source_strategy_id": row.get("source_strategy_id") or row.get("strategy_id"),
        "source_row_id": row.get("source_row_id") or row.get("row_id"),
        "source_run_id": row.get("source_run_id") or run_id,
        "source_table": row.get("source_table") or "strategy_rows",
        "ticker": row.get("ticker"),
        "decision_class": semantics.get("decision_class"),
        "eligibility_status": semantics.get("eligibility_status"),
        "exclusion_code": code,
        "exclusion_reason": semantics.get("exclusion_reason") or reason,
        "dry_run": bool(row.get("dry_run")),
        "blocking_gate": (trace.get("top_blockers") or [None])[0],
        "semantic_source": semantics.get("semantic_source"),
    }


def _semantic_from_row(row: dict[str, Any]) -> dict[str, Any]:
    if row.get("decision_class") and row.get("action_type"):
        return {
            "decision_class": row.get("decision_class"),
            "action_type": row.get("action_type"),
            "actionability": row.get("actionability"),
            "eligibility_status": row.get("eligibility_status"),
            "eligibility_reason": row.get("eligibility_reason"),
            "exclusion_reason": row.get("exclusion_reason"),
            "priority_tier": row.get("priority_tier"),
            "review_status": row.get("review_status"),
            "semantic_source": row.get("semantic_source") or "row",
        }
    sid = str(row.get("strategy_id") or "")
    verdict_upper = str(row.get("verdict") or "").upper()
    row_type = str(row.get("row_type") or "")
    if sid == "forward_factor_calendar":
        return _semantic("diagnostic", "diagnostic", "dry_run_only", "dry_run_excluded", "Forward Factor remains dry-run.", "dry_run", "diagnostic", "blocked", "legacy_verdict_inference")
    if sid == "stock_momentum" and _stock_row_daily_eligible(verdict_upper, str(row.get("friendly_verdict") or "")):
        return _semantic("watch", _stock_action_type(verdict_upper), "monitor_only", "eligible", "Legacy stock row inferred as Daily Opportunity watch.", "", "low", "needs_confirmation", "legacy_verdict_inference")
    if sid == "earnings_calendar" and row_type == "lifecycle_check":
        return _semantic("lifecycle", "active_calendar", "actionable", "eligible", "Legacy lifecycle row inferred as active calendar.", "", "high", "monitor", "legacy_verdict_inference")
    if sid == "skew_momentum_vertical" and verdict_upper.startswith("PASS"):
        return _semantic("entry", "vertical_entry", "review_only", "eligible", "Legacy skew row inferred as vertical entry.", "", "normal", "ready", "legacy_verdict_inference")
    return _semantic("rejected", "none", "non_actionable", "excluded", "", "hard_fail" if verdict_upper.startswith("FAIL") else "not_daily_opportunity_eligible", "diagnostic", "blocked", "legacy_verdict_inference")


def _semantic(
    decision_class: str,
    action_type: str,
    actionability: str,
    eligibility_status: str,
    eligibility_reason: str,
    exclusion_reason: str,
    priority_tier: str,
    review_status: str,
    semantic_source: str,
) -> dict[str, Any]:
    return {
        "decision_class": decision_class,
        "action_type": action_type,
        "actionability": actionability,
        "eligibility_status": eligibility_status,
        "eligibility_reason": eligibility_reason,
        "exclusion_reason": exclusion_reason,
        "priority_tier": priority_tier,
        "review_status": review_status,
        "semantic_source": semantic_source,
    }


def _trace_fields(row: dict[str, Any]) -> dict[str, Any]:
    gates = row.get("gates") or []
    counts = Counter()
    blockers: list[str] = []
    positives: list[str] = []
    for gate in gates if isinstance(gates, list) else []:
        if not isinstance(gate, dict):
            continue
        status = str(gate.get("status") or "").lower()
        name = str(gate.get("name") or gate.get("label") or "gate")
        if status in {"pass", "passed"}:
            counts["passed_gate_count"] += 1
            if len(positives) < 3:
                positives.append(name)
        elif status in {"warn", "warning", "watch"}:
            counts["warning_gate_count"] += 1
        elif status in {"fail", "failed"}:
            counts["failed_gate_count"] += 1
            if len(blockers) < 3:
                blockers.append(str(gate.get("detail") or gate.get("reason") or name))
        if bool(gate.get("blocking") or gate.get("is_hard_block")):
            counts["blocking_gate_count"] += 1
    return {
        "passed_gate_count": int(counts.get("passed_gate_count", 0)),
        "warning_gate_count": int(counts.get("warning_gate_count", 0)),
        "failed_gate_count": int(counts.get("failed_gate_count", 0)),
        "blocking_gate_count": int(counts.get("blocking_gate_count", 0)),
        "top_blockers": blockers,
        "top_positive_signals": positives,
        "data_quality_status": row.get("data_quality") if isinstance(row.get("data_quality"), str) else "",
    }


def _display_block(row: dict[str, Any], semantics: dict[str, Any]) -> dict[str, Any]:
    display = row.get("display") if isinstance(row.get("display"), dict) else {}
    return {
        "title": display.get("title") or row.get("ticker"),
        "subtitle": display.get("subtitle") or row.get("strategy_name") or row.get("strategy_id"),
        "badge": display.get("badge") or row.get("friendly_verdict") or row.get("verdict"),
        "status_label": row.get("friendly_verdict") or row.get("verdict"),
        "primary_reason": row.get("primary_reason") or display.get("public_reason") or semantics.get("eligibility_reason"),
        "next_step": (row.get("details") or {}).get("earnings_calendar", {}).get("next_action") or "Review row details and live data before action.",
        "severity": _severity_for(semantics),
    }


def _severity_for(semantics: dict[str, Any]) -> str:
    if semantics.get("decision_class") in {"risk", "exit"}:
        return "high"
    if semantics.get("eligibility_status") == "eligible":
        return "normal"
    if semantics.get("eligibility_status") == "conditional":
        return "watch"
    return "muted"


def _exclusion_code(row: dict[str, Any], reason: str, semantics: dict[str, Any]) -> str:
    if reason == "dry_run" or semantics.get("eligibility_status") == "dry_run_excluded":
        return "dry_run"
    if reason in {"duplicate_action", "action_limit"}:
        return reason
    row_type = str(row.get("row_type") or "")
    if row_type == "rejected_candidate":
        return str(semantics.get("exclusion_reason") or "rejected_candidate")
    if semantics.get("decision_class") == "diagnostic":
        return "diagnostic_only"
    if str(row.get("verdict") or "").upper().startswith("FAIL"):
        return str(semantics.get("exclusion_reason") or "hard_fail")
    return str(semantics.get("exclusion_reason") or reason or "not_daily_opportunity_eligible")


def _action_limit_exclusion(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_strategy_id": action.get("source_strategy_id"),
        "source_row_id": action.get("source_row_id"),
        "source_run_id": action.get("source_run_id"),
        "source_table": action.get("source_table"),
        "ticker": action.get("ticker"),
        "decision_class": action.get("decision_class"),
        "eligibility_status": action.get("eligibility_status"),
        "exclusion_code": "action_limit",
        "exclusion_reason": "Eligible action omitted by response limit.",
        "dry_run": False,
        "blocking_gate": None,
        "semantic_source": action.get("semantic_source"),
    }


def _daily_sort_key(action: dict[str, Any]) -> tuple[int, float]:
    priority = {
        "active_calendar": 0,
        "active_skew_vertical": 1,
        "calendar": 2,
        "skew_vertical": 3,
        "stock_add": 4,
        "stock": 4,
        "stock_watch": 5,
        "tactical_stock_watch": 6,
        "risk": 7,
        "portfolio_risk": 7,
    }
    return (priority.get(str(action.get("type") or ""), 99), -float(action.get("priority_score") or 0))


def _stock_row_daily_eligible(verdict_upper: str, friendly_verdict: str) -> bool:
    """Preserve legacy Daily Opportunity stock-watch behavior from row-store rows."""
    friendly_upper = friendly_verdict.upper()
    if verdict_upper.startswith(("FAIL", "AVOID")) or "WEAK" in verdict_upper:
        return False
    if verdict_upper.startswith(("CONSIDER ADDING", "ADD ON", "WATCH / CONFIRM TREND")):
        return True
    if verdict_upper.startswith(("TACTICAL ONLY", "STARTER ONLY", "HOLD / DO NOT ADD")):
        return True
    return friendly_upper in {"MOMENTUM PASS", "WATCH", "TACTICAL WATCH"}


def _stock_action_type(verdict_upper: str) -> str:
    if verdict_upper.startswith(("CONSIDER ADDING", "ADD ON")):
        return "stock_add"
    if verdict_upper.startswith(("TACTICAL ONLY", "STARTER ONLY", "HOLD / DO NOT ADD")):
        return "tactical_stock_watch"
    return "stock_watch"


def _dedupe_actions(actions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for action in actions:
        key = (str(action.get("type") or ""), str(action.get("ticker") or ""), str(action.get("action") or ""))
        if key in seen:
            exclusions.append({
                "source_strategy_id": action.get("source_strategy_id"),
                "source_row_id": action.get("source_row_id"),
                "source_run_id": action.get("source_run_id"),
                "source_table": action.get("source_table"),
                "ticker": action.get("ticker"),
                "decision_class": action.get("decision_class"),
                "eligibility_status": action.get("eligibility_status"),
                "exclusion_code": "duplicate_action",
                "exclusion_reason": "Duplicate Daily Opportunity action suppressed.",
                "dry_run": False,
                "blocking_gate": None,
                "semantic_source": action.get("semantic_source"),
            })
            continue
        seen.add(key)
        output.append(action)
    return output, exclusions
