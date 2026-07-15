"""
app/services/options_market_data_gateway.py — Provider-neutral gateway.

Patch 33B: Single entry point for all options chain requests. Strategies call
this instead of any specific provider. Implements controlled failover.

Provider order (default): tradier → marketdata → last_known_good_cache.
Freshness rules:
  - LIVE_PRIMARY   → tradier succeeded
  - LIVE_FAILOVER  → marketdata succeeded
  - DELAYED        → provider returned delayed data (labeled, cannot permit entry)
  - STALE_CACHE    → last_known_good_cache used
  - UNAVAILABLE    → all providers failed
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app import config
from app.models.market_data_contracts import (
    FreshnessState,
    NormalizedOptionsChain,
    OptionsDataRequirements,
    ProviderAttempt,
    ProviderOutcome,
)
from app.providers.options_data_provider import (
    OptionsDataProvider,
    ProviderAuthError,
    ProviderChainError,
    ProviderEmptyChainError,
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from app.services.market_data_provider_registry import ProviderRegistry, get_default_registry
from app.services.options_chain_cache import LastKnownGoodCacheProvider
from app.services.options_chain_validation_service import validate_chain


class GatewayUnavailableError(RuntimeError):
    """Raised when no provider could satisfy the request."""

    def __init__(
        self,
        message: str,
        provider_attempts: list[ProviderAttempt] | None = None,
    ) -> None:
        super().__init__(message)
        self.provider_attempts = provider_attempts or []


class OptionsMarketDataGateway:
    """
    Fetches options chains through a prioritized provider sequence with failover.

    Callers declare requirements; the gateway picks providers, validates results,
    and returns a NormalizedOptionsChain regardless of source.

    Entry authorization (FreshnessState.permits_entry) is enforced here:
    delayed or stale chains are returned with the correct freshness_state so
    callers can choose to block or allow analysis-only use, but the gateway
    never upgrades a stale/delayed chain's freshness label to LIVE_*.
    """

    def __init__(
        self,
        registry: ProviderRegistry | None = None,
        provider_order: list[str] | None = None,
        failover_enabled: bool | None = None,
    ) -> None:
        self._registry = registry or get_default_registry()
        self._provider_order = provider_order or config.OPTIONS_PROVIDER_ORDER
        self._failover_enabled = failover_enabled if failover_enabled is not None else config.OPTIONS_FAILOVER_ENABLED

    def get_chain(
        self,
        symbol: str,
        requirements: OptionsDataRequirements | None = None,
        expirations: list[str] | None = None,
    ) -> NormalizedOptionsChain:
        """
        Fetch an options chain for symbol.

        Tries providers in order, failing over on each failure.
        Returns the first chain that passes validation against requirements.
        Raises GatewayUnavailableError if all providers fail.
        """
        if requirements is None:
            requirements = OptionsDataRequirements()

        symbol = str(symbol or "").upper().strip()
        all_attempts: list[ProviderAttempt] = []

        for provider_id in self._provider_order:
            provider = self._registry.get(provider_id)
            if provider is None:
                continue

            if not provider.is_configured:
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.NOT_CONFIGURED,
                        duration_ms=0,
                    )
                )
                if not self._failover_enabled:
                    break
                continue

            start_ms = int(time.time() * 1000)
            try:
                chain = provider.get_options_chain(symbol, requirements, expirations)
            except ProviderNotConfiguredError as e:
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.NOT_CONFIGURED,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=str(e)[:200],
                    )
                )
                if not self._failover_enabled:
                    break
                continue
            except ProviderAuthError as e:
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.AUTH_UNAVAILABLE,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=str(e)[:200],
                    )
                )
                if not self._failover_enabled:
                    break
                continue
            except ProviderRateLimitError as e:
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.RATE_LIMIT,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=str(e)[:200],
                    )
                )
                if not self._failover_enabled:
                    break
                continue
            except ProviderTimeoutError as e:
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.TIMEOUT,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=str(e)[:200],
                    )
                )
                if not self._failover_enabled:
                    break
                continue
            except ProviderEmptyChainError as e:
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.EMPTY_CHAIN,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=str(e)[:200],
                    )
                )
                if not self._failover_enabled:
                    break
                continue
            except ProviderChainError as e:
                outcome = getattr(e, "outcome", None) or ProviderOutcome.MALFORMED_RESPONSE
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=outcome,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=str(e)[:200],
                    )
                )
                if not self._failover_enabled:
                    break
                continue
            except ProviderError as e:
                outcome = getattr(e, "outcome", None) or ProviderOutcome.SERVER_ERROR
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=outcome,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=str(e)[:200],
                    )
                )
                if not self._failover_enabled:
                    break
                continue
            except Exception as e:
                all_attempts.append(
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.SERVER_ERROR,
                        duration_ms=int(time.time() * 1000) - start_ms,
                        error_summary=f"Unexpected: {str(e)[:180]}",
                    )
                )
                if not self._failover_enabled:
                    break
                continue

            # We got a chain — validate it
            ok, errors, warnings = validate_chain(chain, requirements)

            if not ok:
                all_attempts.extend([
                    ProviderAttempt(
                        provider_id=provider_id,
                        outcome=ProviderOutcome.VALIDATION_FAILED,
                        duration_ms=0,
                        contract_count=len(chain.contracts),
                        error_summary="; ".join(errors[:3]),
                    )
                ])
                # Merge provider attempts already in chain into all_attempts
                for a in chain.provider_attempts:
                    if a not in all_attempts:
                        all_attempts.append(a)
                if not self._failover_enabled:
                    break
                continue

            # Success: merge all prior failure attempts into the chain record
            merged_attempts = all_attempts + [
                a for a in chain.provider_attempts if a not in all_attempts
            ]

            # Opportunistically write successful live chain to stale cache
            if FreshnessState.is_live(chain.freshness_state):
                self._try_cache_write(chain)

            return _replace_attempts(chain, merged_attempts)

        # All providers failed
        raise GatewayUnavailableError(
            f"All providers failed for {symbol}: {[a.outcome for a in all_attempts]}",
            provider_attempts=all_attempts,
        )

    def unavailable_chain(
        self,
        symbol: str,
        provider_attempts: list[ProviderAttempt] | None = None,
    ) -> NormalizedOptionsChain:
        """Build an UNAVAILABLE sentinel chain — useful when callers must not raise."""
        return NormalizedOptionsChain(
            underlying=symbol,
            expirations=[],
            contracts=[],
            provider_id="none",
            provider_attempts=provider_attempts or [],
            retrieved_at=datetime.now(timezone.utc),
            quote_timestamp=None,
            freshness_state=FreshnessState.UNAVAILABLE,
            is_live=False,
            is_complete=False,
            validation_errors=["All providers failed"],
            validation_warnings=[],
        )

    def _try_cache_write(self, chain: NormalizedOptionsChain) -> None:
        try:
            cache_provider = self._registry.get("last_known_good_cache")
            if isinstance(cache_provider, LastKnownGoodCacheProvider):
                cache_provider.store_chain(chain)
        except Exception:
            pass  # cache write failure is never fatal

    def provider_health_summary(self) -> list[dict[str, Any]]:
        """
        Returns a health dict per registered provider. No entry authorization —
        this is for the /api/dev/market-data-providers diagnostic endpoint only.
        Does NOT call providers with live data requests.
        """
        results = []
        for pid in self._provider_order:
            provider = self._registry.get(pid)
            if provider is None:
                results.append({"provider_id": pid, "status": "NOT_REGISTERED"})
                continue
            results.append({
                "provider_id": pid,
                "configured": provider.is_configured,
                "capabilities": provider.capabilities.to_dict(),
            })
        return results


def _replace_attempts(
    chain: NormalizedOptionsChain,
    attempts: list[ProviderAttempt],
) -> NormalizedOptionsChain:
    """Return chain with provider_attempts replaced. Dataclass field replacement."""
    from dataclasses import replace
    return replace(chain, provider_attempts=attempts)
