"""
app/services/analysis_service.py — Main pipeline orchestration.

This file intentionally keeps the public return shape stable for app/main.py:
(payload, positions, news, recommendations, tradier_snapshot, log)

The pipeline internals are now cleaner and more explicit:
- pipeline_helpers.py owns run-mode and ticker-limit helpers
- pipeline_status_service.py records a structured step-by-step status
- provider/strategy modules remain read-only and independently defensive
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from app import config
from app.providers.news_provider import get_news_for_tickers
from app.services.calendar_lifecycle_service import evaluate_calendar_lifecycle
from app.services.calendar_ranking_service import build_calendar_ranking
from app.services.calendar_spread_service import scan_calendar_spreads_for_positions
from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine
from app.services.earnings_calendar_strategy_service import evaluate_earnings_calendar_candidates
from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
from app.services.earnings_service import discover_upcoming_earnings_for_calendar_trades, get_earnings_for_positions
from app.services.earnings_mini_backtest_service import build_earnings_mini_backtest
from app.services.market_data_service import get_market_metrics_for_positions
from app.services.open_options_service import detect_open_options_positions
from app.services.pipeline_helpers import (
    calendar_max_tickers_for_mode,
    config_log_lines,
    config_snapshot,
    earnings_max_tickers_for_mode,
    external_provider_tickers,
    fill_missing_news_keys,
    market_max_tickers_for_mode,
    merge_earnings_events,
    merge_provider_ticker_sets,
    news_max_tickers_for_mode,
    normalize_run_mode,
    positions_from_earnings_discovery,
    tradier_max_tickers_for_mode,
)
from app.services.pipeline_status_service import (
    begin_step,
    complete_step,
    fail_step,
    finish_pipeline,
    new_pipeline_status,
    warn_step,
)
from app.services.portfolio_gap_service import build_portfolio_gap_analysis
from app.services.portfolio_service import get_portfolio_positions
from app.services.report_service import format_payload
from app.services.stock_momentum_strategy_service import build_stock_momentum_strategy, select_stock_momentum_market_data_tickers
from app.services.tradier_service import get_tradier_snapshot_for_positions
from app.services.unified_calendar_trade_engine_service import build_unified_calendar_trade_engine
from app.services.watchlist_review_service import review_watchlist_candidates
from app.services.watchlist_service import get_watchlist_candidates, merge_watchlist_universe_positions
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


EMPTY_WATCHLIST = {
    "source": "watchlist_pipeline_v1",
    "enabled": True,
    "has_data": False,
    "items": [],
    "tickers": [],
    "errors": [],
    "summary": {"candidate_count": 0, "new_candidate_count": 0, "already_held_count": 0, "scan_universe_count": 0},
}

EMPTY_EARNINGS_DISCOVERY = {
    "source": "earnings_discovery_v1",
    "enabled": True,
    "has_data": False,
    "items": [],
    "events_by_ticker": {},
    "tickers": [],
    "errors": [],
    "summary": {"event_count": 0, "ticker_count": 0},
}

EMPTY_EARNINGS_QUALITY = {
    "source": "earnings_discovery_quality_filter_v1",
    "enabled": True,
    "has_data": False,
    "items": [],
    "passed_items": [],
    "rejected_items": [],
    "tickers": [],
    "events_by_ticker": {},
    "errors": [],
    "summary": {"raw_event_count": 0, "checked_count": 0, "passed_count": 0, "rejected_count": 0},
}

EMPTY_OPEN_OPTIONS = {
    "source": "tradier",
    "has_data": False,
    "enabled": True,
    "configured": bool(config.TRADIER_ACCESS_TOKEN),
    "account_ids": [],
    "positions": [],
    "option_legs": [],
    "calendars": [],
    "errors": [],
    "summary": {
        "account_count": 0,
        "total_positions": 0,
        "option_leg_count": 0,
        "calendar_count": 0,
        "has_open_options": False,
        "has_open_calendars": False,
    },
}

EMPTY_LIFECYCLE = {
    "source": "calendar_lifecycle_v1",
    "enabled": True,
    "has_data": False,
    "checks": [],
    "errors": [],
    "summary": {
        "calendar_count": 0,
        "urgent_count": 0,
        "exit_review_count": 0,
        "has_open_calendars": False,
    },
}

EMPTY_TRADE_MEMORY = {
    "source": "sqlite_trade_memory_v1",
    "enabled": True,
    "has_data": False,
    "open_trades": [],
    "closed_trades": [],
    "watch_trades": [],
    "matches": [],
    "errors": [],
    "summary": {"open_count": 0, "closed_count": 0, "watch_count": 0, "match_count": 0},
}

EMPTY_UNIFIED_CALENDAR = {
    "source": "unified_calendar_trade_engine_v1",
    "enabled": True,
    "has_data": False,
    "new_trade_rows": [],
    "open_trade_rows": [],
    "errors": [],
    "summary": {
        "new_trade_count": 0,
        "open_trade_count": 0,
        "pass_count": 0,
        "watch_count": 0,
        "fail_count": 0,
        "urgent_count": 0,
    },
}

EMPTY_CALENDAR_RANKING = {
    "source": "calendar_ranking_v2",
    "enabled": True,
    "has_data": False,
    "items": [],
    "eligible_for_backtest": [],
    "errors": [],
    "summary": {"candidate_count": 0, "pass_count": 0, "backtest_eligible_count": 0},
}

EMPTY_EARNINGS_BACKTEST = {
    "source": "earnings_mini_backtest_v1",
    "enabled": True,
    "has_data": False,
    "items": [],
    "errors": [],
    "summary": {"candidate_count": 0, "with_history_count": 0},
}


def _enrich_open_options_with_underlying_prices(
    open_options: dict[str, Any] | None,
    positions: list[dict[str, Any]] | None,
    tradier_snapshot: dict[str, Any] | None,
    market_metrics: dict[str, Any] | None,
) -> None:
    """Attach best-known underlying prices to detected option calendars.

    Dev mode often prices only a small equity subset through Tradier, so an
    active Robinhood calendar such as PDD may not have a quote in
    ``tradier_snapshot``. The stock position payload is still a reliable source
    for current underlying price, and lifecycle risk/moneyness depends on it.
    This helper mutates the open-options object in-place before lifecycle checks.
    """
    if not isinstance(open_options, dict):
        return

    price_by_ticker: dict[str, tuple[float, str]] = {}

    for pos in positions or []:
        ticker = str((pos or {}).get("ticker") or "").upper().strip()
        price = _safe_float((pos or {}).get("current_price"))
        if ticker and price is not None and price > 0:
            price_by_ticker.setdefault(ticker, (price, "robinhood_position"))

    for ticker, data in (tradier_snapshot or {}).items():
        if str(ticker).startswith("_") or not isinstance(data, dict):
            continue
        quote = data.get("quote") if isinstance(data.get("quote"), dict) else {}
        for key in ("last", "mark", "bid", "ask", "close", "prevclose"):
            price = _safe_float(quote.get(key))
            if price is not None and price > 0:
                price_by_ticker[str(ticker).upper()] = (price, f"tradier_quote.{key}")
                break

    for ticker, data in (market_metrics or {}).items():
        if not isinstance(data, dict):
            continue
        for key in ("last_price", "close", "current_price"):
            price = _safe_float(data.get(key))
            if price is not None and price > 0:
                price_by_ticker.setdefault(str(ticker).upper(), (price, f"market_metrics.{key}"))
                break

    for cal in open_options.get("calendars", []) or []:
        if not isinstance(cal, dict):
            continue
        ticker = str(cal.get("ticker") or cal.get("underlying") or "").upper().strip()
        if not ticker or ticker not in price_by_ticker:
            continue
        price, source = price_by_ticker[ticker]
        cal["underlying_price"] = price
        cal["underlying_price_source"] = source
        for leg_key in ("short_front_leg", "long_back_leg"):
            leg = cal.get(leg_key)
            if isinstance(leg, dict):
                leg.setdefault("underlying_price", price)
                leg.setdefault("underlying_price_source", source)


def _safe_float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _estimate_account_value(positions: list[dict[str, Any]]) -> float | None:
    total = 0.0
    for pos in positions or []:
        if not isinstance(pos, dict):
            continue
        value = _safe_float(pos.get("market_value") or pos.get("equity") or pos.get("current_value"))
        if value is None:
            qty = _safe_float(pos.get("quantity") or pos.get("shares"))
            price = _safe_float(pos.get("current_price") or pos.get("price") or pos.get("last_price"))
            value = qty * price if qty is not None and price is not None else None
        if value is not None and value > 0:
            total += value
    return round(total, 2) if total > 0 else None


def run_portfolio_pipeline(run_mode: str = "prod") -> PipelineResult:
    log: list[str] = []
    news: dict[str, list[dict[str, Any]]] = {}
    market_metrics: dict[str, dict[str, Any]] = {}
    recommendations: list[dict[str, Any]] = []
    tradier_snapshot: dict[str, dict[str, Any]] = {}
    earnings_events: dict[str, dict[str, Any]] = {}
    watchlist_candidates: dict[str, Any] = dict(EMPTY_WATCHLIST)
    watchlist_review: dict[str, Any] = {}
    earnings_trade_discovery: dict[str, Any] = dict(EMPTY_EARNINGS_DISCOVERY)
    earnings_discovery_quality: dict[str, Any] = dict(EMPTY_EARNINGS_QUALITY)
    portfolio_gap_analysis: dict[str, Any] = {}
    stock_momentum_strategy: dict[str, Any] = {}
    daily_opportunity_engine: dict[str, Any] = {}
    calendar_ranking: dict[str, Any] = dict(EMPTY_CALENDAR_RANKING)
    earnings_mini_backtest: dict[str, Any] = dict(EMPTY_EARNINGS_BACKTEST)
    trade_memory: dict[str, Any] = dict(EMPTY_TRADE_MEMORY)

    clean_mode = normalize_run_mode(run_mode)
    pipeline_status = new_pipeline_status(clean_mode)

    def log_print(msg: str) -> None:
        safe_msg = sanitize_for_log(
            msg,
            known_secrets=[
                config.ROBINHOOD_PASSWORD,
                config.NEWS_API_KEY,
                config.FINNHUB_API_KEY,
                config.ALPHA_VANTAGE_API_KEY,
                config.TRADIER_ACCESS_TOKEN,
                config.RUN_TOKEN,
                config.NTFY_TOPIC,
            ],
        )
        print(safe_msg, flush=True)
        log.append(safe_msg)

    def attach_status() -> None:
        tradier_snapshot["_pipeline_status"] = pipeline_status

    def run_optional_step(
        key: str,
        label: str,
        func: Callable[[], Any],
        fallback: Any,
        success_message: Callable[[Any], str] | str = "Complete.",
    ) -> Any:
        begin_step(pipeline_status, key, label)
        log_print(label)
        try:
            result = func()
            message = success_message(result) if callable(success_message) else success_message
            complete_step(pipeline_status, key, message)
            return result
        except Exception as exc:
            message = f"{label} failed: {exc}"
            log_print(f"ERROR in {label}: {exc}\n{traceback.format_exc()}")
            fail_step(pipeline_status, key, message, {"error": str(exc)})
            fallback_copy = dict(fallback) if isinstance(fallback, dict) else fallback
            if isinstance(fallback_copy, dict):
                fallback_copy.setdefault("errors", []).append(str(exc))
            return fallback_copy

    log_print("=== RUN STARTED ===")

    begin_step(pipeline_status, "config", "Load configuration")
    try:
        snapshot = config_snapshot(clean_mode)
        pipeline_status["config_snapshot"] = snapshot
        for line in ["robinhood imported OK", "news imported OK", "market data imported OK", "tradier imported OK", *config_log_lines(snapshot)]:
            log_print(line)
        complete_step(pipeline_status, "config", "Configuration loaded.")
    except Exception as exc:
        log_print(f"IMPORT ERROR config: {exc}\n{traceback.format_exc()}")
        fail_step(pipeline_status, "config", f"Configuration failed: {exc}")
        finish_pipeline(pipeline_status, "error")
        attach_status()
        return None, [], news, recommendations, tradier_snapshot, log

    begin_step(pipeline_status, "positions", "Fetch Robinhood positions")
    log_print("Fetching Robinhood positions...")
    try:
        positions = get_portfolio_positions()
        log_print(f"get_positions returned {len(positions)} positions")
        if not positions:
            warn_step(pipeline_status, "positions", "No positions found or login failed.")
            finish_pipeline(pipeline_status, "error")
            attach_status()
            log_print("No positions found or login failed.")
            return None, [], news, recommendations, tradier_snapshot, log
        complete_step(pipeline_status, "positions", f"Fetched {len(positions)} position(s).")
    except Exception as exc:
        log_print(f"ERROR in get_positions: {exc}\n{traceback.format_exc()}")
        fail_step(pipeline_status, "positions", f"Robinhood positions failed: {exc}")
        finish_pipeline(pipeline_status, "error")
        attach_status()
        return None, [], news, recommendations, tradier_snapshot, log

    portfolio_tickers = list(dict.fromkeys(p.get("ticker") for p in positions if p.get("ticker")))
    log_print(f"Tickers: {portfolio_tickers}")

    watchlist_candidates = run_optional_step(
        "watchlist_candidates",
        "Fetching Watchlist Candidate Pipeline v1...",
        lambda: get_watchlist_candidates(positions=positions, log_print=log_print, run_mode=clean_mode),
        EMPTY_WATCHLIST,
        lambda result: f"Watchlist pipeline produced {len((result or {}).get('items', []) or [])} candidate(s).",
    )
    tradier_snapshot["_watchlist_candidates"] = watchlist_candidates

    analysis_positions = merge_watchlist_universe_positions(positions, watchlist_candidates)
    analysis_tickers = list(dict.fromkeys(p.get("ticker") for p in analysis_positions if p.get("ticker")))
    watchlist_tickers = [item.get("ticker") for item in (watchlist_candidates or {}).get("items", []) if item.get("ticker")]
    if watchlist_tickers:
        log_print(f"Watchlist tickers added to scan universe: {watchlist_tickers}")
        log_print(f"Analysis universe tickers: {analysis_tickers}")

    base_external_tickers = external_provider_tickers(analysis_tickers, clean_mode)
    stock_momentum_market_tickers = select_stock_momentum_market_data_tickers(
        positions=positions,
        watchlist_candidates=watchlist_candidates,
        run_mode=clean_mode,
    )
    external_tickers = merge_provider_ticker_sets(base_external_tickers, stock_momentum_market_tickers)
    pipeline_status["ticker_universe"] = {
        "portfolio_tickers": portfolio_tickers,
        "analysis_tickers": analysis_tickers,
        "base_external_tickers": base_external_tickers,
        "stock_momentum_market_tickers": stock_momentum_market_tickers,
        "external_tickers": external_tickers,
    }
    if clean_mode == "dev":
        log_print(f"DEV MODE base external provider ticker subset: {base_external_tickers}")
        if stock_momentum_market_tickers:
            log_print(f"DEV MODE stock-momentum market-data additions: {stock_momentum_market_tickers}")
        log_print(f"DEV MODE final external provider ticker subset: {external_tickers}")

    news = run_optional_step(
        "news",
        "Fetching relevance-scored news...",
        lambda: fill_missing_news_keys(
            analysis_tickers,
            get_news_for_tickers(external_tickers, max_tickers=news_max_tickers_for_mode(clean_mode)),
        ),
        fill_missing_news_keys(analysis_tickers, {}),
        lambda result: f"News map prepared for {len(result or {})} ticker(s); {sum(len(v) for v in (result or {}).values())} article(s).",
    )

    market_metrics = run_optional_step(
        "market_data",
        "Fetching market data / Tradier fallback...",
        lambda: get_market_metrics_for_positions(
            analysis_positions,
            log_print=log_print,
            max_tickers=market_max_tickers_for_mode(clean_mode),
            allowed_tickers=external_tickers if clean_mode == "dev" else None,
        ),
        {},
        lambda result: f"Market metrics available for {sum(1 for item in (result or {}).values() if item.get('has_data'))}/{len(result or {})} ticker(s).",
    )

    earnings_events = run_optional_step(
        "earnings_timestamp",
        "Fetching Earnings Timestamp Provider v1...",
        lambda: get_earnings_for_positions(
            analysis_positions,
            log_print=log_print,
            max_tickers=earnings_max_tickers_for_mode(clean_mode),
            allowed_tickers=external_tickers if clean_mode == "dev" else None,
        ),
        {},
        lambda result: f"Earnings timestamp events available for {sum(1 for item in (result or {}).values() if item.get('has_data'))}/{len(result or {})} ticker(s).",
    )

    earnings_trade_discovery = run_optional_step(
        "earnings_discovery",
        "Fetching Earnings Trade Discovery v1...",
        lambda: discover_upcoming_earnings_for_calendar_trades(log_print=log_print, run_mode=clean_mode),
        EMPTY_EARNINGS_DISCOVERY,
        lambda result: f"Earnings discovery found {len((result or {}).get('items', []) or [])} raw event row(s).",
    )

    earnings_discovery_quality = run_optional_step(
        "earnings_quality_filter",
        "Running Earnings Discovery Quality Filter v1...",
        lambda: filter_earnings_discovery_for_calendar_scan(
            earnings_trade_discovery=earnings_trade_discovery,
            log_print=log_print,
            run_mode=clean_mode,
        ),
        EMPTY_EARNINGS_QUALITY,
        lambda result: f"Quality filter passed {len((result or {}).get('passed_items', []) or [])} optionable ticker(s).",
    )

    tradier_snapshot = run_optional_step(
        "tradier_snapshot",
        "Fetching Tradier Provider v1...",
        lambda: get_tradier_snapshot_for_positions(
            analysis_positions,
            log_print=log_print,
            max_tickers=tradier_max_tickers_for_mode(clean_mode),
            allowed_tickers=external_tickers if clean_mode == "dev" else None,
        ),
        {},
        lambda result: f"Tradier snapshots available for {sum(1 for item in (result or {}).values() if isinstance(item, dict) and item.get('has_data'))}/{len(result or {})} entries.",
    )

    # Re-attach metadata after the Tradier provider replaces the snapshot dict.
    tradier_snapshot["_watchlist_candidates"] = watchlist_candidates
    tradier_snapshot["_earnings_events"] = {
        "items": earnings_events,
        "has_data": any(item.get("has_data") for item in earnings_events.values()),
        "source": config.EARNINGS_PROVIDER,
    }
    tradier_snapshot["_earnings_trade_discovery"] = earnings_trade_discovery
    tradier_snapshot["_earnings_discovery_quality"] = earnings_discovery_quality

    def run_calendar_scan() -> list[dict[str, Any]]:
        discovery_tickers = [str(t).upper().strip() for t in (earnings_discovery_quality or {}).get("tickers", []) if str(t).strip()]
        discovery_positions = positions_from_earnings_discovery(earnings_discovery_quality or earnings_trade_discovery)
        if not discovery_positions:
            log_print("Calendar Spread Screener v1 skipped: no earnings-discovery tickers passed quality precheck.")
            return []
        log_print(f"Calendar scanner universe from earnings discovery quality filter: {discovery_tickers}")
        return scan_calendar_spreads_for_positions(
            discovery_positions,
            log_print=log_print,
            max_tickers=calendar_max_tickers_for_mode(clean_mode),
            allowed_tickers=discovery_tickers,
        )

    calendar_candidates = run_optional_step(
        "calendar_spread_scan",
        "Running Calendar Spread Screener v1 for earnings-discovery universe...",
        run_calendar_scan,
        [],
        lambda result: f"Calendar scanner produced {len(result or [])} earnings-discovery candidate(s).",
    )
    tradier_snapshot["_calendar_spread_candidates"] = {
        "items": calendar_candidates,
        "has_data": bool(calendar_candidates),
        "source": "tradier",
        "universe_source": "earnings_discovery_v1",
    }

    earnings_calendar_strategy = run_optional_step(
        "earnings_calendar_strategy",
        "Running Earnings Calendar Strategy v1...",
        lambda: evaluate_earnings_calendar_candidates(
            calendar_candidates=calendar_candidates,
            earnings_events=merge_earnings_events(earnings_events, earnings_trade_discovery),
            log_print=log_print,
        ),
        {
            "source": "earnings_calendar_strategy_v1",
            "enabled": True,
            "has_data": False,
            "items": [],
            "errors": [],
            "summary": {"candidate_count": 0, "preferred_count": 0, "urgent_count": 0, "avoid_count": 0, "manual_review_count": 0, "has_candidates": False},
        },
        lambda result: f"Earnings strategy evaluated {((result or {}).get('summary', {}) or {}).get('candidate_count', 0)} candidate(s).",
    )
    tradier_snapshot["_earnings_calendar_strategy"] = earnings_calendar_strategy

    watchlist_review = run_optional_step(
        "watchlist_review",
        "Running Watchlist Stock Candidate Review v2...",
        lambda: review_watchlist_candidates(
            watchlist_result=watchlist_candidates,
            tradier_snapshot=tradier_snapshot,
            earnings_events=earnings_events,
            news_map=news,
            positions=positions,
            log_print=log_print,
        ),
        {
            "source": "watchlist_stock_candidate_review_v2",
            "enabled": True,
            "has_data": False,
            "items": [],
            "errors": [],
            "summary": {"candidate_count": 0, "new_candidate_count": 0, "already_held_count": 0, "potential_trade_count": 0, "urgent_count": 0},
        },
        lambda result: f"Watchlist review produced {((result or {}).get('summary', {}) or {}).get('candidate_count', 0)} row(s).",
    )
    tradier_snapshot["_watchlist_review"] = watchlist_review

    open_options = run_optional_step(
        "open_options",
        "Detecting Open Options Positions v1...",
        lambda: detect_open_options_positions(log_print=log_print),
        EMPTY_OPEN_OPTIONS,
        lambda result: f"Detected {((result or {}).get('summary', {}) or {}).get('calendar_count', 0)} open calendar(s).",
    )
    _enrich_open_options_with_underlying_prices(open_options, positions, tradier_snapshot, market_metrics)
    tradier_snapshot["_open_options_positions"] = open_options

    # Manual trade memory/input is intentionally out of scope. Open calendar
    # lifecycle checks should come from automatically detected broker positions.
    trade_memory = dict(EMPTY_TRADE_MEMORY)
    trade_memory["enabled"] = False
    trade_memory["errors"] = ["Manual trade memory disabled; lifecycle uses auto-detected broker option positions."]

    lifecycle_checks = run_optional_step(
        "calendar_lifecycle",
        "Running Calendar Lifecycle Check v1...",
        lambda: evaluate_calendar_lifecycle(
            open_options=open_options,
            tradier_snapshot=tradier_snapshot,
            earnings_events=merge_earnings_events(earnings_events, earnings_trade_discovery),
            trade_memory=None,
            log_print=log_print,
        ),
        EMPTY_LIFECYCLE,
        lambda result: f"Lifecycle checker produced {((result or {}).get('summary', {}) or {}).get('calendar_count', 0)} check(s).",
    )
    tradier_snapshot["_calendar_lifecycle_checks"] = lifecycle_checks

    account_context = {"account_value_estimate": _estimate_account_value(positions)}

    calendar_ranking = run_optional_step(
        "calendar_ranking",
        "Running Calendar Ranking v2...",
        lambda: build_calendar_ranking(
            calendar_candidates=calendar_candidates,
            earnings_calendar_strategy=earnings_calendar_strategy,
            log_print=log_print,
        ),
        EMPTY_CALENDAR_RANKING,
        lambda result: f"Calendar ranking found {((result or {}).get('summary', {}) or {}).get('pass_count', 0)} fully-qualified candidate(s).",
    )
    tradier_snapshot["_calendar_ranking"] = calendar_ranking

    unified_calendar_engine = run_optional_step(
        "unified_calendar_engine",
        "Running Unified Calendar Trade Engine v1...",
        lambda: build_unified_calendar_trade_engine(
            earnings_trade_discovery=earnings_trade_discovery,
            earnings_discovery_quality=earnings_discovery_quality,
            calendar_candidates=calendar_candidates,
            earnings_calendar_strategy=earnings_calendar_strategy,
            calendar_ranking=calendar_ranking,
            account_context=account_context,
            open_options=open_options,
            lifecycle_checks=lifecycle_checks,
            log_print=log_print,
        ),
        EMPTY_UNIFIED_CALENDAR,
        lambda result: f"Unified calendar engine produced {((result or {}).get('summary', {}) or {}).get('new_trade_count', 0)} new-trade row(s).",
    )
    tradier_snapshot["_unified_calendar_trade_engine"] = unified_calendar_engine

    earnings_mini_backtest = run_optional_step(
        "earnings_mini_backtest",
        "Running Earnings Mini-Backtest v1...",
        lambda: build_earnings_mini_backtest(
            calendar_ranking=calendar_ranking,
            log_print=log_print,
        ),
        EMPTY_EARNINGS_BACKTEST,
        lambda result: f"Earnings mini-backtest produced history for {((result or {}).get('summary', {}) or {}).get('with_history_count', 0)} candidate(s).",
    )
    tradier_snapshot["_earnings_mini_backtest"] = earnings_mini_backtest

    begin_step(pipeline_status, "portfolio_scoring", "Running Portfolio Scoring v2 inputs...")
    log_print("Running Portfolio Scoring v2 inputs...")
    try:
        recommendations = PortfolioSnapshotStrategy().evaluate_portfolio(
            positions=positions,
            news_map=news,
            market_metrics=market_metrics,
        )
        log_print(f"Portfolio scoring generated {len(recommendations)} recommendation(s)")
        complete_step(pipeline_status, "portfolio_scoring", f"Generated {len(recommendations)} recommendation(s).")
    except Exception as exc:
        log_print(f"ERROR in Portfolio Scoring: {exc}\n{traceback.format_exc()}")
        recommendations = []
        fail_step(pipeline_status, "portfolio_scoring", f"Portfolio scoring failed: {exc}")

    portfolio_gap_analysis = run_optional_step(
        "portfolio_gap",
        "Running Portfolio Gap / Sector Suggestions v1...",
        lambda: build_portfolio_gap_analysis(
            positions=positions,
            watchlist_candidates=watchlist_candidates,
            watchlist_review=watchlist_review,
            recommendations=recommendations,
            market_metrics=market_metrics,
            news_map=news,
            log_print=log_print,
        ),
        {
            "source": "portfolio_gap_sector_suggestions_v1",
            "enabled": True,
            "has_data": False,
            "summary": {},
            "exposure_rows": [],
            "risk_rows": [],
            "suggestions": [],
            "errors": [],
        },
        lambda result: f"Portfolio gap produced {len((result or {}).get('suggestions', []) or [])} suggestion(s).",
    )
    tradier_snapshot["_portfolio_gap"] = portfolio_gap_analysis

    stock_momentum_strategy = run_optional_step(
        "stock_momentum",
        "Running Stock Momentum Add Strategy v1...",
        lambda: build_stock_momentum_strategy(
            positions=positions,
            watchlist_candidates=watchlist_candidates,
            recommendations=recommendations,
            market_metrics=market_metrics,
            portfolio_gap_analysis=portfolio_gap_analysis,
            news_map=news,
            log_print=log_print,
        ),
        {"source": "stock_momentum_add_strategy_v1", "enabled": True, "has_data": False, "items": [], "errors": [], "summary": {}},
        lambda result: f"Stock momentum produced {len((result or {}).get('items', []) or [])} candidate(s).",
    )
    tradier_snapshot["_stock_momentum_strategy"] = stock_momentum_strategy

    daily_opportunity_engine = run_optional_step(
        "daily_opportunity",
        "Running Daily Opportunity Engine v1...",
        lambda: build_daily_opportunity_engine(
            unified_calendar_engine=unified_calendar_engine,
            stock_momentum_strategy=stock_momentum_strategy,
            portfolio_gap_analysis=portfolio_gap_analysis,
            recommendations=recommendations,
            log_print=log_print,
        ),
        {"source": "daily_opportunity_engine_v1", "enabled": True, "has_data": False, "actions": [], "errors": [], "summary": {}},
        lambda result: f"Daily opportunity engine produced {len((result or {}).get('actions', []) or [])} action(s).",
    )
    tradier_snapshot["_daily_opportunity_engine"] = daily_opportunity_engine

    begin_step(pipeline_status, "format_payload", "Formatting payload")
    log_print("Formatting payload...")
    try:
        attach_status()
        payload = format_payload(positions, news, recommendations, tradier_snapshot)
        if clean_mode == "dev":
            payload = "MODE: DEV — external provider calls limited for API-budget-safe testing.\n\n" + payload
        log_print(f"Payload length: {len(payload)} chars")
        complete_step(pipeline_status, "format_payload", f"Payload formatted: {len(payload)} chars.")
        finish_pipeline(pipeline_status, "complete")
        attach_status()
    except Exception as exc:
        log_print(f"ERROR in format_payload: {exc}\n{traceback.format_exc()}")
        fail_step(pipeline_status, "format_payload", f"Report payload formatting failed: {exc}")
        finish_pipeline(pipeline_status, "error")
        attach_status()
        return None, positions, news, recommendations, tradier_snapshot, log

    log_print("=== RUN COMPLETE ===")
    return payload, positions, news, recommendations, tradier_snapshot, log
