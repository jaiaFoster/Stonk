"""
app/providers/tradier_options_adapter.py — Tradier options chain adapter.

Patch 33B: Thin wrapper that makes TradierProvider implement OptionsDataProvider.
All existing callers of TradierProvider.get_option_chain() are unchanged;
new gateway code calls this adapter instead.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any

from app import config
from app.models.market_data_contracts import (
    FreshnessState,
    NormalizedOptionContract,
    NormalizedOptionsChain,
    OptionsDataRequirements,
    ProviderAttempt,
    ProviderOutcome,
)
from app.models.provider_capabilities import TRADIER_CAPABILITIES, ProviderCapabilities
from app.providers.options_data_provider import (
    ProviderAuthError,
    ProviderEmptyChainError,
    ProviderMalformedResponseError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.providers.tradier_provider import (
    TradierAuthError,
    TradierProvider,
    TradierProviderError,
    TradierRateLimitError,
)

PROVIDER_ID = "tradier"


class TradierOptionsAdapter:
    """
    Implements OptionsDataProvider using the existing TradierProvider.
    Credentials are read from config at construction time — never at import time.
    """

    def __init__(
        self,
        access_token: str | None = None,
        environment: str | None = None,
    ) -> None:
        self._token = access_token or config.TRADIER_ACCESS_TOKEN
        self._env = (environment or config.TRADIER_ENV or "prod").strip().lower()
        self._inner = TradierProvider(access_token=self._token, environment=self._env)

    # ── Protocol properties ────────────────────────────────────────────────

    @property
    def provider_id(self) -> str:
        return PROVIDER_ID

    @property
    def capabilities(self) -> ProviderCapabilities:
        return TRADIER_CAPABILITIES

    @property
    def is_configured(self) -> bool:
        return bool(self._token)

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
                "TRADIER_ACCESS_TOKEN is not set.", provider_id=PROVIDER_ID
            )

        start_ms = int(time.time() * 1000)
        try:
            available_expirations = self._inner.get_expirations(symbol)
        except TradierAuthError as e:
            raise ProviderAuthError(str(e), provider_id=PROVIDER_ID) from e
        except TradierRateLimitError as e:
            raise ProviderRateLimitError(str(e), provider_id=PROVIDER_ID) from e
        except TradierProviderError as e:
            msg = str(e)
            if "timed out" in msg.lower() or "timeout" in msg.lower():
                raise ProviderTimeoutError(msg, provider_id=PROVIDER_ID) from e
            raise ProviderUnavailableError(msg, provider_id=PROVIDER_ID, outcome=ProviderOutcome.SERVER_ERROR) from e

        if not available_expirations:
            raise ProviderEmptyChainError(
                f"Tradier returned no expirations for {symbol}", provider_id=PROVIDER_ID
            )

        # Filter to requested expirations if specified
        target_expirations = available_expirations
        if expirations:
            target_expirations = [e for e in available_expirations if e in expirations]
        if requirements.requested_expirations:
            requested = set(requirements.requested_expirations)
            target_expirations = [e for e in target_expirations if e in requested] or target_expirations

        include_greeks = requirements.greeks_required or requirements.implied_volatility_required or config.TRADIER_INCLUDE_GREEKS
        retrieved_at = datetime.now(timezone.utc)

        all_contracts: list[NormalizedOptionContract] = []
        validation_errors: list[str] = []
        validation_warnings: list[str] = []

        for exp_str in target_expirations:
            try:
                raw_contracts = self._inner.get_option_chain(symbol, exp_str, greeks=include_greeks)
            except TradierAuthError as e:
                raise ProviderAuthError(str(e), provider_id=PROVIDER_ID) from e
            except TradierRateLimitError as e:
                raise ProviderRateLimitError(str(e), provider_id=PROVIDER_ID) from e
            except TradierProviderError as e:
                validation_warnings.append(f"Tradier error for {symbol}/{exp_str}: {e}")
                continue

            for raw in raw_contracts:
                contract = _normalize_contract(raw, symbol, exp_str)
                if contract is not None:
                    all_contracts.append(contract)

        if not all_contracts:
            raise ProviderEmptyChainError(
                f"Tradier returned no contracts for {symbol}", provider_id=PROVIDER_ID
            )

        duration_ms = int(time.time() * 1000) - start_ms
        attempt = ProviderAttempt(
            provider_id=PROVIDER_ID,
            outcome=ProviderOutcome.SUCCESS,
            duration_ms=duration_ms,
            freshness_state=FreshnessState.LIVE_PRIMARY,
            contract_count=len(all_contracts),
        )

        exp_dates = _parse_exp_dates(target_expirations)

        return NormalizedOptionsChain(
            underlying=symbol,
            expirations=exp_dates,
            contracts=all_contracts,
            provider_id=PROVIDER_ID,
            provider_attempts=[attempt],
            retrieved_at=retrieved_at,
            quote_timestamp=retrieved_at,
            freshness_state=FreshnessState.LIVE_PRIMARY,
            is_live=True,
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
                "message": "TRADIER_ACCESS_TOKEN is not set",
            }
        start = time.time()
        try:
            expirations = self._inner.get_expirations("SPY")
            latency_ms = int((time.time() - start) * 1000)
            return {
                "provider_id": PROVIDER_ID,
                "configured": True,
                "status": "OK",
                "latency_ms": latency_ms,
                "sample_expirations": expirations[:3],
            }
        except TradierAuthError:
            return {"provider_id": PROVIDER_ID, "configured": True, "status": "AUTH_FAILED"}
        except TradierRateLimitError:
            return {"provider_id": PROVIDER_ID, "configured": True, "status": "RATE_LIMITED"}
        except TradierProviderError as e:
            return {"provider_id": PROVIDER_ID, "configured": True, "status": "ERROR", "message": str(e)[:200]}


# ─── Normalization helpers ────────────────────────────────────────────────────

def _normalize_contract(
    raw: dict[str, Any], underlying: str, exp_str: str
) -> NormalizedOptionContract | None:
    if not isinstance(raw, dict):
        return None

    symbol = str(raw.get("symbol") or "").strip()
    option_type_raw = str(raw.get("option_type") or "").lower().strip()
    if option_type_raw not in {"call", "put"}:
        return None

    strike = _float_or_none(raw.get("strike"))
    if strike is None:
        return None

    bid = _float_or_none(raw.get("bid"))
    ask = _float_or_none(raw.get("ask"))
    mid = _float_or_none(raw.get("mid"))
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / 2.0

    try:
        exp_date = date.fromisoformat(exp_str[:10])
    except ValueError:
        return None

    return NormalizedOptionContract(
        symbol=symbol,
        underlying=underlying,
        expiration=exp_date,
        strike=strike,
        option_type=option_type_raw,  # type: ignore[arg-type]
        bid=bid,
        ask=ask,
        mid=mid,
        last=_float_or_none(raw.get("last")),
        volume=_int_or_none(raw.get("volume")),
        open_interest=_int_or_none(raw.get("open_interest")),
        implied_volatility=_float_or_none(raw.get("iv") or raw.get("implied_volatility")),
        delta=_float_or_none(raw.get("delta")),
        gamma=_float_or_none(raw.get("gamma")),
        theta=_float_or_none(raw.get("theta")),
        vega=_float_or_none(raw.get("vega")),
        rho=_float_or_none(raw.get("rho")),
        underlying_price=_float_or_none(raw.get("underlying_price")),
        quote_timestamp=None,
        provider_id=PROVIDER_ID,
        data_delay_seconds=0,
        freshness_state=FreshnessState.LIVE_PRIMARY,
        raw=raw,
    )


def _parse_exp_dates(exp_strings: list[str]) -> list[date]:
    result = []
    for s in exp_strings:
        try:
            result.append(date.fromisoformat(s[:10]))
        except ValueError:
            pass
    return sorted(set(result))


def _float_or_none(v: Any) -> float | None:
    try:
        f = float(v)
        return f if f == f else None  # reject NaN
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
