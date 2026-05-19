"""
app/services/analysis_service.py — Main pipeline orchestration.

This service runs the portfolio/news/market-data/scoring/report pipeline.
Persistence and trade lifecycle checks are intentionally skipped for now; those
will matter later when the app needs to remember open trades and checkpoints.

Dev mode is supported for API-budget-safe testing:
- Robinhood still fetches the full portfolio, because that is the baseline state.
- External providers such as NewsAPI, Finnhub, and future Tradier calls are
  limited to a small ticker subset.
"""

from __future__ import annotations

import traceback
from typing import Any

from app import config
from app.providers.news_provider import get_news_for_tickers
from app.services.market_data_service import get_market_metrics_for_positions
from app.services.portfolio_service import get_portfolio_positions
from app.services.report_service import format_payload
from app.strategies.portfolio_snapshot import PortfolioSnapshotStrategy
from app.utils.log_safety import sanitize_for_log


PipelineResult = tuple[
    str | None,
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[str],
]


def run_portfolio_pipeline(run_mode: str = "prod") -> PipelineResult:
    log: list[str] = []
    news: dict[str, list[dict[str, Any]]] = {}
    market_metrics: dict[str, dict[str, Any]] = {}
    recommendations: list[dict[str, Any]] = []

    clean_mode = _normalize_run_mode(run_mode)

    def log_print(msg: str) -> None:
        safe_msg = sanitize_for_log(
            msg,
            known_secrets=[
                config.ROBINHOOD_PASSWORD,
                config.NEWS_API_KEY,
                config.FINNHUB_API_KEY,
                config.RUN_TOKEN,
                config.NTFY_TOPIC,
            ],
        )
        print(safe_msg, flush=True)
        log.append(safe_msg)

    log_print("=== RUN STARTED ===")

    try:
        # Imports happen at module load, but these log lines are preserved so the
        # browser run log keeps the same useful shape as before.
        log_print("robinhood imported OK")
        log_print("news imported OK")
        log_print("market data imported OK")
        log_print("config imported OK")
        log_print(f"APP_MODE: {config.APP_MODE}")
        log_print(f"Run mode: {clean_mode}")
        log_print(f"ROBINHOOD_USERNAME set: {bool(config.ROBINHOOD_USERNAME)}")
        log_print(f"ROBINHOOD_PASSWORD set: {bool(config.ROBINHOOD_PASSWORD)}")
        log_print(f"NEWS_API_KEY set: {bool(config.NEWS_API_KEY)}")
        log_print(f"NEWS_MAX_TICKERS_PER_RUN: {getattr(config, 'NEWS_MAX_TICKERS_PER_RUN', 8)}")
        log_print(f"FINNHUB_API_KEY set: {bool(config.FINNHUB_API_KEY)}")
        log_print(f"MARKET_BENCHMARK_TICKER: {config.MARKET_BENCHMARK_TICKER}")
        if clean_mode == "dev":
            log_print(
                "DEV MODE active: Robinhood will fetch all positions, but external "
                "provider calls are limited."
            )
            log_print(f"DEV_TICKERS: {config.DEV_TICKERS}; DEV_MAX_TICKERS: {config.DEV_MAX_TICKERS}")
    except Exception as e:
        log_print(f"IMPORT ERROR config: {e}\n{traceback.format_exc()}")
        return None, [], news, recommendations, log

    log_print("Fetching Robinhood positions...")

    try:
        positions = get_portfolio_positions()
        log_print(f"get_positions returned {len(positions)} positions")
    except Exception as e:
        log_print(f"ERROR in get_positions: {e}\n{traceback.format_exc()}")
        return None, [], news, recommendations, log

    if not positions:
        log_print("No positions found or login failed.")
        return None, [], news, recommendations, log

    tickers = list(dict.fromkeys(p.get("ticker") for p in positions if p.get("ticker")))
    log_print(f"Tickers: {tickers}")

    external_tickers = _external_provider_tickers(tickers, clean_mode)
    if clean_mode == "dev":
        log_print(f"DEV MODE external provider ticker subset: {external_tickers}")

    log_print("Fetching relevance-scored news...")

    try:
        news = get_news_for_tickers(
            external_tickers,
            max_tickers=_news_max_tickers_for_mode(clean_mode),
        )
        news = _fill_missing_news_keys(tickers, news)
        article_count = sum(len(articles) for articles in news.values())
        log_print(f"News fetched for {len(news)} tickers; {article_count} relevant article(s)")
    except Exception as e:
        log_print(f"ERROR in get_news_for_tickers: {e}\n{traceback.format_exc()}")
        # News should not break the whole report.
        news = _fill_missing_news_keys(tickers, {})

    log_print("Fetching Finnhub Market Data v1...")

    try:
        market_metrics = get_market_metrics_for_positions(
            positions,
            log_print=log_print,
            max_tickers=_market_max_tickers_for_mode(clean_mode),
            allowed_tickers=external_tickers if clean_mode == "dev" else None,
        )
    except Exception as e:
        log_print(f"ERROR in Market Data v1: {e}\n{traceback.format_exc()}")
        # Market data should not break the whole report. Portfolio scoring will
        # fall back to v1 cost-basis/allocation/news signals.
        market_metrics = {}

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
        # Scoring should not break the whole report. Continue with an empty score table.
        recommendations = []

    log_print("Formatting payload...")

    try:
        payload = format_payload(positions, news, recommendations)
        if clean_mode == "dev":
            payload = "MODE: DEV — external provider calls limited for API-budget-safe testing.\n\n" + payload
        log_print(f"Payload length: {len(payload)} chars")
    except Exception as e:
        log_print(f"ERROR in format_payload: {e}\n{traceback.format_exc()}")
        return None, positions, news, recommendations, log

    log_print("=== RUN COMPLETE ===")
    return payload, positions, news, recommendations, log


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
    return None


def _fill_missing_news_keys(
    all_tickers: list[str],
    news: dict[str, list[dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    filled = {str(t).upper().strip(): [] for t in all_tickers if str(t).strip()}
    for ticker, articles in news.items():
        filled[str(ticker).upper().strip()] = articles or []
    return filled
