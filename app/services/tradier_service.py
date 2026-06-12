"""
app/services/tradier_service.py — Tradier quote/options orchestration.

Tradier Provider v1 is deliberately small and safe:
- limited ticker counts, especially in dev mode
- quote + expiration + one chain sample per ticker
- no order placement
- graceful fallback when token/access/data fails
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from app import config
from app.providers.tradier_provider import TradierProvider, TradierProviderError
from app.utils.log_safety import sanitize_for_log

CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "DOGE", "ADA", "XRP", "LTC", "BCH", "AVAX", "MATIC"}
SPECULATIVE_WITH_OPTIONS = {"QBTS", "SMR", "SOFI", "HOOD"}

LogFn = Callable[[str], None]
TradierSnapshot = dict[str, dict[str, Any]]


def get_tradier_snapshot_for_positions(
    positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
    max_tickers: int | None = None,
    allowed_tickers: list[str] | None = None,
    data_hub: Any | None = None,
) -> TradierSnapshot:
    """Fetch Tradier quote/expiration/chain samples for selected equity tickers."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    provider = TradierProvider()

    all_equity_tickers = _equity_tickers_from_positions(positions)
    selected = _select_tickers(
        all_equity_tickers,
        max_tickers=max_tickers if max_tickers is not None else config.TRADIER_MAX_TICKERS_PER_RUN,
        allowed_tickers=allowed_tickers,
    )

    if not provider.is_configured:
        logger("TRADIER_ACCESS_TOKEN is not set; skipping Tradier Provider v1.")
        return _unavailable_for_tickers(selected or all_equity_tickers, "TRADIER_ACCESS_TOKEN is not set.")

    if not selected:
        logger("Tradier Provider v1 skipped: no eligible equity tickers selected.")
        return {}

    logger(
        f"Fetching Tradier Provider v1 for {len(selected)} equity ticker(s); "
        f"env={provider.environment}; max_tickers={max_tickers if max_tickers is not None else config.TRADIER_MAX_TICKERS_PER_RUN}"
    )

    snapshot: TradierSnapshot = {}

    try:
        quotes = {
            ticker: _record_payload(data_hub.get_quote(ticker, required=False, strategy_id="tradier_snapshot"))
            for ticker in selected
        } if data_hub is not None else provider.get_quotes(selected, greeks=False)
        logger(f"Tradier quotes fetched for {len(quotes)}/{len(selected)} ticker(s)")
    except Exception as e:
        safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
        logger(f"Tradier quote fetch failed: {safe_error}")
        return _unavailable_for_tickers(selected, f"Tradier quote fetch failed: {safe_error}")

    for ticker in selected:
        quote = quotes.get(ticker, {})
        try:
            shared_chain = _record_payload(data_hub.get_preloaded_options_chain(ticker, strategy_id="tradier_snapshot")) if data_hub is not None else {}
            expirations = list(shared_chain.get("expirations", []) or []) if shared_chain else provider.get_expirations(ticker)
            selected_expiration = _select_expiration(expirations, min_days=config.TRADIER_MIN_DAYS_TO_EXPIRATION)
            chain: list[dict[str, Any]] = list((shared_chain.get("chains", {}) or {}).get(selected_expiration, []) or []) if shared_chain else []
            if selected_expiration and not chain and data_hub is None:
                chain = provider.get_option_chain(ticker, selected_expiration, greeks=bool(config.TRADIER_INCLUDE_GREEKS))

            snapshot[ticker] = _summarize_ticker(
                ticker=ticker,
                quote=quote,
                expirations=expirations,
                selected_expiration=selected_expiration,
                chain=chain,
            )
            logger(
                f"Tradier {ticker}: quote={'yes' if quote else 'no'}, "
                f"expirations={len(expirations)}, selected_expiration={selected_expiration or 'none'}, "
                f"contracts={len(chain)}"
            )
        except Exception as e:
            safe_error = sanitize_for_log(e, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
            logger(f"Tradier data unavailable for {ticker}: {safe_error}")
            snapshot[ticker] = {
                "ticker": ticker,
                "source": "tradier",
                "has_data": False,
                "error": str(safe_error),
                "quote": quote,
                "expiration_count": 0,
                "selected_expiration": None,
                "expiration_candidates": [],
                "chain_contract_count": 0,
                "call_count": 0,
                "put_count": 0,
                "atm_call": None,
                "atm_put": None,
                "total_volume": None,
                "total_open_interest": None,
            }

    return snapshot


def _record_payload(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {}
    return record.get("payload") if isinstance(record.get("payload"), dict) else record


def _equity_tickers_from_positions(positions: list[dict[str, Any]]) -> list[str]:
    tickers: list[str] = []
    for pos in positions:
        ticker = str(pos.get("ticker") or "").upper().strip()
        if not ticker or ticker in CRYPTO_TICKERS:
            continue
        if str(pos.get("account", "")).strip().lower() == "crypto":
            continue
        if ticker not in tickers:
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

    # Prefer actively interesting/options-relevant aggressive-growth tickers first,
    # while still respecting the user's dev/prod ticker subset.
    preferred: list[str] = []
    for ticker in normalized:
        if ticker in SPECULATIVE_WITH_OPTIONS or ticker in {"NVDA", "AMZN", "META", "GOOGL"}:
            preferred.append(ticker)
    ordered = preferred + [ticker for ticker in normalized if ticker not in preferred]

    limit = max(1, int(max_tickers or 1))
    return ordered[:limit]


def _select_expiration(expirations: list[str], min_days: int = 7) -> str | None:
    today = date.today()
    parsed: list[tuple[date, str]] = []
    for raw in expirations:
        try:
            exp_date = datetime.strptime(str(raw), "%Y-%m-%d").date()
        except ValueError:
            continue
        if (exp_date - today).days >= int(min_days or 0):
            parsed.append((exp_date, str(raw)))

    if parsed:
        parsed.sort(key=lambda item: item[0])
        return parsed[0][1]

    # Fallback to the first parseable expiration if every date is sooner than the threshold.
    fallback: list[tuple[date, str]] = []
    for raw in expirations:
        try:
            fallback.append((datetime.strptime(str(raw), "%Y-%m-%d").date(), str(raw)))
        except ValueError:
            continue
    fallback.sort(key=lambda item: item[0])
    return fallback[0][1] if fallback else None


def _summarize_ticker(
    ticker: str,
    quote: dict[str, Any],
    expirations: list[str],
    selected_expiration: str | None,
    chain: list[dict[str, Any]],
) -> dict[str, Any]:
    underlying_price = _underlying_price(quote)
    calls = [opt for opt in chain if str(opt.get("option_type")).lower() == "call"]
    puts = [opt for opt in chain if str(opt.get("option_type")).lower() == "put"]
    atm_call = _nearest_atm(calls, underlying_price)
    atm_put = _nearest_atm(puts, underlying_price)

    total_volume = sum(int(opt.get("volume") or 0) for opt in chain)
    total_open_interest = sum(int(opt.get("open_interest") or 0) for opt in chain)

    return {
        "ticker": ticker,
        "source": "tradier",
        "has_data": bool(quote or chain or expirations),
        "error": None,
        "quote": quote,
        "underlying_price": underlying_price,
        "expiration_count": len(expirations),
        "selected_expiration": selected_expiration,
        "expiration_candidates": expirations[:8],
        "chain_contract_count": len(chain),
        "call_count": len(calls),
        "put_count": len(puts),
        "atm_call": _compact_option(atm_call),
        "atm_put": _compact_option(atm_put),
        "total_volume": total_volume,
        "total_open_interest": total_open_interest,
    }


def _underlying_price(quote: dict[str, Any]) -> float | None:
    for key in ["last", "bid", "ask", "close", "prevclose"]:
        value = _float_or_none(quote.get(key))
        if value is not None and value > 0:
            return value
    bid = _float_or_none(quote.get("bid"))
    ask = _float_or_none(quote.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return None


def _nearest_atm(options: list[dict[str, Any]], underlying_price: float | None) -> dict[str, Any] | None:
    if not options:
        return None
    if underlying_price is None:
        return sorted(options, key=lambda opt: float(opt.get("strike") or 0))[len(options) // 2]
    return min(options, key=lambda opt: abs(float(opt.get("strike") or 0) - underlying_price))


def _compact_option(option: dict[str, Any] | None) -> dict[str, Any] | None:
    if not option:
        return None
    bid = _float_or_none(option.get("bid"))
    ask = _float_or_none(option.get("ask"))
    mid = _float_or_none(option.get("mid"))
    spread_pct = None
    if bid is not None and ask is not None and mid and mid > 0:
        spread_pct = ((ask - bid) / mid) * 100.0
    return {
        "symbol": option.get("symbol"),
        "option_type": option.get("option_type"),
        "expiration_date": option.get("expiration_date"),
        "strike": _float_or_none(option.get("strike")),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": _float_or_none(option.get("last")),
        "volume": option.get("volume"),
        "open_interest": option.get("open_interest"),
        "iv": _float_or_none(option.get("iv")),
        "delta": _float_or_none(option.get("delta")),
        "theta": _float_or_none(option.get("theta")),
        "spread_pct": spread_pct,
    }


def _unavailable_for_tickers(tickers: list[str], error: str) -> TradierSnapshot:
    return {
        str(ticker).upper().strip(): {
            "ticker": str(ticker).upper().strip(),
            "source": "tradier",
            "has_data": False,
            "error": error,
            "quote": {},
            "expiration_count": 0,
            "selected_expiration": None,
            "expiration_candidates": [],
            "chain_contract_count": 0,
            "call_count": 0,
            "put_count": 0,
            "atm_call": None,
            "atm_put": None,
            "total_volume": None,
            "total_open_interest": None,
        }
        for ticker in tickers
    }


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
