"""
app/models/market_data_contracts.py — Provider-neutral options data contracts.

Patch 33B: Every strategy receives NormalizedOptionsChain regardless of source.
No strategy or orchestration code should reference provider-specific field names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal


# ─── Freshness states ─────────────────────────────────────────────────────────

class FreshnessState:
    LIVE_PRIMARY = "LIVE_PRIMARY"        # Live data from primary provider
    LIVE_FAILOVER = "LIVE_FAILOVER"      # Live data from failover provider
    DELAYED = "DELAYED"                  # Delayed quote (labeled; may not permit entry)
    STALE_CACHE = "STALE_CACHE"          # Last-known-good; reference-only
    INCOMPLETE = "INCOMPLETE"            # Chain exists but missing required fields
    UNAVAILABLE = "UNAVAILABLE"          # No data available from any source

    _LIVE = {LIVE_PRIMARY, LIVE_FAILOVER}
    _ENTRY_PERMITTING = {LIVE_PRIMARY, LIVE_FAILOVER}

    @classmethod
    def permits_entry(cls, state: str) -> bool:
        return state in cls._ENTRY_PERMITTING

    @classmethod
    def is_live(cls, state: str) -> bool:
        return state in cls._LIVE


# ─── Request requirements ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class OptionsDataRequirements:
    """Strategy-declared requirements for options chain data."""
    live_required: bool = False
    maximum_delay_seconds: int | None = None
    greeks_required: bool = False
    implied_volatility_required: bool = False
    bid_ask_required: bool = True
    open_interest_required: bool = False
    volume_required: bool = False
    minimum_contract_count: int | None = None
    requested_expirations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "live_required": self.live_required,
            "maximum_delay_seconds": self.maximum_delay_seconds,
            "greeks_required": self.greeks_required,
            "implied_volatility_required": self.implied_volatility_required,
            "bid_ask_required": self.bid_ask_required,
            "open_interest_required": self.open_interest_required,
            "volume_required": self.volume_required,
            "minimum_contract_count": self.minimum_contract_count,
            "requested_expirations": list(self.requested_expirations),
        }


# ─── Underlying quote ─────────────────────────────────────────────────────────

@dataclass
class NormalizedUnderlyingQuote:
    symbol: str
    last: float | None
    bid: float | None
    ask: float | None
    mid: float | None
    volume: int | None
    average_volume: int | None
    provider_id: str
    quote_timestamp: datetime | None
    freshness_state: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def price(self) -> float | None:
        return self.last or self.mid


# ─── Normalized option contract ───────────────────────────────────────────────

@dataclass
class NormalizedOptionContract:
    symbol: str
    underlying: str
    expiration: date
    strike: float
    option_type: Literal["call", "put"]
    bid: float | None
    ask: float | None
    mid: float | None
    last: float | None
    volume: int | None
    open_interest: int | None
    implied_volatility: float | None
    delta: float | None
    gamma: float | None
    theta: float | None
    vega: float | None
    rho: float | None
    underlying_price: float | None
    quote_timestamp: datetime | None
    provider_id: str
    data_delay_seconds: int | None
    freshness_state: str
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def spread(self) -> float | None:
        if self.bid is not None and self.ask is not None:
            return self.ask - self.bid
        return None

    @property
    def spread_pct(self) -> float | None:
        if self.mid and self.mid > 0 and self.spread is not None:
            return self.spread / self.mid
        return None

    def to_compact_dict(self) -> dict[str, Any]:
        """Compact dict compatible with existing ASA strategy row field names."""
        return {
            "symbol": self.symbol,
            "underlying": self.underlying,
            "expiration_date": self.expiration.isoformat(),
            "strike": self.strike,
            "option_type": self.option_type,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "last": self.last,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "iv": self.implied_volatility,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "rho": self.rho,
            "underlying_price": self.underlying_price,
            "spread_pct": self.spread_pct,
            "provider_id": self.provider_id,
            "freshness_state": self.freshness_state,
        }


# ─── Provider attempt record ──────────────────────────────────────────────────

@dataclass
class ProviderAttempt:
    provider_id: str
    outcome: str           # SUCCESS, AUTH_UNAVAILABLE, TIMEOUT, RATE_LIMIT, SERVER_ERROR,
                           # EMPTY_CHAIN, INCOMPLETE_CHAIN, STALE_QUOTE, MISSING_CAPABILITY,
                           # MALFORMED_RESPONSE, NOT_CONFIGURED
    duration_ms: int | None
    request_id: str | None = None
    freshness_state: str | None = None
    contract_count: int | None = None
    error_summary: str | None = None  # no secret material; sanitized

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "request_id": self.request_id,
            "freshness_state": self.freshness_state,
            "contract_count": self.contract_count,
            "error_summary": self.error_summary,
        }


class ProviderOutcome:
    SUCCESS = "SUCCESS"
    AUTH_UNAVAILABLE = "AUTH_UNAVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    TIMEOUT = "TIMEOUT"
    RATE_LIMIT = "RATE_LIMIT"
    SERVER_ERROR = "SERVER_ERROR"
    EMPTY_CHAIN = "EMPTY_CHAIN"
    INCOMPLETE_CHAIN = "INCOMPLETE_CHAIN"
    STALE_QUOTE = "STALE_QUOTE"
    MISSING_CAPABILITY = "MISSING_CAPABILITY"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    VALIDATION_FAILED = "VALIDATION_FAILED"

    _RETRYABLE = {AUTH_UNAVAILABLE, TIMEOUT, RATE_LIMIT, SERVER_ERROR,
                  EMPTY_CHAIN, INCOMPLETE_CHAIN, STALE_QUOTE, MALFORMED_RESPONSE}

    @classmethod
    def is_retryable(cls, outcome: str) -> bool:
        return outcome in cls._RETRYABLE


# ─── Normalized options chain ─────────────────────────────────────────────────

@dataclass
class NormalizedOptionsChain:
    underlying: str
    expirations: list[date]
    contracts: list[NormalizedOptionContract]
    provider_id: str
    provider_attempts: list[ProviderAttempt]
    retrieved_at: datetime
    quote_timestamp: datetime | None
    freshness_state: str
    is_live: bool
    is_complete: bool
    validation_errors: list[str]
    validation_warnings: list[str]
    underlying_price: float | None = None

    @property
    def call_contracts(self) -> list[NormalizedOptionContract]:
        return [c for c in self.contracts if c.option_type == "call"]

    @property
    def put_contracts(self) -> list[NormalizedOptionContract]:
        return [c for c in self.contracts if c.option_type == "put"]

    def contracts_for_expiration(self, exp: date | str) -> list[NormalizedOptionContract]:
        if isinstance(exp, str):
            from datetime import datetime as _dt
            exp = _dt.strptime(exp[:10], "%Y-%m-%d").date()
        return [c for c in self.contracts if c.expiration == exp]

    def to_legacy_chain_set(self) -> dict[str, Any]:
        """
        Project to the dict shape that MarketDataHub.get_options_chain_set() returns.
        Allows strategies that already consume hub output to receive gateway data without changes.
        """
        chains_by_exp: dict[str, list[dict]] = {}
        for exp in self.expirations:
            contracts = self.contracts_for_expiration(exp)
            chains_by_exp[exp.isoformat()] = [c.to_compact_dict() for c in contracts]

        return {
            "ticker": self.underlying,
            "data_state": "COMPLETE" if self.is_complete else "INCOMPLETE",
            "listed_expirations": [e.isoformat() for e in self.expirations],
            "expirations": [e.isoformat() for e in self.expirations],
            "chains": chains_by_exp,
            "chains_by_expiration": chains_by_exp,
            "errors": self.validation_errors,
            "provider_id": self.provider_id,
            "freshness_state": self.freshness_state,
            "is_live": self.is_live,
            "provider_attempts": [a.to_dict() for a in self.provider_attempts],
        }


# ─── Provider health ──────────────────────────────────────────────────────────

@dataclass
class ProviderHealth:
    provider_id: str
    is_configured: bool
    credential_source: str      # "railway_env", "user_credential", "none"
    last_check_at: datetime | None
    last_outcome: str | None
    last_latency_ms: int | None
    error_summary: str | None   # sanitized; no token material

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "configured": self.is_configured,
            "credential_source": self.credential_source,
            "last_check_at": self.last_check_at.isoformat() if self.last_check_at else None,
            "last_outcome": self.last_outcome,
            "last_latency_ms": self.last_latency_ms,
            "error_summary": self.error_summary,
        }
