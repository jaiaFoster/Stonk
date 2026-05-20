"""
app/services/earnings_service.py — Earnings Timestamp Provider v1.

Fetches upcoming earnings events for portfolio tickers. This is used as context
for calendar-spread screening/lifecycle checks, but it does not block the run.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Callable

from app import config
from app.providers.earnings_provider import (
    EarningsAuthError,
    EarningsProviderError,
    EarningsRateLimitError,
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
        logger("Earnings Timestamp Provider v1 skipped: FINNHUB_API_KEY is not set.")
        return _fill_unavailable(_all_equity_tickers_from_positions(positions), {}, "FINNHUB_API_KEY is not set.")

    start = date.today() - timedelta(days=max(0, int(config.EARNINGS_LOOKBACK_DAYS or 0)))
    end = date.today() + timedelta(days=max(1, int(config.EARNINGS_LOOKAHEAD_DAYS or 45)))

    logger(
        "Fetching Earnings Timestamp Provider v1 for "
        f"{len(tickers)} equity ticker(s); provider={config.EARNINGS_PROVIDER}; "
        f"window={start.isoformat()}..{end.isoformat()}"
        + (" (limited by dev/test mode)" if allowed_tickers is not None else "")
    )

    access_error: str | None = None
    for ticker in tickers:
        try:
            events = provider.get_earnings_calendar(ticker, start, end)
        except EarningsRateLimitError as e:
            safe_error = sanitize_for_log(e, [config.FINNHUB_API_KEY, config.RUN_TOKEN])
            logger(f"Earnings fetch stopped: {safe_error}")
            access_error = str(safe_error)
            break
        except (EarningsAuthError, EarningsProviderError, Exception) as e:
            safe_error = sanitize_for_log(e, [config.FINNHUB_API_KEY, config.RUN_TOKEN])
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


def _select_best_event(ticker: str, events: list[dict[str, Any]]) -> dict[str, Any] | None:
    today = date.today()
    parsed: list[tuple[int, dict[str, Any]]] = []
    for event in events:
        event_date = _parse_date(event.get("earnings_date") or event.get("date"))
        if not event_date:
            continue
        distance = (event_date - today).days
        # Prefer future/upcoming events, but allow a small lookback so recent reports still show.
        if distance < -max(0, int(config.EARNINGS_LOOKBACK_DAYS or 0)):
            continue
        priority = distance if distance >= 0 else 10_000 + abs(distance)
        event = dict(event)
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
        "source": str(config.EARNINGS_PROVIDER or "finnhub"),
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


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None
