"""Map legacy loose-dict strategy rows into canonical opportunities."""

from __future__ import annotations

from typing import Any

from app.models.strategy_opportunity_models import (
    ExpirationPair,
    StrategyDataLineage,
    StrategyGate,
    StrategyLeg,
    StrategyOpportunity,
    StrategyPipelineTrace,
)

_VERDICT_TIER = {"PASS": 100, "WATCH": 80, "NEAR_MISS": 60, "FAIL": 35, "SKIP": 10}


def normalize_legacy_strategy_row(
    strategy_id: str, row: dict[str, Any], run_id: str | None = None
) -> StrategyOpportunity:
    """Normalize a legacy row without ever raising to the caller."""
    try:
        return _normalize(strategy_id, row, run_id)
    except Exception as exc:
        safe_row = row if isinstance(row, dict) else {}
        return _minimal_opportunity(strategy_id, safe_row, run_id, exc)


def _minimal_opportunity(
    strategy_id: str, row: dict, run_id: str | None, exc: Exception
) -> StrategyOpportunity:
    return StrategyOpportunity(
        strategy_id=strategy_id,
        strategy_version="unknown",
        ticker=str(row.get("ticker") or "UNKNOWN"),
        run_id=run_id,
        verdict=str(row.get("verdict") or "UNKNOWN"),
        verdict_tier=35,
        score=None,
        actionability_score=None,
        reason_code="NORMALIZATION_ERROR",
        reason_label=f"Failed to normalize row: {exc}",
        blockers=[str(exc)],
        warnings=[],
        structure_type=None,
        legs=[],
        expiration_pair=None,
        debit=None,
        credit=None,
        max_risk=None,
        max_reward=None,
        slippage_pct=None,
        edge_on_margin=None,
        iv_percentile=None,
        iv_edge=None,
        liquidity_status=None,
        bid_ask_spread_pct=None,
        open_interest=None,
        source_mode="unknown",
        can_trade_live=False,
        can_enter_daily_opportunity=False,
        stale_structure=None,
        stale_structure_note=None,
        data_lineage=None,
        pipeline_trace=None,
        gates=[],
        raw=dict(row),
    )


def _normalize(strategy_id: str, row: dict, run_id: str | None) -> StrategyOpportunity:
    ticker = str(row.get("ticker") or "UNKNOWN")
    verdict = str(row.get("verdict") or "UNKNOWN")
    verdict_tier = _VERDICT_TIER.get(verdict.split("/")[0].strip().upper(), 35)
    spread = row.get("possible_spread") or row.get("spread") or {}

    return StrategyOpportunity(
        strategy_id=strategy_id,
        strategy_version=str(row.get("strategy_version") or "v1"),
        ticker=ticker,
        run_id=run_id,
        verdict=verdict,
        verdict_tier=verdict_tier,
        score=_float_or_none(row.get("score") or row.get("signal_score")),
        actionability_score=_float_or_none(row.get("actionability_score")),
        reason_code=str(row.get("reason_code") or row.get("primary_blocker") or ""),
        reason_label=str(row.get("reason_label") or row.get("main_blocker") or ""),
        blockers=list(row.get("blockers") or []),
        warnings=list(row.get("warnings") or []),
        structure_type=_extract_structure_type(strategy_id, row),
        legs=_extract_legs(row),
        expiration_pair=_extract_expiration_pair(row),
        debit=_float_or_none(spread.get("net_debit") or spread.get("conservative_debit") or row.get("debit")),
        credit=_float_or_none(spread.get("net_credit") or row.get("credit")),
        max_risk=_float_or_none(spread.get("max_risk") or row.get("max_risk")),
        max_reward=_float_or_none(spread.get("max_reward") or row.get("max_reward")),
        slippage_pct=_float_or_none(spread.get("slippage_pct") or row.get("package_slippage_pct")),
        edge_on_margin=_float_or_none(row.get("edge_on_margin")),
        iv_percentile=_float_or_none(row.get("iv_percentile")),
        iv_edge=_float_or_none(spread.get("iv_edge") or row.get("iv_edge") or row.get("forward_factor")),
        liquidity_status=str(row.get("liquidity_status") or ""),
        bid_ask_spread_pct=_float_or_none(spread.get("bid_ask_spread_pct") or row.get("bid_ask_spread_pct")),
        open_interest=_int_or_none(row.get("open_interest")),
        source_mode=_extract_source_mode(strategy_id, row),
        can_trade_live=bool(row.get("can_trade_live", False)),
        can_enter_daily_opportunity=bool(row.get("can_enter_daily_opportunity", False)),
        stale_structure=_bool_or_none(row.get("stale_structure")),
        stale_structure_note=str(row.get("stale_structure_note") or ""),
        data_lineage=_extract_data_lineage(row),
        pipeline_trace=_extract_pipeline_trace(row),
        gates=_extract_gates(row),
        raw=dict(row),
    )


def _extract_source_mode(strategy_id: str, row: dict) -> str:
    if row.get("source_mode"):
        return str(row["source_mode"])
    if strategy_id == "forward_factor_calendar":
        if row.get("is_source_qualified") or row.get("source_qualified"):
            return "source_qualified"
        if row.get("diagnostic_model") or row.get("diagnostic_only") or row.get("earnings_contaminated"):
            return "diagnostic"
    return "unknown"


def _extract_expiration_pair(row: dict) -> ExpirationPair | None:
    quality_precheck = row.get("quality_precheck") or {}
    pair = quality_precheck.get("expiration_pair") or row.get("expiration_pair")
    if isinstance(pair, dict):
        try:
            return ExpirationPair.from_dict(pair)
        except Exception:
            pass
    front = quality_precheck.get("front_expiration") or row.get("front_expiration")
    back = quality_precheck.get("back_expiration") or row.get("back_expiration")
    if not (front and back):
        return None
    return ExpirationPair(
        front_expiration=str(front),
        back_expiration=str(back),
        front_dte=_int_or_none(quality_precheck.get("front_dte")) or 0,
        back_dte=_int_or_none(quality_precheck.get("back_dte")) or 0,
        earnings_date=quality_precheck.get("earnings_date"),
        days_to_earnings=_int_or_none(quality_precheck.get("days_to_earnings")),
        front_before_earnings=bool(quality_precheck.get("front_before_earnings")),
        gap_days=_int_or_none(quality_precheck.get("gap_days")),
        is_near_miss=bool(quality_precheck.get("expiry_near_miss")),
        selection_method="reconstructed_from_fields",
    )


def _extract_legs(row: dict) -> list[StrategyLeg]:
    legs = []
    for leg in row.get("legs") or []:
        if not isinstance(leg, dict):
            continue
        try:
            legs.append(StrategyLeg(
                leg_id=str(leg.get("leg_id") or leg.get("position") or ""),
                position=str(leg.get("position") or leg.get("side") or ""),
                option_type=str(leg.get("option_type") or ""),
                strike=_float_or_none(leg.get("strike")),
                expiration=str(leg.get("expiration") or leg.get("expiration_date") or ""),
                dte=_int_or_none(leg.get("dte")),
                bid=_float_or_none(leg.get("bid")),
                ask=_float_or_none(leg.get("ask")),
                mid=_float_or_none(leg.get("mid") or leg.get("current_price")),
                iv=_float_or_none(leg.get("iv") or leg.get("implied_volatility")),
                delta=_float_or_none(leg.get("delta")),
                open_interest=_int_or_none(leg.get("open_interest")),
                volume=_int_or_none(leg.get("volume")),
                current_price=_float_or_none(leg.get("current_price") or leg.get("mid")),
                average_price=_float_or_none(leg.get("average_price") or leg.get("avg_cost_per_share")),
            ))
        except Exception:
            continue
    return legs


def _extract_gates(row: dict) -> list[StrategyGate]:
    gates = []
    for requirement in row.get("requirements") or row.get("checks") or []:
        if not isinstance(requirement, dict):
            continue
        try:
            gates.append(StrategyGate(
                name=str(requirement.get("name") or requirement.get("check") or ""),
                status=str(requirement.get("status") or requirement.get("result") or ""),
                detail=str(requirement.get("detail") or requirement.get("message") or ""),
                is_hard_block=bool(requirement.get("is_hard_block") or requirement.get("blocks")),
                value=requirement.get("value"),
            ))
        except Exception:
            continue
    return gates


def _extract_data_lineage(row: dict) -> StrategyDataLineage | None:
    earnings = row.get("earnings") or {}
    quality_precheck = row.get("quality_precheck") or {}
    confidence = (row.get("date_confidence") or quality_precheck.get("date_confidence")
                  or earnings.get("date_confidence") or earnings.get("earnings_date_confidence"))
    if not confidence and not earnings:
        return None
    return StrategyDataLineage(
        earnings_date=earnings.get("earnings_date") or earnings.get("date") or quality_precheck.get("earnings_date"),
        earnings_date_confidence=str(confidence or "unknown"),
        earnings_date_sources=list(earnings.get("sources_seen") or row.get("date_sources") or []),
        earnings_date_conflict=bool(row.get("date_conflict") or earnings.get("date_conflict")),
        conflicting_dates=list(row.get("conflicting_dates") or earnings.get("conflicting_dates") or []),
        source_call_log=dict(row.get("source_call_log") or {}),
        iv_source=str(row.get("iv_source") or row.get("front_iv_derivation_method") or ""),
        price_source=str(row.get("price_source") or "tradier"),
        volume_source=str(row.get("volume_source") or "tradier"),
        data_as_of=str(row.get("data_as_of") or ""),
    )


def _extract_pipeline_trace(row: dict) -> StrategyPipelineTrace | None:
    trace = row.get("pipeline_trace") or row.get("_pipeline_trace")
    if not isinstance(trace, dict):
        return None
    return StrategyPipelineTrace(
        stages=dict(trace.get("stages") or {}),
        stage_details=dict(trace.get("stage_details") or {}),
        removed_at_stage=trace.get("removed_at_stage"),
        removal_reason=trace.get("removal_reason"),
        prescreen_stats=trace.get("prescreen_stats"),
    )


def _extract_structure_type(strategy_id: str, row: dict) -> str | None:
    if row.get("structure_type"):
        return str(row["structure_type"])
    return {"earnings_calendar": "calendar", "forward_factor_calendar": "double_calendar",
            "skew_momentum_vertical": "vertical"}.get(strategy_id)


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    return None if value is None else bool(value)
