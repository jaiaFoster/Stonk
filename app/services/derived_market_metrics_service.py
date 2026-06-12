"""Shared daily-candle metric calculations."""

from __future__ import annotations

import math
import statistics
from typing import Any


def compute_derived_metrics(bars: list[dict[str, Any]], benchmark_bars: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    closes = [_num(row.get("close")) for row in bars or []]
    closes = [value for value in closes if value is not None]
    volumes = [_num(row.get("volume")) for row in (bars or [])[-30:]]
    volumes = [value for value in volumes if value is not None]
    out: dict[str, Any] = {}
    for name, days in (("momentum_1m", 21), ("momentum_3m", 63), ("momentum_6m", 126), ("momentum_12m", 252)):
        out[name] = _return(closes, days)
    out["sma_50"] = _average_tail(closes, 50)
    out["sma_200"] = _average_tail(closes, 200)
    last = closes[-1] if closes else None
    out["price_vs_sma_50_pct"] = _distance(last, out["sma_50"])
    out["price_vs_sma_200_pct"] = _distance(last, out["sma_200"])
    out["average_volume_30d"] = round(statistics.fmean(volumes), 2) if volumes else None
    out["realized_volatility_20d"] = _realized_volatility(closes, 20)
    out["realized_volatility_30d"] = _realized_volatility(closes, 30)
    benchmark_closes = [_num(row.get("close")) for row in benchmark_bars or []]
    benchmark_closes = [value for value in benchmark_closes if value is not None]
    stock_6m, bench_6m = out["momentum_6m"], _return(benchmark_closes, 126)
    out["relative_strength_vs_QQQ"] = round(stock_6m - bench_6m, 2) if stock_6m is not None and bench_6m is not None else None
    missing = [name for name, value in out.items() if value is None]
    return {"has_data": bool(closes), "metrics": out, "missing": missing, "reason": "" if not missing else f"Insufficient bars for: {', '.join(missing)}"}


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _return(values: list[float], days: int) -> float | None:
    return round((values[-1] / values[-days - 1] - 1) * 100, 2) if len(values) > days and values[-days - 1] else None


def _average_tail(values: list[float], days: int) -> float | None:
    return round(statistics.fmean(values[-days:]), 4) if len(values) >= days else None


def _distance(price: float | None, average: float | None) -> float | None:
    return round((price / average - 1) * 100, 2) if price is not None and average else None


def _realized_volatility(values: list[float], days: int) -> float | None:
    if len(values) <= days:
        return None
    returns = [math.log(values[i] / values[i - 1]) for i in range(len(values) - days, len(values)) if values[i - 1] > 0 and values[i] > 0]
    return round(statistics.stdev(returns) * math.sqrt(252) * 100, 2) if len(returns) >= 2 else None
