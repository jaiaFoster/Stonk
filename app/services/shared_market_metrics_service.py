"""Canonical strategy-facing equity metrics map built from MarketDataHub facts."""

from __future__ import annotations

from typing import Any

from app.services.data_state_message_service import data_state_message, required_market_metrics_complete


DERIVED_FIELDS = [
    "momentum_1m", "momentum_3m", "momentum_6m", "momentum_12m",
    "sma_50", "sma_200", "price_vs_sma_50_pct", "price_vs_sma_200_pct",
    "relative_strength_vs_QQQ", "average_volume_30d", "realized_volatility_30d",
]


def build_canonical_market_metrics(hub: Any, tickers: list[str], plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(ticker).upper(): build_ticker_market_metrics(hub, str(ticker).upper(), plan) for ticker in tickers}


def build_ticker_market_metrics(hub: Any, ticker: str, plan: dict[str, Any] | None = None) -> dict[str, Any]:
    symbol = str(ticker).upper()
    state = (((plan or {}).get("by_ticker", {}).get(symbol, {}) or {}).get("state") or "APPROVED")
    if state in {"SKIPPED_DEV_CAP", "SKIPPED_PROVIDER_BUDGET"}:
        return _missing(symbol, state)

    candle_record = hub.get_daily_candles(symbol, min_bars=240, required=True, strategy_id="shared_market_metrics")
    candle_payload = _payload(candle_record)
    bars = candle_payload.get("bars", []) or []
    derived = hub.get_derived_metrics(symbol, metrics=DERIVED_FIELDS, required=True, strategy_id="shared_market_metrics")
    quote_record = hub.get_quote(symbol, required=False, strategy_id="shared_market_metrics")
    quote = _payload(quote_record)
    current_price = _number(quote.get("last") or quote.get("close") or quote.get("bid"))
    if current_price is None and bars:
        current_price = _number((bars[-1] or {}).get("close"))
    provider = _first(candle_record, "provider") or candle_payload.get("provider") or _first(quote_record, "provider")
    fetched_at = _first(candle_record, "fetched_at") or _first(quote_record, "fetched_at")
    fresh = _first(candle_record, "fresh")
    missing = [
        name for name, value in {
            "current_price": current_price,
            "momentum_3m": derived.get("momentum_3m"),
            "sma_200": derived.get("sma_200"),
            "average_volume_30d": derived.get("average_volume_30d"),
        }.items() if value is None
    ]
    if len(bars) < 200:
        missing.append("sufficient_daily_candles")
    data_state = "COMPLETE" if not missing and fresh is not False else "STALE_CACHE_USED" if fresh is False else "PARTIAL"
    row = {
        "ticker": symbol, "has_data": bool(bars), "data_state": data_state,
        "provider": provider, "source": "market_data_hub", "fetched_at": fetched_at,
        "fresh": fresh is not False, "confidence": candle_payload.get("quality", {}).get("confidence") or _first(candle_record, "confidence") or "unknown",
        "bar_count": len(bars), "current_price": current_price,
        "momentum_1m": derived.get("momentum_1m"), "momentum_3m": derived.get("momentum_3m"),
        "momentum_6m": derived.get("momentum_6m"), "momentum_12m": derived.get("momentum_12m"),
        "return_1m_pct": derived.get("momentum_1m"), "return_3m_pct": derived.get("momentum_3m"),
        "return_6m_pct": derived.get("momentum_6m"), "return_12m_pct": derived.get("momentum_12m"),
        "sma_50": derived.get("sma_50"), "sma_200": derived.get("sma_200"),
        "price_vs_sma_50_pct": derived.get("price_vs_sma_50_pct"), "price_vs_sma_200_pct": derived.get("price_vs_sma_200_pct"),
        "above_sma_50": _above(current_price, derived.get("sma_50")), "above_sma_200": _above(current_price, derived.get("sma_200")),
        "relative_strength_vs_qqq": derived.get("relative_strength_vs_QQQ"),
        "relative_strength_6m_pct": derived.get("relative_strength_vs_QQQ"),
        "average_volume_30d": derived.get("average_volume_30d"), "avg_volume_30d": derived.get("average_volume_30d"),
        "realized_volatility_30d": derived.get("realized_volatility_30d"), "volatility_30d_pct": derived.get("realized_volatility_30d"),
        "benchmark_ticker": "QQQ", "errors": [], "missing_reasons": missing,
    }
    row["required_market_data_complete"] = required_market_metrics_complete(row)
    row["error"] = data_state_message(data_state, fetched_at=fetched_at, reason=derived.get("reason") if missing else None)
    return row


def _missing(ticker: str, state: str) -> dict[str, Any]:
    return {"ticker": ticker, "has_data": False, "data_state": state, "required_market_data_complete": False, "errors": [], "missing_reasons": [state], "error": data_state_message(state)}


def _payload(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    return record.get("payload") if isinstance(record.get("payload"), dict) else record


def _first(record: Any, key: str) -> Any:
    return record.get(key) if isinstance(record, dict) else None


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _above(price: float | None, average: Any) -> bool | None:
    value = _number(average)
    return price > value if price is not None and value is not None else None
