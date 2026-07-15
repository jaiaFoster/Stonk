"""
app/services/options_market_data_gateway.py — Provider-neutral gateway.

Patch 33B: Single entry point for all options chain requests. Strategies call
this instead of any specific provider. Implements controlled failover plus
shadow comparison (parallel shadow request, compare without affecting verdicts).

Provider order (default): tradier → marketdata → last_known_good_cache.
Freshness rules:
  - LIVE_PRIMARY   → tradier succeeded
  - LIVE_FAILOVER  → marketdata succeeded
  - DELAYED        → provider returned delayed data (labeled, cannot permit entry)
  - STALE_CACHE    → last_known_good_cache used
  - UNAVAILABLE    → all providers failed

Shadow rules:
  - Shadow requests run in parallel with the primary; results never affect verdicts.
  - Shadow chain is promoted (not re-fetched) when primary fails.
  - Shadow is bounded by OPTIONS_PROVIDER_SHADOW_MAX_TICKERS_PER_RUN per gateway instance.
"""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import replace
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
from app.services.options_provider_comparison_service import (
    ChainComparisonResult,
    ComparisonClassification,
    SelectionOutcome,
    ShadowSkipReason,
    compare_chains,
)

logger = logging.getLogger(__name__)

_GATEWAY_VERSION = "33B.v1"


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
        shadow_enabled: bool | None = None,
        shadow_sample_rate: float | None = None,
        env: str = "dev",
    ) -> None:
        self._registry = registry or get_default_registry()
        self._provider_order = provider_order or config.OPTIONS_PROVIDER_ORDER
        self._failover_enabled = failover_enabled if failover_enabled is not None else config.OPTIONS_FAILOVER_ENABLED
        self._shadow_enabled = shadow_enabled if shadow_enabled is not None else config.OPTIONS_PROVIDER_SHADOW_ENABLED
        self._env = env
        if shadow_sample_rate is not None:
            self._shadow_sample_rate = shadow_sample_rate
        elif env == "prod":
            self._shadow_sample_rate = config.OPTIONS_PROVIDER_SHADOW_PROD_SAMPLE_RATE
        else:
            self._shadow_sample_rate = config.OPTIONS_PROVIDER_SHADOW_DEV_SAMPLE_RATE
        self._shadow_budget_remaining = config.OPTIONS_PROVIDER_SHADOW_MAX_TICKERS_PER_RUN
        logger.info(
            "OPTIONS_PROVIDER_GATEWAY version=%s provider_order=%s failover=%s "
            "shadow_enabled=%s shadow_sample_rate=%s env=%s",
            _GATEWAY_VERSION,
            self._provider_order,
            self._failover_enabled,
            self._shadow_enabled,
            self._shadow_sample_rate,
            self._env,
        )

    def get_chain(
        self,
        symbol: str,
        requirements: OptionsDataRequirements | None = None,
        expirations: list[str] | None = None,
    ) -> NormalizedOptionsChain:
        """
        Fetch an options chain for symbol with optional shadow comparison.

        Tries providers in order, failing over on each failure. When shadow is
        sampled, issues primary and shadow in parallel; promotes shadow if
        primary fails (no re-fetch). Returns the first chain that passes
        validation against requirements.

        Raises GatewayUnavailableError if all providers fail.
        """
        if requirements is None:
            requirements = OptionsDataRequirements()

        symbol = str(symbol or "").upper().strip()
        all_attempts: list[ProviderAttempt] = []

        # Determine shadow participation for this request
        shadow_pid, shadow_skip = self._shadow_decision(symbol)
        shadow_future: Future | None = None
        shadow_executor: ThreadPoolExecutor | None = None
        shadow_chain: NormalizedOptionsChain | None = None

        # Issue shadow request in background if selected.
        # Shadow is a form of failover — don't run when failover is disabled.
        if shadow_pid and shadow_skip is None and self._failover_enabled:
            shadow_provider = self._registry.get(shadow_pid)
            if shadow_provider and shadow_provider.is_configured:
                shadow_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shadow")
                shadow_future = shadow_executor.submit(
                    self._fetch_chain_safe, shadow_provider, symbol, requirements, expirations
                )
                self._shadow_budget_remaining -= 1
            else:
                shadow_skip = ShadowSkipReason.PROVIDER_UNCONFIGURED

        comparison_result: ChainComparisonResult | None = None
        primary_chain: NormalizedOptionsChain | None = None
        selection_outcome = SelectionOutcome.UNAVAILABLE

        try:
            for provider_id in self._provider_order:
                # Skip shadow provider in the primary loop (it runs in parallel)
                if provider_id == shadow_pid and shadow_future is not None:
                    continue

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
                chain, fetch_attempt = self._fetch_chain_with_attempt(
                    provider, symbol, requirements, expirations, start_ms
                )
                all_attempts.append(fetch_attempt)

                if chain is None:
                    if not self._failover_enabled:
                        break
                    continue

                ok, errors, warnings = validate_chain(chain, requirements)
                if not ok:
                    all_attempts.append(
                        ProviderAttempt(
                            provider_id=provider_id,
                            outcome=ProviderOutcome.VALIDATION_FAILED,
                            duration_ms=0,
                            contract_count=len(chain.contracts),
                            error_summary="; ".join(errors[:3]),
                        )
                    )
                    for a in chain.provider_attempts:
                        if a not in all_attempts:
                            all_attempts.append(a)
                    if not self._failover_enabled:
                        break
                    continue

                # Primary succeeded
                primary_chain = chain
                is_primary = (provider_id == config.OPTIONS_PROVIDER_PRIMARY)
                selection_outcome = (
                    SelectionOutcome.PRIMARY_SELECTED if is_primary
                    else SelectionOutcome.FAILOVER_SELECTED_PRIMARY_UNAVAILABLE
                )
                break

        finally:
            # Collect shadow result (wait up to shadow timeout)
            if shadow_future is not None:
                shadow_chain, shadow_err_attempt = self._collect_shadow(shadow_future, shadow_pid)
                if shadow_err_attempt:
                    all_attempts.append(shadow_err_attempt)
                if shadow_executor:
                    shadow_executor.shutdown(wait=False)

        # Shadow promotion: if primary failed but shadow succeeded, promote shadow.
        # Shadow promotion is a form of failover — skip when failover_enabled=False.
        if primary_chain is None and shadow_chain is not None and self._failover_enabled:
            ok, errors, _ = validate_chain(shadow_chain, requirements)
            if ok:
                primary_chain = shadow_chain
                shadow_chain = None
                selection_outcome = SelectionOutcome.SHADOW_PROMOTED_PRIMARY_FAILED
                logger.info(
                    "OPTIONS_PROVIDER_SELECTED ticker=%s provider=%s reason=shadow_promoted",
                    symbol, shadow_pid,
                )

        # Stale cache fallback: if still no chain, try last_known_good_cache
        if primary_chain is None and config.OPTIONS_STALE_CACHE_ENABLED:
            primary_chain = self._try_stale_cache(symbol, requirements, all_attempts)
            if primary_chain is not None:
                selection_outcome = SelectionOutcome.STALE_CACHE_SELECTED_ALL_PROVIDERS_FAILED

        if primary_chain is None:
            raise GatewayUnavailableError(
                f"All providers failed for {symbol}: {[a.outcome for a in all_attempts]}",
                provider_attempts=all_attempts,
            )

        # Shadow comparison
        if shadow_chain is not None and primary_chain is not None:
            so = (
                SelectionOutcome.PRIMARY_SELECTED_SHADOW_AGREES
                if selection_outcome == SelectionOutcome.PRIMARY_SELECTED
                else selection_outcome
            )
            comparison_result = compare_chains(primary_chain, shadow_chain, so)
            if comparison_result.classification == ComparisonClassification.MATERIAL_DIVERGENCE:
                selection_outcome = SelectionOutcome.PRIMARY_SELECTED_SHADOW_DIVERGES
            elif selection_outcome == SelectionOutcome.PRIMARY_SELECTED:
                selection_outcome = SelectionOutcome.PRIMARY_SELECTED_SHADOW_AGREES

            logger.info(
                "OPTIONS_PROVIDER_COMPARE ticker=%s coverage_match_pct=%.3f "
                "mid_median_diff_pct=%s iv_median_diff_abs=%s "
                "primary_count=%d shadow_count=%d classification=%s",
                symbol,
                comparison_result.coverage_pct,
                f"{comparison_result.mid_median_diff_pct:.4f}" if comparison_result.mid_median_diff_pct is not None else "n/a",
                f"{comparison_result.iv_median_diff_abs:.4f}" if comparison_result.iv_median_diff_abs is not None else "n/a",
                comparison_result.primary_contract_count,
                comparison_result.shadow_contract_count,
                comparison_result.classification,
            )
            self._persist_comparison(comparison_result)
        else:
            if shadow_skip:
                logger.debug(
                    "OPTIONS_PROVIDER_COMPARE ticker=%s shadow_skip=%s",
                    symbol, shadow_skip,
                )

        logger.info(
            "OPTIONS_PROVIDER_SELECTED ticker=%s provider=%s freshness=%s reason=%s",
            symbol,
            primary_chain.provider_id,
            primary_chain.freshness_state,
            selection_outcome,
        )

        # Merge all prior failure attempts into the chain record
        merged_attempts = all_attempts + [
            a for a in primary_chain.provider_attempts if a not in all_attempts
        ]

        # Opportunistically write successful live chain to stale cache
        if FreshnessState.is_live(primary_chain.freshness_state):
            self._try_cache_write(primary_chain)

        return _replace_attempts(primary_chain, merged_attempts)

    # ─── Shadow helpers ───────────────────────────────────────────────────────

    def _shadow_decision(self, symbol: str) -> tuple[str | None, str | None]:
        """Return (shadow_provider_id, skip_reason). skip_reason is None if shadow should run."""
        if not self._shadow_enabled:
            return None, ShadowSkipReason.SHADOW_DISABLED
        if not config.OPTIONS_PROVIDER_SHADOW_PROVIDERS:
            return None, ShadowSkipReason.CAPABILITY
        shadow_pid = config.OPTIONS_PROVIDER_SHADOW_PROVIDERS[0]
        if self._shadow_budget_remaining <= 0:
            return shadow_pid, ShadowSkipReason.BUDGET
        if random.random() > self._shadow_sample_rate:
            return shadow_pid, ShadowSkipReason.SAMPLE
        return shadow_pid, None

    def _fetch_chain_safe(
        self,
        provider: OptionsDataProvider,
        symbol: str,
        requirements: OptionsDataRequirements,
        expirations: list[str] | None,
    ) -> NormalizedOptionsChain | None:
        """Run a provider fetch for the shadow thread. Exceptions propagate to the future."""
        logger.info(
            "OPTIONS_PROVIDER_ATTEMPT ticker=%s provider=%s role=shadow",
            symbol, provider.provider_id,
        )
        return provider.get_options_chain(symbol, requirements, expirations)

    def _collect_shadow(
        self,
        future: Future,
        shadow_pid: str | None,
    ) -> tuple[NormalizedOptionsChain | None, ProviderAttempt | None]:
        """Collect shadow result from future with timeout."""
        try:
            timeout = config.MARKETDATA_TIMEOUT_SECONDS + 2
            chain = future.result(timeout=timeout)
            return chain, None
        except FutureTimeout:
            logger.warning("OPTIONS_PROVIDER_ATTEMPT provider=%s role=shadow outcome=TIMEOUT", shadow_pid)
            return None, ProviderAttempt(
                provider_id=shadow_pid or "shadow",
                outcome=ProviderOutcome.TIMEOUT,
                duration_ms=0,
                error_summary="shadow future timeout",
            )
        except Exception as e:
            # Map exception types to appropriate ProviderOutcome
            outcome = ProviderOutcome.SERVER_ERROR
            if isinstance(e, ProviderTimeoutError):
                outcome = ProviderOutcome.TIMEOUT
            elif isinstance(e, ProviderAuthError):
                outcome = ProviderOutcome.AUTH_UNAVAILABLE
            elif isinstance(e, ProviderRateLimitError):
                outcome = ProviderOutcome.RATE_LIMIT
            elif isinstance(e, ProviderNotConfiguredError):
                outcome = ProviderOutcome.NOT_CONFIGURED
            elif isinstance(e, ProviderEmptyChainError):
                outcome = ProviderOutcome.EMPTY_CHAIN
            logger.debug("OPTIONS_PROVIDER_ATTEMPT provider=%s role=shadow outcome=%s: %s", shadow_pid, outcome, str(e)[:120])
            return None, ProviderAttempt(
                provider_id=shadow_pid or "shadow",
                outcome=outcome,
                duration_ms=0,
                error_summary=str(e)[:120],
            )

    def _fetch_chain_with_attempt(
        self,
        provider: OptionsDataProvider,
        symbol: str,
        requirements: OptionsDataRequirements,
        expirations: list[str] | None,
        start_ms: int,
    ) -> tuple[NormalizedOptionsChain | None, ProviderAttempt]:
        """Fetch from a provider, return (chain or None, ProviderAttempt)."""
        is_primary = (provider.provider_id == config.OPTIONS_PROVIDER_PRIMARY)
        role = "primary" if is_primary else "failover"
        try:
            logger.info(
                "OPTIONS_PROVIDER_ATTEMPT ticker=%s provider=%s role=%s",
                symbol, provider.provider_id, role,
            )
            chain = provider.get_options_chain(symbol, requirements, expirations)
            duration = int(time.time() * 1000) - start_ms
            logger.info(
                "OPTIONS_PROVIDER_ATTEMPT ticker=%s provider=%s role=%s outcome=SUCCESS duration_ms=%d contracts=%d",
                symbol, provider.provider_id, role, duration, len(chain.contracts),
            )
            attempt = ProviderAttempt(
                provider_id=provider.provider_id,
                outcome=ProviderOutcome.SUCCESS,
                duration_ms=duration,
                freshness_state=chain.freshness_state,
                contract_count=len(chain.contracts),
            )
            return chain, attempt
        except ProviderNotConfiguredError as e:
            return None, ProviderAttempt(provider.provider_id, ProviderOutcome.NOT_CONFIGURED, int(time.time() * 1000) - start_ms, error_summary=str(e)[:200])
        except ProviderAuthError as e:
            return None, ProviderAttempt(provider.provider_id, ProviderOutcome.AUTH_UNAVAILABLE, int(time.time() * 1000) - start_ms, error_summary=str(e)[:200])
        except ProviderRateLimitError as e:
            return None, ProviderAttempt(provider.provider_id, ProviderOutcome.RATE_LIMIT, int(time.time() * 1000) - start_ms, error_summary=str(e)[:200])
        except ProviderTimeoutError as e:
            return None, ProviderAttempt(provider.provider_id, ProviderOutcome.TIMEOUT, int(time.time() * 1000) - start_ms, error_summary=str(e)[:200])
        except ProviderEmptyChainError as e:
            return None, ProviderAttempt(provider.provider_id, ProviderOutcome.EMPTY_CHAIN, int(time.time() * 1000) - start_ms, error_summary=str(e)[:200])
        except ProviderChainError as e:
            outcome = getattr(e, "outcome", None) or ProviderOutcome.MALFORMED_RESPONSE
            return None, ProviderAttempt(provider.provider_id, outcome, int(time.time() * 1000) - start_ms, error_summary=str(e)[:200])
        except ProviderError as e:
            outcome = getattr(e, "outcome", None) or ProviderOutcome.SERVER_ERROR
            return None, ProviderAttempt(provider.provider_id, outcome, int(time.time() * 1000) - start_ms, error_summary=str(e)[:200])
        except Exception as e:
            return None, ProviderAttempt(provider.provider_id, ProviderOutcome.SERVER_ERROR, int(time.time() * 1000) - start_ms, error_summary=f"Unexpected: {str(e)[:180]}")

    def _try_stale_cache(
        self,
        symbol: str,
        requirements: OptionsDataRequirements,
        all_attempts: list[ProviderAttempt],
    ) -> NormalizedOptionsChain | None:
        try:
            cache = self._registry.get("last_known_good_cache")
            if cache is None or not cache.is_configured:
                return None
            start = int(time.time() * 1000)
            chain = cache.get_options_chain(symbol, requirements, None)
            all_attempts.append(ProviderAttempt(
                provider_id="last_known_good_cache",
                outcome=ProviderOutcome.SUCCESS,
                duration_ms=int(time.time() * 1000) - start,
                freshness_state=FreshnessState.STALE_CACHE,
                contract_count=len(chain.contracts) if chain else 0,
            ))
            return chain
        except Exception:
            return None

    def _persist_comparison(self, comparison: ChainComparisonResult) -> None:
        try:
            from app.db.options_provider_comparison_repository import store_comparison
            store_comparison(comparison)
        except Exception:
            pass

    # ─── Other public methods ─────────────────────────────────────────────────

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
    return replace(chain, provider_attempts=attempts)
