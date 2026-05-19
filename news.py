"""
news.py — Compatibility wrapper.

Existing code that imports `from news import get_news_for_tickers` will continue
to work. The real NewsAPI provider now lives in `app/providers/news_provider.py`.
"""

from app.providers.news_provider import get_news_for_tickers  # noqa: F401
