"""
app/services/analysis_service.py — Main pipeline orchestration.

This service runs the portfolio/news/market-data/Tradier/calendar/scoring/report
pipeline.

Dev mode is supported for API-budget-safe testing:
- Robinhood still fetches the full portfolio, because that is the baseline state.
- External providers such as NewsAPI, Finnhub, earnings, and Tradier are limited
  to a small ticker subset.
"""

from __future__ import annotations

import traceback
from typing import Any

from app import config
from app.providers.news_provider import get_news_for_tickers
from app.services.market_data_service import get_market_metrics_for_positions
from app.services.tradier_service import get_tradier_snapshot_for_positions
from app.services.calendar_spread_service import scan_calendar_spreads_for_positions
from app.services.open_options_service import detect_open_options_positions
from app.services.earnings_service import get_earnings_for_positions, discover_upcoming_earnings_for_calendar_trades
from app.services.calendar_lifecycle_service import evaluate_calendar_lifecycle
from app.services.earnings_calendar_strategy_service import evaluate_earnings_calendar_candidates
from app.services.portfolio_service import get_portfolio_positions
from app.services.watchlist_service import get_watchlist_candidates, merge_watchlist_universe_positions
from app.services.watchlist_review_service import review_watchlist_candidates
from app.services.report_service import format_payload
from app.strategies.portfolio_snapshot import PortfolioSnapshotStrategy
from app.utils.log_safety import sanitize_for_log


PipelineResult = tuple[
    str | None,
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    list[str],
]


def run_portfolio_pipeline(run_mode: str = "prod") -> PipelineResult:
    log: list[str] = []
    news: dict[str, list[dict[str, Any]]] = {}
    market_metrics: dict[str, dict[str, Any]] = {}
    recommendations: list[dict[str, Any]] = []
    tradier_snapshot: dict[str, dict[str, Any]] = {}
    earnings_events: dict[str, dict[str, Any]] = {}
    watchlist_candidates: dict[str, Any] = {}
    watchlist_review: dict[str, Any] = {}
    earnings_trade_discovery: dict[str, Any] = {}

    clean_mode = _normalize_run_mode(run_mode)

    def log_print(msg: str) -> None:
        safe_msg = sanitize_for_log(
            msg,
            known_secrets=[
                config.ROBINHOOD_PASSWORD,
                config.NEWS_API_KEY,
                config.FINNHUB_API_KEY,
                config.TRADIER_ACCESS_TOKEN,
                config.RUN_TOKEN,
                config.NTFY_TOPIC,
            ],
        )
        print(safe_msg, flush=True)
        log.append(safe_msg)

    log_print("=== RUN STARTED ===")

    try:
        log_print("robinhood imported OK")
        log_print("news imported OK")
        log_print("market data imported OK")
        log_print("tradier imported OK")
        log_print("config imported OK")
        log_print(f"APP_MODE: {config.APP_MODE}")
        log_print(f"Run mode: {clean_mode}")
        log_print(f"ROBINHOOD_USERNAME set: {bool(config.ROBINHOOD_USERNAME)}")
        log_print(f"ROBINHOOD_PASSWORD set: {bool(config.ROBINHOOD_PASSWORD)}")
        log_print(f"NEWS_API_KEY set: {bool(config.NEWS_API_KEY)}")
        log_print(f"NEWS_MAX_TICKERS_PER_RUN: {getattr(config, 'NEWS_MAX_TICKERS_PER_RUN', 8)}")
        log_print(f"FINNHUB_API_KEY set: {bool(config.FINNHUB_API_KEY)}")
        log_print(f"MARKET_BENCHMARK_TICKER: {config.MARKET_BENCHMARK_TICKER}")
        log_print(f"MARKET_DATA_USE_TRADIER_FALLBACK: {config.MARKET_DATA_USE_TRADIER_FALLBACK}")
        log_print(f"MARKET_DATA_MAX_TICKERS_PER_RUN: {config.MARKET_DATA_MAX_TICKERS_PER_RUN}")
        log_print(f"TRADIER_ACCESS_TOKEN set: {bool(config.TRADIER_ACCESS_TOKEN)}")
        log_print(f"TRADIER_ENV: {config.TRADIER_ENV}")
        log_print(f"TRADIER_MAX_TICKERS_PER_RUN: {config.TRADIER_MAX_TICKERS_PER_RUN}")
        log_print(f"CALENDAR_SCANNER_ENABLED: {config.CALENDAR_SCANNER_ENABLED}")
        log_print(f"CALENDAR_MAX_TICKERS_PER_RUN: {config.CALENDAR_MAX_TICKERS_PER_RUN}")
        log_print(f"OPEN_OPTIONS_DETECTOR_ENABLED: {config.OPEN_OPTIONS_DETECTOR_ENABLED}")
        log_print(f"TRADIER_ACCOUNT_ID set: {bool(config.TRADIER_ACCOUNT_ID)}")
        log_print(f"EARNINGS_PROVIDER_ENABLED: {config.EARNINGS_PROVIDER_ENABLED}")
        log_print(f"EARNINGS_PROVIDER: {config.EARNINGS_PROVIDER}")
        log_print(f"EARNINGS_LOOKAHEAD_DAYS: {config.EARNINGS_LOOKAHEAD_DAYS}")
        log_print(f"EARNINGS_DISCOVERY_ENABLED: {config.EARNINGS_DISCOVERY_ENABLED}")
        log_print(f"EARNINGS_DISCOVERY_WINDOW: +{config.EARNINGS_DISCOVERY_START_DAYS}..+{config.EARNINGS_DISCOVERY_END_DAYS} days")
        log_print(f"EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN: {config.EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN}")
        log_print(f"CALENDAR_LIFECYCLE_ENABLED: {config.CALENDAR_LIFECYCLE_ENABLED}")
        log_print(f"CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT: {config.CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT}")
        log_print(f"EARNINGS_CALENDAR_STRATEGY_ENABLED: {config.EARNINGS_CALENDAR_STRATEGY_ENABLED}")
        log_print(f"WATCHLIST_ENABLED: {config.WATCHLIST_ENABLED}")
        log_print(f"WATCHLIST_SOURCE: {config.WATCHLIST_SOURCE}")
        log_print(f"WATCHLIST_NAMES: {config.WATCHLIST_NAMES}")
        log_print(f"WATCHLIST_MAX_TICKERS_PER_RUN: {config.WATCHLIST_MAX_TICKERS_PER_RUN}")
        log_print(f"WATCHLIST_PRIORITIZE_FOR_SCANS: {config.WATCHLIST_PRIORITIZE_FOR_SCANS}")
        if clean_mode == "dev":
            log_print(
                "DEV MODE active: Robinhood will fetch all positions, but external "
                "provider calls are limited."
            )
            log_print(f"DEV_TICKERS: {config.DEV_TICKERS}; DEV_MAX_TICKERS: {config.DEV_MAX_TICKERS}")
    except Exception as e:
        log_print(f"IMPORT ERROR config: {e}\n{traceback.format_exc()}")
        return None, [], news, recommendations, tradier_snapshot, log

    log_print("Fetching Robinhood positions...")

    try:
        positions = get_portfolio_positions()
        log_print(f"get_positions returned {len(positions)} positions")
    except Exception as e:
        log_print(f"ERROR in get_positions: {e}\n{traceback.format_exc()}")
        return None, [], news, recommendations, tradier_snapshot, log

    if not positions:
        log_print("No positions found or login failed.")
        return None, [], news, recommendations, tradier_snapshot, log

    portfolio_tickers = list(dict.fromkeys(p.get("ticker") for p in positions if p.get("ticker")))
    log_print(f"Tickers: {portfolio_tickers}")

    try:
        watchlist_candidates = get_watchlist_candidates(
            positions=positions,
            log_print=log_print,
            run_mode=clean_mode,
        )
        tradier_snapshot["_watchlist_candidates"] = watchlist_candidates
    except Exception as e:
        log_print(f"ERROR in Watchlist Candidate Pipeline v1: {e}\n{traceback.format_exc()}")
        watchlist_candidates = {
            "source": "watchlist_pipeline_v1",
            "enabled": True,
            "has_data": False,
            "items": [],
            "tickers": [],
            "errors": [str(e)],
            "summary": {"candidate_count": 0, "new_candidate_count": 0, "already_held_count": 0, "scan_universe_count": 0},
        }
        tradier_snapshot["_watchlist_candidates"] = watchlist_candidates

    analysis_positions = merge_watchlist_universe_positions(positions, watchlist_candidates)
    analysis_tickers = list(dict.fromkeys(p.get("ticker") for p in analysis_positions if p.get("ticker")))
    watchlist_tickers = [item.get("ticker") for item in (watchlist_candidates or {}).get("items", []) if item.get("ticker")]
    if watchlist_tickers:
        log_print(f"Watchlist tickers added to scan universe: {watchlist_tickers}")
        log_print(f"Analysis universe tickers: {analysis_tickers}")

    external_tickers = _external_provider_tickers(analysis_tickers, clean_mode)
    if clean_mode == "dev":
        log_print(f"DEV MODE external provider ticker subset: {external_tickers}")

    log_print("Fetching relevance-scored news...")

    try:
        news = get_news_for_tickers(
            external_tickers,
            max_tickers=_news_max_tickers_for_mode(clean_mode),
        )
        news = _fill_missing_news_keys(analysis_tickers, news)
        article_count = sum(len(articles) for articles in news.values())
        log_print(f"News fetched for {len(news)} tickers; {article_count} relevant article(s)")
    except Exception as e:
        log_print(f"ERROR in get_news_for_tickers: {e}\n{traceback.format_exc()}")
        news = _fill_missing_news_keys(analysis_tickers, {})

    log_print("Fetching Finnhub Market Data v1...")

    try:
        market_metrics = get_market_metrics_for_positions(
            analysis_positions,
            log_print=log_print,
            max_tickers=_market_max_tickers_for_mode(clean_mode),
            allowed_tickers=external_tickers if clean_mode == "dev" else None,
        )
    except Exception as e:
        log_print(f"ERROR in Market Data v1: {e}\n{traceback.format_exc()}")
        market_metrics = {}

    log_print("Fetching Earnings Timestamp Provider v1...")

    try:
        earnings_events = get_earnings_for_positions(
            analysis_positions,
            log_print=log_print,
            max_tickers=_earnings_max_tickers_for_mode(clean_mode),
            allowed_tickers=external_tickers if clean_mode == "dev" else None,
        )
        fetched_count = sum(1 for item in earnings_events.values() if item.get("has_data"))
        log_print(f"Earnings Timestamp Provider v1 fetched {fetched_count}/{len(earnings_events)} event(s)")
    except Exception as e:
        log_print(f"ERROR in Earnings Timestamp Provider v1: {e}\n{traceback.format_exc()}")
        earnings_events = {}

    log_print("Fetching Earnings Trade Discovery v1...")

    try:
        earnings_trade_discovery = discover_upcoming_earnings_for_calendar_trades(
            log_print=log_print,
            run_mode=clean_mode,
        )
    except Exception as e:
        log_print(f"ERROR in Earnings Trade Discovery v1: {e}\n{traceback.format_exc()}")
        earnings_trade_discovery = {
            "source": "earnings_discovery_v1",
            "enabled": True,
            "has_data": False,
            "items": [],
            "events_by_ticker": {},
            "tickers": [],
            "errors": [str(e)],
            "summary": {"event_count": 0, "ticker_count": 0},
        }

    log_print("Fetching Tradier Provider v1...")

    try:
        tradier_snapshot = get_tradier_snapshot_for_positions(
            analysis_positions,
            log_print=log_print,
            max_tickers=_tradier_max_tickers_for_mode(clean_mode),
            allowed_tickers=external_tickers if clean_mode == "dev" else None,
        )
        tradier_count = sum(1 for item in tradier_snapshot.values() if item.get("has_data"))
        log_print(f"Tradier Provider v1 fetched {tradier_count}/{len(tradier_snapshot)} ticker snapshot(s)")
    except Exception as e:
        log_print(f"ERROR in Tradier Provider v1: {e}\n{traceback.format_exc()}")
        tradier_snapshot = {}

    # Re-attach non-provider metadata after Tradier Provider v1 replaces the snapshot dict.
    tradier_snapshot["_watchlist_candidates"] = watchlist_candidates
    tradier_snapshot["_earnings_events"] = {
        "items": earnings_events,
        "has_data": any(item.get("has_data") for item in earnings_events.values()),
        "source": config.EARNINGS_PROVIDER,
    }
    tradier_snapshot["_earnings_trade_discovery"] = earnings_trade_discovery

    log_print("Running Calendar Spread Screener v1 for earnings-discovery universe...")

    try:
        discovery_tickers = [
            str(t).upper().strip()
            for t in (earnings_trade_discovery or {}).get("tickers", [])
            if str(t).strip()
        ]
        discovery_positions = _positions_from_earnings_discovery(earnings_trade_discovery)
        if not discovery_positions:
            log_print("Calendar Spread Screener v1 skipped: no earnings-discovery tickers in configured window.")
            calendar_candidates = []
        else:
            log_print(f"Calendar scanner universe from earnings discovery: {discovery_tickers}")
            calendar_candidates = scan_calendar_spreads_for_positions(
                discovery_positions,
                log_print=log_print,
                max_tickers=_calendar_max_tickers_for_mode(clean_mode),
                allowed_tickers=discovery_tickers,
            )
        tradier_snapshot["_calendar_spread_candidates"] = {
            "items": calendar_candidates,
            "has_data": bool(calendar_candidates),
            "source": "tradier",
            "universe_source": "earnings_discovery_v1",
        }
        log_print(f"Calendar Spread Screener v1 produced {len(calendar_candidates)} earnings-discovery candidate(s)")
    except Exception as e:
        log_print(f"ERROR in Calendar Spread Screener v1: {e}\n{traceback.format_exc()}")
        tradier_snapshot["_calendar_spread_candidates"] = {
            "items": [],
            "has_data": False,
            "source": "tradier",
            "universe_source": "earnings_discovery_v1",
            "error": str(e),
        }

    log_print("Running Earnings Calendar Strategy v1...")

    try:
        earnings_calendar_strategy = evaluate_earnings_calendar_candidates(
            calendar_candidates=tradier_snapshot.get("_calendar_spread_candidates", {}).get("items", [])
            if isinstance(tradier_snapshot.get("_calendar_spread_candidates", {}), dict) else [],
            earnings_events=_merge_earnings_events(earnings_events, earnings_trade_discovery),
            log_print=log_print,
        )
        tradier_snapshot["_earnings_calendar_strategy"] = earnings_calendar_strategy
        summary = earnings_calendar_strategy.get("summary", {}) if isinstance(earnings_calendar_strategy, dict) else {}
        log_print(
            "Earnings Calendar Strategy v1 produced "
            f"{summary.get('candidate_count', 0)} evaluation(s), "
            f"{summary.get('preferred_count', 0)} preferred, "
            f"{summary.get('urgent_count', 0)} urgent-review."
        )
    except Exception as e:
        log_print(f"ERROR in Earnings Calendar Strategy v1: {e}\n{traceback.format_exc()}")
        tradier_snapshot["_earnings_calendar_strategy"] = {
            "source": "earnings_calendar_strategy_v1",
            "enabled": True,
            "has_data": False,
            "items": [],
            "errors": [str(e)],
            "summary": {
                "candidate_count": 0,
                "preferred_count": 0,
                "urgent_count": 0,
                "avoid_count": 0,
                "manual_review_count": 0,
                "has_candidates": False,
            },
        }

    log_print("Running Watchlist Stock Candidate Review v2...")

    try:
        watchlist_review = review_watchlist_candidates(
            watchlist_result=watchlist_candidates,
            tradier_snapshot=tradier_snapshot,
            earnings_events=earnings_events,
            news_map=news,
            positions=positions,
            log_print=log_print,
        )
        tradier_snapshot["_watchlist_review"] = watchlist_review
        summary = watchlist_review.get("summary", {}) if isinstance(watchlist_review, dict) else {}
        log_print(
            "Watchlist Stock Candidate Review v2 produced "
            f"{summary.get('candidate_count', 0)} review(s), "
            f"{summary.get('potential_trade_count', 0)} potential trade setup(s), "
            f"{summary.get('urgent_count', 0)} urgent."
        )
    except Exception as e:
        log_print(f"ERROR in Watchlist Stock Candidate Review v2: {e}\n{traceback.format_exc()}")
        tradier_snapshot["_watchlist_review"] = {
            "source": "watchlist_stock_candidate_review_v2",
            "enabled": True,
            "has_data": False,
            "items": [],
            "errors": [str(e)],
            "summary": {
                "candidate_count": 0,
                "new_candidate_count": 0,
                "already_held_count": 0,
                "potential_trade_count": 0,
                "urgent_count": 0,
            },
        }

    log_print("Detecting Open Options Positions v1...")

    try:
        open_options = detect_open_options_positions(log_print=log_print)
        tradier_snapshot["_open_options_positions"] = open_options
        summary = open_options.get("summary", {}) if isinstance(open_options, dict) else {}
        log_print(
            "Open Options Position Detector v1 produced "
            f"{summary.get('option_leg_count', 0)} option leg(s) and "
            f"{summary.get('calendar_count', 0)} detected calendar(s)."
        )
    except Exception as e:
        log_print(f"ERROR in Open Options Position Detector v1: {e}\n{traceback.format_exc()}")
        tradier_snapshot["_open_options_positions"] = {
            "source": "tradier",
            "has_data": False,
            "enabled": True,
            "configured": bool(config.TRADIER_ACCESS_TOKEN),
            "account_ids": [],
            "positions": [],
            "option_legs": [],
            "calendars": [],
            "errors": [str(e)],
            "summary": {
                "account_count": 0,
                "total_positions": 0,
                "option_leg_count": 0,
                "calendar_count": 0,
                "has_open_options": False,
                "has_open_calendars": False,
            },
        }

    log_print("Running Calendar Lifecycle Check v1...")

    try:
        lifecycle_checks = evaluate_calendar_lifecycle(
            open_options=tradier_snapshot.get("_open_options_positions", {}),
            tradier_snapshot=tradier_snapshot,
            earnings_events=_merge_earnings_events(earnings_events, earnings_trade_discovery),
            log_print=log_print,
        )
        tradier_snapshot["_calendar_lifecycle_checks"] = lifecycle_checks
        summary = lifecycle_checks.get("summary", {}) if isinstance(lifecycle_checks, dict) else {}
        log_print(
            "Calendar Lifecycle Check v1 produced "
            f"{summary.get('calendar_count', 0)} check(s), "
            f"{summary.get('urgent_count', 0)} urgent, "
            f"{summary.get('exit_review_count', 0)} exit-review."
        )
    except Exception as e:
        log_print(f"ERROR in Calendar Lifecycle Check v1: {e}\n{traceback.format_exc()}")
        tradier_snapshot["_calendar_lifecycle_checks"] = {
            "source": "calendar_lifecycle_v1",
            "enabled": True,
            "has_data": False,
            "checks": [],
            "errors": [str(e)],
            "summary": {
                "calendar_count": 0,
                "urgent_count": 0,
                "exit_review_count": 0,
                "has_open_calendars": False,
            },
        }

    log_print("Running Portfolio Scoring v2 inputs...")

    try:
        strategy = PortfolioSnapshotStrategy()
        recommendations = strategy.evaluate_portfolio(
            positions=positions,
            news_map=news,
            market_metrics=market_metrics,
        )
        log_print(f"Portfolio scoring generated {len(recommendations)} recommendation(s)")
    except Exception as e:
        log_print(f"ERROR in Portfolio Scoring: {e}\n{traceback.format_exc()}")
        recommendations = []

    log_print("Formatting payload...")

    try:
        payload = format_payload(positions, news, recommendations, tradier_snapshot)
        if clean_mode == "dev":
            payload = "MODE: DEV — external provider calls limited for API-budget-safe testing.\n\n" + payload
        log_print(f"Payload length: {len(payload)} chars")
    except Exception as e:
        log_print(f"ERROR in format_payload: {e}\n{traceback.format_exc()}")
        return None, positions, news, recommendations, tradier_snapshot, log

    log_print("=== RUN COMPLETE ===")
    return payload, positions, news, recommendations, tradier_snapshot, log


def _positions_from_earnings_discovery(discovery: dict[str, Any]) -> list[dict[str, Any]]:
    positions: list[dict[str, Any]] = []
    for event in (discovery or {}).get("items", []) or []:
        ticker = str(event.get("ticker") or event.get("symbol") or "").upper().strip()
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
                "source": "earnings_discovery_v1",
                "earnings_event": event,
            }
        )
    return positions


def _merge_earnings_events(
    earnings_events: dict[str, dict[str, Any]],
    discovery: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    merged = dict(earnings_events or {})
    for ticker, event in ((discovery or {}).get("events_by_ticker", {}) or {}).items():
        clean = str(ticker).upper().strip()
        if clean:
            merged[clean] = event
    return merged


def _normalize_run_mode(run_mode: str | None) -> str:
    value = str(run_mode or config.APP_MODE or "prod").strip().lower()
    return "dev" if value in {"dev", "development", "test", "testing"} else "prod"


def _external_provider_tickers(tickers: list[str], run_mode: str) -> list[str]:
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


def _news_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return None


def _market_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return max(1, int(config.MARKET_DATA_MAX_TICKERS_PER_RUN or 1))


def _tradier_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return max(1, int(config.TRADIER_MAX_TICKERS_PER_RUN or 1))


def _calendar_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return max(1, int(config.CALENDAR_MAX_TICKERS_PER_RUN or 1))


def _earnings_max_tickers_for_mode(run_mode: str) -> int | None:
    if run_mode == "dev":
        return max(1, int(config.DEV_MAX_TICKERS or 1))
    return max(1, int(config.EARNINGS_MAX_TICKERS_PER_RUN or 1))


def _fill_missing_news_keys(
    all_tickers: list[str],
    news: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    filled = {str(t).upper().strip(): [] for t in all_tickers if str(t).strip()}
    for ticker, articles in news.items():
        filled[str(ticker).upper().strip()] = articles or []
    return filled
