"""
app/services/analysis_service.py — Main pipeline orchestration.

This service runs the current portfolio/news/report pipeline. Strategy scanning,
recommendation generation, SQLite persistence, and trade lifecycle checks can be
added here later or delegated to dedicated services.
"""

from __future__ import annotations

import traceback
from typing import Any

from app import config
from app.providers.news_provider import get_news_for_tickers
from app.services.portfolio_service import get_portfolio_positions
from app.services.report_service import format_payload


PipelineResult = tuple[str | None, list[dict[str, Any]], dict[str, list[dict[str, Any]]], list[str]]


def run_portfolio_pipeline() -> PipelineResult:
    log: list[str] = []
    news: dict[str, list[dict[str, Any]]] = {}

    def log_print(msg: str) -> None:
        print(msg, flush=True)
        log.append(msg)

    log_print("=== RUN STARTED ===")

    try:
        # Imports now happen at module load, but these log lines are preserved
        # so the browser run log keeps the same useful shape as before.
        log_print("robinhood imported OK")
        log_print("news imported OK")
        log_print("config imported OK")
        log_print(f"ROBINHOOD_USERNAME set: {bool(config.ROBINHOOD_USERNAME)}")
        log_print(f"ROBINHOOD_PASSWORD set: {bool(config.ROBINHOOD_PASSWORD)}")
        log_print(f"NEWS_API_KEY set: {bool(config.NEWS_API_KEY)}")
    except Exception as e:
        log_print(f"IMPORT ERROR config: {e}\n{traceback.format_exc()}")
        return None, [], news, log

    log_print("Fetching Robinhood positions...")

    try:
        positions = get_portfolio_positions()
        log_print(f"get_positions returned {len(positions)} positions")
    except Exception as e:
        log_print(f"ERROR in get_positions: {e}\n{traceback.format_exc()}")
        return None, [], news, log

    if not positions:
        log_print("No positions found or login failed.")
        return None, [], news, log

    tickers = list(dict.fromkeys(p.get("ticker") for p in positions if p.get("ticker")))
    log_print(f"Tickers: {tickers}")

    log_print("Fetching relevance-scored news...")

    try:
        news = get_news_for_tickers(tickers)
        article_count = sum(len(articles) for articles in news.values())
        log_print(f"News fetched for {len(news)} tickers; {article_count} relevant article(s)")
    except Exception as e:
        log_print(f"ERROR in get_news_for_tickers: {e}\n{traceback.format_exc()}")
        return None, positions, news, log

    log_print("Formatting payload...")

    try:
        payload = format_payload(positions, news)
        log_print(f"Payload length: {len(payload)} chars")
    except Exception as e:
        log_print(f"ERROR in format_payload: {e}\n{traceback.format_exc()}")
        return None, positions, news, log

    log_print("=== RUN COMPLETE ===")
    return payload, positions, news, log
