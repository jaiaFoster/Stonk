"""Forward Factor-specific validation of normalized shared facts."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app import config


def validate_required_data(
    quote_record: Any,
    candle_record: Any,
    metrics: dict[str, Any] | None,
    now: datetime | None = None,
    planned_state: str | None = None,
) -> dict[str, Any]:
    """Validate only FF cheap-filter facts, not stock-momentum trend gates."""
    current = now or datetime.now(timezone.utc)
    state = str(planned_state or "").upper()
    if state in {"SKIPPED_DEV_CAP", "SKIPPED_PROVIDER_BUDGET"}:
        return _result(False, state, missing_fields=[state])

    quote, candles = _payload(quote_record), _payload(candle_record)
    metric = metrics or {}
    price = _number(quote.get("last") or quote.get("price") or quote.get("close") or quote.get("bid") or metric.get("current_price"))
    bars = candles.get("bars", []) or []
    average_volume = _number(metric.get("average_volume_30d") or metric.get("avg_volume_30d"))
    if average_volume is None and bars:
        volumes = [_number(row.get("volume")) for row in bars[-30:] if isinstance(row, dict)]
        clean = [value for value in volumes if value is not None]
        average_volume = sum(clean) / len(clean) if clean else None
    missing = []
    if price is None:
        missing.append("price")
    if len(bars) < 240:
        missing.append("daily_candles_240")
    if average_volume is None:
        missing.append("average_volume_30d")

    quote_age = _age_seconds(quote_record, current)
    candle_age = _age_seconds(candle_record, current)
    stale = []
    if _fresh(quote_record) is False or (quote_age is not None and quote_age > config.MARKET_DATA_QUOTE_TTL_SECONDS):
        stale.append("quote")
    if _fresh(candle_record) is False or (candle_age is not None and candle_age > config.MARKET_DATA_CANDLES_TTL_SECONDS):
        stale.append("daily_candles")
    if missing:
        return _result(False, "PARTIAL", price, average_volume, quote_age, candle_age, missing, stale, quote_record, candle_record)
    if stale:
        return _result(False, "STALE", price, average_volume, quote_age, candle_age, missing, stale, quote_record, candle_record)
    if price < config.FF_MIN_UNDERLYING_PRICE:
        return _result(False, "PRICE_BELOW_MINIMUM", price, average_volume, quote_age, candle_age, [], [], quote_record, candle_record)
    if average_volume < config.FF_MIN_AVERAGE_VOLUME:
        return _result(False, "AVERAGE_VOLUME_BELOW_MINIMUM", price, average_volume, quote_age, candle_age, [], [], quote_record, candle_record)
    return _result(True, "COMPLETE", price, average_volume, quote_age, candle_age, [], [], quote_record, candle_record)


def _result(eligible: bool, state: str, price=None, average_volume=None, quote_age=None, candle_age=None, missing_fields=None, stale_fields=None, quote_record=None, candle_record=None) -> dict[str, Any]:
    return {
        "eligible": eligible, "data_state": state, "price": price,
        "minimum_price": config.FF_MIN_UNDERLYING_PRICE, "price_pass": price is not None and price >= config.FF_MIN_UNDERLYING_PRICE,
        "average_volume_30d": average_volume, "minimum_average_volume": config.FF_MIN_AVERAGE_VOLUME,
        "average_volume_pass": average_volume is not None and average_volume >= config.FF_MIN_AVERAGE_VOLUME,
        "quote_age_seconds": quote_age, "candle_age_seconds": candle_age,
        "confidence": _confidence(quote_record, candle_record), "missing_fields": missing_fields or [],
        "stale_fields": stale_fields or [], "source_summary": {
            "quote_provider": _field(quote_record, "provider"), "candle_provider": _field(candle_record, "provider"),
            "quote_fetched_at": _field(quote_record, "fetched_at"), "candle_fetched_at": _field(candle_record, "fetched_at"),
        },
    }


def _payload(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    for key in ("payload", "data"):
        if isinstance(record.get(key), dict):
            return record[key]
    return record


def _fresh(record: Any) -> bool | None:
    value = _field(record, "fresh")
    return value if isinstance(value, bool) else None


def _age_seconds(record: Any, now: datetime) -> float | None:
    raw = _field(record, "fetched_at") or _field(record, "timestamp")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            # A provider-naive timestamp has no reliable offset. Trust the
            # normalized record's `fresh` flag instead of inventing UTC.
            return None
        return max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


def _field(record: Any, key: str) -> Any:
    return record.get(key) if isinstance(record, dict) else None


def _confidence(*records: Any) -> str:
    values = [str(_field(record, "confidence") or "").lower() for record in records]
    return "low" if "low" in values else "high" if "high" in values else "unknown"


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
