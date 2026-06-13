"""
app/services/config_check_service.py — Redacted runtime configuration diagnostics.

The /config-check route uses this module to show whether Railway variables are
present and whether obvious defaults/stale values are still active. It never
returns secret values.
"""

from __future__ import annotations

from typing import Any

from app import config


def build_config_check(run_mode: str = "prod") -> dict[str, Any]:
    clean_mode = "dev" if str(run_mode).lower() == "dev" else "prod"
    providers = {
        "robinhood": {
            "ready": bool(config.ROBINHOOD_USERNAME and config.ROBINHOOD_PASSWORD),
            "required": True,
            "details": "ROBINHOOD_USERNAME and ROBINHOOD_PASSWORD",
        },
        "newsapi": {
            "ready": bool(config.NEWS_API_KEY),
            "required": False,
            "details": f"NEWS_MAX_TICKERS_PER_RUN={config.NEWS_MAX_TICKERS_PER_RUN}",
        },
        "finnhub": {
            "ready": bool(config.FINNHUB_API_KEY),
            "required": False,
            "details": "Used for earnings and attempted candle data; candles may be plan-restricted.",
        },
        "alpha_vantage": {
            "ready": bool(config.ALPHA_VANTAGE_API_KEY),
            "required": False,
            "details": f"Earnings horizon={config.ALPHA_VANTAGE_EARNINGS_HORIZON}; candle fallback available when key is set.",
        },
        "tradier": {
            "ready": bool(config.TRADIER_ACCESS_TOKEN),
            "required": True,
            "details": f"TRADIER_ENV={config.TRADIER_ENV}; account_id_set={bool(config.TRADIER_ACCOUNT_ID)}",
        },
        "run_token": {
            "ready": bool(config.RUN_TOKEN),
            "required": True,
            "details": "Protects /run and /config-check routes. Manual /trades routes are disabled.",
        },
        "robinhood_options": {
            "ready": bool(getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True) and config.ROBINHOOD_USERNAME and config.ROBINHOOD_PASSWORD),
            "required": False,
            "details": f"auto_detect={getattr(config, 'ROBINHOOD_OPTIONS_DETECTOR_ENABLED', True)}; scan_default_account={getattr(config, 'ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT', True)}; default_label={getattr(config, 'ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL', 'Investing')}; infer_calendars={getattr(config, 'ROBINHOOD_OPTIONS_INFER_CALENDARS', True)}; avg_price_scale={getattr(config, 'ROBINHOOD_OPTION_AVG_PRICE_SCALE', 'auto')}",
        },
    }

    warnings: list[str] = []
    if config.WATCHLIST_NAMES:
        warnings.append(
            f"WATCHLIST_NAMES is set to {config.WATCHLIST_NAMES}. Configured aliases: {config.WATCHLIST_NAME_ALIASES}. Confirm requested names exist; preferred current list is List 01."
        )
    if clean_mode == "dev" and int(config.DEV_MAX_TICKERS or 0) <= 2:
        warnings.append("DEV_MAX_TICKERS is very low; stock/earnings discovery will be API-safe but narrow.")
    if not config.ALPHA_VANTAGE_API_KEY:
        warnings.append("ALPHA_VANTAGE_API_KEY is missing; earnings discovery relies primarily on Finnhub.")
    if not config.MARKET_DATA_USE_TRADIER_FALLBACK:
        warnings.append("MARKET_DATA_USE_TRADIER_FALLBACK is off; Finnhub candle restrictions may leave momentum blank.")
    if config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED != config.EARNINGS_DISCOVERY_END_DAYS:
        warnings.append(
            f"Railway requested EARNINGS_DISCOVERY_END_DAYS={config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED}; "
            f"Railway EARNINGS_DISCOVERY_END_DAYS requests {config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED}, "
            f"but application minimum is {config.EARNINGS_DISCOVERY_END_DAYS}. Update Railway variable to {config.EARNINGS_DISCOVERY_END_DAYS}."
        )

    limits = {
        "run_mode": clean_mode,
        "dev_tickers": list(config.DEV_TICKERS),
        "dev_max_tickers": config.DEV_MAX_TICKERS,
        "news_max_tickers_per_run": config.NEWS_MAX_TICKERS_PER_RUN,
        "market_data_max_tickers_per_run": config.MARKET_DATA_MAX_TICKERS_PER_RUN,
        "market_data_provider_order": list(config.MARKET_DATA_PROVIDER_ORDER),
        "market_data_candle_required_bars": config.MARKET_DATA_CANDLE_REQUIRED_BARS,
        "market_data_hub_enabled": config.MARKET_DATA_HUB_ENABLED,
        "market_data_db_path": config.MARKET_DATA_DB_PATH,
        "market_data_enable_sqlite_cache": config.MARKET_DATA_ENABLE_SQLITE_CACHE,
        "market_data_enable_wal": config.MARKET_DATA_ENABLE_WAL,
        "market_data_quote_ttl_seconds": config.MARKET_DATA_QUOTE_TTL_SECONDS,
        "market_data_options_chain_ttl_seconds": config.MARKET_DATA_OPTIONS_CHAIN_TTL_SECONDS,
        "market_data_candles_ttl_seconds": config.MARKET_DATA_CANDLES_TTL_SECONDS,
        "market_data_earnings_ttl_seconds": config.MARKET_DATA_EARNINGS_TTL_SECONDS,
        "market_data_derived_metrics_ttl_seconds": config.MARKET_DATA_DERIVED_METRICS_TTL_SECONDS,
        "market_data_max_provider_fetches_per_run": config.MARKET_DATA_MAX_PROVIDER_FETCHES_PER_RUN,
        "report_snapshot_db_path": config.REPORT_SNAPSHOT_DB_PATH,
        "broker_position_snapshot_db_path": config.BROKER_POSITION_SNAPSHOT_DB_PATH,
        "strategy_opportunity_db_path": config.STRATEGY_OPPORTUNITY_DB_PATH,
        "tradier_max_tickers_per_run": config.TRADIER_MAX_TICKERS_PER_RUN,
        "calendar_max_tickers_per_run": config.CALENDAR_MAX_TICKERS_PER_RUN,
        "earnings_discovery_raw_event_limit": config.EARNINGS_DISCOVERY_RAW_EVENT_LIMIT,
        "earnings_discovery_dev_raw_event_limit": config.EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT,
        "earnings_discovery_max_optionable_to_check": config.EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK,
        "earnings_discovery_dev_max_optionable_to_check": config.EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK,
        "earnings_discovery_max_final_candidates": config.EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES,
        "earnings_discovery_window_days": f"+{config.EARNINGS_DISCOVERY_START_DAYS}..+{config.EARNINGS_DISCOVERY_END_DAYS}",
        "earnings_discovery_end_days_requested": config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED,
        "earnings_discovery_end_override_adjusted": config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED != config.EARNINGS_DISCOVERY_END_DAYS,
        "earnings_calendar_ideal_entry_window": f"{getattr(config, 'EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE', 6)}-{getattr(config, 'EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE', 12)} DTE",
        "calendar_earnings_event_aware_expirations": getattr(config, "CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS", True),
        "calendar_backtest_enabled": getattr(config, "CALENDAR_BACKTEST_ENABLED", True),
        "calendar_backtest_max_events": getattr(config, "CALENDAR_BACKTEST_MAX_EVENTS", 10),
        "calendar_backtest_gate": "only candidates passing Calendar Ranking v2 and high/medium candle-quality gates",
        "calendar_opportunity_cache_enabled": config.CALENDAR_OPPORTUNITY_CACHE_ENABLED,
        "calendar_opportunity_db_path": config.CALENDAR_OPPORTUNITY_DB_PATH,
        "stock_momentum_watchlist_market_data_max": config.STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX,
        "watchlist_name_aliases": dict(config.WATCHLIST_NAME_ALIASES),
        "robinhood_options_detector_enabled": getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True),
        "robinhood_options_scan_default_account": getattr(config, "ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT", True),
        "robinhood_options_default_account_label": getattr(config, "ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL", "Investing"),
        "robinhood_options_infer_calendars": getattr(config, "ROBINHOOD_OPTIONS_INFER_CALENDARS", True),
        "robinhood_option_avg_price_scale": getattr(config, "ROBINHOOD_OPTION_AVG_PRICE_SCALE", "auto"),
        "calendar_lifecycle_take_profit_pct": getattr(config, "CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT", config.CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT),
        "calendar_lifecycle_stop_loss_pct": getattr(config, "CALENDAR_LIFECYCLE_STOP_LOSS_PCT", config.CALENDAR_LIFECYCLE_MAX_LOSS_PCT),
        "calendar_lifecycle_assignment_dte": getattr(config, "CALENDAR_LIFECYCLE_ASSIGNMENT_DTE", config.CALENDAR_LIFECYCLE_URGENT_DTE),
        "calendar_lifecycle_near_money_pct": getattr(config, "CALENDAR_LIFECYCLE_NEAR_MONEY_PCT", 2),
        "calendar_true_iv_front_max_days_after_event": getattr(config, "CALENDAR_TRUE_IV_FRONT_MAX_DAYS_AFTER_EVENT", 7),
        "calendar_pre_earnings_financing_can_pass": getattr(config, "CALENDAR_PRE_EARNINGS_FINANCING_CAN_PASS", False),
        "calendar_unknown_timestamp_can_pass": getattr(config, "CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS", False),
        "daily_opportunity_prioritize_active_calendars": getattr(config, "DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS", True),
        "calendar_lifecycle_fetch_underlying_quotes": getattr(config, "CALENDAR_LIFECYCLE_FETCH_UNDERLYING_QUOTES", True),
        "skew_vertical_max_tickers_per_run": config.SKEW_VERTICAL_MAX_TICKERS_PER_RUN,
        "skew_vertical_dev_max_tickers_per_run": config.SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN,
        "skew_vertical_dte_range": f"{config.SKEW_VERTICAL_MIN_DTE}-{config.SKEW_VERTICAL_MAX_DTE}",
        "skew_vertical_liquidity": f"OI>={config.SKEW_VERTICAL_MIN_OPEN_INTEREST}; volume>={config.SKEW_VERTICAL_MIN_VOLUME}; leg spread<={config.SKEW_VERTICAL_MAX_LEG_SPREAD_PCT}%",
        "skew_vertical_skew_thresholds": f"IV edge>={config.SKEW_VERTICAL_MIN_SHORT_IV_EDGE}; financing>={config.SKEW_VERTICAL_MIN_SHORT_PREMIUM_FINANCING_PCT}%",
        "skew_vertical_max_debit_dollars": config.SKEW_VERTICAL_MAX_DEBIT_DOLLARS,
        "skew_vertical_max_account_risk_pct": config.SKEW_VERTICAL_MAX_ACCOUNT_RISK_PCT,
        "skew_vertical_lifecycle_enabled": config.SKEW_VERTICAL_LIFECYCLE_ENABLED,
        "skew_vertical_opportunity_cache_enabled": config.SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED,
        "skew_vertical_opportunity_db_path": config.SKEW_VERTICAL_OPPORTUNITY_DB_PATH,
        "forward_factor_dry_run": config.FORWARD_FACTOR_DRY_RUN,
        "forward_factor_formula_version": config.FF_FORMULA_VERSION,
        "forward_factor_threshold": config.FF_MIN_FORWARD_FACTOR,
        "forward_factor_dte_ranges": f"{config.FF_FRONT_DTE_MIN}-{config.FF_FRONT_DTE_MAX}/{config.FF_BACK_DTE_MIN}-{config.FF_BACK_DTE_MAX}",
        "forward_factor_max_tickers_per_run": config.FF_MAX_TICKERS_PER_RUN,
        "forward_factor_dev_max_tickers_per_run": config.FF_DEV_MAX_TICKERS_PER_RUN,
        "forward_factor_dev_max_chain_tickers_per_run": config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN,
        "forward_factor_chain_expirations_per_ticker": config.FF_CHAIN_EXPIRATIONS_PER_TICKER,
        "forward_factor_max_chain_tickers_per_run": config.FF_MAX_CHAIN_TICKERS_PER_RUN,
        "forward_factor_earnings_lookahead_days": config.FF_EARNINGS_LOOKAHEAD_DAYS,
        "forward_factor_allow_diagnostic_structure_without_source_iv": config.FF_ALLOW_DIAGNOSTIC_STRUCTURE_WITHOUT_SOURCE_IV,
        "forward_factor_warn_package_slippage_pct": config.FF_WARN_PACKAGE_SLIPPAGE_PCT,
        "forward_factor_require_nonzero_short_bid": config.FF_REQUIRE_NONZERO_SHORT_BID,
        "forward_factor_require_valid_long_ask": config.FF_REQUIRE_VALID_LONG_ASK,
    }

    enabled_modules = {
        "calendar_scanner": config.CALENDAR_SCANNER_ENABLED,
        "earnings_provider": config.EARNINGS_PROVIDER_ENABLED,
        "earnings_discovery": config.EARNINGS_DISCOVERY_ENABLED,
        "unified_calendar_engine": config.UNIFIED_CALENDAR_ENGINE_ENABLED,
        "open_options_detector": config.OPEN_OPTIONS_DETECTOR_ENABLED,
        "calendar_lifecycle": config.CALENDAR_LIFECYCLE_ENABLED,
        "watchlist": config.WATCHLIST_ENABLED,
        "portfolio_gap": config.PORTFOLIO_GAP_ENABLED,
        "stock_momentum": config.STOCK_MOMENTUM_STRATEGY_ENABLED,
        "daily_opportunity": config.DAILY_OPPORTUNITY_ENGINE_ENABLED,
        "calendar_ranking": True,
        "earnings_mini_backtest": getattr(config, "CALENDAR_BACKTEST_ENABLED", True),
        "multi_provider_candles": True,
        "market_data_hub": config.MARKET_DATA_HUB_ENABLED,
        "market_data_sqlite_cache": config.MARKET_DATA_ENABLE_SQLITE_CACHE,
        "strategy_registry": True,
        "generic_opportunity_registry": True,
        "persistent_report_snapshots": True,
        "calendar_opportunity_cache": config.CALENDAR_OPPORTUNITY_CACHE_ENABLED,
        "robinhood_options_detector": getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True),
        "calendar_trade_type_rules": True,
        "daily_opportunity_active_calendar_priority": getattr(config, "DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS", True),
        "lifecycle_underlying_quote_enrichment": getattr(config, "CALENDAR_LIFECYCLE_FETCH_UNDERLYING_QUOTES", True),
        "skew_momentum_vertical": config.SKEW_VERTICAL_STRATEGY_ENABLED,
        "skew_vertical_lifecycle": config.SKEW_VERTICAL_LIFECYCLE_ENABLED,
        "skew_vertical_opportunity_cache": config.SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED,
        "forward_factor_calendar": config.FORWARD_FACTOR_STRATEGY_ENABLED,
    }

    ready_count = sum(1 for provider in providers.values() if provider["ready"])
    required_ready = all(provider["ready"] for provider in providers.values() if provider["required"])

    return {
        "status": "ok" if required_ready else "missing_required",
        "provider_ready_count": ready_count,
        "provider_count": len(providers),
        "providers": providers,
        "warnings": warnings,
        "limits": limits,
        "enabled_modules": enabled_modules,
        "earnings_provider_order": list(config.EARNINGS_PROVIDER_ORDER),
        "market_data_provider_order": list(config.MARKET_DATA_PROVIDER_ORDER),
        "report_show_calendar_debug_sections": config.REPORT_SHOW_CALENDAR_DEBUG_SECTIONS,
    }
