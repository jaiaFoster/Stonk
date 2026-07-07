"""Explicit local strategy registry. No dynamic plugin packages."""

from __future__ import annotations

from typing import Any

from app.strategies.adapters import EarningsCalendarStrategy, ForwardFactorCalendarStrategy, SkewMomentumVerticalStrategy, StockMomentumStrategy

STRATEGY_REGISTRY = [EarningsCalendarStrategy(), SkewMomentumVerticalStrategy(), ForwardFactorCalendarStrategy(), StockMomentumStrategy()]

# ─── Unified spec registry (read-only metadata dict) ──────────────────────────
# STRATEGY_SPEC_REGISTRY is a dict-based complement to the list-based STRATEGY_REGISTRY.
# It includes the four production strategies plus the test clone, keyed by strategy_id.
# Use this for developer tools, API endpoints, and universal row consumers.
# CAVEMAN: Adding an entry here does not enable a strategy or grant Daily Opportunity access.


def _build_spec_registry() -> dict[str, dict[str, Any]]:
    from app.services.strategy_spec_registry import STRATEGY_SPECS
    from app.strategies.schema import SCHEMA_VERSION
    registry: dict[str, dict[str, Any]] = {**STRATEGY_SPECS}
    registry["stock_momentum_unified_test"] = {
        "strategy_id": "stock_momentum_unified_test",
        "strategy_name": "Stock Momentum Unified (Test Clone)",
        "strategy_family": "equity_momentum",
        "strategy_goal": (
            "Test clone of stock_momentum that normalizes rows into the universal "
            "strategy row schema. Never runs in production; is_enabled() is False."
        ),
        "status": "test",
        "dry_run": True,
        "daily_opportunity_allowed": False,
        "requires_broker_positions": False,
        "requires_options_chain": False,
        "requires_earnings_date": False,
        "primary_outputs": ["test_candidate"],
        "gate_ids": ["trend", "momentum", "relative_strength", "volume", "risk"],
        "inputs_required": ["price_data", "sma_data", "return_metrics", "volume_metrics"],
        "schema_version": SCHEMA_VERSION,
    }
    return registry


STRATEGY_SPEC_REGISTRY: dict[str, dict[str, Any]] = _build_spec_registry()


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
    _PASSTHROUGH_KEYS = {"ff_journal"}
    normalized: dict[str, dict[str, Any]] = {}
    for strategy in enabled_strategies():
        try:
            raw = raw_results.get(strategy.strategy_id, {})
            result = strategy.normalize_result(raw, context).to_dict()
            for key in _PASSTHROUGH_KEYS:
                if key in raw:
                    result[key] = raw[key]
            normalized[strategy.strategy_id] = result
        except Exception as exc:
            normalized[strategy.strategy_id] = {
                "strategy_id": strategy.strategy_id, "strategy_label": strategy.strategy_label, "version": strategy.version,
                "enabled": True, "ran": False, "rows": [], "active_rows": [], "pass_count": 0, "watch_count": 0,
                "fail_count": 0, "skipped_count": 0, "scanned_tickers": [], "data_coverage": {},
                "provider_notes": [], "errors": [str(exc)], "summary": {},
            }
    return normalized
