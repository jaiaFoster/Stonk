"""Registry result collection isolated from legacy strategy math."""

from __future__ import annotations

from typing import Any

from app.services.strategy_opportunity_normalizer import normalize_legacy_strategy_row
from app.services.strategy_row_normalization_service import normalize_strategy_row
from app.strategies.registry import enabled_strategies, normalize_strategy_results


def execute_strategy_registry(context: Any, evaluators: dict[str, Any], log_print=None) -> dict[str, dict[str, Any]]:
    """Execute registered compatibility adapters with per-plugin failure isolation."""
    log = log_print or (lambda message: None)
    raw_results: dict[str, dict[str, Any]] = {}
    plugins = enabled_strategies()
    log(f"StrategyRegistry: {len(plugins)} enabled strategy plugin(s)")
    for plugin in plugins:
        log(f"StrategyRegistry: executing {plugin.strategy_id}")
        try:
            evaluator = evaluators.get(plugin.strategy_id)
            if evaluator is None:
                raise RuntimeError("strategy evaluator not registered")
            raw_results[plugin.strategy_id] = evaluator() or {}
        except Exception as exc:
            raw_results[plugin.strategy_id] = {"items": [], "errors": [str(exc)], "execution_failed": True}
        normalized = plugin.normalize_result(raw_results[plugin.strategy_id], context)
        log(
            f"StrategyRegistry: {plugin.strategy_id} complete "
            f"pass={normalized.pass_count} watch={normalized.watch_count} fail={normalized.fail_count}"
        )
    return _attach_canonical_opportunities(normalize_strategy_results(context, raw_results), context)


def collect_strategy_results(context: Any, raw_results: dict[str, dict[str, Any]], log_print=None) -> dict[str, dict[str, Any]]:
    """Normalize every registered strategy independently.

    Existing services still evaluate their own math. This service is the
    migration boundary that keeps report assembly strategy-agnostic.
    """
    log = log_print or (lambda message: None)
    normalized = _attach_canonical_opportunities(normalize_strategy_results(context, raw_results), context)
    for strategy_id, result in normalized.items():
        log(f"StrategyRegistry: executing {strategy_id}")
        log(
            f"StrategyRegistry: {strategy_id} complete "
            f"pass={result.get('pass_count', 0)} watch={result.get('watch_count', 0)} "
            f"fail={result.get('fail_count', 0)} skipped={result.get('skipped_count', 0)}"
        )
    return normalized


_SURVIVAL_FIELDS = (
    "action", "final_verdict", "type", "primary_reason", "primary_rejection_reason", "reason",
    "expiration_pair", "stale_structure", "date_confidence", "source_mode",
    "side", "position_type", "provider_fetch_count", "pipeline_trace",
    "earnings_date_confidence", "earnings_source_count", "earnings_sources_seen",
    "earnings_source_conflict", "earnings_conflict_details", "earnings_trust_label",
    "earnings_trust_reason", "calendar_entry_allowed", "entry_window_status",
    "entry_window_open", "entry_window_reason", "short_leg_expires_before_earnings",
    "short_leg_dte_minimum", "short_leg_time_value_minimum", "short_leg_does_not_span_event",
    "entry_window_front_expiration", "entry_window_front_dte", "expiry_gap_valid",
    "available_pre_earnings_expirations", "rejected_expirations",
    "proposed_short_expiration", "proposed_long_expiration",
    "available_expirations", "current_dte_to_earnings", "ideal_entry_window",
    "estimated_entry_date", "days_until_entry_window", "blocker_code", "blocker_detail",
    "opportunity_id", "anchor_type", "anchor_id", "clock_type", "clock_value", "anchor_timestamp",
    "lifecycle_stage", "strategy_stage", "calendar_stage", "evaluation_state", "trade_verdict",
    "recommended_action", "build_eligible", "surface_eligible", "entry_evaluation_eligible",
    "entry_allowed", "terminal", "policy_version", "policy_source", "disposition_code",
    "disposition_reason", "current_structure_id", "structure_id", "structure_version",
    "previous_structure_id", "structure_changed", "structure_change_reason",
)


def _attach_canonical_opportunities(results: dict[str, dict[str, Any]], context: Any) -> dict[str, dict[str, Any]]:
    """Add canonical rows while preserving every legacy result field."""
    run_id = getattr(context, "run_id", None)
    for strategy_id, result in results.items():
        canonical = []
        errors = []
        lost_fields: dict[str, int] = {}
        for index, row in enumerate(result.get("rows") or []):
            if not isinstance(row, dict):
                errors.append({"row_index": index, "error": "row_not_dict"})
                continue
            opportunity = normalize_legacy_strategy_row(strategy_id, row, run_id=run_id)
            if strategy_id == "forward_factor_calendar":
                opportunity.can_trade_live = False
                opportunity.can_enter_daily_opportunity = False
            serialized = opportunity.to_dict()
            for field in _SURVIVAL_FIELDS:
                if field in row:
                    serialized[field] = row[field]
            original_verdict = row.get("final_verdict") or row.get("verdict") or row.get("action")
            if original_verdict and str(serialized.get("verdict") or "").upper() in {"", "UNKNOWN"}:
                serialized["verdict"] = original_verdict
            if row.get("entry_window_reason"):
                serialized["primary_reason"] = row["entry_window_reason"]
            elif not serialized.get("primary_reason"):
                primary_reason = (
                    row.get("primary_reason")
                    or row.get("primary_rejection_reason")
                    or row.get("reason")
                )
                if primary_reason:
                    serialized["primary_reason"] = primary_reason
            normalize_strategy_row(serialized, strategy_id)
            canonical.append(serialized)
            if opportunity.reason_code == "NORMALIZATION_ERROR":
                errors.append({"row_index": index, "error": opportunity.reason_label})
            for field in _SURVIVAL_FIELDS:
                if row.get(field) is not None and field not in serialized and field not in serialized.get("raw", {}):
                    lost_fields[field] = lost_fields.get(field, 0) + 1
        result["canonical_opportunities"] = canonical
        for active_row in result.get("active_rows") or []:
            if isinstance(active_row, dict):
                normalize_strategy_row(active_row, strategy_id)
        result["canonical_opportunity_count"] = len(canonical)
        result["canonical_normalizer_errors"] = errors
        result["canonical_normalizer_error_count"] = len(errors)
        result["canonical_lost_field_counts"] = lost_fields
    return results
