"""
news.py — Compatibility wrapper.

Existing code that imports `from news import get_news_for_tickers` will continue
to work. The real NewsAPI provider now lives in `app/providers/news_provider.py`.
"""

from app.providers.news_provider import (  # noqa: F401
    build_query_for_ticker,
    get_headlines_for_tickers,
    get_news_for_tickers,
)
