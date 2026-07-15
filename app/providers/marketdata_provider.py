"""
app/providers/marketdata_provider.py — MarketData.app options chain adapter.

Patch 33B: Second production adapter implementing OptionsDataProvider.
Reads MARKETDATA_KEY from config — never logs the key value.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

import requests

from app import config
from app.models.market_data_contracts import (
    FreshnessState,
    NormalizedOptionContract,
    NormalizedOptionsChain,
    OptionsDataRequirements,
    ProviderAttempt,
    ProviderOutcome,
)
from app.models.provider_capabilities import MARKETDATA_CAPABILITIES, ProviderCapabilities
from app.providers.options_data_provider import (
    ProviderAuthError,
    ProviderEmptyChainError,
    ProviderMalformedResponseError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.utils.log_safety import sanitize_for_log

PROVIDER_ID = "marketdata"


class MarketDataProvider:
    """
    Options chain provider backed by MarketData.app REST API.

    Credentials are read from config at construction — never at import time.
    API key is never logged, stored in strategy rows, or returned in diagnostics.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self._api_key = api_key or config.MARKETDATA_KEY
        self._base_url = (base_url or config.MARKETDATA_BASE_URL).rstrip("/")
        self._timeout = timeout_seconds if timeout_seconds is not None else config.MARKETDATA_TIMEOUT_SECONDS

    # ── Protocol properties ────────────────────────────────────────────────

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return MARKETDATA_CAPABILITIES

    @property
    def is_configured(self) -> bool:
        return bool(self._api_key)

    # ── Core method ────────────────────────────────────────────────────────

    def get_options_chain(
        self,
        symbol: str,
        requirements: OptionsDataRequirements,
        expirations: list[str] | None = None,
    ) -> NormalizedOptionsChain:
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            raise ProviderEmptyChainError("symbol is required", provider_id=PROVIDER_ID)

        if not self.is_configured:
            raise ProviderNotConfiguredError(
                "MARKETDATA_KEY is not set.", provider_id=PROVIDER_ID
            )

        start_ms = int(time.time() * 1000)
        retrieved_at = datetime.now(timezone.utc)

        try:
            raw_data = self._fetch_chain(symbol, expirations, requirements)
        except ProviderUnavailableError:
            raise
        except Exception as e:
            duration_ms = int(time.time() * 1000) - start_ms
            raise ProviderUnavailableError(
                sanitize_for_log(f"MarketData.app unexpected error: {e}", [self._api_key or ""]),
                provider_id=PROVIDER_ID,
                outcome=ProviderOutcome.SERVER_ERROR,
            ) from e

        duration_ms = int(time.time() * 1000) - start_ms

        contracts, validation_errors, validation_warnings = _parse_chain_response(
            raw_data, symbol, retrieved_at
        )

        if not contracts:
            raise ProviderEmptyChainError(
                f"MarketData.app returned no contracts for {symbol}", provider_id=PROVIDER_ID
            )

        # Determine freshness from response metadata
        is_delayed = _is_delayed_response(raw_data)
        freshness = FreshnessState.LIVE_FAILOVER if not is_delayed else FreshnessState.DELAYED
        if is_delayed:
            validation_warnings.append("MarketData.app returned delayed data — labeled as DELAYED")

        exp_dates = sorted({c.expiration for c in contracts})

        attempt = ProviderAttempt(
            provider_id=PROVIDER_ID,
            outcome=ProviderOutcome.SUCCESS,
            duration_ms=duration_ms,
            freshness_state=freshness,
            contract_count=len(contracts),
        )

        return NormalizedOptionsChain(
            underlying=symbol,
            expirations=exp_dates,
            contracts=contracts,
            provider_id=PROVIDER_ID,
            provider_attempts=[attempt],
            retrieved_at=retrieved_at,
            quote_timestamp=retrieved_at,
            freshness_state=freshness,
            is_live=not is_delayed,
            is_complete=len(validation_errors) == 0,
            validation_errors=validation_errors,
            validation_warnings=validation_warnings,
        )

    def health_check(self) -> dict[str, Any]:
        if not self.is_configured:
            return {
                "provider_id": PROVIDER_ID,
                "configured": False,
                "status": "NOT_CONFIGURED",
                "message": "MARKETDATA_KEY is not set",
            }
        start = time.time()
        try:
            url = f"{self._base_url}/options/chain/SPY/"
            params = {"expiration": "next", "side": "call"}
            resp = self._get(url, params=params)
            latency_ms = int((time.time() - start) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                return {
                    "provider_id": PROVIDER_ID,
                    "configured": True,
                    "status": "OK",
                    "latency_ms": latency_ms,
                    "response_keys": list((data or {}).keys())[:5],
                }
            return {
                "provider_id": PROVIDER_ID,
                "configured": True,
                "status": f"HTTP_{resp.status_code}",
                "latency_ms": latency_ms,
            }
        except requests.Timeout:
            return {"provider_id": PROVIDER_ID, "configured": True, "status": "TIMEOUT"}
        except Exception as e:
            return {
                "provider_id": PROVIDER_ID,
                "configured": True,
                "status": "ERROR",
                "message": sanitize_for_log(str(e)[:200], [self._api_key or ""]),
            }

    # ── Internal helpers ───────────────────────────────────────────────────

    def _fetch_chain(
        self,
        symbol: str,
        expirations: list[str] | None,
        requirements: OptionsDataRequirements,
    ) -> dict[str, Any]:
        """
        Fetch options chain from MarketData.app /options/chain/{symbol}/ endpoint.
        Uses token-based auth via Authorization header — key is never in params or logs.
        """
        url = f"{self._base_url}/options/chain/{symbol}/"
        params: dict[str, Any] = {}

        if expirations:
            params["expiration"] = expirations[0]  # API supports one at a time per request
        elif requirements.requested_expirations:
            params["expiration"] = requirements.requested_expirations[0]

        try:
            resp = self._get(url, params=params)
        except requests.Timeout as e:
            raise ProviderTimeoutError(
                f"MarketData.app timed out for {symbol}", provider_id=PROVIDER_ID
            ) from e
        except requests.RequestException as e:
            raise ProviderUnavailableError(
                sanitize_for_log(f"MarketData.app request error: {e}", [self._api_key or ""]),
                provider_id=PROVIDER_ID,
                outcome=ProviderOutcome.SERVER_ERROR,
            ) from e

        if resp.status_code in {401, 403}:
            raise ProviderAuthError(
                f"MarketData.app returned HTTP {resp.status_code}. Check MARKETDATA_KEY.",
                provider_id=PROVIDER_ID,
            )

        if resp.status_code == 429:
            raise ProviderRateLimitError(
                "MarketData.app returned HTTP 429 Too Many Requests.", provider_id=PROVIDER_ID
            )

        if resp.status_code >= 500:
            raise ProviderUnavailableError(
                f"MarketData.app returned HTTP {resp.status_code}.",
                provider_id=PROVIDER_ID,
                outcome=ProviderOutcome.SERVER_ERROR,
            )

        if resp.status_code >= 400:
            raise ProviderUnavailableError(
                f"MarketData.app returned HTTP {resp.status_code}.",
                provider_id=PROVIDER_ID,
                outcome=ProviderOutcome.SERVER_ERROR,
            )

        try:
            return resp.json()
        except ValueError as e:
            raise ProviderMalformedResponseError(
                "MarketData.app returned non-JSON response.", provider_id=PROVIDER_ID
            ) from e

    def _get(self, url: str, params: dict[str, Any] | None = None) -> requests.Response:
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Accept": "application/json",
        }
        return requests.get(
            url,
            params=params or {},
            headers=headers,
            timeout=self._timeout,
        )


# ─── Response parsing ─────────────────────────────────────────────────────────

def _parse_chain_response(
    data: dict[str, Any],
    underlying: str,
    retrieved_at: datetime,
) -> tuple[list[NormalizedOptionContract], list[str], list[str]]:
    """
    Parse MarketData.app /options/chain response into NormalizedOptionContract list.

    MarketData.app returns parallel arrays keyed by field name (optionSymbol, side, strike, etc.)
    """
    contracts: list[NormalizedOptionContract] = []
    validation_errors: list[str] = []
    validation_warnings: list[str] = []

    if not isinstance(data, dict):
        validation_errors.append("Response is not a dict")
        return contracts, validation_errors, validation_warnings

    # status field: MarketData.app returns "ok", "no_data", "error"
    status = str(data.get("s") or data.get("status") or "").lower()
    if status in {"no_data", "error"}:
        validation_errors.append(f"MarketData.app status: {status}")
        return contracts, validation_errors, validation_warnings

    # Parallel array fields
    symbols = data.get("optionSymbol") or []
    sides = data.get("side") or []
    strikes = data.get("strike") or []
    expirations = data.get("expiration") or []
    bids = data.get("bid") or []
    asks = data.get("ask") or []
    mids = data.get("mid") or []
    lasts = data.get("last") or []
    volumes = data.get("volume") or []
    open_interests = data.get("openInterest") or []
    ivs = data.get("iv") or []
    deltas = data.get("delta") or []
    gammas = data.get("gamma") or []
    thetas = data.get("theta") or []
    vegas = data.get("vega") or []
    rhos = data.get("rho") or []
    underlying_prices = data.get("underlyingPrice") or []
    updated_times = data.get("updated") or []

    count = len(symbols)
    if count == 0:
        return contracts, validation_errors, validation_warnings

    def _get(arr: list, i: int, default: Any = None) -> Any:
        return arr[i] if i < len(arr) else default

    for i in range(count):
        symbol = str(_get(symbols, i) or "").strip()
        side = str(_get(sides, i) or "").lower().strip()
        if side not in {"call", "put"}:
            continue

        strike = _float_or_none(_get(strikes, i))
        if strike is None:
            continue

        exp_raw = _get(expirations, i)
        exp_date = _parse_exp(exp_raw)
        if exp_date is None:
            continue

        bid = _float_or_none(_get(bids, i))
        ask = _float_or_none(_get(asks, i))
        mid = _float_or_none(_get(mids, i))
        if mid is None and bid is not None and ask is not None:
            mid = (bid + ask) / 2.0

        quote_ts: datetime | None = None
        updated_raw = _get(updated_times, i)
        if updated_raw:
            try:
                quote_ts = datetime.fromtimestamp(float(updated_raw), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass

        contracts.append(
            NormalizedOptionContract(
                symbol=symbol,
                underlying=underlying,
                expiration=exp_date,
                strike=strike,
                option_type=side,  # type: ignore[arg-type]
                bid=bid,
                ask=ask,
                mid=mid,
                last=_float_or_none(_get(lasts, i)),
                volume=_int_or_none(_get(volumes, i)),
                open_interest=_int_or_none(_get(open_interests, i)),
                implied_volatility=_float_or_none(_get(ivs, i)),
                delta=_float_or_none(_get(deltas, i)),
                gamma=_float_or_none(_get(gammas, i)),
                theta=_float_or_none(_get(thetas, i)),
                vega=_float_or_none(_get(vegas, i)),
                rho=_float_or_none(_get(rhos, i)),
                underlying_price=_float_or_none(_get(underlying_prices, i)),
                quote_timestamp=quote_ts or retrieved_at,
                provider_id=PROVIDER_ID,
                data_delay_seconds=0,
                freshness_state=FreshnessState.LIVE_FAILOVER,
                raw={},
            )
        )

    return contracts, validation_errors, validation_warnings


def _is_delayed_response(data: dict[str, Any]) -> bool:
    """Check if MarketData.app flagged the response as delayed."""
    delayed_flag = data.get("delayed")
    if isinstance(delayed_flag, bool):
        return delayed_flag
    if isinstance(delayed_flag, str):
        return delayed_flag.lower() in {"true", "1", "yes"}
    return False


def _parse_exp(v: Any) -> date | None:
    if v is None:
        return None
    # May arrive as Unix timestamp (int/float) or ISO string
    if isinstance(v, (int, float)):
        try:
            return datetime.fromtimestamp(float(v), tz=timezone.utc).date()
        except (ValueError, OSError):
            return None
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            pass
    return None


def _float_or_none(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
