"""
app/models/market_metrics.py — Normalized market momentum/trend metrics.

MarketMetrics is intentionally provider-neutral. Finnhub fills it today, but the
rest of the app should only depend on this normalized dictionary shape.

All return, distance, and volatility fields are stored as percentages, not
fractions. Example: 12.5 means +12.5%.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class MarketMetrics:
    ticker: str
    source: str = "finnhub"
    benchmark_ticker: str | None = None
    has_data: bool = False
    error: str | None = None
    as_of: str | None = None

    current_price: float | None = None
    close_price: float | None = None

    return_1m_pct: float | None = None
    return_3m_pct: float | None = None
    return_6m_pct: float | None = None
    return_12m_pct: float | None = None

    benchmark_return_3m_pct: float | None = None
    benchmark_return_6m_pct: float | None = None
    benchmark_return_12m_pct: float | None = None

    relative_strength_3m_pct: float | None = None
    relative_strength_6m_pct: float | None = None
    relative_strength_12m_pct: float | None = None

    sma_50: float | None = None
    sma_200: float | None = None
    above_sma_50: bool | None = None
    above_sma_200: bool | None = None
    price_vs_sma_200_pct: float | None = None

    week_52_high: float | None = None
    week_52_low: float | None = None
    distance_from_52w_high_pct: float | None = None
    distance_from_52w_low_pct: float | None = None

    volatility_30d_pct: float | None = None
    avg_volume_30d: float | None = None
    candle_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in list(data.items()):
            if isinstance(value, float):
                if key == "avg_volume_30d":
                    data[key] = round(value, 0)
                else:
                    data[key] = round(value, 2)
        return data

    @classmethod
    def unavailable(
        cls,
        ticker: str,
        error: str,
        benchmark_ticker: str | None = None,
        source: str = "finnhub",
    ) -> "MarketMetrics":
        return cls(
            ticker=ticker,
            source=source,
            benchmark_ticker=benchmark_ticker,
            has_data=False,
            error=error,
        )
