"""
app/services/analysis_service.py — Main pipeline orchestration.

This service runs the current portfolio/news/scoring/report pipeline.
Persistence and trade lifecycle checks are intentionally skipped for now; those
will matter later when the app needs to remember open trades and checkpoints.
"""

from __future__ import annotations

import traceback
from typing import Any

from app import config
from app.providers.news_provider import get_news_for_tickers
from app.services.portfolio_service import get_portfolio_positions
from app.services.report_service import format_payload
from app.strategies.portfolio_snapshot import PortfolioSnapshotStrategy


PipelineResult = tuple[
    str | None,
    list[dict[str, Any]],
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[str],
]


def run_portfolio_pipeline() -> PipelineResult:
    log: list[str] = []
    news: dict[str, list[dict[str, Any]]] = {}
    recommendations: list[dict[str, Any]] = []

    def log_print(msg: str) -> None:
        print(msg, flush=True)
        log.append(msg)

    log_print("=== RUN STARTED ===")

    try:
        # Imports happen at module load, but these log lines are preserved so the
        # browser run log keeps the same useful shape as before.
        log_print("robinhood imported OK")
        log_print("news imported OK")
        log_print("config imported OK")
        log_print(f"ROBINHOOD_USERNAME set: {bool(config.ROBINHOOD_USERNAME)}")
        log_print(f"ROBINHOOD_PASSWORD set: {bool(config.ROBINHOOD_PASSWORD)}")
        log_print(f"NEWS_API_KEY set: {bool(config.NEWS_API_KEY)}")
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

    log_print("Fetching relevance-scored news...")

    try:
        news = get_news_for_tickers(tickers)
        article_count = sum(len(articles) for articles in news.values())
        log_print(f"News fetched for {len(news)} tickers; {article_count} relevant article(s)")
    except Exception as e:
        log_print(f"ERROR in get_news_for_tickers: {e}\n{traceback.format_exc()}")
        return None, positions, news, recommendations, log

    log_print("Running Portfolio Scoring v1...")

    try:
        strategy = PortfolioSnapshotStrategy()
        recommendations = strategy.evaluate_portfolio(positions=positions, news_map=news)
        log_print(f"Portfolio scoring generated {len(recommendations)} recommendation(s)")
    except Exception as e:
        log_print(f"ERROR in Portfolio Scoring v1: {e}\n{traceback.format_exc()}")
        # Scoring should not break the whole report. Continue with an empty score table.
        recommendations = []

    log_print("Formatting payload...")

    try:
        payload = format_payload(positions, news, recommendations)
        log_print(f"Payload length: {len(payload)} chars")
    except Exception as e:
        log_print(f"ERROR in format_payload: {e}\n{traceback.format_exc()}")
        return None, positions, news, recommendations, log

    log_print("=== RUN COMPLETE ===")
    return payload, positions, news, recommendations, log
