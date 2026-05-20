"""
app/providers/tradier_provider.py — Tradier market/options data provider.

Tradier Provider v1 intentionally proves the plumbing first:
- quotes for one or more symbols
- available option expirations
- one option chain sample per ticker

It does not place orders. Trading/order endpoints are intentionally out of scope.
"""

from __future__ import annotations

from typing import Any

import requests

from app import config
from app.utils.log_safety import sanitize_for_log

REQUEST_TIMEOUT_SECONDS = 15


class TradierProviderError(RuntimeError):
    """Base exception for Tradier provider errors."""


class TradierAuthError(TradierProviderError):
    """Raised when Tradier authentication fails."""


class TradierRateLimitError(TradierProviderError):
    """Raised when Tradier rate limits the request."""


class TradierProvider:
    """Small defensive client for Tradier quote and option-chain data."""

    def __init__(self, access_token: str | None = None, environment: str | None = None):
        self.access_token = access_token or config.TRADIER_ACCESS_TOKEN
        self.environment = (environment or config.TRADIER_ENV or "prod").strip().lower()
        self.base_url = self._base_url_for_env(self.environment)

    @staticmethod
    def _base_url_for_env(environment: str) -> str:
        if environment in {"sandbox", "paper", "test"}:
            return "https://sandbox.tradier.com/v1"
        return "https://api.tradier.com/v1"

    @property
    def is_configured(self) -> bool:
        return bool(self.access_token)

    def get_quotes(self, symbols: list[str], greeks: bool = False) -> dict[str, dict[str, Any]]:
        """Return normalized quote dictionaries keyed by ticker."""
        clean_symbols = [str(s).upper().strip() for s in symbols if str(s).strip()]
        if not clean_symbols:
            return {}

        data = self._request_json(
            "GET",
            "/markets/quotes",
            params={
                "symbols": ",".join(clean_symbols),
                "greeks": str(bool(greeks)).lower(),
            },
        )
        raw_quotes = ((data or {}).get("quotes") or {}).get("quote")
        quote_list = _as_list(raw_quotes)

        normalized: dict[str, dict[str, Any]] = {}
        for raw in quote_list:
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol") or "").upper().strip()
            if not symbol:
                continue
            normalized[symbol] = {
                "symbol": symbol,
                "description": raw.get("description"),
                "last": _float_or_none(raw.get("last")),
                "bid": _float_or_none(raw.get("bid")),
                "ask": _float_or_none(raw.get("ask")),
                "open": _float_or_none(raw.get("open")),
                "high": _float_or_none(raw.get("high")),
                "low": _float_or_none(raw.get("low")),
                "close": _float_or_none(raw.get("close")),
                "prevclose": _float_or_none(raw.get("prevclose")),
                "change": _float_or_none(raw.get("change")),
                "change_percentage": _float_or_none(raw.get("change_percentage")),
                "volume": _int_or_none(raw.get("volume")),
                "average_volume": _int_or_none(raw.get("average_volume")),
                "trade_date": raw.get("trade_date"),
                "type": raw.get("type"),
                "raw": raw,
            }
        return normalized

    def get_expirations(self, symbol: str, include_all_roots: bool = False) -> list[str]:
        """Return available option expiration dates for an underlying symbol."""
        symbol = str(symbol).upper().strip()
        if not symbol:
            return []

        data = self._request_json(
            "GET",
            "/markets/options/expirations",
            params={
                "symbol": symbol,
                "includeAllRoots": str(bool(include_all_roots)).lower(),
                "strikes": "false",
                "contractSize": "false",
                "expirationType": "false",
            },
        )
        dates = ((data or {}).get("expirations") or {}).get("date")
        return [str(d) for d in _as_list(dates) if str(d).strip()]

    def get_option_chain(self, symbol: str, expiration: str, greeks: bool = True) -> list[dict[str, Any]]:
        """Return normalized option contract dictionaries for one expiration."""
        symbol = str(symbol).upper().strip()
        expiration = str(expiration).strip()
        if not symbol or not expiration:
            return []

        data = self._request_json(
            "GET",
            "/markets/options/chains",
            params={
                "symbol": symbol,
                "expiration": expiration,
                "greeks": str(bool(greeks)).lower(),
            },
        )
        raw_options = ((data or {}).get("options") or {}).get("option")
        return [_normalize_option(raw, symbol, expiration) for raw in _as_list(raw_options) if isinstance(raw, dict)]

    def _request_json(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.access_token:
            raise TradierAuthError("TRADIER_ACCESS_TOKEN is not set.")

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                params=params or {},
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise TradierProviderError(
                sanitize_for_log(f"Tradier request failed before provider response: {e}", [self.access_token])
            ) from e

        if response.status_code in {401, 403}:
            raise TradierAuthError(
                f"Tradier returned HTTP {response.status_code}. Check TRADIER_ACCESS_TOKEN and TRADIER_ENV."
            )

        if response.status_code == 429:
            raise TradierRateLimitError("Tradier returned HTTP 429 Too Many Requests. Reduce ticker count or retry later.")

        if response.status_code >= 400:
            message = _safe_response_message(response)
            raise TradierProviderError(
                sanitize_for_log(f"Tradier returned HTTP {response.status_code}: {message}", [self.access_token])
            )

        try:
            return response.json()
        except ValueError as e:
            raise TradierProviderError("Tradier returned a non-JSON response.") from e


def _normalize_option(raw: dict[str, Any], underlying: str, expiration: str) -> dict[str, Any]:
    greeks = raw.get("greeks") if isinstance(raw.get("greeks"), dict) else {}
    bid = _float_or_none(raw.get("bid"))
    ask = _float_or_none(raw.get("ask"))
    mid = _midpoint(bid, ask)
    option_type = str(raw.get("option_type") or raw.get("type") or "").lower()
    return {
        "underlying": underlying,
        "symbol": raw.get("symbol"),
        "description": raw.get("description"),
        "option_type": option_type,
        "expiration_date": raw.get("expiration_date") or expiration,
        "strike": _float_or_none(raw.get("strike")),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": _float_or_none(raw.get("last")),
        "change": _float_or_none(raw.get("change")),
        "volume": _int_or_none(raw.get("volume")),
        "open_interest": _int_or_none(raw.get("open_interest")),
        "bid_size": _int_or_none(raw.get("bidsize")),
        "ask_size": _int_or_none(raw.get("asksize")),
        "delta": _float_or_none(greeks.get("delta")),
        "gamma": _float_or_none(greeks.get("gamma")),
        "theta": _float_or_none(greeks.get("theta")),
        "vega": _float_or_none(greeks.get("vega")),
        "rho": _float_or_none(greeks.get("rho")),
        "iv": _first_float(
            greeks.get("mid_iv"),
            greeks.get("smv_vol"),
            greeks.get("bid_iv"),
            greeks.get("ask_iv"),
        ),
        "greeks_updated_at": greeks.get("updated_at"),
        "raw": raw,
    }


def _safe_response_message(response: requests.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("message") or data.get("error") or data)
        return str(data)
    except Exception:
        return response.text[:300] or response.reason


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value in {None, ""}:
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _first_float(*values: Any) -> float | None:
    for value in values:
        converted = _float_or_none(value)
        if converted is not None:
            return converted
    return None


def _midpoint(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0
