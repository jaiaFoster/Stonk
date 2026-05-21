"""
app/services/market_data_service.py — Market metric orchestration.

Market Data v1 tries Finnhub candles first. If Finnhub stock/candle is blocked
or returns no usable candles, it falls back to Tradier historical quotes so the
portfolio/watchlist scoring can still get momentum/trend inputs.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta
from typing import Any, Callable

from app import config
from app.models.market_metrics import MarketMetrics
from app.providers.market_data_provider import FinnhubMarketDataProvider
from app.providers.tradier_provider import TradierProvider
from app.utils.log_safety import sanitize_for_log

CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "LTC", "BCH", "AVAX", "MATIC"}

LogFn = Callable[[str], None]


def get_market_metrics_for_positions(
    positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
    max_tickers: int | None = None,
    allowed_tickers: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return normalized market metrics keyed by ticker."""

    def log(msg: str) -> None:
        if log_print:
            log_print(msg)

    benchmark_ticker = (config.MARKET_BENCHMARK_TICKER or "QQQ").upper().strip()
    tickers = _select_tickers(
        _equity_tickers_from_positions(positions),
        max_tickers=max_tickers,
        allowed_tickers=allowed_tickers,
    )

    if not tickers:
        log("No equity tickers found for Market Data v1.")
        return {}

    finnhub_result = _try_finnhub_metrics(tickers, benchmark_ticker, log)
    if _has_any_data(finnhub_result):
        return finnhub_result

    first_error = _first_error(finnhub_result)
    if not config.MARKET_DATA_USE_TRADIER_FALLBACK:
        return finnhub_result

    log("Market Data v1: Finnhub unavailable; using Tradier historical quotes fallback.")
    tradier_result = _try_tradier_historical_metrics(tickers, benchmark_ticker, log)
    if _has_any_data(tradier_result):
        return tradier_result

    # Preserve the Finnhub error when both providers fail, because that is what
    # currently explains most empty metric rows in the report.
    if finnhub_result:
        return finnhub_result
    return {
        ticker: MarketMetrics.unavailable(
            ticker=ticker,
            benchmark_ticker=benchmark_ticker,
            source="market_data",
            error=first_error or "No market data provider returned usable candles.",
        ).to_dict()
        for ticker in tickers
    }


def _try_finnhub_metrics(
    tickers: list[str],
    benchmark_ticker: str,
    log: LogFn,
) -> dict[str, dict[str, Any]]:
    if not config.FINNHUB_API_KEY:
        log("FINNHUB_API_KEY is not set; skipping Finnhub Market Data v1.")
        return {
            ticker: MarketMetrics.unavailable(
                ticker=ticker,
                benchmark_ticker=benchmark_ticker,
                error="FINNHUB_API_KEY is not set.",
            ).to_dict()
            for ticker in tickers
        }

    provider = FinnhubMarketDataProvider()
    log(f"Fetching Finnhub Market Data v1 for {len(tickers)} equity ticker(s); benchmark={benchmark_ticker}")

    benchmark_metrics = provider.get_market_metrics(
        ticker=benchmark_ticker,
        benchmark_ticker=benchmark_ticker,
        benchmark_metrics=None,
    )

    if benchmark_metrics.get("has_data"):
        log(
            f"Benchmark {benchmark_ticker}: "
            f"3M {benchmark_metrics.get('return_3m_pct')}%, "
            f"6M {benchmark_metrics.get('return_6m_pct')}%, "
            f"12M {benchmark_metrics.get('return_12m_pct')}%"
        )
    else:
        benchmark_error = str(benchmark_metrics.get("error") or "Unknown benchmark error")
        log(f"Benchmark {benchmark_ticker} unavailable: {benchmark_error}")
        if "HTTP 403" in benchmark_error or "HTTP 401" in benchmark_error:
            log("Finnhub candle access appears unavailable for this key; skipping per-ticker candle calls.")
            return {
                ticker: MarketMetrics.unavailable(
                    ticker=ticker,
                    benchmark_ticker=benchmark_ticker,
                    error=benchmark_error,
                ).to_dict()
                for ticker in tickers
            }

    market_metrics: dict[str, dict[str, Any]] = {}
    success_count = 0

    for ticker in tickers:
        if ticker == benchmark_ticker:
            metrics = benchmark_metrics
        else:
            metrics = provider.get_market_metrics(
                ticker=ticker,
                benchmark_ticker=benchmark_ticker,
                benchmark_metrics=benchmark_metrics if benchmark_metrics.get("has_data") else None,
            )
        market_metrics[ticker] = metrics
        if metrics.get("has_data"):
            success_count += 1
        else:
            log(f"Finnhub market data unavailable for {ticker}: {metrics.get('error')}")

    log(f"Finnhub Market Data v1 fetched for {success_count}/{len(tickers)} equity ticker(s)")
    return market_metrics


def _try_tradier_historical_metrics(
    tickers: list[str],
    benchmark_ticker: str,
    log: LogFn,
) -> dict[str, dict[str, Any]]:
    provider = TradierProvider()
    if not provider.is_configured:
        log("Tradier historical fallback skipped: TRADIER_ACCESS_TOKEN is not set.")
        return {}

    end = date.today()
    start = end - timedelta(days=max(260, int(config.TRADIER_HISTORICAL_LOOKBACK_DAYS or 460)))
    interval = str(config.TRADIER_HISTORICAL_INTERVAL or "daily")

    log(
        "Fetching Tradier historical fallback metrics for "
        f"{len(tickers)} equity ticker(s); benchmark={benchmark_ticker}; "
        f"window={start.isoformat()}..{end.isoformat()}"
    )

    benchmark_metrics = _tradier_metrics_for_ticker(
        provider=provider,
        ticker=benchmark_ticker,
        benchmark_ticker=benchmark_ticker,
        benchmark_metrics=None,
        start=start,
        end=end,
        interval=interval,
        log=log,
    )

    result: dict[str, dict[str, Any]] = {}
    success_count = 0
    for ticker in tickers:
        if ticker == benchmark_ticker:
            metrics = benchmark_metrics
        else:
            metrics = _tradier_metrics_for_ticker(
                provider=provider,
                ticker=ticker,
                benchmark_ticker=benchmark_ticker,
                benchmark_metrics=benchmark_metrics if benchmark_metrics.get("has_data") else None,
                start=start,
                end=end,
                interval=interval,
                log=log,
            )
        result[ticker] = metrics
        if metrics.get("has_data"):
            success_count += 1
        else:
            log(f"Tradier historical market data unavailable for {ticker}: {metrics.get('error')}")

    log(f"Tradier historical fallback fetched for {success_count}/{len(tickers)} equity ticker(s)")
    return result


def _tradier_metrics_for_ticker(
    provider: TradierProvider,
    ticker: str,
    benchmark_ticker: str,
    benchmark_metrics: dict[str, Any] | None,
    start: date,
    end: date,
    interval: str,
    log: LogFn,
) -> dict[str, Any]:
    try:
        days = provider.get_historical_quotes(
            symbol=ticker,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            interval=interval,
        )
        return _build_metrics_from_tradier_days(
            ticker=ticker,
            days=days,
            benchmark_ticker=benchmark_ticker,
            benchmark_metrics=benchmark_metrics,
        ).to_dict()
    except Exception as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        return MarketMetrics.unavailable(
            ticker=ticker,
            benchmark_ticker=benchmark_ticker,
            source="tradier_history",
            error=f"Tradier historical data error: {safe_error}",
        ).to_dict()


def _build_metrics_from_tradier_days(
    ticker: str,
    days: list[dict[str, Any]],
    benchmark_ticker: str,
    benchmark_metrics: dict[str, Any] | None,
) -> MarketMetrics:
    clean_days = [d for d in days if _float(d.get("close")) is not None]
    clean_days.sort(key=lambda d: str(d.get("date") or ""))
    if len(clean_days) < 30:
        return MarketMetrics.unavailable(
            ticker=ticker,
            benchmark_ticker=benchmark_ticker,
            source="tradier_history",
            error=f"Insufficient Tradier historical candles: {len(clean_days)} daily candles.",
        )

    closes = [_float(d.get("close")) or 0.0 for d in clean_days]
    highs = [_float(d.get("high")) or closes[i] for i, d in enumerate(clean_days)]
    lows = [_float(d.get("low")) or closes[i] for i, d in enumerate(clean_days)]
    volumes = [_float(d.get("volume")) or 0.0 for d in clean_days]
    close_price = closes[-1]
    as_of = str(clean_days[-1].get("date") or "")[:10] or None

    return_1m = _return_pct(closes, 21)
    return_3m = _return_pct(closes, 63)
    return_6m = _return_pct(closes, 126)
    return_12m = _return_pct(closes, 252)
    sma_50 = _sma(closes, 50)
    sma_200 = _sma(closes, 200)
    week_52_high = max(highs[-252:]) if highs else max(closes[-252:])
    week_52_low = min(lows[-252:]) if lows else min(closes[-252:])

    benchmark_return_3m = _float((benchmark_metrics or {}).get("return_3m_pct"))
    benchmark_return_6m = _float((benchmark_metrics or {}).get("return_6m_pct"))
    benchmark_return_12m = _float((benchmark_metrics or {}).get("return_12m_pct"))

    return MarketMetrics(
        ticker=ticker,
        source="tradier_history",
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
        relative_strength_3m_pct=_subtract(return_3m, benchmark_return_3m),
        relative_strength_6m_pct=_subtract(return_6m, benchmark_return_6m),
        relative_strength_12m_pct=_subtract(return_12m, benchmark_return_12m),
        sma_50=sma_50,
        sma_200=sma_200,
        above_sma_50=(close_price >= sma_50 if sma_50 else None),
        above_sma_200=(close_price >= sma_200 if sma_200 else None),
        price_vs_sma_200_pct=((close_price / sma_200) - 1.0) * 100.0 if sma_200 else None,
        week_52_high=week_52_high,
        week_52_low=week_52_low,
        distance_from_52w_high_pct=((close_price / week_52_high) - 1.0) * 100.0 if week_52_high else None,
        distance_from_52w_low_pct=((close_price / week_52_low) - 1.0) * 100.0 if week_52_low else None,
        volatility_30d_pct=_annualized_volatility_pct(closes, 30),
        avg_volume_30d=_average(volumes[-30:]),
        candle_count=len(closes),
    )


def _equity_tickers_from_positions(positions: list[dict[str, Any]]) -> list[str]:
    tickers: list[str] = []
    seen: set[str] = set()
    for position in positions:
        ticker = str(position.get("ticker", "")).upper().strip()
        account = str(position.get("account", "")).lower()
        if not ticker or ticker in seen:
            continue
        if ticker in CRYPTO_TICKERS or account == "crypto":
            continue
        seen.add(ticker)
        tickers.append(ticker)
    return tickers


def _select_tickers(
    tickers: list[str],
    max_tickers: int | None,
    allowed_tickers: list[str] | None = None,
) -> list[str]:
    normalized = [str(t).upper().strip() for t in tickers if str(t).strip()]
    if allowed_tickers is not None:
        allowed = {str(t).upper().strip() for t in allowed_tickers if str(t).strip()}
        normalized = [t for t in normalized if t in allowed]
    if max_tickers is not None:
        normalized = normalized[: max(1, int(max_tickers or 1))]
    return normalized


def _has_any_data(metrics: dict[str, dict[str, Any]]) -> bool:
    return any((m or {}).get("has_data") for m in metrics.values())


def _first_error(metrics: dict[str, dict[str, Any]]) -> str | None:
    for item in metrics.values():
        error = (item or {}).get("error")
        if error:
            return str(error)
    return None


def unavailable_metric(ticker: str, error: str) -> dict[str, Any]:
    return MarketMetrics.unavailable(ticker=ticker, error=error).to_dict()


def _return_pct(values: list[float], periods_back: int) -> float | None:
    if len(values) <= periods_back:
        return None
    previous = values[-periods_back]
    current = values[-1]
    if previous == 0:
        return None
    return ((current / previous) - 1.0) * 100.0


def _sma(values: list[float], window: int) -> float | None:
    if len(values) < window:
        return None
    return sum(values[-window:]) / window


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


def _average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _subtract(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a - b


def _float(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
