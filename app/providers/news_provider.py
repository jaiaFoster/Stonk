"""
app/providers/news_provider.py — Fetches recent news headlines for each ticker.

This intentionally preserves the current behavior: it queries NewsAPI directly
with the ticker symbol and returns up to three recent headlines per ticker.
Improving relevance is the next planned step, but this refactor avoids changing
runtime behavior.
"""

from datetime import date, timedelta

import requests

from app import config


def get_news_for_tickers(tickers: list[str]) -> dict[str, list[str]]:
    news_map: dict[str, list[str]] = {}

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    for ticker in tickers:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": ticker,
                    "from": yesterday,
                    "to": today,
                    "sortBy": "relevancy",
                    "language": "en",
                    "pageSize": 3,
                    "apiKey": config.NEWS_API_KEY,
                },
                timeout=10,
            )
            data = resp.json()
            articles = data.get("articles", [])
            headlines = [a["title"] for a in articles if a.get("title")]
            news_map[ticker] = headlines if headlines else ["No recent news found."]

        except Exception as e:
            print(f"News fetch error for {ticker}: {e}", flush=True)
            news_map[ticker] = ["News unavailable."]

    return news_map
