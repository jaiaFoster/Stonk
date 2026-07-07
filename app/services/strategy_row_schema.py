"""Universal strategy row schema constants and field documentation — ASA 30A.

This module defines the canonical field names and schema version for unified
strategy rows. All four strategies (earnings_calendar, skew_momentum_vertical,
forward_factor_calendar, stock_momentum) normalize their output to this shape.

Pattern: strategy engines call normalize_strategy_row(row, strategy_id) at
the end of their row-building logic. Legacy fields are preserved; normalized
fields are added alongside them.
"""

from __future__ import annotations

from typing import Final

# Schema version — bump when the normalized field contract changes.
STRATEGY_ROW_SCHEMA_VERSION: Final[str] = "30A.v1"

# Canonical top-level field names all normalized rows carry.
# These fields are guaranteed present after normalization.
CANONICAL_REQUIRED_FIELDS: Final[tuple[str, ...]] = (
    "strategy_id",
    "strategy_name",
    "strategy_family",
    "strategy_goal",
    "ticker",
    "verdict",
    "friendly_verdict",
    "primary_reason",
    "gates",
    "metrics",
    "data_quality",
    "daily_opportunity_eligible",
    "daily_opportunity_reason",
    "can_trade_live",
    "dry_run",
    "journal_eligible",
    "observation_key",
    "strategy_row_schema_version",
)

# Fields excluded from compact normalized summaries (too large or sensitive).
# This mirrors _STRATEGY_SUMMARY_EXCLUDE in developer_snapshot_service but
# applies specifically to normalized row compaction.
NORMALIZED_ROW_EXCLUDE: Final[frozenset[str]] = frozenset({
    "observation_history",
    "ff_journal",
    "raw_chain_data",
    "raw_json",
    "raw_provider_payload",
    "full_chain",
    "options_chain",
    "chain_snapshot",
    "provider_payload",
    "debug_trace",
    "lifecycle_log_full",
    "payload",
    "scenario_grid",
    "candidate_selection_audit",
    "criteria",        # raw calendar criteria list — large
    "requirements",    # raw skew requirements — large
    "ff_journal_refs", # FF raw journal references — large
    "source_row",      # original pre-normalization row — redundant in compact
})

# Strategy families — stable identifiers for grouping strategies by type.
STRATEGY_FAMILY_OPTIONS_EVENT: Final[str] = "options_event_volatility"
STRATEGY_FAMILY_OPTIONS_SKEW: Final[str] = "options_skew_momentum"
STRATEGY_FAMILY_OPTIONS_FORWARD: Final[str] = "options_forward_volatility"
STRATEGY_FAMILY_EQUITY_MOMENTUM: Final[str] = "equity_momentum"

# Candidate types — what kind of opportunity a row represents.
CANDIDATE_TYPE_CALENDAR_SPREAD: Final[str] = "calendar_spread_candidate"
CANDIDATE_TYPE_NEAR_MISS: Final[str] = "near_miss"
CANDIDATE_TYPE_VERTICAL_SPREAD: Final[str] = "vertical_spread_candidate"
CANDIDATE_TYPE_FORWARD_FACTOR_SIGNAL: Final[str] = "forward_factor_signal"
CANDIDATE_TYPE_STOCK_ADD: Final[str] = "stock_add_candidate"
CANDIDATE_TYPE_WATCHLIST: Final[str] = "watchlist_hold"
CANDIDATE_TYPE_AVOID: Final[str] = "avoid"
