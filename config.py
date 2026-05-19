"""
config.py — Root compatibility wrapper.

The app now stores configuration in app/config.py, but root imports are kept for
older modules or manual debugging commands.
"""

from app.config import (  # noqa: F401
    APP_MODE,
    DEV_MAX_TICKERS,
    DEV_TICKERS,
    FINNHUB_API_KEY,
    MARKET_BENCHMARK_TICKER,
    NEWS_API_KEY,
    NEWS_MAX_TICKERS_PER_RUN,
    NEWS_PAGE_SIZE,
    NTFY_TOPIC,
    ROBINHOOD_PASSWORD,
    ROBINHOOD_USERNAME,
    RUN_TOKEN,
)
