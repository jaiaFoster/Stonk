"""Explicit local strategy registry. No dynamic plugin packages."""

from __future__ import annotations

from typing import Any

from app.strategies.adapters import EarningsCalendarStrategy, ForwardFactorCalendarStrategy, SkewMomentumVerticalStrategy, StockMomentumStrategy

STRATEGY_REGISTRY = [EarningsCalendarStrategy(), SkewMomentumVerticalStrategy(), ForwardFactorCalendarStrategy(), StockMomentumStrategy()]


def enabled_strategies() -> list[Any]:
    return [strategy for strategy in STRATEGY_REGISTRY if strategy.is_enabled()]


def collect_requirements(context: Any, log_print=None) -> list[Any]:
    log = log_print or (lambda message: None)
    requirements = []
    for strategy in enabled_strategies():
        universe = strategy.build_universe(context)
        if strategy.strategy_id == "forward_factor_calendar":
            log(f"FF adapter universe count={len(universe)} tickers={universe[:10]}")
        requirements.append(strategy.data_requirements(context, universe))
    return requirements


def normalize_strategy_results(context: Any, raw_results: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    normalized: dict[str, dict[str, Any]] = {}
    for strategy in enabled_strategies():
        try:
            normalized[strategy.strategy_id] = strategy.normalize_result(raw_results.get(strategy.strategy_id, {}), context).to_dict()
        except Exception as exc:
            normalized[strategy.strategy_id] = {
                "strategy_id": strategy.strategy_id, "strategy_label": strategy.strategy_label, "version": strategy.version,
                "enabled": True, "ran": False, "rows": [], "active_rows": [], "pass_count": 0, "watch_count": 0,
                "fail_count": 0, "skipped_count": 0, "scanned_tickers": [], "data_coverage": {},
                "provider_notes": [], "errors": [str(exc)], "summary": {},
            }
    return normalized
