"""Alpha Vantage daily candle fallback provider."""

from __future__ import annotations

from typing import Any

from app import config
from app.utils.log_safety import sanitize_for_log

REQUEST_TIMEOUT_SECONDS = 20


class AlphaVantageMarketDataProvider:
    source = "alphavantage"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.ALPHA_VANTAGE_API_KEY

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_daily_candles(self, ticker: str) -> list[dict[str, Any]]:
        import requests

        if not self.api_key:
            raise RuntimeError("ALPHA_VANTAGE_API_KEY is not set.")
        response = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY",
                "symbol": str(ticker or "").upper().strip(),
                "outputsize": "full",
                "apikey": self.api_key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Alpha Vantage daily candles returned HTTP {response.status_code}.")
        payload = response.json()
        if payload.get("Note") or payload.get("Information"):
            raise RuntimeError(str(payload.get("Note") or payload.get("Information")))
        series = payload.get("Time Series (Daily)")
        if not isinstance(series, dict):
            message = payload.get("Error Message") or "No daily time series returned."
            raise RuntimeError(sanitize_for_log(message, [self.api_key]))

        bars: list[dict[str, Any]] = []
        for day, raw in series.items():
            if not isinstance(raw, dict):
                continue
            try:
                bars.append(
                    {
                        "date": str(day)[:10],
                        "open": float(raw.get("1. open")),
                        "high": float(raw.get("2. high")),
                        "low": float(raw.get("3. low")),
                        "close": float(raw.get("4. close")),
                        "volume": float(raw.get("5. volume")) if raw.get("5. volume") not in {None, ""} else None,
                    }
                )
            except (TypeError, ValueError):
                continue
        bars.sort(key=lambda item: item["date"])
        return bars
