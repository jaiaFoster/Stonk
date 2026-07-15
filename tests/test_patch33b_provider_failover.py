"""Patch 33B — Provider failover and freshness enforcement tests."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock


def _live_chain(underlying="SPY", freshness="LIVE_PRIMARY", provider_id="tradier"):
    from app.models.market_data_contracts import (
        FreshnessState,
        NormalizedOptionContract,
        NormalizedOptionsChain,
        ProviderAttempt,
        ProviderOutcome,
    )
    from dataclasses import replace

    exp = date(2024, 3, 15)
    now = datetime.now(timezone.utc)
    contracts = [
        NormalizedOptionContract(
            symbol=f"{underlying}_C0",
            underlying=underlying,
            expiration=exp,
            strike=100.0,
            option_type="call",
            bid=1.0, ask=1.2, mid=1.1, last=1.1,
            volume=100, open_interest=500,
            implied_volatility=0.20,
            delta=0.50, gamma=0.01, theta=-0.05, vega=0.10, rho=0.02,
            underlying_price=100.0,
            quote_timestamp=now,
            provider_id=provider_id,
            data_delay_seconds=0,
            freshness_state=freshness,
        )
    ]
    attempt = ProviderAttempt(
        provider_id=provider_id,
        outcome=ProviderOutcome.SUCCESS,
        duration_ms=50,
        freshness_state=freshness,
        contract_count=1,
    )
    chain = NormalizedOptionsChain(
        underlying=underlying,
        expirations=[exp],
        contracts=contracts,
        provider_id=provider_id,
        provider_attempts=[attempt],
        retrieved_at=now,
        quote_timestamp=now,
        freshness_state=freshness,
        is_live=FreshnessState.is_live(freshness),
        is_complete=True,
        validation_errors=[],
        validation_warnings=[],
    )
    return chain


class TestFreshnessNeverSilentlyUpgraded(unittest.TestCase):
    """Delayed data must never be relabeled as LIVE."""

    def test_delayed_freshness_unchanged_through_gateway(self):
        from app.models.market_data_contracts import FreshnessState, OptionsDataRequirements
        from app.services.market_data_provider_registry import ProviderRegistry
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        delayed = _live_chain(freshness="DELAYED", provider_id="marketdata")
        from dataclasses import replace
        delayed = replace(delayed, is_live=False)

        provider = MagicMock()
        provider.provider_id = "marketdata"
        provider.is_configured = True
        from app.models.provider_capabilities import MARKETDATA_CAPABILITIES
        provider.capabilities = MARKETDATA_CAPABILITIES
        provider.get_options_chain.return_value = delayed

        registry = ProviderRegistry()
        registry.register("marketdata", lambda: provider)
        gateway = OptionsMarketDataGateway(registry=registry, provider_order=["marketdata"])

        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertEqual(result.freshness_state, FreshnessState.DELAYED)
        self.assertFalse(FreshnessState.permits_entry(result.freshness_state))

    def test_live_primary_permits_entry(self):
        from app.models.market_data_contracts import FreshnessState, OptionsDataRequirements
        from app.services.market_data_provider_registry import ProviderRegistry
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        live = _live_chain(freshness="LIVE_PRIMARY", provider_id="tradier")
        provider = MagicMock()
        provider.provider_id = "tradier"
        provider.is_configured = True
        from app.models.provider_capabilities import TRADIER_CAPABILITIES
        provider.capabilities = TRADIER_CAPABILITIES
        provider.get_options_chain.return_value = live

        registry = ProviderRegistry()
        registry.register("tradier", lambda: provider)
        gateway = OptionsMarketDataGateway(registry=registry, provider_order=["tradier"])

        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertTrue(FreshnessState.permits_entry(result.freshness_state))


class TestEntryAuthorizationContract(unittest.TestCase):
    """FreshnessState.permits_entry is the single gate; no other code may override it."""

    def _entry_allowed(self, state: str) -> bool:
        from app.models.market_data_contracts import FreshnessState
        return FreshnessState.permits_entry(state)

    def test_live_primary_entry_allowed(self):
        self.assertTrue(self._entry_allowed("LIVE_PRIMARY"))

    def test_live_failover_entry_allowed(self):
        self.assertTrue(self._entry_allowed("LIVE_FAILOVER"))

    def test_delayed_entry_not_allowed(self):
        self.assertFalse(self._entry_allowed("DELAYED"))

    def test_stale_cache_entry_not_allowed(self):
        self.assertFalse(self._entry_allowed("STALE_CACHE"))

    def test_incomplete_entry_not_allowed(self):
        self.assertFalse(self._entry_allowed("INCOMPLETE"))

    def test_unavailable_entry_not_allowed(self):
        self.assertFalse(self._entry_allowed("UNAVAILABLE"))

    def test_arbitrary_state_not_allowed(self):
        self.assertFalse(self._entry_allowed("UNKNOWN_STATE"))


class TestValidationServiceIntegration(unittest.TestCase):
    def _make_chain(self, **kwargs):
        return _live_chain(**kwargs)

    def test_validates_live_required(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.services.options_chain_validation_service import validate_chain

        chain = _live_chain(freshness="STALE_CACHE")
        from dataclasses import replace
        chain = replace(chain, is_live=False)

        req = OptionsDataRequirements(live_required=True)
        ok, errors, warnings = validate_chain(chain, req)
        self.assertFalse(ok)
        self.assertTrue(any("live" in e.lower() for e in errors))

    def test_validates_live_passes_when_live(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.services.options_chain_validation_service import validate_chain

        chain = _live_chain(freshness="LIVE_PRIMARY")
        req = OptionsDataRequirements(live_required=True, bid_ask_required=True)
        ok, errors, warnings = validate_chain(chain, req)
        self.assertTrue(ok, f"Errors: {errors}")

    def test_minimum_contract_count_enforced(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.services.options_chain_validation_service import validate_chain

        chain = _live_chain()  # only 1 contract
        req = OptionsDataRequirements(minimum_contract_count=10)
        ok, errors, warnings = validate_chain(chain, req)
        self.assertFalse(ok)
        self.assertTrue(any("minimum" in e.lower() or "contracts" in e.lower() for e in errors))

    def test_empty_chain_always_errors(self):
        from app.models.market_data_contracts import (
            FreshnessState,
            NormalizedOptionsChain,
            OptionsDataRequirements,
        )
        from app.services.options_chain_validation_service import validate_chain
        from datetime import datetime, timezone

        empty = NormalizedOptionsChain(
            underlying="SPY",
            expirations=[],
            contracts=[],
            provider_id="tradier",
            provider_attempts=[],
            retrieved_at=datetime.now(timezone.utc),
            quote_timestamp=None,
            freshness_state=FreshnessState.LIVE_PRIMARY,
            is_live=True,
            is_complete=False,
            validation_errors=[],
            validation_warnings=[],
        )
        ok, errors, _ = validate_chain(empty, OptionsDataRequirements())
        self.assertFalse(ok)
        self.assertTrue(any("no contracts" in e.lower() or "chain has" in e.lower() for e in errors))


class TestProviderOutcomeRetryability(unittest.TestCase):
    def test_retryable_outcomes(self):
        from app.models.market_data_contracts import ProviderOutcome
        for outcome in [
            ProviderOutcome.TIMEOUT,
            ProviderOutcome.RATE_LIMIT,
            ProviderOutcome.SERVER_ERROR,
            ProviderOutcome.EMPTY_CHAIN,
        ]:
            self.assertTrue(ProviderOutcome.is_retryable(outcome), f"{outcome} should be retryable")

    def test_non_retryable_outcomes(self):
        from app.models.market_data_contracts import ProviderOutcome
        for outcome in [
            ProviderOutcome.NOT_CONFIGURED,
            ProviderOutcome.MISSING_CAPABILITY,
            ProviderOutcome.SUCCESS,
        ]:
            self.assertFalse(ProviderOutcome.is_retryable(outcome), f"{outcome} should not be retryable")


if __name__ == "__main__":
    unittest.main()
