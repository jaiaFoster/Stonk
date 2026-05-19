"""
app/services/market_data_service.py — Market metric orchestration.

This service connects the portfolio pipeline to the Finnhub provider. It fetches
one benchmark snapshot, then one daily-candle snapshot per equity ticker. Crypto
positions are skipped for now because their trend/risk model should be separate.
"""

from __future__ import annotations

from typing import Any, Callable

from app import config
from app.models.market_metrics import MarketMetrics
from app.providers.market_data_provider import FinnhubMarketDataProvider

CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "LTC"}

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

    benchmark_ticker = (config.MARKET_BENCHMARK_TICKER or "QQQ").upper()

    if not config.FINNHUB_API_KEY:
        log("FINNHUB_API_KEY is not set; skipping Market Data v1.")
        return {}

    provider = FinnhubMarketDataProvider()
    tickers = _equity_tickers_from_positions(positions)

    if allowed_tickers is not None:
        allowed = {str(t).upper().strip() for t in allowed_tickers if str(t).strip()}
        tickers = [ticker for ticker in tickers if ticker in allowed]

    if max_tickers is not None:
        tickers = tickers[: max(0, int(max_tickers or 0))]

    if not tickers:
        log("No equity tickers found for Finnhub Market Data v1.")
        return {}

    limit_note = ""
    if max_tickers is not None or allowed_tickers is not None:
        limit_note = " (limited by dev/test mode)"
    log(f"Fetching Finnhub Market Data v1 for {len(tickers)} equity ticker(s); benchmark={benchmark_ticker}{limit_note}")

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

        # If the benchmark is denied by the provider, it is almost certainly an
        # endpoint/key/plan issue, not a symbol-specific issue. Avoid making one
        # failing request per holding and return clean unavailable metrics.
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
            log(f"Market data unavailable for {ticker}: {metrics.get('error')}")

    log(f"Market Data v1 fetched for {success_count}/{len(tickers)} equity ticker(s)")
    return market_metrics


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


def unavailable_metric(ticker: str, error: str) -> dict[str, Any]:
    return MarketMetrics.unavailable(ticker=ticker, error=error).to_dict()
