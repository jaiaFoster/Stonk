"""
news.py — Fetches recent news headlines for each ticker using NewsAPI.
Free tier at newsapi.org gives 100 requests/day, plenty for personal use.
"""

import requests
import config
from datetime import date, timedelta


def get_news_for_tickers(tickers: list[str]) -> dict[str, list[str]]:
    news_map: dict = {}

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
            print(f"News fetch error for {ticker}: {e}")
            news_map[ticker] = ["News unavailable."]

    return news_map
