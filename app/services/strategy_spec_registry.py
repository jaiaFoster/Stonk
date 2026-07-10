"""ASA Strategy Spec Registry — 30A.

One central place to inspect each strategy's goal, required inputs, gate IDs,
output types, dry-run policy, and Daily Opportunity eligibility.

This registry is intentionally read-only. Strategies are added here by humans;
they are not self-registered at runtime. This makes the registry a stable source
of truth for the normalization layer, developer tools, and 30B journal setup.

Usage:
    from app.services.strategy_spec_registry import get_spec, all_strategy_ids
    spec = get_spec("forward_factor_calendar")
    print(spec["daily_opportunity_allowed"])  # False
"""

from __future__ import annotations

from typing import Any

from app.services.strategy_row_schema import (
    STRATEGY_ROW_SCHEMA_VERSION,
    STRATEGY_FAMILY_OPTIONS_EVENT,
    STRATEGY_FAMILY_OPTIONS_SKEW,
    STRATEGY_FAMILY_OPTIONS_FORWARD,
    STRATEGY_FAMILY_EQUITY_MOMENTUM,
)

# ─── Registry ─────────────────────────────────────────────────────────────────

STRATEGY_SPECS: dict[str, dict[str, Any]] = {
    "earnings_calendar": {
        "strategy_id": "earnings_calendar",
        "strategy_name": "Earnings Calendar Spread",
        "strategy_family": STRATEGY_FAMILY_OPTIONS_EVENT,
        "strategy_goal": (
            "Find liquid earnings calendar spreads with favorable event-volatility setup, "
            "confirmed earnings date, and clean expiration structure."
        ),
        "status": "active",
        "dry_run": False,
        "daily_opportunity_allowed": True,
        "requires_broker_positions": False,
        "requires_options_chain": True,
        "requires_earnings_date": True,
        "primary_outputs": [
            "calendar_spread_candidate",
            "near_miss",
            "open_trade_lifecycle",
        ],
        "gate_ids": [
            "earnings_date",
            "expiration_pair",
            "iv_relationship",
            "liquidity",
            "debit_risk",
            "structure",
        ],
        "inputs_required": [
            "options_chain",
            "earnings_date",
            "iv_data",
            "debit_estimate",
            "liquidity_metrics",
        ],
        "schema_version": STRATEGY_ROW_SCHEMA_VERSION,
    },

    "skew_momentum_vertical": {
        "strategy_id": "skew_momentum_vertical",
        "strategy_name": "Skew Momentum Vertical",
        "strategy_family": STRATEGY_FAMILY_OPTIONS_SKEW,
        "strategy_goal": (
            "Find directional vertical spreads when momentum is confirmed "
            "and short-wing skew provides meaningful premium financing."
        ),
        "status": "active",
        "dry_run": False,
        "daily_opportunity_allowed": True,
        "requires_broker_positions": False,
        "requires_options_chain": True,
        "requires_earnings_date": False,
        "primary_outputs": [
            "vertical_spread_candidate",
        ],
        "gate_ids": [
            "momentum",
            "skew",
            "liquidity",
            "reward_risk",
            "earnings_risk",
            "structure",
        ],
        "inputs_required": [
            "options_chain",
            "momentum_metrics",
            "iv_skew_data",
            "liquidity_metrics",
        ],
        "schema_version": STRATEGY_ROW_SCHEMA_VERSION,
    },

    "forward_factor_calendar": {
        "strategy_id": "forward_factor_calendar",
        "strategy_name": "Forward Factor Calendar",
        "strategy_family": STRATEGY_FAMILY_OPTIONS_FORWARD,
        "strategy_goal": (
            "Identify calendar spreads where front/back IV divergence creates a structural edge. "
            "PASS and WATCH are research signals; all execution remains dry-run."
        ),
        "description": (
            "Evaluates double-calendar spreads using Forward Factor (FF) — the ratio of implied "
            "forward variance to total back variance. A positive FF above threshold indicates the "
            "market prices a term-structure dislocation exploitable via calendar spread. "
            "Four-tier verdict: PASS / WATCH / NEAR MISS / FAIL. Dry-run only."
        ),
        "status": "dry_run",
        "dry_run": True,
        "catalog_visible": True,
        "daily_opportunity_allowed": True,
        "display_order": 3,
        "tags": ["options", "calendar", "volatility", "dry_run", "forward_factor"],
        "requires_broker_positions": False,
        "requires_options_chain": True,
        "requires_earnings_date": True,
        "primary_outputs": [
            "forward_factor_signal",
            "forward_factor_watch",
            "forward_factor_near_miss",
            "diagnostic_signal",
        ],
        "gate_ids": [
            "cheap_filter",
            "source_qualification",
            "chain_approval",
            "expiration_pair",
            "structure",
            "liquidity",
            "earnings_contamination",
            "execution",
        ],
        "inputs_required": [
            "options_chain",
            "iv_source_data",
            "earnings_date",
            "forward_factor_model",
        ],
        "schema_version": STRATEGY_ROW_SCHEMA_VERSION,
    },

    "stock_momentum": {
        "strategy_id": "stock_momentum",
        "strategy_name": "Stock Momentum",
        "strategy_family": STRATEGY_FAMILY_EQUITY_MOMENTUM,
        "strategy_goal": (
            "Score stocks on trend, momentum, and relative strength to identify "
            "portfolio add candidates, holds, and names to avoid."
        ),
        "status": "active",
        "dry_run": False,
        "daily_opportunity_allowed": True,
        "requires_broker_positions": False,
        "requires_options_chain": False,
        "requires_earnings_date": False,
        "primary_outputs": [
            "stock_add_candidate",
            "watchlist_hold",
            "avoid",
        ],
        "gate_ids": [
            "trend",
            "momentum",
            "relative_strength",
            "volume",
            "risk",
        ],
        "inputs_required": [
            "price_data",
            "sma_data",
            "return_metrics",
            "volume_metrics",
            "relative_strength_data",
        ],
        "schema_version": STRATEGY_ROW_SCHEMA_VERSION,
    },
}


# ─── Accessors ────────────────────────────────────────────────────────────────

def get_spec(strategy_id: str) -> dict[str, Any] | None:
    """Return the spec for a strategy_id, or None if not registered."""
    return STRATEGY_SPECS.get(str(strategy_id or ""))


def all_strategy_ids() -> list[str]:
    """Return all registered strategy IDs in stable order."""
    return list(STRATEGY_SPECS.keys())


def all_specs() -> list[dict[str, Any]]:
    """Return all strategy specs as a list."""
    return list(STRATEGY_SPECS.values())


def is_daily_opportunity_allowed(strategy_id: str) -> bool:
    """Return True if the strategy spec allows Daily Opportunity participation."""
    spec = get_spec(strategy_id)
    return bool(spec and spec.get("daily_opportunity_allowed"))


def is_dry_run(strategy_id: str) -> bool:
    """Return True if the strategy is in dry-run / research mode."""
    spec = get_spec(strategy_id)
    return bool(spec and spec.get("dry_run"))
