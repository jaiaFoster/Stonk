"""Patch 33B — OptionsMarketDataGateway tests."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch


def _make_live_chain(underlying="SPY", freshness="LIVE_PRIMARY"):
    from app.models.market_data_contracts import (
        FreshnessState,
        NormalizedOptionContract,
        NormalizedOptionsChain,
        ProviderAttempt,
        ProviderOutcome,
    )
    exp = date(2024, 1, 19)
    now = datetime.now(timezone.utc)
    contracts = []
    for i, ot in enumerate(["call", "put"]):
        contracts.append(NormalizedOptionContract(
            symbol=f"SPY_{ot[:1].upper()}{i}",
            underlying=underlying,
            expiration=exp,
            strike=450.0 + i * 5,
            option_type=ot,
            bid=1.0, ask=1.2, mid=1.1, last=1.1,
            volume=100, open_interest=500,
            implied_volatility=0.20,
            delta=0.5, gamma=0.01, theta=-0.05, vega=0.10, rho=0.02,
            underlying_price=450.0,
            quote_timestamp=now,
            provider_id="tradier",
            data_delay_seconds=0,
            freshness_state=freshness,
        ))
    attempt = ProviderAttempt(
        provider_id="tradier",
        outcome=ProviderOutcome.SUCCESS,
        duration_ms=50,
        freshness_state=freshness,
        contract_count=len(contracts),
    )
    return NormalizedOptionsChain(
        underlying=underlying,
        expirations=[exp],
        contracts=contracts,
        provider_id="tradier",
        provider_attempts=[attempt],
        retrieved_at=now,
        quote_timestamp=now,
        freshness_state=freshness,
        is_live=FreshnessState.is_live(freshness),
        is_complete=True,
        validation_errors=[],
        validation_warnings=[],
        underlying_price=450.0,
    )


def _make_mock_provider(pid="tradier", configured=True, chain=None, raise_exc=None):
    from app.models.provider_capabilities import TRADIER_CAPABILITIES
    p = MagicMock()
    p.provider_id = pid
    p.is_configured = configured
    p.capabilities = TRADIER_CAPABILITIES
    if raise_exc:
        p.get_options_chain.side_effect = raise_exc
    else:
        p.get_options_chain.return_value = chain or _make_live_chain()
    return p


def _make_registry_with_providers(providers: dict):
    from app.services.market_data_provider_registry import ProviderRegistry
    registry = ProviderRegistry()
    for pid, provider in providers.items():
        captured = provider
        registry.register(pid, lambda p=captured: p)
    return registry


class TestGatewaySuccessPath(unittest.TestCase):
    def test_returns_chain_from_first_configured_provider(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        chain = _make_live_chain()
        tradier = _make_mock_provider(pid="tradier", chain=chain)
        registry = _make_registry_with_providers({"tradier": tradier})
        gateway = OptionsMarketDataGateway(registry=registry, provider_order=["tradier"])

        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertEqual(result.underlying, "SPY")
        self.assertEqual(result.provider_id, "tradier")

    def test_successful_chain_is_live(self):
        from app.models.market_data_contracts import FreshnessState, OptionsDataRequirements
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        tradier = _make_mock_provider(pid="tradier", chain=_make_live_chain())
        registry = _make_registry_with_providers({"tradier": tradier})
        gateway = OptionsMarketDataGateway(registry=registry, provider_order=["tradier"])
        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertTrue(FreshnessState.is_live(result.freshness_state))


class TestGatewayFailover(unittest.TestCase):
    def test_failover_to_second_provider_on_auth_error(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.providers.options_data_provider import ProviderAuthError
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        failover_chain = _make_live_chain(freshness="LIVE_FAILOVER")
        failover_chain_copy = _make_live_chain(freshness="LIVE_FAILOVER")

        tradier = _make_mock_provider(pid="tradier", raise_exc=ProviderAuthError("auth failed", "tradier"))
        marketdata_chain = _make_live_chain(freshness="LIVE_FAILOVER")
        # Adjust freshness_state properly
        from dataclasses import replace
        marketdata_chain = replace(marketdata_chain, freshness_state="LIVE_FAILOVER", provider_id="marketdata")
        marketdata = _make_mock_provider(pid="marketdata", chain=marketdata_chain)

        registry = _make_registry_with_providers({"tradier": tradier, "marketdata": marketdata})
        gateway = OptionsMarketDataGateway(
            registry=registry,
            provider_order=["tradier", "marketdata"],
            failover_enabled=True,
        )
        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertIsNotNone(result)
        # Should have attempted tradier (failed) and then marketdata
        outcomes = [a.outcome for a in result.provider_attempts]
        self.assertIn("AUTH_UNAVAILABLE", outcomes)

    def test_all_providers_fail_raises_gateway_unavailable(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.providers.options_data_provider import ProviderTimeoutError
        from app.services.options_market_data_gateway import (
            GatewayUnavailableError,
            OptionsMarketDataGateway,
        )

        t1 = _make_mock_provider(pid="tradier", raise_exc=ProviderTimeoutError("timeout", "tradier"))
        t2 = _make_mock_provider(pid="marketdata", raise_exc=ProviderTimeoutError("timeout", "marketdata"))
        registry = _make_registry_with_providers({"tradier": t1, "marketdata": t2})
        gateway = OptionsMarketDataGateway(
            registry=registry,
            provider_order=["tradier", "marketdata"],
            failover_enabled=True,
        )
        with self.assertRaises(GatewayUnavailableError) as ctx:
            gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertGreater(len(ctx.exception.provider_attempts), 0)

    def test_failover_disabled_stops_at_first_failure(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.providers.options_data_provider import ProviderAuthError
        from app.services.options_market_data_gateway import (
            GatewayUnavailableError,
            OptionsMarketDataGateway,
        )

        t1 = _make_mock_provider(pid="tradier", raise_exc=ProviderAuthError("auth failed", "tradier"))
        t2 = _make_mock_provider(pid="marketdata", chain=_make_live_chain())
        registry = _make_registry_with_providers({"tradier": t1, "marketdata": t2})
        gateway = OptionsMarketDataGateway(
            registry=registry,
            provider_order=["tradier", "marketdata"],
            failover_enabled=False,
        )
        with self.assertRaises(GatewayUnavailableError):
            gateway.get_chain("SPY", OptionsDataRequirements())
        # marketdata should NOT have been called
        t2.get_options_chain.assert_not_called()

    def test_unconfigured_provider_skipped(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        unconfigured = _make_mock_provider(pid="tradier", configured=False)
        configured_chain = _make_live_chain(freshness="LIVE_FAILOVER")
        from dataclasses import replace
        configured_chain = replace(configured_chain, provider_id="marketdata")
        configured = _make_mock_provider(pid="marketdata", chain=configured_chain)
        registry = _make_registry_with_providers({"tradier": unconfigured, "marketdata": configured})
        gateway = OptionsMarketDataGateway(
            registry=registry,
            provider_order=["tradier", "marketdata"],
            failover_enabled=True,
        )
        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertIsNotNone(result)
        unconfigured.get_options_chain.assert_not_called()

    def test_provider_attempts_accumulated_across_failures(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.providers.options_data_provider import ProviderTimeoutError
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        chain = _make_live_chain(freshness="LIVE_FAILOVER")
        from dataclasses import replace
        chain = replace(chain, provider_id="last_known_good_cache", freshness_state="STALE_CACHE")

        t1 = _make_mock_provider(pid="tradier", raise_exc=ProviderTimeoutError("timeout", "tradier"))
        t2 = _make_mock_provider(pid="marketdata", raise_exc=ProviderTimeoutError("timeout", "marketdata"))
        t3 = _make_mock_provider(pid="last_known_good_cache", chain=chain)

        registry = _make_registry_with_providers({
            "tradier": t1, "marketdata": t2, "last_known_good_cache": t3
        })
        gateway = OptionsMarketDataGateway(
            registry=registry,
            provider_order=["tradier", "marketdata", "last_known_good_cache"],
            failover_enabled=True,
        )
        result = gateway.get_chain("SPY", OptionsDataRequirements())
        # Should have 2 failure attempts + 1 success attempt
        outcomes = [a.outcome for a in result.provider_attempts]
        self.assertGreaterEqual(outcomes.count("TIMEOUT"), 2)


class TestGatewayFreshnessNeverUpgraded(unittest.TestCase):
    """The gateway must never label delayed data as LIVE_*."""

    def test_delayed_chain_does_not_permit_entry(self):
        from app.models.market_data_contracts import FreshnessState, OptionsDataRequirements
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        delayed_chain = _make_live_chain(freshness="DELAYED")
        from dataclasses import replace
        delayed_chain = replace(delayed_chain, is_live=False, freshness_state="DELAYED")
        provider = _make_mock_provider(pid="tradier", chain=delayed_chain)
        registry = _make_registry_with_providers({"tradier": provider})
        gateway = OptionsMarketDataGateway(
            registry=registry, provider_order=["tradier"]
        )
        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertFalse(FreshnessState.permits_entry(result.freshness_state))

    def test_stale_chain_does_not_permit_entry(self):
        from app.models.market_data_contracts import FreshnessState, OptionsDataRequirements
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        stale_chain = _make_live_chain(freshness="STALE_CACHE")
        from dataclasses import replace
        stale_chain = replace(stale_chain, is_live=False)
        provider = _make_mock_provider(pid="last_known_good_cache", chain=stale_chain)
        registry = _make_registry_with_providers({"last_known_good_cache": provider})
        gateway = OptionsMarketDataGateway(
            registry=registry, provider_order=["last_known_good_cache"]
        )
        result = gateway.get_chain("SPY", OptionsDataRequirements())
        self.assertFalse(FreshnessState.permits_entry(result.freshness_state))


class TestGatewayUnavailableSentinel(unittest.TestCase):
    def test_unavailable_chain_has_no_contracts(self):
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        registry = _make_registry_with_providers({})
        gateway = OptionsMarketDataGateway(registry=registry, provider_order=[])
        sentinel = gateway.unavailable_chain("SPY")
        self.assertEqual(sentinel.underlying, "SPY")
        self.assertEqual(len(sentinel.contracts), 0)
        self.assertEqual(sentinel.freshness_state, "UNAVAILABLE")

    def test_unavailable_chain_does_not_permit_entry(self):
        from app.models.market_data_contracts import FreshnessState
        from app.services.options_market_data_gateway import OptionsMarketDataGateway

        gateway = OptionsMarketDataGateway(registry=_make_registry_with_providers({}), provider_order=[])
        sentinel = gateway.unavailable_chain("SPY")
        self.assertFalse(FreshnessState.permits_entry(sentinel.freshness_state))


if __name__ == "__main__":
    unittest.main()
