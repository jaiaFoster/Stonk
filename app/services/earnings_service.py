"""
app/services/earnings_service.py — Earnings Timestamp Provider v1 + discovery.

The timestamp provider fetches earnings context for known tickers. The discovery
helper starts from an earnings-calendar date window and produces an independent
trade-discovery universe for the earnings-calendar strategy.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from app import config
from app.providers.earnings_provider import (
    EarningsAuthError,
    EarningsProviderError,
    EarningsRateLimitError,
    configured_provider_names,
    earnings_provider_secret_values,
    get_provider,
)
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]

NON_EQUITY_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "AVAX", "MATIC", "USDC", "USDT"}


def get_earnings_for_positions(
    positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
    max_tickers: int | None = None,
    allowed_tickers: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return upcoming earnings data keyed by ticker."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    provider = get_provider()
    provider_names = configured_provider_names()
    result: dict[str, dict[str, Any]] = {}

    tickers = _equity_tickers_from_positions(positions)
    if allowed_tickers is not None:
        allowed = {str(t).upper().strip() for t in allowed_tickers if str(t).strip()}
        tickers = [ticker for ticker in tickers if ticker in allowed]

    if max_tickers is not None:
        tickers = tickers[: max(1, int(max_tickers or 1))]

    if not config.EARNINGS_PROVIDER_ENABLED:
        logger("Earnings Timestamp Provider v1 disabled by EARNINGS_PROVIDER_ENABLED=false.")
        return _fill_unavailable(_all_equity_tickers_from_positions(positions), {}, "Earnings provider disabled.")

    if not provider.is_configured:
        logger("Earnings Timestamp Provider v1 skipped: no earnings provider keys are configured.")
        return _fill_unavailable(_all_equity_tickers_from_positions(positions), {}, "No earnings provider keys are configured.")

    start = date.today() - timedelta(days=max(0, int(config.EARNINGS_LOOKBACK_DAYS or 0)))
    end = date.today() + timedelta(days=max(1, int(config.EARNINGS_LOOKAHEAD_DAYS or 45)))

    logger(
        "Fetching Earnings Timestamp Provider v1 for "
        f"{len(tickers)} equity ticker(s); providers={provider_names or [config.EARNINGS_PROVIDER]}; "
        f"window={start.isoformat()}..{end.isoformat()}"
        + (" (limited by dev/test mode)" if allowed_tickers is not None else "")
    )

    access_error: str | None = None
    for ticker in tickers:
        try:
            events = provider.get_earnings_calendar(ticker, start, end)
        except EarningsRateLimitError as e:
            safe_error = sanitize_for_log(e, earnings_provider_secret_values())
            logger(f"Earnings fetch stopped: {safe_error}")
            access_error = str(safe_error)
            break
        except (EarningsAuthError, EarningsProviderError, Exception) as e:
            safe_error = sanitize_for_log(e, earnings_provider_secret_values())
            result[ticker] = _unavailable_event(ticker, str(safe_error))
            logger(f"Earnings {ticker}: unavailable — {safe_error}")
            continue

        selected = _select_best_event(ticker, events)
        if selected:
            result[ticker] = selected
            logger(
                f"Earnings {ticker}: {selected.get('earnings_date') or 'unknown date'} | "
                f"{selected.get('session_label') or 'Unknown'} | source={selected.get('source')}"
            )
        else:
            result[ticker] = _unavailable_event(ticker, "No upcoming earnings event returned in lookahead window.")
            logger(f"Earnings {ticker}: no event found in lookahead window.")

    all_equity_tickers = _all_equity_tickers_from_positions(positions)
    if access_error:
        return _fill_unavailable(all_equity_tickers, result, access_error)
    return _fill_unavailable(all_equity_tickers, result, "Not fetched this run; limited by provider budget/dev mode.")


def discover_upcoming_earnings_for_calendar_trades(
    log_print: LogFn | None = None,
    run_mode: str = "prod",
) -> dict[str, Any]:
    """Discover an independent earnings universe for possible calendar trades.

    This is deliberately separate from portfolio and watchlist tickers. The
    calendar strategy should start from upcoming earnings events, then use
    Tradier chains to decide whether a trade is interesting.
    """
    logger = log_print or (lambda msg: print(msg, flush=True))
    provider = get_provider()
    provider_names = configured_provider_names()

    result: dict[str, Any] = {
        "source": "earnings_discovery_v1",
        "provider": "+".join(provider_names) if provider_names else str(config.EARNINGS_PROVIDER or "finnhub"),
        "provider_order": provider_names,
        "enabled": bool(config.EARNINGS_DISCOVERY_ENABLED),
        "has_data": False,
        "window_start": None,
        "window_end": None,
        "items": [],
        "events_by_ticker": {},
        "tickers": [],
        "errors": [],
        "summary": {
            "event_count": 0,
            "ticker_count": 0,
            "window_start_days": int(config.EARNINGS_DISCOVERY_START_DAYS or 2),
            "window_end_days": int(config.EARNINGS_DISCOVERY_END_DAYS or 4),
        },
    }

    if not config.EARNINGS_DISCOVERY_ENABLED:
        logger("Earnings Trade Discovery v1 disabled by EARNINGS_DISCOVERY_ENABLED=false.")
        return result

    if not config.EARNINGS_PROVIDER_ENABLED:
        result["errors"].append("Earnings provider disabled.")
        logger("Earnings Trade Discovery v1 skipped: earnings provider disabled.")
        return result

    if not provider.is_configured:
        result["errors"].append("No earnings provider keys are configured.")
        logger("Earnings Trade Discovery v1 skipped: no earnings provider keys are configured.")
        return result

    start_offset = int(config.EARNINGS_DISCOVERY_START_DAYS or 2)
    end_offset = int(config.EARNINGS_DISCOVERY_END_DAYS or 4)
    if end_offset < start_offset:
        end_offset = start_offset
    start = date.today() + timedelta(days=max(0, start_offset))
    end = date.today() + timedelta(days=max(0, end_offset))
    result["window_start"] = start.isoformat()
    result["window_end"] = end.isoformat()

    max_events = max(1, int(config.EARNINGS_DISCOVERY_MAX_EVENTS or 25))
    max_tickers = max(1, int(config.EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN or 6))
    if str(run_mode or "prod").lower() == "dev":
        max_tickers = max(1, int(config.DEV_MAX_TICKERS or 1))
        max_events = max_tickers

    logger(
        "Fetching Earnings Trade Discovery v1 universe; "
        f"providers={provider_names or [config.EARNINGS_PROVIDER]}; window={start.isoformat()}..{end.isoformat()}; "
        f"max_tickers={max_tickers}"
        + (" (limited by dev/test mode)" if str(run_mode or "prod").lower() == "dev" else "")
    )

    try:
        raw_events = provider.get_earnings_calendar_range(start, end)
    except EarningsRateLimitError as e:
        safe_error = sanitize_for_log(e, earnings_provider_secret_values())
        result["errors"].append(str(safe_error))
        logger(f"Earnings Trade Discovery v1 stopped: {safe_error}")
        return result
    except (EarningsAuthError, EarningsProviderError, Exception) as e:
        safe_error = sanitize_for_log(e, earnings_provider_secret_values())
        result["errors"].append(str(safe_error))
        logger(f"Earnings Trade Discovery v1 unavailable: {safe_error}")
        return result

    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for event in raw_events:
        ticker = str(event.get("ticker") or event.get("symbol") or "").upper().strip()
        if not _valid_equity_symbol(ticker) or ticker in seen:
            continue
        selected = _select_best_event(ticker, [event])
        if not selected:
            continue
        selected = dict(selected)
        selected["discovery_reason"] = "Upcoming earnings event in configured discovery window."
        events.append(selected)
        seen.add(ticker)
        if len(events) >= max_events or len(seen) >= max_tickers:
            break

    events.sort(key=lambda item: (item.get("earnings_date") or "9999-99-99", str(item.get("ticker") or "")))
    events = events[:max_tickers]
    tickers = [str(event.get("ticker") or "").upper().strip() for event in events if event.get("ticker")]

    result["items"] = events
    result["events_by_ticker"] = {ticker: event for ticker, event in zip(tickers, events)}
    result["tickers"] = tickers
    result["has_data"] = bool(events)
    result["summary"] = {
        "event_count": len(events),
        "ticker_count": len(tickers),
        "window_start_days": start_offset,
        "window_end_days": end_offset,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
    }
    logger(f"Earnings Trade Discovery v1 found {len(events)} event(s): {tickers}")
    return result


def _select_best_event(ticker: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    today = date.today()
    parsed: list[tuple[int, dict[str, Any]]] = []
    for event in events:
        event_date = _parse_date(event.get("earnings_date") or event.get("date"))
        if not event_date:
            continue
        distance = (event_date - today).days
        if distance < -max(0, int(config.EARNINGS_LOOKBACK_DAYS or 0)):
            continue
        priority = distance if distance >= 0 else 10_000 + abs(distance)
        event = dict(event)
        event["ticker"] = str(event.get("ticker") or ticker).upper().strip()
        event["symbol"] = event["ticker"]
        event["days_until_earnings"] = distance
        event["has_data"] = True
        parsed.append((priority, event))
    parsed.sort(key=lambda pair: pair[0])
    return parsed[0][1] if parsed else None


def _fill_unavailable(
    all_tickers: list[str],
    current: dict[str, dict[str, Any]],
    reason: str,
) -> dict[str, dict[str, Any]]:
    filled = dict(current)
    for ticker in all_tickers:
        filled.setdefault(ticker, _unavailable_event(ticker, reason))
    return filled


def _unavailable_event(ticker: str, reason: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "symbol": ticker,
        "source": "+".join(config.EARNINGS_PROVIDER_ORDER or [config.EARNINGS_PROVIDER or "finnhub"]),
        "has_data": False,
        "earnings_date": None,
        "date": None,
        "hour": None,
        "time_of_day": "unknown",
        "session_label": "Unknown",
        "is_timestamp_confirmed": False,
        "days_until_earnings": None,
        "error": reason,
    }


def _equity_tickers_from_positions(positions: list[dict[str, Any]]) -> list[str]:
    return _all_equity_tickers_from_positions(positions)


def _all_equity_tickers_from_positions(positions: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for pos in positions:
        ticker = str(pos.get("ticker") or "").upper().strip()
        account = str(pos.get("account") or "").lower()
        if not ticker or ticker in NON_EQUITY_TICKERS or account == "crypto":
            continue
        if ticker not in seen:
            seen.append(ticker)
    return seen


def _valid_equity_symbol(ticker: str) -> bool:
    if not ticker or ticker in NON_EQUITY_TICKERS:
        return False
    # Tradier usually supports simple US equity tickers cleanly. Skip symbols
    # with punctuation for v1 discovery to avoid wasting option-chain calls.
    return ticker.replace(".", "").replace("-", "").isalnum() and len(ticker) <= 6


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None
