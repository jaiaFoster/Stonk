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

import json
import traceback
from dataclasses import asdict
from time import perf_counter
from typing import Any, Callable

from app import config
from app.providers.news_provider import get_news_for_tickers
from app.services.calendar_lifecycle_service import evaluate_calendar_lifecycle
from app.services.calendar_opportunity_cache_service import cache_calendar_opportunities
from app.services.calendar_ranking_service import build_calendar_ranking
from app.services.calendar_spread_service import scan_calendar_spreads_for_positions
from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine
from app.services.earnings_calendar_strategy_service import evaluate_earnings_calendar_candidates
from app.services.earnings_discovery_quality_service import filter_earnings_discovery_for_calendar_scan
from app.services.earnings_service import discover_upcoming_earnings_for_calendar_trades, get_earnings_for_positions
from app.services.earnings_mini_backtest_service import build_earnings_mini_backtest
from app.services.market_data_hub_service import MarketDataHub
from app.services.market_data_repository import MarketDataRepository
from app.services.open_options_service import detect_open_options_positions
from app.services.pipeline_helpers import (
    calendar_max_tickers_for_mode,
    config_log_lines,
    config_snapshot,
    earnings_max_tickers_for_mode,
    external_provider_tickers,
    fill_missing_news_keys,
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
    skip_step,
    warn_step,
)
from app.services.portfolio_gap_service import build_portfolio_gap_analysis
from app.services.portfolio_service import get_portfolio_positions_with_status
from app.services.report_service import format_payload
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_data_context_service import create_run_data_context
from app.services.data_coverage_service import build_data_coverage
from app.services.data_requirement_planner import DataRequirementPlanner
from app.services.strategy_opportunity_repository import StrategyOpportunityRepository
from app.services.strategy_execution_service import collect_strategy_results
from app.services.actionability_service import attach_actionability_to_rows
from app.services.shared_market_metrics_service import build_canonical_market_metrics, build_ticker_market_metrics
from app.services.stock_momentum_strategy_service import build_stock_momentum_strategy, select_stock_momentum_market_data_tickers
from app.services.skew_momentum_vertical_service import build_skew_momentum_vertical_strategy
from app.services.skew_momentum_vertical_cache_service import cache_skew_momentum_vertical_opportunities
from app.services.forward_factor_service import build_forward_factor_strategy
from app.services.tradier_service import get_tradier_snapshot_for_positions
from app.services.unified_calendar_trade_engine_service import build_unified_calendar_trade_engine
from app.services.watchlist_review_service import review_watchlist_candidates
from app.services.watchlist_service import get_watchlist_candidates, merge_watchlist_universe_positions
from app.strategies.portfolio_snapshot import PortfolioSnapshotStrategy
from app.strategies.registry import collect_requirements
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

EMPTY_CALENDAR_OPPORTUNITY_CACHE = {
    "source": "calendar_opportunity_cache_v1",
    "enabled": True,
    "has_data": False,
    "recent": [],
    "errors": [],
    "summary": {"write_count": 0, "recent_count": 0},
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


def _robinhood_unavailable(provider_status: dict[str, Any] | None) -> bool:
    status = str((provider_status or {}).get("status") or "").lower()
    return status in {"rate_limited", "auth_required", "auth_failed", "positions_failed", "positions_partial"}


def _robinhood_skip_log(log_print: Callable[[str], None], provider_status: dict[str, Any]) -> None:
    status = str(provider_status.get("status") or "unknown")
    reason = provider_status.get("error") or status
    if provider_status.get("rate_limited"):
        reason = "429 rate limit encountered during verification."
    elif provider_status.get("auth_required"):
        reason = f"Robinhood authentication/verification required. {reason}"
    heading = "Position refresh failed." if status in {"positions_failed", "positions_partial"} else "Login failed."
    log_print(
        "[ROBINHOOD]\n"
        f"{heading}\n\n"
        "Reason:\n"
        f"{reason}\n\n"
        "Skipping live Robinhood-dependent refreshes:\n"
        "- watchlists\n"
        "- option detection\n"
        "- calendar inference\n\n"
        "Continuing with cached holdings when available and non-Robinhood modules."
    )


def _robinhood_unavailable_open_options(provider_status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(EMPTY_OPEN_OPTIONS)
    payload["source"] = "combined_broker_options"
    payload["provider_status"] = {"robinhood": provider_status}
    payload["errors"] = [
        "Robinhood unavailable during this run; active option/calendar detection was not refreshed."
    ]
    payload["summary"] = dict(payload.get("summary") or {})
    payload["summary"]["robinhood_unavailable"] = True
    payload["summary"]["provider_status"] = provider_status.get("status")
    return payload


def _robinhood_unavailable_lifecycle(provider_status: dict[str, Any]) -> dict[str, Any]:
    payload = dict(EMPTY_LIFECYCLE)
    payload["provider_status"] = {"robinhood": provider_status}
    payload["errors"] = [
        "Robinhood unavailable during this run; lifecycle checks were not refreshed."
    ]
    payload["summary"] = dict(payload.get("summary") or {})
    payload["summary"]["robinhood_unavailable"] = True
    payload["summary"]["provider_status"] = provider_status.get("status")
    return payload


def _latest_complete_broker_state(log_print: Callable[[str], None]) -> dict[str, Any]:
    try:
        repository = ReportSnapshotRepository(log_print=log_print)
        snapshot = repository.latest_success()
        summary = repository.load_summary(snapshot)
        tradier = ((summary.get("report_data") or {}).get("tradier_snapshot") or {})
        return {
            "open_options": tradier.get("_open_options_positions"),
            "calendar_lifecycle": tradier.get("_calendar_lifecycle_checks"),
        }
    except Exception as exc:
        log_print(f"Broker active-state fallback unavailable: {exc}")
        return {}


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


def _attach_candidate_candle_quality(
    calendar_candidates: list[dict[str, Any]],
    log_print: Callable[[str], None],
    data_hub: MarketDataHub,
) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for candidate in calendar_candidates or []:
        if not isinstance(candidate, dict):
            continue
        ticker = str(candidate.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        if ticker not in status:
            record = data_hub.get_daily_candles(ticker, min_bars=240, required=True, strategy_id="earnings_calendar")
            history = _record_payload(record)
            status[ticker] = {
                "provider": history.get("provider"),
                "status": history.get("status"),
                "quality": history.get("quality") or {},
                "errors": history.get("errors") or [],
            }
        candidate["candle_quality"] = status[ticker].get("quality") or {}
        candidate["candle_provider"] = status[ticker].get("provider")
    return status


def _record_payload(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else record


def _attach_strategy_actionability(result: dict[str, Any], *keys: str) -> dict[str, Any]:
    for key in keys:
        if isinstance(result.get(key), list):
            result[key] = attach_actionability_to_rows(result[key])
    return result


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
    skew_momentum_vertical_strategy: dict[str, Any] = {}
    forward_factor_strategy: dict[str, Any] = {}
    calendar_ranking: dict[str, Any] = dict(EMPTY_CALENDAR_RANKING)
    earnings_mini_backtest: dict[str, Any] = dict(EMPTY_EARNINGS_BACKTEST)
    calendar_opportunity_cache: dict[str, Any] = dict(EMPTY_CALENDAR_OPPORTUNITY_CACHE)
    candle_status: dict[str, Any] = {}
    trade_memory: dict[str, Any] = dict(EMPTY_TRADE_MEMORY)
    provider_status: dict[str, Any] = {
        "robinhood": {
            "provider": "robinhood",
            "configured": bool(config.ROBINHOOD_USERNAME and config.ROBINHOOD_PASSWORD),
            "success": False,
            "status": "unknown",
            "error": None,
            "rate_limited": False,
            "auth_required": False,
        }
    }

    clean_mode = normalize_run_mode(run_mode)
    pipeline_status = new_pipeline_status(clean_mode)
    run_context = create_run_data_context(clean_mode)
    market_data_repository = MarketDataRepository()

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
        tradier_snapshot["_provider_status"] = provider_status
        tradier_snapshot["_run_data_context"] = run_context.to_summary()

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
        portfolio_result = get_portfolio_positions_with_status()
        positions = list((portfolio_result or {}).get("positions") or [])
        rh_status = ((portfolio_result or {}).get("provider_status") or {})
        provider_status["robinhood"] = rh_status
        pipeline_status["broker_summary"] = {
            "accounts": (portfolio_result or {}).get("account_summary", {}),
            "account_results": (portfolio_result or {}).get("account_results", []),
        }
        pipeline_status["report_quality"] = (portfolio_result or {}).get("report_quality", "SUCCESS_COMPLETE")
        log_print(f"get_positions returned {len(positions)} positions")
        if _robinhood_unavailable(rh_status):
            _robinhood_skip_log(log_print, rh_status)
            warn_step(
                pipeline_status,
                "positions",
                f"Robinhood unavailable: {rh_status.get('status') or 'auth_failed'}.",
                {"provider_status": rh_status},
            )
            if not any(position.get("broker_data_state") == "STALE_FALLBACK" for position in positions):
                positions = []
        elif not positions:
            warn_step(
                pipeline_status,
                "positions",
                "Robinhood login succeeded, but no open positions were returned.",
                {"provider_status": rh_status},
            )
        else:
            complete_step(pipeline_status, "positions", f"Fetched {len(positions)} position(s).", {"provider_status": rh_status})
    except Exception as exc:
        log_print(f"ERROR in get_positions: {exc}\n{traceback.format_exc()}")
        fail_step(pipeline_status, "positions", f"Robinhood positions failed: {exc}")
        provider_status["robinhood"] = {
            "provider": "robinhood",
            "configured": bool(config.ROBINHOOD_USERNAME and config.ROBINHOOD_PASSWORD),
            "success": False,
            "status": "auth_failed",
            "error": str(exc),
            "rate_limited": "429" in str(exc) or "Too Many Requests" in str(exc),
            "auth_required": False,
        }
        positions = []
        pipeline_status["report_quality"] = "SUCCESS_DEGRADED"
        _robinhood_skip_log(log_print, provider_status["robinhood"])

    portfolio_tickers = list(dict.fromkeys(p.get("ticker") for p in positions if p.get("ticker")))
    log_print(f"Tickers: {portfolio_tickers}")

    robinhood_failed = _robinhood_unavailable(provider_status.get("robinhood"))
    cached_broker_state = _latest_complete_broker_state(log_print) if robinhood_failed else {}

    if robinhood_failed:
        skip_step(
            pipeline_status,
            "watchlist_candidates",
            "Fetching Watchlist Candidate Pipeline v1...",
            "Skipped because Robinhood login failed; watchlists were not refreshed.",
        )
        watchlist_candidates = dict(EMPTY_WATCHLIST)
        watchlist_candidates["enabled"] = False
        watchlist_candidates["errors"] = ["Robinhood unavailable; watchlist fetch skipped."]
        watchlist_candidates["provider_status"] = {"robinhood": provider_status.get("robinhood")}
    else:
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
    run_context.analysis_tickers = analysis_tickers
    run_context.analysis_positions = analysis_positions
    hub = MarketDataHub(run_context, repository=market_data_repository, log_print=log_print)
    strategy_requirements = collect_requirements(run_context, log_print=log_print)
    planner = DataRequirementPlanner(
        clean_mode,
        dev_ticker_cap=config.DEV_MAX_TICKERS if clean_mode == "dev" else None,
    )
    log_print(f"StrategyRegistry: collecting requirements for {len(strategy_requirements)} strategy plugins")
    chain_cap_for_mode = config.FF_DEV_MAX_CHAIN_TICKERS_PER_RUN if clean_mode == "dev" else config.FF_MAX_CHAIN_TICKERS_PER_RUN
    if config.FF_CHAIN_BUDGET_RESERVED and config.FORWARD_FACTOR_STRATEGY_ENABLED:
        ff_chain_reserve = min(chain_cap_for_mode, max(config.FF_MIN_CHAIN_SET_BUDGET, min(hub.budget.remaining, chain_cap_for_mode)))
    else:
        ff_chain_reserve = min(hub.budget.remaining, chain_cap_for_mode)
    requirement_plan = planner.merge(strategy_requirements, provider_budget=max(0, hub.budget.remaining - ff_chain_reserve))
    requirement_plan["forward_factor_chain_reserve"] = ff_chain_reserve
    log_print(f"DataRequirementPlanner: received {len(strategy_requirements)} strategy requirement set(s)")
    log_print(f"DataRequirementPlanner: merged requirements for {requirement_plan['ticker_count']} ticker(s)")
    log_print(f"ProviderBudget: approved {len(requirement_plan['approved'])} ticker(s); skipped {len(requirement_plan['skipped_provider_budget'])}")
    log_print("MarketDataHub: fulfilling approved requirements")
    planner.fulfill_plan(hub, strategy_requirements, requirement_plan)
    if config.FF_CHAIN_BUDGET_RESERVED and config.FORWARD_FACTOR_STRATEGY_ENABLED:
        ff_budget_gap = ff_chain_reserve - hub.budget.remaining
        if ff_budget_gap > 0:
            hub.budget.max_requests += ff_budget_gap
            log_print(f"FF chain reserve: boosted hub budget by {ff_budget_gap} to guarantee {ff_chain_reserve} chain slot(s)")
    run_context.requirements = {item.strategy_id: asdict(item) for item in strategy_requirements}
    tradier_snapshot["_strategy_requirement_plan"] = requirement_plan
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
        "Building shared MarketDataHub metrics...",
        lambda: build_canonical_market_metrics(hub, analysis_tickers, requirement_plan),
        {},
        lambda result: f"Market metrics available for {sum(1 for item in (result or {}).values() if item.get('has_data'))}/{len(result or {})} ticker(s).",
    )
    for position in analysis_positions:
        ticker = str(position.get("ticker") or "").upper()
        if ticker in market_metrics and str(position.get("account") or "").lower() == "crypto":
            market_metrics[ticker]["asset_type"] = "crypto"
    benchmark_metrics = build_ticker_market_metrics(hub, config.MARKET_BENCHMARK_TICKER, None)

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
            data_hub=hub,
        ),
        {},
        lambda result: f"Tradier snapshots available for {sum(1 for item in (result or {}).values() if isinstance(item, dict) and item.get('has_data'))}/{len(result or {})} entries.",
    )

    # Re-attach metadata after the Tradier provider replaces the snapshot dict.
    tradier_snapshot["_watchlist_candidates"] = watchlist_candidates
    tradier_snapshot["_benchmark_metrics"] = benchmark_metrics
    tradier_snapshot["_earnings_events"] = {
        "items": earnings_events,
        "has_data": any(item.get("has_data") for item in earnings_events.values()),
        "source": config.EARNINGS_PROVIDER,
    }
    tradier_snapshot["_earnings_trade_discovery"] = earnings_trade_discovery
    tradier_snapshot["_earnings_discovery_quality"] = earnings_discovery_quality
    for ticker, event in earnings_events.items():
        hub.seed("earnings_event", ticker, event, provider=str(event.get("provider") or "legacy_pipeline"))
    for ticker, snapshot_row in list(tradier_snapshot.items()):
        if str(ticker).startswith("_") or not isinstance(snapshot_row, dict):
            continue
        if snapshot_row.get("quote"):
            hub.seed("quote", ticker, snapshot_row["quote"], provider="tradier")
        if snapshot_row.get("chains") or snapshot_row.get("option_chains"):
            hub.seed("options_chain", ticker, snapshot_row.get("chains") or snapshot_row.get("option_chains"), provider="tradier")

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
    candle_status = run_optional_step(
        "calendar_candle_rescue",
        "Running Calendar Candidate Candle Rescue...",
        lambda: _attach_candidate_candle_quality(calendar_candidates, log_print, hub),
        {},
        lambda result: f"Candle rescue selected usable data for {sum(1 for item in (result or {}).values() if item.get('provider'))}/{len(result or {})} candidate ticker(s).",
    )
    provider_status["candles"] = {
        "provider": "multi_provider_candles",
        "configured_order": list(config.MARKET_DATA_PROVIDER_ORDER),
        "success_count": sum(1 for item in candle_status.values() if isinstance(item, dict) and item.get("provider")),
        "ticker_count": len(candle_status),
        "selected_providers": sorted({str(item.get("provider")) for item in candle_status.values() if isinstance(item, dict) and item.get("provider")}),
    }
    tradier_snapshot["_calendar_spread_candidates"] = {
        "items": calendar_candidates,
        "has_data": bool(calendar_candidates),
        "source": "tradier",
        "universe_source": "earnings_discovery_v1",
    }
    tradier_snapshot["_candle_status"] = candle_status

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

    if robinhood_failed:
        skip_step(
            pipeline_status,
            "open_options",
            "Detecting Open Options Positions v1...",
            "Skipped because Robinhood login failed; active broker option/calendar detection was not refreshed.",
        )
        open_options = cached_broker_state.get("open_options") or _robinhood_unavailable_open_options(provider_status.get("robinhood") or {})
        if cached_broker_state.get("open_options"):
            open_options = dict(open_options)
            open_options["stale_fallback"] = True
            open_options.setdefault("errors", []).append("Robinhood unavailable; retained active option structures from latest complete report.")
    else:
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

    if robinhood_failed:
        skip_step(
            pipeline_status,
            "calendar_lifecycle",
            "Running Calendar Lifecycle Check v1...",
            "Skipped because Robinhood login failed; existing active calendar state must not be replaced by empty data.",
        )
        lifecycle_checks = cached_broker_state.get("calendar_lifecycle") or _robinhood_unavailable_lifecycle(provider_status.get("robinhood") or {})
        if cached_broker_state.get("calendar_lifecycle"):
            lifecycle_checks = dict(lifecycle_checks)
            lifecycle_checks["stale_fallback"] = True
            lifecycle_checks.setdefault("errors", []).append("Robinhood unavailable; retained lifecycle state from latest complete report.")
    else:
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

    calendar_opportunity_cache = run_optional_step(
        "calendar_opportunity_cache",
        "Updating Calendar Opportunity Cache v1...",
        lambda: cache_calendar_opportunities(
            (unified_calendar_engine or {}).get("new_trade_rows", []) or [],
            log_print=log_print,
        ),
        EMPTY_CALENDAR_OPPORTUNITY_CACHE,
        lambda result: f"Calendar opportunity cache wrote {((result or {}).get('summary', {}) or {}).get('write_count', 0)} row(s).",
    )
    tradier_snapshot["_calendar_opportunity_cache"] = calendar_opportunity_cache

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

    skew_momentum_vertical_strategy = run_optional_step(
        "skew_momentum_vertical",
        "Running Skew Momentum Vertical Strategy v1...",
        lambda: build_skew_momentum_vertical_strategy(
            positions=positions,
            watchlist_candidates=watchlist_candidates,
            portfolio_gap_analysis=portfolio_gap_analysis,
            market_metrics=market_metrics,
            earnings_events=earnings_events,
            account_context=account_context,
            run_mode=clean_mode,
            log_print=log_print,
            data_hub=hub,
        ),
        {"source": "skew_momentum_vertical_strategy_v1", "enabled": True, "has_data": False, "items": [], "pass_items": [], "watch_items": [], "blocked_items": [], "active_items": [], "errors": [], "summary": {}},
        lambda result: f"Skew momentum vertical strategy produced {len((result or {}).get('items', []) or [])} decision row(s).",
    )
    tradier_snapshot["_skew_momentum_vertical_strategy"] = skew_momentum_vertical_strategy
    _attach_strategy_actionability(
        skew_momentum_vertical_strategy,
        "items", "pass_items", "watch_items", "blocked_items", "active_items",
    )
    _attach_strategy_actionability(unified_calendar_engine, "new_trade_rows", "open_trade_rows", "blocked_rows")
    skew_vertical_cache = run_optional_step(
        "skew_vertical_opportunity_cache",
        "Updating Strategy 2 Opportunity Cache v1...",
        lambda: cache_skew_momentum_vertical_opportunities((skew_momentum_vertical_strategy or {}).get("items", []) or [], log_print=log_print),
        {"source": "skew_momentum_vertical_cache_v1", "enabled": True, "has_data": False, "recent": [], "summary": {}, "errors": []},
        lambda result: f"Strategy 2 opportunity cache wrote {((result or {}).get('summary', {}) or {}).get('write_count', 0)} row(s).",
    )
    tradier_snapshot["_skew_momentum_vertical_cache"] = skew_vertical_cache

    begin_step(pipeline_status, "forward_factor_history", "Loading Forward Factor observation history...")
    try:
        opportunity_repository = StrategyOpportunityRepository()
        prior_ff_history = opportunity_repository.observation_summary(
            "forward_factor_calendar", limit=max(50, config.FF_CANDIDATE_HISTORY_LOOKBACK_RUNS * 10),
        )
        complete_step(pipeline_status, "forward_factor_history", f"Loaded history for {len(prior_ff_history)} ticker(s).")
    except Exception as exc:
        opportunity_repository = None
        prior_ff_history = {}
        log_print(f"FF candidate history unavailable: {exc}")
        fail_step(pipeline_status, "forward_factor_history", f"FF history lookup failed: {exc}")

    forward_factor_strategy = run_optional_step(
        "forward_factor_calendar",
        "Running Forward Factor Calendar Strategy v1 in dry-run mode...",
        lambda: build_forward_factor_strategy(
            universe=analysis_tickers,
            market_metrics=market_metrics,
            data_hub=hub,
            run_mode=clean_mode,
            log_print=log_print,
            requirement_plan=requirement_plan,
            observation_history=prior_ff_history,
            run_id=run_context.run_id,
            run_date=str(run_context.created_at or "")[:10],
        ),
        {"strategy_id": "forward_factor_calendar", "strategy_label": "Forward Factor Calendar", "version": "v1", "enabled": True, "dry_run": True, "items": [], "rows": [], "errors": []},
        lambda result: f"Forward Factor produced {len((result or {}).get('items', []) or [])} dry-run decision row(s).",
    )
    tradier_snapshot["_forward_factor_strategy"] = forward_factor_strategy
    _attach_strategy_actionability(forward_factor_strategy, "items", "rows")

    daily_opportunity_engine = run_optional_step(
        "daily_opportunity",
        "Running Daily Opportunity Engine v1...",
        lambda: build_daily_opportunity_engine(
            unified_calendar_engine=unified_calendar_engine,
            stock_momentum_strategy=stock_momentum_strategy,
            portfolio_gap_analysis=portfolio_gap_analysis,
            recommendations=recommendations,
            log_print=log_print,
            skew_momentum_vertical_strategy=skew_momentum_vertical_strategy,
        ),
        {"source": "daily_opportunity_engine_v1", "enabled": True, "has_data": False, "actions": [], "errors": [], "summary": {}},
        lambda result: f"Daily opportunity engine produced {len((result or {}).get('actions', []) or [])} action(s).",
    )
    tradier_snapshot["_daily_opportunity_engine"] = daily_opportunity_engine

    normalized_strategy_results = collect_strategy_results(
        run_context,
        {
            "earnings_calendar": unified_calendar_engine,
            "skew_momentum_vertical": skew_momentum_vertical_strategy,
            "forward_factor_calendar": forward_factor_strategy,
            "stock_momentum": stock_momentum_strategy,
        },
        log_print=log_print,
    )
    run_context.strategy_results = normalized_strategy_results
    coverage = build_data_coverage(run_context)
    run_context.coverage = coverage
    tradier_snapshot["_strategy_results"] = normalized_strategy_results
    tradier_snapshot["_data_coverage"] = coverage
    begin_step(pipeline_status, "strategy_registry_persistence", "Persisting strategy registry and data coverage...")
    try:
        market_data_repository.save_coverage(run_context.run_id, coverage)
        opportunity_repository = opportunity_repository or StrategyOpportunityRepository()
        write_count = opportunity_repository.upsert_results(normalized_strategy_results, run_id=run_context.run_id)
        ff_history = opportunity_repository.observation_summary("forward_factor_calendar")
        normalized_strategy_results.get("forward_factor_calendar", {})["observation_history"] = ff_history
        tradier_snapshot["_strategy_opportunity_registry"] = {
            "write_count": write_count,
            "recent": opportunity_repository.recent(20),
            "forward_factor_observation_history": ff_history,
        }
        complete_step(pipeline_status, "strategy_registry_persistence", f"Persisted coverage and {write_count} strategy opportunity row(s).")
    except Exception as exc:
        log_print(f"Shared foundation persistence warning: {exc}")
        fail_step(pipeline_status, "strategy_registry_persistence", f"Shared persistence failed: {exc}")

    begin_step(pipeline_status, "format_payload", "Formatting payload")
    log_print("Formatting payload...")
    try:
        attach_status()
        payload = format_payload(positions, news, recommendations, tradier_snapshot)
        if clean_mode == "dev":
            payload = "MODE: DEV — external provider calls limited for API-budget-safe testing.\n\n" + payload
        log_print(f"Payload length: {len(payload)} chars")
        complete_step(pipeline_status, "format_payload", f"Payload formatted: {len(payload)} chars.")
        report_quality = pipeline_status.get("report_quality", "SUCCESS_COMPLETE")
        finish_pipeline(pipeline_status, "complete" if report_quality == "SUCCESS_COMPLETE" else "degraded")
        if config.ENABLE_RUNTIME_PROFILE:
            from app.services.runtime_profile_service import build_runtime_profile, compact_runtime_log
            runtime_profile = build_runtime_profile(pipeline_status)
            tradier_snapshot["_runtime_profile"] = runtime_profile
            log_print(compact_runtime_log(runtime_profile))
        else:
            runtime_profile = {}
        if config.ENABLE_STORAGE_PROFILE:
            storage_profile = market_data_repository.storage_profile()
            tradier_snapshot["_storage_profile"] = storage_profile
        else:
            storage_profile = {}
        report_summary = {
            "strategy_results": normalized_strategy_results,
            "pipeline_status": pipeline_status,
            "report_quality": report_quality,
        }
        if config.ENABLE_PAYLOAD_SIZE_PROFILE:
            from app.services.payload_profile_service import build_payload_size_profile, compact_payload_log
            payload_profile = build_payload_size_profile(payload, positions, news, recommendations, tradier_snapshot, log, report_summary)
            payload_profile["sections_bytes"]["report_snapshot_save"] = (
                payload_profile["sections_bytes"].get("payload_text", 0)
                + payload_profile["sections_bytes"].get("report_summary_json", 0)
            )
            tradier_snapshot["_payload_size_profile"] = payload_profile
            log_print(compact_payload_log(payload_profile))
        else:
            payload_profile = {}
        attach_status()
        try:
            report_repository = ReportSnapshotRepository(log_print=log_print)
            snapshot_method = report_repository.save_success if report_quality == "SUCCESS_COMPLETE" else report_repository.save_degraded
            trimmed_log = log[-config.REPORT_SNAPSHOT_MAX_LOG_LINES:]
            snapshot_summary = {
                **report_summary,
                "runtime_profile": runtime_profile,
                "payload_size_profile": payload_profile,
                "storage_profile": storage_profile,
                "report_data": {
                    "positions": positions,
                    "news": news,
                    "recommendations": recommendations,
                    "tradier_snapshot": tradier_snapshot,
                    "log": trimmed_log,
                },
            }
            if config.ENABLE_PAYLOAD_SIZE_PROFILE:
                import zlib
                from app.services.report_snapshot_service import build_compact_full_report_summary, build_hot_report_summary
                full_summary_json = json.dumps(
                    build_compact_full_report_summary(snapshot_summary),
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
                hot_summary_json = json.dumps(build_hot_report_summary(snapshot_summary), default=str, separators=(",", ":")).encode("utf-8")
                compressed_summary = zlib.compress(full_summary_json)
                compressed_payload = zlib.compress(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
                raw_provider_json = json.dumps(tradier_snapshot, default=str, separators=(",", ":")).encode("utf-8")
                compressed_raw_provider = zlib.compress(raw_provider_json)
                payload_profile["sections_bytes"].update({
                    "report_hot_summary_json": len(hot_summary_json),
                    "report_compressed_full_summary": len(compressed_summary),
                    "report_compressed_full_payload": len(compressed_payload),
                    "report_compressed_raw_provider": len(compressed_raw_provider),
                    "report_snapshot_save": (
                        len(hot_summary_json)
                        + len(compressed_summary)
                        + len(compressed_payload)
                        + len(compressed_raw_provider)
                    ),
                })
            snapshot_started = perf_counter()
            snapshot_method(
                run_context.run_id,
                clean_mode,
                payload,
                snapshot_summary,
                coverage,
                provider_status,
            )
            snapshot_save_ms = round((perf_counter() - snapshot_started) * 1000)
            runtime_profile.setdefault("phases_ms", {})["report_snapshot_save"] = snapshot_save_ms
            runtime_profile["total_ms"] = int(runtime_profile.get("total_ms", 0)) + snapshot_save_ms
            log_print(f"RuntimeProfile: report_snapshot_save={snapshot_save_ms}ms")
            from app.services.run_manifest_repository import RunManifestRepository, build_run_manifest
            manifest = build_run_manifest(
                run_context.run_id, clean_mode, pipeline_status.get("overall_status", "complete"),
                report_quality, runtime_profile, payload_profile, pipeline_status, normalized_strategy_results,
                daily_opportunity_engine, provider_fetch_count=len(run_context.fetch_audit),
                provider_status=provider_status,
            )
            RunManifestRepository().save(manifest)
            tradier_snapshot["_run_manifest"] = manifest
        except Exception as exc:
            log_print(f"Report snapshot persistence warning: {exc}")
    except Exception as exc:
        log_print(f"ERROR in format_payload: {exc}\n{traceback.format_exc()}")
        fail_step(pipeline_status, "format_payload", f"Report payload formatting failed: {exc}")
        finish_pipeline(pipeline_status, "error")
        attach_status()
        return None, positions, news, recommendations, tradier_snapshot, log

    log_print("=== RUN COMPLETE ===")
    return payload, positions, news, recommendations, tradier_snapshot, log
