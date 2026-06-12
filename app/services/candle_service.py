"""Provider-neutral historical candle retrieval with per-ticker fallback."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
import time

from app import config
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]
_PROVIDER_FAILURE_SUPPRESS_UNTIL: dict[str, float] = {}


def get_candle_history(
    ticker: str,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_days: int | None = None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: None)
    symbol = str(ticker or "").upper().strip()
    end = _parse_date(end_date) or date.today()
    start = _parse_date(start_date) or (end - timedelta(days=int(lookback_days or config.TRADIER_HISTORICAL_LOOKBACK_DAYS or 460)))
    attempted: list[str] = []
    failures: list[dict[str, str]] = []

    for provider_name in config.MARKET_DATA_PROVIDER_ORDER:
        suppression_key = f"{provider_name}:candles"
        if _PROVIDER_FAILURE_SUPPRESS_UNTIL.get(suppression_key, 0) > time.monotonic():
            failures.append({"provider": provider_name, "error": "temporarily suppressed after recent candle failure"})
            logger(f"[CANDLE] {symbol} skipping {provider_name}: recent candle failure suppressed")
            continue
        attempted.append(provider_name)
        logger(f"[CANDLE] {symbol} trying {provider_name}")
        try:
            bars = _fetch_provider(provider_name, symbol, start, end)
            bars = [bar for bar in _normalize_bars(bars) if start.isoformat() <= bar["date"] <= end.isoformat()]
            quality = build_candle_quality(symbol, provider_name, bars, attempted, failures)
            if quality["confidence"] == "missing":
                raise RuntimeError(f"insufficient valid candles: {len(bars)}")
            logger(
                f"[CANDLE] {symbol} selected {provider_name}: "
                f"{len(bars)} bars, {quality['confidence']} confidence"
            )
            return {
                "ticker": symbol,
                "provider": provider_name,
                "bars": bars,
                "start_date": bars[0]["date"] if bars else start.isoformat(),
                "end_date": bars[-1]["date"] if bars else end.isoformat(),
                "bar_count": len(bars),
                "status": "ok" if quality["confidence"] in {"high", "medium"} else "partial",
                "errors": failures,
                "quality": quality,
            }
        except Exception as exc:
            safe = str(sanitize_for_log(exc, [config.FINNHUB_API_KEY, config.TRADIER_ACCESS_TOKEN, config.ALPHA_VANTAGE_API_KEY, config.RUN_TOKEN]))
            failures.append({"provider": provider_name, "error": safe})
            if "403" in safe or "forbidden" in safe.lower() or "too many requests" in safe.lower() or "429" in safe:
                _PROVIDER_FAILURE_SUPPRESS_UNTIL[suppression_key] = time.monotonic() + max(1, int(config.MARKET_DATA_PROVIDER_ERROR_TTL_SECONDS or 900))
            logger(f"[CANDLE] {symbol} {provider_name} failed: {safe}")

    logger(f"[CANDLE] {symbol} all providers failed; market metrics unavailable")
    quality = build_candle_quality(symbol, None, [], attempted, failures)
    return {
        "ticker": symbol,
        "provider": None,
        "bars": [],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "bar_count": 0,
        "status": "provider_error" if failures else "missing",
        "errors": failures,
        "quality": quality,
    }


def build_candle_quality(
    ticker: str,
    selected_provider: str | None,
    bars: list[dict[str, Any]],
    providers_attempted: list[str] | None = None,
    providers_failed: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    latest = _parse_date(bars[-1].get("date")) if bars else None
    recent_days = max(1, int(config.MARKET_DATA_CANDLE_RECENT_DAYS or 7))
    has_recent = bool(latest and latest >= date.today() - timedelta(days=recent_days))
    count = len(bars)
    required = max(30, int(config.MARKET_DATA_CANDLE_REQUIRED_BARS or 240))
    if count >= 240 and has_recent:
        confidence = "high"
    elif count >= 120 and has_recent:
        confidence = "medium"
    elif count >= 30:
        confidence = "low"
    else:
        confidence = "missing"
    status = "ok" if confidence in {"high", "medium"} else "partial" if confidence == "low" else "provider_error" if providers_failed else "missing"
    return {
        "ticker": ticker,
        "selected_provider": selected_provider,
        "providers_attempted": list(providers_attempted or []),
        "providers_failed": list(providers_failed or []),
        "bar_count": count,
        "required_bars": required,
        "coverage_pct": round(min(count / required * 100.0, 100.0), 1),
        "has_recent_bar": has_recent,
        "latest_bar_date": latest.isoformat() if latest else None,
        "confidence": confidence,
        "status": status,
    }


def _fetch_provider(provider_name: str, ticker: str, start: date, end: date) -> list[dict[str, Any]]:
    if provider_name == "finnhub":
        from app.providers.market_data_provider import FinnhubMarketDataProvider

        provider = FinnhubMarketDataProvider()
        if not provider.api_key:
            raise RuntimeError("FINNHUB_API_KEY is not set.")
        raw = provider._fetch_daily_candles(ticker, lookback_days=max(30, (end - start).days))
        return [
            {
                "date": datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(),
                "open": _series_at(raw.get("open") or raw.get("close"), i),
                "high": _series_at(raw.get("high") or raw.get("close"), i),
                "low": _series_at(raw.get("low") or raw.get("close"), i),
                "close": _series_at(raw.get("close"), i),
                "volume": _series_at(raw.get("volume"), i),
            }
            for i, ts in enumerate(raw.get("timestamp") or [])
            if i < len(raw.get("close") or [])
        ]
    if provider_name == "tradier":
        from app.providers.tradier_provider import TradierProvider

        provider = TradierProvider()
        if not provider.is_configured:
            raise RuntimeError("TRADIER_ACCESS_TOKEN is not set.")
        return provider.get_historical_quotes(ticker, start.isoformat(), end.isoformat(), interval="daily")
    if provider_name == "alphavantage":
        from app.providers.alpha_vantage_market_data_provider import AlphaVantageMarketDataProvider

        provider = AlphaVantageMarketDataProvider()
        if not provider.is_configured:
            raise RuntimeError("ALPHA_VANTAGE_API_KEY is not set.")
        return provider.get_daily_candles(ticker)
    raise RuntimeError(f"Unsupported candle provider: {provider_name}")


def _normalize_bars(raw_bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    for raw in raw_bars or []:
        try:
            close = float(raw.get("close"))
            bars.append(
                {
                    "date": str(raw.get("date") or "")[:10],
                    "open": float(raw.get("open") if raw.get("open") is not None else close),
                    "high": float(raw.get("high") if raw.get("high") is not None else close),
                    "low": float(raw.get("low") if raw.get("low") is not None else close),
                    "close": close,
                    "volume": float(raw.get("volume")) if raw.get("volume") not in {None, ""} else None,
                }
            )
        except (TypeError, ValueError):
            continue
    bars = [bar for bar in bars if bar["date"]]
    bars.sort(key=lambda item: item["date"])
    return bars


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _series_at(values: Any, index: int) -> Any:
    return values[index] if isinstance(values, list) and index < len(values) else None
