"""Static data catalog for future custom strategy definitions.

This module is intentionally descriptive only. It does not fetch providers,
evaluate rules, execute user code, or persist strategy drafts.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.models.strategy_catalog_models import CATALOG_SCHEMA_VERSION, StrategyFieldDefinition
from app.services.strategy_row_schema import (
    MINIMUM_SUPPORTED_STRATEGY_ROW_SCHEMA_VERSION,
    SEMANTIC_FIELDS_VERSION,
    STRATEGY_ROW_SCHEMA_VERSION,
)

READ_ONLY_BASE = {"provider_calls_triggered": False, "read_only": True}

VALUE_TYPES = {
    "number", "integer", "boolean", "string", "enum", "date", "datetime",
    "duration_days", "percentage", "currency", "list",
}
ALLOWED_USES = {"universe_filter", "data_requirement", "gate", "score", "verdict", "display", "post_process"}
AVAILABILITY_STAGES = {"static", "broker_snapshot", "run_context", "strategy_input", "strategy_output", "lifecycle"}
REQUIREMENT_TYPES = {
    "quote", "candles", "benchmark_candles", "earnings_date", "earnings_history",
    "options_expirations", "options_chain", "options_chain_set", "broker_positions",
    "open_option_positions", "portfolio_summary",
}
PROVIDER_COST_CLASSES = {"none", "low", "medium", "high", "very_high"}
MISSING_BEHAVIORS = {"fail_gate", "skip_rule", "treat_as_false", "use_default", "mark_data_needed", "diagnostic_only"}

NUMERIC_OPERATORS = (
    "greater_than", "greater_than_or_equal", "less_than", "less_than_or_equal",
    "equal", "not_equal", "between", "outside", "exists", "not_exists",
)
BOOLEAN_OPERATORS = ("is_true", "is_false", "exists", "not_exists")
ENUM_OPERATORS = ("equal", "not_equal", "in", "not_in", "exists", "not_exists")
DATE_OPERATORS = ("before", "after", "on", "between", "within_next_days", "within_previous_days", "exists", "not_exists")
LIST_OPERATORS = ("contains", "not_contains", "contains_any", "contains_all", "is_empty", "is_not_empty")
OPERATOR_SETS = {
    "number": NUMERIC_OPERATORS,
    "integer": NUMERIC_OPERATORS,
    "percentage": NUMERIC_OPERATORS,
    "currency": NUMERIC_OPERATORS,
    "duration_days": NUMERIC_OPERATORS,
    "boolean": BOOLEAN_OPERATORS,
    "string": ENUM_OPERATORS,
    "enum": ENUM_OPERATORS,
    "date": DATE_OPERATORS,
    "datetime": DATE_OPERATORS,
    "list": LIST_OPERATORS,
}

BUILT_IN_STRATEGY_FIELDS = {
    "earnings_calendar": {
        "earnings.date", "earnings.days_until", "earnings.session", "earnings.date_confidence",
        "earnings.sources", "options.front_expiration", "options.back_expiration",
        "options.front_dte", "options.back_dte", "options.expiration_gap_days",
        "options.strike", "options.option_type", "options.front_iv", "options.back_iv",
        "options.iv_difference", "options.debit", "options.bid", "options.ask",
        "options.mid", "options.open_interest", "options.volume", "options.spread_pct",
        "options.short_leg_expires_before_earnings", "options.short_leg_spans_earnings",
        "market.price.last",
    },
    "skew_momentum_vertical": {
        "momentum.time_series_score", "momentum.relative_strength_score", "momentum.direction",
        "options.long_leg_delta", "options.short_leg_delta", "options.long_leg_iv",
        "options.short_leg_iv", "options.skew", "options.skew_zscore", "options.debit",
        "options.max_profit", "options.max_loss", "options.reward_risk_ratio",
        "options.open_interest", "options.volume", "options.spread_pct", "earnings.days_until",
    },
    "forward_factor_calendar": {
        "volatility.front_iv", "volatility.back_iv", "volatility.forward_variance",
        "volatility.forward_factor", "volatility.forward_factor_threshold",
        "volatility.earnings_contaminated", "options.front_expiration",
        "options.back_expiration", "options.expiration_gap_days", "options.open_interest",
        "options.volume", "options.spread_pct", "data_quality.source_qualified",
    },
    "stock_momentum": {
        "market.price.last", "market.volume.average_20d", "technical.return.5d",
        "technical.return.20d", "technical.return.60d", "technical.distance_from_high_pct",
        "technical.distance_from_moving_average_pct", "momentum.time_series_score",
        "momentum.relative_strength_score", "momentum.cross_sectional_rank",
        "momentum.extension_status", "portfolio.has_position",
    },
}


def field_catalog() -> dict[str, StrategyFieldDefinition]:
    fields = [
        _f("market.price.last", "Last Price", "Most recent approved market price.", "market", "currency", "market_data", "quote.last", "run_context", ("quote",), "usd", cost="low", uses=("universe_filter", "gate", "score", "display"), req_market=True),
        _f("market.volume.average_20d", "Average Volume 20D", "Average daily share volume over roughly 20 sessions.", "market", "integer", "market_data", "derived.average_volume_20d", "run_context", ("candles",), "shares", cost="medium", uses=("universe_filter", "gate", "score", "display"), req_market=True),
        _f("technical.return.5d", "Return 5D", "Five-day underlying return.", "technical", "percentage", "derived_metrics", "returns.5d", "run_context", ("candles",), "pct", cost="medium", req_market=True),
        _f("technical.return.20d", "Return 20D", "Twenty-day underlying return.", "technical", "percentage", "derived_metrics", "returns.20d", "run_context", ("candles",), "pct", cost="medium", req_market=True),
        _f("technical.return.60d", "Return 60D", "Sixty-day underlying return.", "technical", "percentage", "derived_metrics", "returns.60d", "run_context", ("candles",), "pct", cost="medium", req_market=True),
        _f("technical.distance_from_high_pct", "Distance From High", "Percent distance from recent high.", "technical", "percentage", "derived_metrics", "distance_from_high_pct", "run_context", ("candles",), "pct", cost="medium", req_market=True),
        _f("technical.distance_from_moving_average_pct", "Distance From Moving Average", "Percent distance from selected moving average.", "technical", "percentage", "derived_metrics", "distance_from_ma_pct", "run_context", ("candles",), "pct", cost="medium", req_market=True),
        _f("momentum.time_series_score", "Time-Series Momentum Score", "Internal time-series momentum score.", "momentum", "number", "strategy_input", "momentum.time_series_score", "strategy_input", ("candles",), "", cost="medium", req_market=True),
        _f("momentum.relative_strength_score", "Relative Strength Score", "Relative strength score versus benchmark or universe.", "momentum", "number", "strategy_input", "momentum.relative_strength_score", "strategy_input", ("candles", "benchmark_candles"), "", cost="medium", req_market=True),
        _f("momentum.cross_sectional_rank", "Cross-Sectional Rank", "Rank within comparison universe.", "momentum", "number", "strategy_input", "momentum.cross_sectional_rank", "strategy_input", ("candles", "benchmark_candles"), "", cost="medium", req_market=True),
        _f("momentum.extension_status", "Extension Status", "Momentum extension classification.", "momentum", "enum", "strategy_input", "momentum.extension_status", "strategy_input", ("candles",), enum=("normal", "extended", "overextended"), cost="medium", req_market=True),
        _f("momentum.direction", "Momentum Direction", "Directional momentum classification.", "momentum", "enum", "strategy_input", "momentum.direction", "strategy_input", ("candles",), enum=("bullish", "bearish", "neutral"), cost="medium", req_market=True),
        _f("earnings.date", "Earnings Date", "Normalized earnings event date.", "earnings", "date", "earnings", "earnings.date", "run_context", ("earnings_date",), cost="medium", req_earnings=True),
        _f("earnings.days_until", "Days Until Earnings", "Calendar days until earnings event.", "earnings", "duration_days", "earnings", "earnings.days_until", "run_context", ("earnings_date",), "days", cost="medium", req_earnings=True),
        _f("earnings.session", "Earnings Session", "Reported timing of earnings event.", "earnings", "enum", "earnings", "earnings.session", "run_context", ("earnings_date",), enum=("before_market_open", "after_market_close", "unknown"), cost="medium", req_earnings=True),
        _f("earnings.date_confidence", "Earnings Date Confidence", "Confidence or trust label for earnings date.", "earnings", "enum", "earnings", "earnings.date_confidence", "run_context", ("earnings_date",), enum=("multi_source_confirmed", "single_source_verify", "conflict_do_not_trade", "unknown_research_only"), cost="medium", req_earnings=True),
        _f("earnings.sources", "Earnings Sources", "Source names seen for earnings event.", "earnings", "list", "earnings", "earnings.sources", "run_context", ("earnings_date",), cost="medium", req_earnings=True, uses=("gate", "display")),
        _f("options.expiration", "Option Expiration", "Single option expiration date.", "options", "date", "options", "contract.expiration", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.front_expiration", "Front Expiration", "Nearer expiration in a multi-leg setup.", "options", "date", "options", "front.expiration", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.back_expiration", "Back Expiration", "Farther expiration in a calendar setup.", "options", "date", "options", "back.expiration", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.front_dte", "Front DTE", "Days to front expiration.", "options", "duration_days", "options", "front.dte", "strategy_input", ("options_chain",), "days", cost="high", req_options=True),
        _f("options.back_dte", "Back DTE", "Days to back expiration.", "options", "duration_days", "options", "back.dte", "strategy_input", ("options_chain",), "days", cost="high", req_options=True),
        _f("options.expiration_gap_days", "Expiration Gap", "Days between front and back expirations.", "options", "duration_days", "options", "expiration_gap_days", "strategy_input", ("options_chain",), "days", cost="high", req_options=True),
        _f("options.strike", "Option Strike", "Contract strike price.", "options", "currency", "options", "contract.strike", "strategy_input", ("options_chain",), "usd", cost="high", req_options=True),
        _f("options.option_type", "Option Type", "Call or put.", "options", "enum", "options", "contract.option_type", "strategy_input", ("options_chain",), enum=("call", "put"), cost="high", req_options=True),
        _f("options.delta", "Option Delta", "Contract delta supplied by provider.", "options", "number", "options", "contract.delta", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.long_leg_delta", "Long Leg Delta", "Delta for long option leg.", "options", "number", "options", "long_leg.delta", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.short_leg_delta", "Short Leg Delta", "Delta for short option leg.", "options", "number", "options", "short_leg.delta", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.iv", "Option IV", "Contract implied volatility.", "options", "percentage", "options", "contract.iv", "strategy_input", ("options_chain",), "decimal_iv", cost="high", req_options=True),
        _f("options.front_iv", "Front IV", "Front leg or expiration implied volatility.", "options", "percentage", "options", "front.iv", "strategy_input", ("options_chain",), "decimal_iv", cost="high", req_options=True),
        _f("options.back_iv", "Back IV", "Back leg or expiration implied volatility.", "options", "percentage", "options", "back.iv", "strategy_input", ("options_chain",), "decimal_iv", cost="high", req_options=True),
        _f("options.long_leg_iv", "Long Leg IV", "Long leg implied volatility.", "options", "percentage", "options", "long_leg.iv", "strategy_input", ("options_chain",), "decimal_iv", cost="high", req_options=True),
        _f("options.short_leg_iv", "Short Leg IV", "Short leg implied volatility.", "options", "percentage", "options", "short_leg.iv", "strategy_input", ("options_chain",), "decimal_iv", cost="high", req_options=True),
        _f("options.iv_difference", "IV Difference", "Front IV minus back IV or leg IV difference.", "options", "percentage", "options", "iv_difference", "strategy_input", ("options_chain",), "decimal_iv", cost="high", req_options=True),
        _f("options.skew", "Option Skew", "IV skew between selected legs.", "options", "percentage", "options", "skew", "strategy_input", ("options_chain",), "decimal_iv", cost="high", req_options=True),
        _f("options.skew_zscore", "Skew Z-Score", "Standardized skew value.", "options", "number", "options", "skew_zscore", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.debit", "Net Debit", "Estimated net debit for package.", "options", "currency", "options", "package.debit", "strategy_input", ("options_chain",), "usd", cost="high", req_options=True),
        _f("options.max_profit", "Max Profit", "Modeled max profit where applicable.", "options", "currency", "options", "max_profit", "strategy_input", ("options_chain",), "usd", cost="high", req_options=True),
        _f("options.max_loss", "Max Loss", "Modeled max loss where applicable.", "options", "currency", "options", "max_loss", "strategy_input", ("options_chain",), "usd", cost="high", req_options=True),
        _f("options.reward_risk_ratio", "Reward/Risk Ratio", "Estimated reward to risk ratio.", "options", "number", "options", "reward_risk_ratio", "strategy_input", ("options_chain",), cost="high", req_options=True),
        _f("options.bid", "Option Bid", "Contract bid.", "options", "currency", "options", "contract.bid", "strategy_input", ("options_chain",), "usd", cost="high", req_options=True),
        _f("options.ask", "Option Ask", "Contract ask.", "options", "currency", "options", "contract.ask", "strategy_input", ("options_chain",), "usd", cost="high", req_options=True),
        _f("options.mid", "Option Mid", "Contract mid price.", "options", "currency", "options", "contract.mid", "strategy_input", ("options_chain",), "usd", cost="high", req_options=True),
        _f("options.open_interest", "Open Interest", "Contract open interest.", "options", "integer", "options", "contract.open_interest", "strategy_input", ("options_chain",), "contracts", cost="high", req_options=True),
        _f("options.volume", "Option Volume", "Contract volume.", "options", "integer", "options", "contract.volume", "strategy_input", ("options_chain",), "contracts", cost="high", req_options=True),
        _f("options.spread_pct", "Bid/Ask Spread Percent", "Relative option bid/ask spread.", "options", "percentage", "options", "contract.spread_pct", "strategy_input", ("options_chain",), "pct", cost="high", req_options=True),
        _f("options.short_leg_expires_before_earnings", "Short Leg Expires Before Earnings", "Whether short leg expires before event.", "options", "boolean", "options", "short_leg.expires_before_earnings", "strategy_input", ("options_chain", "earnings_date"), cost="high", req_options=True, req_earnings=True),
        _f("options.short_leg_spans_earnings", "Short Leg Spans Earnings", "Whether short leg spans or follows earnings.", "options", "boolean", "options", "short_leg.spans_earnings", "strategy_input", ("options_chain", "earnings_date"), cost="high", req_options=True, req_earnings=True),
        _f("volatility.front_iv", "FF Front IV", "Forward Factor front expiration IV.", "volatility", "percentage", "forward_factor", "front_iv", "strategy_input", ("options_chain_set",), "decimal_iv", cost="very_high", req_options=True),
        _f("volatility.back_iv", "FF Back IV", "Forward Factor back expiration IV.", "volatility", "percentage", "forward_factor", "back_iv", "strategy_input", ("options_chain_set",), "decimal_iv", cost="very_high", req_options=True),
        _f("volatility.forward_variance", "Forward Variance", "Calculated forward variance.", "volatility", "number", "forward_factor", "forward_variance", "strategy_output", ("options_chain_set",), cost="very_high", req_options=True, uses=("display", "post_process")),
        _f("volatility.forward_factor", "Forward Factor", "Forward Factor signal value.", "volatility", "number", "forward_factor", "forward_factor", "strategy_output", ("options_chain_set",), cost="very_high", req_options=True, uses=("display", "post_process")),
        _f("volatility.forward_factor_threshold", "Forward Factor Threshold", "Configured Forward Factor threshold.", "volatility", "number", "forward_factor", "threshold", "static", (), cost="none", uses=("display", "post_process")),
        _f("volatility.earnings_contaminated", "Earnings Contaminated", "Whether earnings contamination affects IV input.", "volatility", "boolean", "forward_factor", "earnings_contaminated", "strategy_output", ("earnings_date", "options_chain_set"), cost="very_high", req_options=True, req_earnings=True, uses=("display", "post_process")),
        _f("volatility.realized_20d", "Realized Volatility 20D", "Twenty-day realized volatility.", "volatility", "percentage", "derived_metrics", "realized_volatility_20d", "run_context", ("candles",), "decimal_vol", cost="medium", req_market=True),
        _f("volatility.iv_rank", "IV Rank", "Approximate IV rank or percentile.", "volatility", "percentage", "options", "iv_rank", "strategy_input", ("options_chain",), "pct", cost="high", req_options=True),
        _f("portfolio.has_position", "Has Position", "Whether portfolio already holds the symbol.", "portfolio", "boolean", "broker_snapshot", "portfolio.has_position", "broker_snapshot", ("broker_positions",), cost="none", req_broker=True),
        _f("portfolio.position_quantity", "Position Quantity", "Aggregate held quantity.", "portfolio", "number", "broker_snapshot", "portfolio.quantity", "broker_snapshot", ("broker_positions",), "shares", cost="none", req_broker=True),
        _f("portfolio.market_value", "Position Market Value", "Aggregate position market value.", "portfolio", "currency", "broker_snapshot", "portfolio.market_value", "broker_snapshot", ("broker_positions",), "usd", cost="none", req_broker=True),
        _f("portfolio.exposure_pct", "Portfolio Exposure", "Position exposure as percent of portfolio.", "portfolio", "percentage", "broker_snapshot", "portfolio.exposure_pct", "broker_snapshot", ("portfolio_summary",), "pct", cost="none", req_broker=True),
        _f("position.structure_type", "Position Structure Type", "Detected open option structure type.", "position", "enum", "lifecycle", "position.structure_type", "lifecycle", ("open_option_positions",), enum=("calendar", "vertical", "double_calendar", "single_leg"), cost="none", req_broker=True, uses=("display", "post_process")),
        _f("position.current_debit", "Current Debit", "Current debit for open option structure.", "position", "currency", "lifecycle", "position.current_debit", "lifecycle", ("open_option_positions",), "usd", cost="none", req_broker=True, uses=("display", "post_process")),
        _f("strategy.verdict", "Strategy Verdict", "Verdict emitted by a strategy row.", "strategy", "string", "strategy_row", "strategy.verdict", "strategy_output", (), cost="none", uses=("display", "post_process")),
        _f("strategy.score", "Strategy Score", "Score emitted by a strategy row.", "strategy", "number", "strategy_row", "strategy.score", "strategy_output", (), cost="none", uses=("display", "post_process")),
        _f("data_quality.status", "Data Quality Status", "Normalized data quality status.", "data_quality", "enum", "data_quality", "status", "strategy_input", (), enum=("complete", "partial", "stale", "unavailable"), cost="none", uses=("gate", "verdict", "display")),
        _f("data_quality.source_qualified", "Source Qualified", "Whether source-required inputs are available.", "data_quality", "boolean", "strategy_row", "source_qualified", "strategy_output", (), cost="none", uses=("display", "post_process")),
    ]
    return {field.field_id: field for field in fields}


def _f(
    field_id: str,
    display_name: str,
    description: str,
    category: str,
    value_type: str,
    source_domain: str,
    source_path: str,
    availability_stage: str,
    requirement_types: tuple[str, ...],
    unit: str = "",
    *,
    enum: tuple[str, ...] = (),
    cost: str = "none",
    uses: tuple[str, ...] = ("gate", "score", "display"),
    req_market: bool = False,
    req_options: bool = False,
    req_earnings: bool = False,
    req_broker: bool = False,
) -> StrategyFieldDefinition:
    return StrategyFieldDefinition(
        field_id=field_id,
        display_name=display_name,
        description=description,
        category=category,
        value_type=value_type,
        nullable=True,
        source_domain=source_domain,
        source_path=source_path,
        availability_stage=availability_stage,
        supported_asset_types=("equity", "etf"),
        supported_strategy_types=("stock", "options"),
        allowed_uses=uses,
        allowed_operators=OPERATOR_SETS[value_type],
        requirement_types=requirement_types,
        unit=unit,
        enum_values=enum,
        default_missing_behavior="mark_data_needed" if req_options or req_earnings or req_broker else "fail_gate",
        requires_market_data=req_market,
        requires_options_data=req_options,
        requires_earnings_data=req_earnings,
        requires_broker_data=req_broker,
        provider_cost_class=cost,
        sensitivity="portfolio_fact" if req_broker else "public_market_data",
        examples=(),
    )


def build_catalog_response(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    fields = list(field_catalog().values())
    filtered = [_field.to_dict() for _field in _apply_filters(fields, filters or {})]
    return {
        **READ_ONLY_BASE,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "strategy_row_schema_version": STRATEGY_ROW_SCHEMA_VERSION,
        "minimum_supported_strategy_row_schema_version": MINIMUM_SUPPORTED_STRATEGY_ROW_SCHEMA_VERSION,
        "semantic_fields_version": SEMANTIC_FIELDS_VERSION,
        "field_count": len(filtered),
        "total_field_count": len(fields),
        "categories": sorted({field.category for field in fields}),
        "value_types": sorted(VALUE_TYPES),
        "operators": sorted({op for ops in OPERATOR_SETS.values() for op in ops}),
        "fields": filtered,
    }


def get_field(field_id: str) -> dict[str, Any]:
    field = field_catalog().get(field_id)
    if not field:
        return {**READ_ONLY_BASE, "error": {"code": "FIELD_NOT_FOUND", "field_id": field_id}}, 404
    return {
        **READ_ONLY_BASE,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "field": field.to_dict(),
        "allowed_operators": list(field.allowed_operators),
        "requirements": list(field.requirement_types),
        "deprecated": field.deprecated,
    }, 200


def operator_catalog() -> dict[str, Any]:
    return {
        **READ_ONLY_BASE,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "operator_sets": {key: list(value) for key, value in OPERATOR_SETS.items()},
        "operators": sorted({op for ops in OPERATOR_SETS.values() for op in ops}),
    }


def requirements_for_fields(field_ids: list[str]) -> dict[str, Any]:
    catalog = field_catalog()
    grouped: dict[str, list[str]] = defaultdict(list)
    missing: list[str] = []
    max_cost = "none"
    cost_rank = ["none", "low", "medium", "high", "very_high"]
    for field_id in field_ids:
        field = catalog.get(field_id)
        if not field:
            missing.append(field_id)
            continue
        if cost_rank.index(field.provider_cost_class) > cost_rank.index(max_cost):
            max_cost = field.provider_cost_class
        for req in field.requirement_types:
            grouped[req].append(field_id)
    return {
        **READ_ONLY_BASE,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "requirements": [
            {
                "requirement_type": req,
                "fields": sorted(fields),
                **({"minimum_bars": 240} if req in {"candles", "benchmark_candles"} else {}),
            }
            for req, fields in sorted(grouped.items())
        ],
        "provider_cost_estimate": max_cost,
        "missing_fields": missing,
    }


def validate_rule_definition(rule: dict[str, Any]) -> dict[str, Any]:
    field_id = str((rule or {}).get("field_id") or "")
    operator = str((rule or {}).get("operator") or "")
    use = str((rule or {}).get("use") or "gate")
    value = (rule or {}).get("value")
    catalog = field_catalog()
    field = catalog.get(field_id)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if not field:
        errors.append({"code": "FIELD_NOT_FOUND", "field_id": field_id, "operator": operator, "message": "Unknown field_id."})
        return _validation_response(False, rule, errors, warnings, [])
    if operator not in field.allowed_operators:
        errors.append({
            "code": "OPERATOR_NOT_ALLOWED",
            "field_id": field_id,
            "operator": operator,
            "message": "Operator is not allowed for this field.",
            "allowed_operators": list(field.allowed_operators),
            "expected_value_type": field.value_type,
        })
    if use not in field.allowed_uses:
        errors.append({
            "code": "USE_NOT_ALLOWED",
            "field_id": field_id,
            "operator": operator,
            "message": "Field is not allowed for this use.",
            "allowed_uses": list(field.allowed_uses),
        })
    if use in {"universe_filter", "data_requirement", "gate", "score", "verdict"} and field.availability_stage in {"strategy_output", "lifecycle"}:
        errors.append({
            "code": "CIRCULAR_FIELD_USE",
            "field_id": field_id,
            "operator": operator,
            "message": "Strategy-output/lifecycle fields cannot be used as pre-run input gates.",
        })
    value_error = _validate_value_type(field, operator, value)
    if value_error:
        errors.append(value_error)
    normalized = {"field_id": field_id, "operator": operator, "value": value, "use": use}
    return _validation_response(not errors, normalized, errors, warnings, list(field.requirement_types))


def _validation_response(valid: bool, normalized_rule: dict[str, Any], errors: list[dict[str, Any]], warnings: list[dict[str, Any]], requirements: list[str]) -> dict[str, Any]:
    return {
        **READ_ONLY_BASE,
        "catalog_schema_version": CATALOG_SCHEMA_VERSION,
        "valid": valid,
        "normalized_rule": normalized_rule if valid else None,
        "errors": errors,
        "warnings": warnings,
        "requirements": requirements,
    }


def _validate_value_type(field: StrategyFieldDefinition, operator: str, value: Any) -> dict[str, Any] | None:
    if operator in {"exists", "not_exists", "is_true", "is_false", "is_empty", "is_not_empty"}:
        return None
    if operator in {"within_next_days", "within_previous_days"}:
        ok = isinstance(value, int) and not isinstance(value, bool)
        if ok:
            return None
        return {
            "code": "INVALID_VALUE_TYPE",
            "field_id": field.field_id,
            "operator": operator,
            "message": "Date window operators require an integer day count.",
            "expected_value_type": "integer",
            "received_value_type": type(value).__name__,
        }
    if operator in {"between", "outside"}:
        ok = isinstance(value, list) and len(value) == 2
    elif operator in {"in", "not_in", "contains_any", "contains_all"}:
        ok = isinstance(value, list)
    elif field.value_type in {"number", "percentage", "currency", "duration_days"}:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif field.value_type == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif field.value_type == "boolean":
        ok = isinstance(value, bool)
    elif field.value_type == "list":
        ok = isinstance(value, list) or operator in {"contains", "not_contains"}
    else:
        ok = isinstance(value, str)
    if ok:
        return None
    return {
        "code": "INVALID_VALUE_TYPE",
        "field_id": field.field_id,
        "operator": operator,
        "message": "Rule value does not match expected type for this field/operator.",
        "expected_value_type": field.value_type,
        "received_value_type": type(value).__name__,
    }


def _apply_filters(fields: list[StrategyFieldDefinition], filters: dict[str, Any]) -> list[StrategyFieldDefinition]:
    output = fields
    if filters.get("category"):
        output = [field for field in output if field.category == filters["category"]]
    if filters.get("value_type"):
        output = [field for field in output if field.value_type == filters["value_type"]]
    if filters.get("allowed_use"):
        output = [field for field in output if filters["allowed_use"] in field.allowed_uses]
    if filters.get("strategy_type"):
        output = [field for field in output if filters["strategy_type"] in field.supported_strategy_types]
    if filters.get("asset_type"):
        output = [field for field in output if filters["asset_type"] in field.supported_asset_types]
    for key in ("requires_options_data", "requires_market_data", "requires_earnings_data", "requires_broker_data"):
        if key in filters:
            wanted = str(filters[key]).lower() in {"1", "true", "yes"}
            output = [field for field in output if getattr(field, key) is wanted]
    return sorted(output, key=lambda field: field.field_id)


def catalog_diagnostics() -> dict[str, Any]:
    fields = list(field_catalog().values())
    return {
        "field_count": len(fields),
        "field_count_by_category": _count(fields, "category"),
        "field_count_by_value_type": _count(fields, "value_type"),
        "requirement_type_count": len({req for field in fields for req in field.requirement_types}),
        "operator_count": len({op for field in fields for op in field.allowed_operators}),
        "built_in_strategy_coverage": {
            strategy_id: {
                "required_count": len(required),
                "covered_count": len(required & set(field_catalog())),
                "missing": sorted(required - set(field_catalog())),
            }
            for strategy_id, required in BUILT_IN_STRATEGY_FIELDS.items()
        },
    }


def _count(fields: list[StrategyFieldDefinition], attr: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for field in fields:
        value = str(getattr(field, attr))
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))
