"""
app/providers/options_data_provider.py — OptionsDataProvider Protocol.

Patch 33B: Every chain provider implements this Protocol.
Strategies call the gateway — never a specific provider class.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.models.market_data_contracts import (
    NormalizedOptionsChain,
    OptionsDataRequirements,
)
from app.models.provider_capabilities import ProviderCapabilities


@runtime_checkable
class OptionsDataProvider(Protocol):
    """
    Protocol that every options chain provider must implement.

    Providers must NOT be constructed at import time — credentials and HTTP
    clients are initialized in __init__ and the registry creates instances
    on demand (factory pattern).
    """

    @property
    def provider_id(self) -> str:
        """Unique stable identifier, e.g. 'tradier', 'marketdata'."""
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Declares what this provider can supply. Must be a constant."""
        ...

    @property
    def is_configured(self) -> bool:
        """
        True when the required credentials/config exist.
        Must not make network calls — reads env/config only.
        """
        ...

    def get_options_chain(
        self,
        symbol: str,
        requirements: OptionsDataRequirements,
        expirations: list[str] | None = None,
    ) -> NormalizedOptionsChain:
        """
        Fetch and return a normalized options chain.

        Must:
        - Never log API keys or credentials.
        - Populate freshness_state accurately (LIVE_PRIMARY only when data is truly live).
        - Raise ProviderUnavailableError for auth failures, timeouts, rate limits.
        - Raise ProviderChainError for empty or malformed chains.
        - Never set freshness_state=LIVE_* on delayed data.

        The gateway calls this; strategies never call providers directly.
        """
        ...

    def health_check(self) -> dict[str, Any]:
        """
        Lightweight connectivity probe for the /api/dev/market-data-providers endpoint.
        Must not return credential values. Returns a sanitized summary dict.
        """
        ...


# ─── Provider exceptions ──────────────────────────────────────────────────────

class ProviderError(RuntimeError):
    """Base exception for all options data provider errors."""

    def __init__(self, message: str, provider_id: str = "", outcome: str = "") -> None:
        super().__init__(message)
        self.provider_id = provider_id
        self.outcome = outcome


class ProviderUnavailableError(ProviderError):
    """Provider is not reachable — auth failure, timeout, rate limit, not configured."""


class ProviderAuthError(ProviderUnavailableError):
    """Authentication failed. outcome=AUTH_UNAVAILABLE."""

    def __init__(self, message: str, provider_id: str = "") -> None:
        super().__init__(message, provider_id=provider_id, outcome="AUTH_UNAVAILABLE")


class ProviderTimeoutError(ProviderUnavailableError):
    """Request timed out. outcome=TIMEOUT."""

    def __init__(self, message: str, provider_id: str = "") -> None:
        super().__init__(message, provider_id=provider_id, outcome="TIMEOUT")


class ProviderRateLimitError(ProviderUnavailableError):
    """Rate limit hit. outcome=RATE_LIMIT."""

    def __init__(self, message: str, provider_id: str = "") -> None:
        super().__init__(message, provider_id=provider_id, outcome="RATE_LIMIT")


class ProviderNotConfiguredError(ProviderUnavailableError):
    """Provider has no credentials. outcome=NOT_CONFIGURED."""

    def __init__(self, message: str, provider_id: str = "") -> None:
        super().__init__(message, provider_id=provider_id, outcome="NOT_CONFIGURED")


class ProviderChainError(ProviderError):
    """Chain data is missing, empty, or malformed."""


class ProviderEmptyChainError(ProviderChainError):
    """Provider returned an empty chain. outcome=EMPTY_CHAIN."""

    def __init__(self, message: str, provider_id: str = "") -> None:
        super().__init__(message, provider_id=provider_id, outcome="EMPTY_CHAIN")


class ProviderMalformedResponseError(ProviderChainError):
    """Provider returned a malformed payload. outcome=MALFORMED_RESPONSE."""

    def __init__(self, message: str, provider_id: str = "") -> None:
        super().__init__(message, provider_id=provider_id, outcome="MALFORMED_RESPONSE")
