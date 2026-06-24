"""
app/services/pipeline_helpers.py — Shared pipeline helpers.

These helpers keep analysis_service.py focused on orchestration instead of
configuration printing, run-mode normalization, ticker limiting, and small
fallback structures.
"""

from __future__ import annotations

import os
from typing import Any

from app import config


def normalize_run_mode(run_mode: str | None) -> str:
    value = str(run_mode or config.APP_MODE or "prod").strip().lower()
    return "dev" if value in {"dev", "development", "test", "testing"} else "prod"


def config_snapshot(run_mode: str) -> dict[str, Any]:
    return {
        "app_mode": config.APP_MODE,
        "run_mode": run_mode,
        "has_robinhood_username": bool(config.ROBINHOOD_USERNAME),
        "has_robinhood_password": bool(config.ROBINHOOD_PASSWORD),
        "has_news_api_key": bool(config.NEWS_API_KEY),
        "has_finnhub_api_key": bool(config.FINNHUB_API_KEY),
        "has_alpha_vantage_api_key": bool(config.ALPHA_VANTAGE_API_KEY),
        "has_tradier_access_token": bool(config.TRADIER_ACCESS_TOKEN),
        "tradier_env": config.TRADIER_ENV,
        "has_run_token": bool(config.RUN_TOKEN),
        "market_benchmark_ticker": config.MARKET_BENCHMARK_TICKER,
        "market_data_use_tradier_fallback": config.MARKET_DATA_USE_TRADIER_FALLBACK,
        "market_data_provider_order": list(config.MARKET_DATA_PROVIDER_ORDER),
        "market_data_candle_required_bars": config.MARKET_DATA_CANDLE_REQUIRED_BARS,
        "market_data_hub_enabled": config.MARKET_DATA_HUB_ENABLED,
        "market_data_db_path": config.MARKET_DATA_DB_PATH,
        "market_data_enable_sqlite_cache": config.MARKET_DATA_ENABLE_SQLITE_CACHE,
        "market_data_enable_wal": config.MARKET_DATA_ENABLE_WAL,
        "market_data_max_provider_fetches_per_run": config.MARKET_DATA_MAX_PROVIDER_FETCHES_PER_RUN,
        "earnings_provider_order": list(config.EARNINGS_PROVIDER_ORDER),
        "earnings_merge_provider_events": config.EARNINGS_MERGE_PROVIDER_EVENTS,
        "earnings_discovery_window": f"+{config.EARNINGS_DISCOVERY_START_DAYS}..+{config.EARNINGS_DISCOVERY_END_DAYS} days",
        "earnings_discovery_end_days_requested": config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED,
        "earnings_discovery_end_override_adjusted": config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED != config.EARNINGS_DISCOVERY_END_DAYS,
        "earnings_discovery_raw_event_limit": config.EARNINGS_DISCOVERY_RAW_EVENT_LIMIT,
        "earnings_discovery_dev_raw_event_limit": config.EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT,
        "earnings_discovery_max_optionable_to_check": config.EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK,
        "earnings_discovery_dev_max_optionable_to_check": config.EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK,
        "earnings_discovery_max_final_candidates": config.EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES,
        "tradier_max_tickers_per_run": config.TRADIER_MAX_TICKERS_PER_RUN,
        "tradier_chain_expirations_per_ticker": config.TRADIER_CHAIN_EXPIRATIONS_PER_TICKER,
        "calendar_max_tickers_per_run": config.CALENDAR_MAX_TICKERS_PER_RUN,
        "calendar_max_expiration_pairs_per_ticker": config.CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER,
        "calendar_max_candidates_per_ticker": config.CALENDAR_MAX_CANDIDATES_PER_TICKER,
        "calendar_opportunity_cache_enabled": config.CALENDAR_OPPORTUNITY_CACHE_ENABLED,
        "calendar_opportunity_db_path": config.CALENDAR_OPPORTUNITY_DB_PATH,
        "report_show_calendar_debug_sections": config.REPORT_SHOW_CALENDAR_DEBUG_SECTIONS,
        "watchlist_enabled": config.WATCHLIST_ENABLED,
        "watchlist_names": list(config.WATCHLIST_NAMES),
        "watchlist_max_tickers_per_run": config.WATCHLIST_MAX_TICKERS_PER_RUN,
        "portfolio_gap_enabled": config.PORTFOLIO_GAP_ENABLED,
        "stock_momentum_strategy_enabled": config.STOCK_MOMENTUM_STRATEGY_ENABLED,
        "daily_opportunity_engine_enabled": config.DAILY_OPPORTUNITY_ENGINE_ENABLED,
        "skew_vertical_strategy_enabled": config.SKEW_VERTICAL_STRATEGY_ENABLED,
        "skew_vertical_max_tickers_per_run": config.SKEW_VERTICAL_MAX_TICKERS_PER_RUN,
        "skew_vertical_dev_max_tickers_per_run": config.SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN,
        "skew_vertical_dte_range": f"{config.SKEW_VERTICAL_MIN_DTE}..{config.SKEW_VERTICAL_MAX_DTE}",
        "robinhood_options_detector_enabled": getattr(config, "ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True),
        "robinhood_options_infer_calendars": getattr(config, "ROBINHOOD_OPTIONS_INFER_CALENDARS", True),
        "universe_discovery_enabled": getattr(config, "UNIVERSE_DISCOVERY_ENABLED", True),
        "universe_discovery_max_candidates": getattr(config, "EARNINGS_DISCOVERY_UNIVERSE_MAX_CANDIDATES", 50),
        "universe_min_avg_volume": getattr(config, "UNIVERSE_MIN_AVG_VOLUME", 500000),
        "skew_universe_max_candidates": getattr(config, "SKEW_UNIVERSE_MAX_CANDIDATES", 30),
        "ff_universe_max_tickers": getattr(config, "FF_UNIVERSE_MAX_TICKERS", 40),
        "dev_tickers": list(config.DEV_TICKERS),
        "dev_max_tickers": config.DEV_MAX_TICKERS,
    }


def config_log_lines(snapshot: dict[str, Any]) -> list[str]:
    lines = [
        "config imported OK",
        f"APP_MODE: {snapshot.get('app_mode')}",
        f"Run mode: {snapshot.get('run_mode')}",
        f"ROBINHOOD_USERNAME set: {snapshot.get('has_robinhood_username')}",
        f"ROBINHOOD_PASSWORD set: {snapshot.get('has_robinhood_password')}",
        f"NEWS_API_KEY set: {snapshot.get('has_news_api_key')}",
        f"NEWS_MAX_TICKERS_PER_RUN: {getattr(config, 'NEWS_MAX_TICKERS_PER_RUN', 8)}",
        f"FINNHUB_API_KEY set: {snapshot.get('has_finnhub_api_key')}",
        f"ALPHA_VANTAGE_API_KEY set: {snapshot.get('has_alpha_vantage_api_key')}",
        f"MARKET_BENCHMARK_TICKER: {snapshot.get('market_benchmark_ticker')}",
        f"MARKET_DATA_USE_TRADIER_FALLBACK: {snapshot.get('market_data_use_tradier_fallback')}",
        f"MARKET_DATA_PROVIDER_ORDER: {snapshot.get('market_data_provider_order')}",
        f"MARKET_DATA_CANDLE_REQUIRED_BARS: {snapshot.get('market_data_candle_required_bars')}",
        f"MARKET_DATA_HUB_ENABLED: {snapshot.get('market_data_hub_enabled')}",
        f"MARKET_DATA_DB_PATH: {snapshot.get('market_data_db_path')}",
        f"MARKET_DATA_ENABLE_SQLITE_CACHE: {snapshot.get('market_data_enable_sqlite_cache')}",
        f"MARKET_DATA_ENABLE_WAL: {snapshot.get('market_data_enable_wal')}",
        f"MARKET_DATA_MAX_PROVIDER_FETCHES_PER_RUN: {snapshot.get('market_data_max_provider_fetches_per_run')}",
        f"MARKET_DATA_MAX_TICKERS_PER_RUN: {getattr(config, 'MARKET_DATA_MAX_TICKERS_PER_RUN', None)}",
        f"TRADIER_ACCESS_TOKEN set: {snapshot.get('has_tradier_access_token')}",
        f"TRADIER_ENV: {snapshot.get('tradier_env')}",
        f"TRADIER_MAX_TICKERS_PER_RUN: {getattr(config, 'TRADIER_MAX_TICKERS_PER_RUN', None)}",
        f"TRADIER_CHAIN_EXPIRATIONS_PER_TICKER: {snapshot.get('tradier_chain_expirations_per_ticker')}",
        f"CALENDAR_SCANNER_ENABLED: {getattr(config, 'CALENDAR_SCANNER_ENABLED', None)}",
        f"CALENDAR_MAX_TICKERS_PER_RUN: {snapshot.get('calendar_max_tickers_per_run')}",
        f"CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER: {snapshot.get('calendar_max_expiration_pairs_per_ticker')}",
        f"CALENDAR_MAX_CANDIDATES_PER_TICKER: {snapshot.get('calendar_max_candidates_per_ticker')}",
        f"CALENDAR_OPPORTUNITY_CACHE_ENABLED: {snapshot.get('calendar_opportunity_cache_enabled')}",
        f"CALENDAR_OPPORTUNITY_DB_PATH: {snapshot.get('calendar_opportunity_db_path')}",
        f"OPEN_OPTIONS_DETECTOR_ENABLED: {getattr(config, 'OPEN_OPTIONS_DETECTOR_ENABLED', None)}",
        f"TRADIER_ACCOUNT_ID set: {bool(getattr(config, 'TRADIER_ACCOUNT_ID', None))}",
        f"EARNINGS_PROVIDER_ENABLED: {getattr(config, 'EARNINGS_PROVIDER_ENABLED', None)}",
        f"EARNINGS_PROVIDER: {getattr(config, 'EARNINGS_PROVIDER', None)}",
        f"EARNINGS_PROVIDER_ORDER: {snapshot.get('earnings_provider_order')}",
        f"EARNINGS_MERGE_PROVIDER_EVENTS: {snapshot.get('earnings_merge_provider_events')}",
        f"ALPHA_VANTAGE_EARNINGS_HORIZON: {getattr(config, 'ALPHA_VANTAGE_EARNINGS_HORIZON', None)}",
        f"EARNINGS_LOOKAHEAD_DAYS: {getattr(config, 'EARNINGS_LOOKAHEAD_DAYS', None)}",
        f"EARNINGS_DISCOVERY_ENABLED: {getattr(config, 'EARNINGS_DISCOVERY_ENABLED', None)}",
        f"EARNINGS_DISCOVERY_WINDOW: {snapshot.get('earnings_discovery_window')}",
        f"EARNINGS_DISCOVERY_END_DAYS requested/effective: {snapshot.get('earnings_discovery_end_days_requested')}/{getattr(config, 'EARNINGS_DISCOVERY_END_DAYS', None)}",
        f"EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN: {getattr(config, 'EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN', None)}",
        f"EARNINGS_DISCOVERY_RAW_EVENT_LIMIT: {snapshot.get('earnings_discovery_raw_event_limit')}",
        f"EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK: {snapshot.get('earnings_discovery_max_optionable_to_check')}",
        f"EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES: {snapshot.get('earnings_discovery_max_final_candidates')}",
        f"CALENDAR_LIFECYCLE_ENABLED: {getattr(config, 'CALENDAR_LIFECYCLE_ENABLED', None)}",
        f"CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT: {getattr(config, 'CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT', None)}",
        f"EARNINGS_CALENDAR_STRATEGY_ENABLED: {getattr(config, 'EARNINGS_CALENDAR_STRATEGY_ENABLED', None)}",
        f"UNIFIED_CALENDAR_ENGINE_ENABLED: {getattr(config, 'UNIFIED_CALENDAR_ENGINE_ENABLED', None)}",
        f"REPORT_SHOW_CALENDAR_DEBUG_SECTIONS: {snapshot.get('report_show_calendar_debug_sections')}",
        f"WATCHLIST_ENABLED: {snapshot.get('watchlist_enabled')}",
        f"WATCHLIST_SOURCE: {getattr(config, 'WATCHLIST_SOURCE', None)}",
        f"WATCHLIST_NAMES: {snapshot.get('watchlist_names')}",
        f"WATCHLIST_MAX_TICKERS_PER_RUN: {snapshot.get('watchlist_max_tickers_per_run')}",
        f"WATCHLIST_PRIORITIZE_FOR_SCANS: {getattr(config, 'WATCHLIST_PRIORITIZE_FOR_SCANS', None)}",
        f"PORTFOLIO_GAP_ENABLED: {snapshot.get('portfolio_gap_enabled')}",
        f"PORTFOLIO_GAP_TARGET_PROFILE: {getattr(config, 'PORTFOLIO_GAP_TARGET_PROFILE', None)}",
        f"PORTFOLIO_GAP_MACRO_WINNING_BUCKETS: {getattr(config, 'PORTFOLIO_GAP_MACRO_WINNING_BUCKETS', None)}",
        f"PORTFOLIO_GAP_MAX_SUGGESTIONS: {getattr(config, 'PORTFOLIO_GAP_MAX_SUGGESTIONS', None)}",
        f"STOCK_MOMENTUM_STRATEGY_ENABLED: {snapshot.get('stock_momentum_strategy_enabled')}",
        f"STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX: {getattr(config, 'STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX', None)}",
        f"DAILY_OPPORTUNITY_ENGINE_ENABLED: {snapshot.get('daily_opportunity_engine_enabled')}",
        f"SKEW_VERTICAL_STRATEGY_ENABLED: {snapshot.get('skew_vertical_strategy_enabled')}",
        f"SKEW_VERTICAL_MAX_TICKERS_PER_RUN: {snapshot.get('skew_vertical_max_tickers_per_run')}",
        f"SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN: {snapshot.get('skew_vertical_dev_max_tickers_per_run')}",
        f"SKEW_VERTICAL_DTE_RANGE: {snapshot.get('skew_vertical_dte_range')}",
        f"ROBINHOOD_OPTIONS_DETECTOR_ENABLED: {snapshot.get('robinhood_options_detector_enabled')}",
        f"ROBINHOOD_OPTIONS_INFER_CALENDARS: {snapshot.get('robinhood_options_infer_calendars')}",
        f"UNIVERSE_DISCOVERY_ENABLED: {snapshot.get('universe_discovery_enabled')}",
        f"EARNINGS_DISCOVERY_UNIVERSE_MAX_CANDIDATES: {snapshot.get('universe_discovery_max_candidates')}",
        f"UNIVERSE_MIN_AVG_VOLUME: {snapshot.get('universe_min_avg_volume')}",
        f"SKEW_UNIVERSE_MAX_CANDIDATES: {snapshot.get('skew_universe_max_candidates')}",
        f"FF_UNIVERSE_MAX_TICKERS: {snapshot.get('ff_universe_max_tickers')}",
    ]
    _warn_env_overrides(lines)
    if snapshot.get("run_mode") == "dev":
        lines.extend(
            [
                "DEV MODE active: Robinhood will fetch all positions, but external provider calls are limited.",
                f"DEV_TICKERS: {snapshot.get('dev_tickers')}; DEV_MAX_TICKERS: {snapshot.get('dev_max_tickers')}",
            ]
        )
    return lines


_DISCOVERY_CAP_DEFAULTS: list[tuple[str, int]] = [
    ("EARNINGS_DISCOVERY_RAW_EVENT_LIMIT", 200),
    ("EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK", 40),
    ("EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES", 20),
]


def _warn_env_overrides(lines: list[str]) -> None:
    for env_name, code_default in _DISCOVERY_CAP_DEFAULTS:
        env_val = os.environ.get(env_name)
        if env_val is not None:
            try:
                actual = int(env_val)
            except (TypeError, ValueError):
                continue
            if actual < code_default:
                lines.append(
                    f"ENV OVERRIDE: {env_name}={actual} (Railway env var overrides code default {code_default})"
                )


def merge_provider_ticker_sets(primary: list[str], secondary: list[str]) -> list[str]:
    merged: list[str] = []
    for ticker in list(primary or []) + list(secondary or []):
        clean = str(ticker).upper().strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged


def external_provider_tickers(tickers: list[str], run_mode: str) -> list[str]:
    normalized = [str(t).upper().strip() for t in tickers if str(t).strip()]
    if run_mode != "dev":
        return normalized
    preferred = [str(t).upper().strip() for t in config.DEV_TICKERS if str(t).strip()]
    selected: list[str] = []
    for ticker in preferred:
        if ticker in normalized and ticker not in selected:
            selected.append(ticker)
    for ticker in normalized:
        if len(selected) >= max(1, int(config.DEV_MAX_TICKERS or 1)):
            break
        if ticker not in selected:
            selected.append(ticker)
    return selected[: max(1, int(config.DEV_MAX_TICKERS or 1))]


def news_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return None


def market_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1) + int(getattr(config, "STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX", 0) or 0))
    return max(1, int(config.MARKET_DATA_MAX_TICKERS_PER_RUN or 1))


def tradier_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return max(1, int(config.TRADIER_MAX_TICKERS_PER_RUN or 1))


def calendar_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(getattr(config, "EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES", config.DEV_MAX_TICKERS) or config.DEV_MAX_TICKERS or 1))
    return max(1, int(config.CALENDAR_MAX_TICKERS_PER_RUN or 1))


def earnings_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return max(1, int(config.EARNINGS_MAX_TICKERS_PER_RUN or 1))


def fill_missing_news_keys(all_tickers: list[str], news: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    filled = {str(t).upper().strip(): [] for t in all_tickers if str(t).strip()}
    for ticker, articles in (news or {}).items():
        filled[str(ticker).upper().strip()] = articles or []
    return filled


def positions_from_earnings_discovery(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    source_items = (discovery or {}).get("passed_items") or (discovery or {}).get("items", []) or []
    for raw in source_items:
        event = raw.get("event") if isinstance(raw, dict) and isinstance(raw.get("event"), dict) else raw
        ticker = str((raw or {}).get("ticker") or (event or {}).get("ticker") or (event or {}).get("symbol") or "").upper().strip()
        if not ticker:
            continue
        positions.append(
            {
                "ticker": ticker,
                "quantity": 0,
                "avg_buy_price": None,
                "current_price": None,
                "gain_loss": None,
                "gain_loss_pct": None,
                "market_value": 0,
                "account": "Earnings Discovery",
                "source": str((discovery or {}).get("source") or "earnings_discovery_v2"),
                "earnings_event": event,
            }
        )
    return positions


def merge_earnings_events(earnings_events: dict[str, dict[str, Any]], discovery: dict[str, Any]) -> dict[str, dict[str, Any]]:
    merged = dict(earnings_events or {})
    for ticker, event in ((discovery or {}).get("events_by_ticker", {}) or {}).items():
        clean = str(ticker).upper().strip()
        if clean:
            merged[clean] = event
    return merged
