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
            "details": f"Horizon={config.ALPHA_VANTAGE_EARNINGS_HORIZON}",
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
            "details": f"auto_detect={getattr(config, 'ROBINHOOD_OPTIONS_DETECTOR_ENABLED', True)}; scan_default_account={getattr(config, 'ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT', True)}; default_label={getattr(config, 'ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL', 'Investing')}; infer_calendars={getattr(config, 'ROBINHOOD_OPTIONS_INFER_CALENDARS', True)}",
        },
    }

    warnings: list[str] = []
    if config.WATCHLIST_NAMES:
        warnings.append(
            "WATCHLIST_NAMES is set. Leave it blank to scan all discovered Robinhood watchlists unless you intentionally want a specific list."
        )
    if clean_mode == "dev" and int(config.DEV_MAX_TICKERS or 0) <= 2:
        warnings.append("DEV_MAX_TICKERS is very low; stock/earnings discovery will be API-safe but narrow.")
    if not config.ALPHA_VANTAGE_API_KEY:
        warnings.append("ALPHA_VANTAGE_API_KEY is missing; earnings discovery relies primarily on Finnhub.")
    if not config.MARKET_DATA_USE_TRADIER_FALLBACK:
        warnings.append("MARKET_DATA_USE_TRADIER_FALLBACK is off; Finnhub candle restrictions may leave momentum blank.")

    limits = {
        "run_mode": clean_mode,
        "dev_tickers": list(config.DEV_TICKERS),
        "dev_max_tickers": config.DEV_MAX_TICKERS,
        "news_max_tickers_per_run": config.NEWS_MAX_TICKERS_PER_RUN,
        "market_data_max_tickers_per_run": config.MARKET_DATA_MAX_TICKERS_PER_RUN,
        "tradier_max_tickers_per_run": config.TRADIER_MAX_TICKERS_PER_RUN,
        "calendar_max_tickers_per_run": config.CALENDAR_MAX_TICKERS_PER_RUN,
        "earnings_discovery_raw_event_limit": config.EARNINGS_DISCOVERY_RAW_EVENT_LIMIT,
        "earnings_discovery_dev_raw_event_limit": config.EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT,
        "earnings_discovery_max_optionable_to_check": config.EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK,
        "earnings_discovery_dev_max_optionable_to_check": config.EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK,
        "earnings_discovery_max_final_candidates": config.EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES,
        "stock_momentum_watchlist_market_data_max": config.STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX,
        "robinhood_options_detector_enabled": getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True),
        "robinhood_options_scan_default_account": getattr(config, "ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT", True),
        "robinhood_options_default_account_label": getattr(config, "ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL", "Investing"),
        "robinhood_options_infer_calendars": getattr(config, "ROBINHOOD_OPTIONS_INFER_CALENDARS", True),
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
        "robinhood_options_detector": getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True),
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
        "report_show_calendar_debug_sections": config.REPORT_SHOW_CALENDAR_DEBUG_SECTIONS,
    }
