"""Patch 33B — Provider registry tests."""

from __future__ import annotations

import unittest


class TestProviderRegistry(unittest.TestCase):
    def test_default_registry_has_tradier(self):
        from app.services.market_data_provider_registry import get_default_registry
        registry = get_default_registry()
        self.assertIn("tradier", registry.available_ids())

    def test_default_registry_has_marketdata(self):
        from app.services.market_data_provider_registry import get_default_registry
        registry = get_default_registry()
        self.assertIn("marketdata", registry.available_ids())

    def test_default_registry_has_last_known_good_cache(self):
        from app.services.market_data_provider_registry import get_default_registry
        registry = get_default_registry()
        self.assertIn("last_known_good_cache", registry.available_ids())

    def test_get_unknown_provider_returns_none(self):
        from app.services.market_data_provider_registry import get_default_registry
        registry = get_default_registry()
        self.assertIsNone(registry.get("nonexistent_provider"))

    def test_registry_get_returns_provider_with_correct_id(self):
        from app.services.market_data_provider_registry import get_default_registry
        registry = get_default_registry()
        provider = registry.get("tradier")
        self.assertIsNotNone(provider)
        self.assertEqual(provider.provider_id, "tradier")

    def test_capabilities_summary_returns_list(self):
        from app.services.market_data_provider_registry import get_default_registry
        registry = get_default_registry()
        caps = registry.capabilities_summary()
        self.assertIsInstance(caps, list)
        self.assertGreater(len(caps), 0)

    def test_capabilities_summary_has_provider_id(self):
        from app.services.market_data_provider_registry import get_default_registry
        registry = get_default_registry()
        for cap in registry.capabilities_summary():
            self.assertIn("provider_id", cap)

    def test_select_providers_excludes_unconfigured(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.services.market_data_provider_registry import ProviderRegistry

        registry = ProviderRegistry()
        unconfigured_calls = []

        class MockUnconfigured:
            provider_id = "mock_unconfigured"
            @property
            def is_configured(self):
                return False
            @property
            def capabilities(self):
                from app.models.provider_capabilities import TRADIER_CAPABILITIES
                return TRADIER_CAPABILITIES

        registry.register("mock_unconfigured", MockUnconfigured)
        selected = registry.select_providers(OptionsDataRequirements(), order=["mock_unconfigured"])
        self.assertEqual(len(selected), 0)

    def test_custom_registry_register_and_get(self):
        from app.services.market_data_provider_registry import ProviderRegistry

        registry = ProviderRegistry()

        class FakeProvider:
            provider_id = "fake"
            is_configured = True
            @property
            def capabilities(self):
                from app.models.provider_capabilities import TRADIER_CAPABILITIES
                return TRADIER_CAPABILITIES

        registry.register("fake", FakeProvider)
        p = registry.get("fake")
        self.assertIsNotNone(p)
        self.assertEqual(p.provider_id, "fake")


class TestProviderCapabilities(unittest.TestCase):
    def test_tradier_capabilities_live(self):
        from app.models.provider_capabilities import TRADIER_CAPABILITIES
        self.assertTrue(TRADIER_CAPABILITIES.can_supply_live_chains)
        self.assertTrue(TRADIER_CAPABILITIES.can_supply_greeks)
        self.assertEqual(TRADIER_CAPABILITIES.typical_delay_seconds, 0)

    def test_marketdata_capabilities_live(self):
        from app.models.provider_capabilities import MARKETDATA_CAPABILITIES
        self.assertTrue(MARKETDATA_CAPABILITIES.can_supply_live_chains)
        self.assertTrue(MARKETDATA_CAPABILITIES.can_supply_greeks)

    def test_last_known_good_not_live(self):
        from app.models.provider_capabilities import LAST_KNOWN_GOOD_CAPABILITIES
        self.assertFalse(LAST_KNOWN_GOOD_CAPABILITIES.can_supply_live_chains)

    def test_satisfies_live_required_fails_for_cache(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.models.provider_capabilities import LAST_KNOWN_GOOD_CAPABILITIES
        req = OptionsDataRequirements(live_required=True)
        ok, failures = LAST_KNOWN_GOOD_CAPABILITIES.satisfies(req)
        self.assertFalse(ok)
        self.assertGreater(len(failures), 0)

    def test_satisfies_live_required_passes_for_tradier(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.models.provider_capabilities import TRADIER_CAPABILITIES
        req = OptionsDataRequirements(live_required=True, bid_ask_required=True, greeks_required=True)
        ok, failures = TRADIER_CAPABILITIES.satisfies(req)
        self.assertTrue(ok, f"Tradier should satisfy live+bid_ask+greeks: {failures}")

    def test_satisfies_delay_constraint(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.models.provider_capabilities import ProviderCapabilities
        slow_provider = ProviderCapabilities(
            provider_id="slow",
            can_supply_live_chains=True,
            can_supply_bid_ask=True,
            typical_delay_seconds=900,
        )
        req = OptionsDataRequirements(maximum_delay_seconds=60)
        ok, failures = slow_provider.satisfies(req)
        self.assertFalse(ok)

    def test_to_dict_has_provider_id(self):
        from app.models.provider_capabilities import TRADIER_CAPABILITIES
        d = TRADIER_CAPABILITIES.to_dict()
        self.assertEqual(d["provider_id"], "tradier")


if __name__ == "__main__":
    unittest.main()
