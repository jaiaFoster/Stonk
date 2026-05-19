"""
app/providers/market_data_provider.py — Finnhub market data provider.

This provider fetches daily stock candles from Finnhub and converts them into
normalized market metrics. It is intentionally defensive: missing tickers,
empty candle responses, and provider errors produce unavailable metrics instead
of breaking the portfolio report.
"""

from __future__ import annotations

import math
import statistics
import time
from datetime import datetime, timezone
from typing import Any

import requests

from app import config
from app.models.market_metrics import MarketMetrics
from app.utils.log_safety import sanitize_for_log

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_LOOKBACK_DAYS = 460
REQUEST_TIMEOUT_SECONDS = 12

def _safe_finnhub_error(response: requests.Response, api_key: str | None) -> str:
    try:
        data = response.json()
        message = data.get("error") or data.get("message") or response.reason
    except Exception:
        message = response.reason

    return (
        f"Finnhub stock/candle returned HTTP {response.status_code}: "
        f"{sanitize_for_log(message, [api_key])}"
    )


class FinnhubMarketDataProvider:
    source = "finnhub"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.FINNHUB_API_KEY

    def get_market_metrics(
        self,
        ticker: str,
        benchmark_ticker: str | None = None,
        benchmark_metrics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ticker = str(ticker or "").upper().strip()
        if not ticker:
            return MarketMetrics.unavailable("UNKNOWN", "Missing ticker", benchmark_ticker).to_dict()

        if not self.api_key:
            return MarketMetrics.unavailable(
                ticker=ticker,
                benchmark_ticker=benchmark_ticker,
                error="FINNHUB_API_KEY is not set.",
            ).to_dict()

        try:
            candles = self._fetch_daily_candles(ticker)
            return self._build_metrics(
                ticker=ticker,
                candles=candles,
                benchmark_ticker=benchmark_ticker,
                benchmark_metrics=benchmark_metrics,
            ).to_dict()
        except Exception as e:
            return MarketMetrics.unavailable(
                ticker=ticker,
                benchmark_ticker=benchmark_ticker,
                error=sanitize_for_log(f"Finnhub market data error: {e}", [self.api_key]),
            ).to_dict()

    def _fetch_daily_candles(self, ticker: str, lookback_days: int = DEFAULT_LOOKBACK_DAYS) -> dict[str, Any]:
        now = int(time.time())
        start = now - (lookback_days * 24 * 60 * 60)

        try:
            response = requests.get(
                f"{FINNHUB_BASE_URL}/stock/candle",
                params={
                    "symbol": ticker,
                    "resolution": "D",
                    "from": start,
                    "to": now,
                },
                # Use the header instead of token= in the URL so access logs and
                # requests exceptions cannot leak FINNHUB_API_KEY.
                headers={"X-Finnhub-Token": self.api_key or ""},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise RuntimeError(
                f"request failed before provider response: {sanitize_for_log(e, [self.api_key])}"
            ) from e

        if response.status_code == 403:
            raise PermissionError(
                "HTTP 403 Forbidden from Finnhub stock/candle. The key is present, "
                "but this endpoint or symbol appears unavailable for the current plan/key."
            )

        if response.status_code == 401:
            raise PermissionError(
                "HTTP 401 Unauthorized from Finnhub stock/candle. Check FINNHUB_API_KEY."
            )

        if response.status_code == 429:
            raise RuntimeError(
                "HTTP 429 Too Many Requests from Finnhub stock/candle. Retry later or reduce ticker count."
            )

        if response.status_code >= 400:
            raise RuntimeError(_safe_finnhub_error(response, self.api_key))

        data = response.json()

        if data.get("s") != "ok":
            status = data.get("s", "unknown")
            error_message = data.get("error") or data.get("message") or "No candle data returned"
            raise ValueError(
                f"No candle data returned for {ticker}; Finnhub status={status}; "
                f"message={sanitize_for_log(error_message, [self.api_key])}"
            )

        closes = data.get("c") or []
        highs = data.get("h") or []
        lows = data.get("l") or []
        timestamps = data.get("t") or []
        volumes = data.get("v") or []

        if len(closes) < 30:
            raise ValueError(f"Insufficient candle history for {ticker}: {len(closes)} daily candles")

        return {
            "close": [float(x) for x in closes],
            "high": [float(x) for x in highs],
            "low": [float(x) for x in lows],
            "timestamp": [int(x) for x in timestamps],
            "volume": [float(x) for x in volumes],
        }


    def _build_metrics(
        self,
        ticker: str,
        candles: dict[str, list[float]],
        benchmark_ticker: str | None,
        benchmark_metrics: dict[str, Any] | None,
    ) -> MarketMetrics:
        closes = candles["close"]
        highs = candles.get("high", [])
        lows = candles.get("low", [])
        timestamps = candles.get("timestamp", [])
        volumes = candles.get("volume", [])

        close_price = closes[-1]
        as_of = None
        if timestamps:
            as_of = datetime.fromtimestamp(int(timestamps[-1]), tz=timezone.utc).date().isoformat()

        return_1m = self._return_pct(closes, 21)
        return_3m = self._return_pct(closes, 63)
        return_6m = self._return_pct(closes, 126)
        return_12m = self._return_pct(closes, 252)

        sma_50 = self._sma(closes, 50)
        sma_200 = self._sma(closes, 200)
        week_52_high = max(highs[-252:]) if highs else max(closes[-252:])
        week_52_low = min(lows[-252:]) if lows else min(closes[-252:])

        volatility_30d = self._annualized_volatility_pct(closes, 30)
        avg_volume_30d = self._average(volumes[-30:]) if volumes else None

        benchmark_return_3m = self._float_or_none((benchmark_metrics or {}).get("return_3m_pct"))
        benchmark_return_6m = self._float_or_none((benchmark_metrics or {}).get("return_6m_pct"))
        benchmark_return_12m = self._float_or_none((benchmark_metrics or {}).get("return_12m_pct"))

        return MarketMetrics(
            ticker=ticker,
            source=self.source,
            benchmark_ticker=benchmark_ticker,
            has_data=True,
            as_of=as_of,
            current_price=close_price,
            close_price=close_price,
            return_1m_pct=return_1m,
            return_3m_pct=return_3m,
            return_6m_pct=return_6m,
            return_12m_pct=return_12m,
            benchmark_return_3m_pct=benchmark_return_3m,
            benchmark_return_6m_pct=benchmark_return_6m,
            benchmark_return_12m_pct=benchmark_return_12m,
            relative_strength_3m_pct=self._subtract(return_3m, benchmark_return_3m),
            relative_strength_6m_pct=self._subtract(return_6m, benchmark_return_6m),
            relative_strength_12m_pct=self._subtract(return_12m, benchmark_return_12m),
            sma_50=sma_50,
            sma_200=sma_200,
            above_sma_50=(close_price >= sma_50 if sma_50 else None),
            above_sma_200=(close_price >= sma_200 if sma_200 else None),
            price_vs_sma_200_pct=(
                ((close_price / sma_200) - 1.0) * 100.0 if sma_200 else None
            ),
            week_52_high=week_52_high,
            week_52_low=week_52_low,
            distance_from_52w_high_pct=(
                ((close_price / week_52_high) - 1.0) * 100.0 if week_52_high else None
            ),
            distance_from_52w_low_pct=(
                ((close_price / week_52_low) - 1.0) * 100.0 if week_52_low else None
            ),
            volatility_30d_pct=volatility_30d,
            avg_volume_30d=avg_volume_30d,
            candle_count=len(closes),
        )

    @staticmethod
    def _return_pct(values: list[float], periods_back: int) -> float | None:
        if len(values) <= periods_back:
            return None
        previous = values[-periods_back]
        current = values[-1]
        if previous == 0:
            return None
        return ((current / previous) - 1.0) * 100.0

    @staticmethod
    def _sma(values: list[float], window: int) -> float | None:
        if len(values) < window:
            return None
        return sum(values[-window:]) / window

    @staticmethod
    def _annualized_volatility_pct(closes: list[float], window: int) -> float | None:
        if len(closes) < window + 1:
            return None
        recent = closes[-(window + 1):]
        returns: list[float] = []
        for i in range(1, len(recent)):
            prev = recent[i - 1]
            curr = recent[i]
            if prev:
                returns.append((curr / prev) - 1.0)
        if len(returns) < 2:
            return None
        return statistics.stdev(returns) * math.sqrt(252) * 100.0

    @staticmethod
    def _average(values: list[float]) -> float | None:
        if not values:
            return None
        return sum(values) / len(values)

    @staticmethod
    def _subtract(a: float | None, b: float | None) -> float | None:
        if a is None or b is None:
            return None
        return a - b

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
