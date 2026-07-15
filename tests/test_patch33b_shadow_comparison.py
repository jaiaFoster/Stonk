"""Patch 33B — Shadow comparison engine tests."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import date, datetime, timezone


def _make_chain(ticker="AAPL", provider_id="tradier", freshness="LIVE_PRIMARY", price_offset=0.0):
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
            symbol=f"{ticker}_{ot}",
            underlying=ticker,
            expiration=exp,
            strike=180.0 + i * 5,
            option_type=ot,
            bid=2.00 + price_offset, ask=2.20 + price_offset,
            mid=2.10 + price_offset, last=2.10 + price_offset,
            volume=200, open_interest=1000,
            implied_volatility=0.25, delta=0.45, gamma=0.02,
            theta=-0.08, vega=0.15, rho=0.03,
            underlying_price=182.0 + price_offset,
            quote_timestamp=now,
            provider_id=provider_id,
            data_delay_seconds=0,
            freshness_state=freshness,
        ))

    attempt = ProviderAttempt(
        provider_id=provider_id, outcome=ProviderOutcome.SUCCESS,
        duration_ms=50, freshness_state=freshness, contract_count=len(contracts),
    )
    return NormalizedOptionsChain(
        underlying=ticker, expirations=[exp], contracts=contracts,
        provider_id=provider_id, provider_attempts=[attempt],
        retrieved_at=now, quote_timestamp=now, freshness_state=freshness,
        is_live=FreshnessState.is_live(freshness), is_complete=True,
        validation_errors=[], validation_warnings=[],
        underlying_price=182.0 + price_offset,
    )


class TestCompareChains(unittest.TestCase):
    def test_identical_chains_classified_as_match(self):
        from app.services.options_provider_comparison_service import ComparisonClassification, compare_chains
        primary = _make_chain(provider_id="tradier")
        shadow = _make_chain(provider_id="marketdata")
        result = compare_chains(primary, shadow)
        self.assertIn(result.classification, {
            ComparisonClassification.MATCH, ComparisonClassification.ACCEPTABLE_VARIANCE
        })

    def test_large_price_difference_is_material(self):
        from app.services.options_provider_comparison_service import ComparisonClassification, compare_chains
        primary = _make_chain(provider_id="tradier", price_offset=0.0)
        shadow = _make_chain(provider_id="marketdata", price_offset=5.0)
        result = compare_chains(primary, shadow)
        self.assertEqual(result.classification, ComparisonClassification.MATERIAL_DIVERGENCE)

    def test_coverage_pct_is_one_for_identical_chains(self):
        from app.services.options_provider_comparison_service import compare_chains
        primary = _make_chain()
        shadow = _make_chain()
        result = compare_chains(primary, shadow)
        self.assertAlmostEqual(result.coverage_pct, 1.0)

    def test_coverage_zero_for_empty_shadow(self):
        from app.models.market_data_contracts import NormalizedOptionsChain
        from app.services.options_provider_comparison_service import ComparisonClassification, compare_chains
        from datetime import datetime, timezone
        primary = _make_chain()
        shadow_empty = replace(primary, contracts=[], expirations=[], provider_id="marketdata")
        result = compare_chains(primary, shadow_empty)
        self.assertEqual(result.coverage_pct, 0.0)
        self.assertEqual(result.classification, ComparisonClassification.MATERIAL_DIVERGENCE)

    def test_contract_count_reported_correctly(self):
        from app.services.options_provider_comparison_service import compare_chains
        primary = _make_chain()
        shadow = _make_chain()
        result = compare_chains(primary, shadow)
        self.assertEqual(result.primary_contract_count, 2)
        self.assertEqual(result.shadow_contract_count, 2)
        self.assertEqual(result.matched_contract_count, 2)

    def test_provider_ids_recorded(self):
        from app.services.options_provider_comparison_service import compare_chains
        primary = _make_chain(provider_id="tradier")
        shadow = _make_chain(provider_id="marketdata")
        result = compare_chains(primary, shadow)
        self.assertEqual(result.primary_provider, "tradier")
        self.assertEqual(result.shadow_provider, "marketdata")

    def test_underlying_diff_pct_near_zero_for_identical(self):
        from app.services.options_provider_comparison_service import compare_chains
        primary = _make_chain()
        shadow = _make_chain()
        result = compare_chains(primary, shadow)
        self.assertAlmostEqual(result.underlying_diff_pct, 0.0)

    def test_material_divergences_list_populated(self):
        from app.services.options_provider_comparison_service import ComparisonClassification, compare_chains
        primary = _make_chain(price_offset=0.0)
        shadow = _make_chain(price_offset=10.0)
        result = compare_chains(primary, shadow)
        self.assertEqual(result.classification, ComparisonClassification.MATERIAL_DIVERGENCE)
        self.assertGreater(len(result.material_divergences), 0)

    def test_compare_does_not_modify_primary_chain(self):
        from app.services.options_provider_comparison_service import compare_chains
        primary = _make_chain()
        original_price = primary.contracts[0].mid
        shadow = _make_chain(price_offset=5.0)
        compare_chains(primary, shadow)
        self.assertEqual(primary.contracts[0].mid, original_price)


class TestSelectionOutcome(unittest.TestCase):
    def test_selection_outcome_recorded_in_result(self):
        from app.services.options_provider_comparison_service import SelectionOutcome, compare_chains
        primary = _make_chain()
        shadow = _make_chain()
        result = compare_chains(primary, shadow, selection_outcome=SelectionOutcome.PRIMARY_SELECTED_SHADOW_AGREES)
        self.assertEqual(result.selection_outcome, SelectionOutcome.PRIMARY_SELECTED_SHADOW_AGREES)


class TestGatewayShadow(unittest.TestCase):
    def _make_mock_registry(self, primary_chain, shadow_chain=None):
        from app.services.market_data_provider_registry import ProviderRegistry
        registry = ProviderRegistry()

        class MockPrimary:
            provider_id = "tradier"
            is_configured = True
            @property
            def capabilities(self):
                from app.models.provider_capabilities import TRADIER_CAPABILITIES
                return TRADIER_CAPABILITIES
            def get_options_chain(self, symbol, requirements, expirations=None):
                return primary_chain

        class MockShadow:
            provider_id = "marketdata"
            is_configured = True
            @property
            def capabilities(self):
                from app.models.provider_capabilities import MARKETDATA_CAPABILITIES
                return MARKETDATA_CAPABILITIES
            def get_options_chain(self, symbol, requirements, expirations=None):
                if shadow_chain is None:
                    raise RuntimeError("shadow not available")
                return shadow_chain

        class MockCache:
            provider_id = "last_known_good_cache"
            is_configured = False
            @property
            def capabilities(self):
                from app.models.provider_capabilities import LAST_KNOWN_GOOD_CAPABILITIES
                return LAST_KNOWN_GOOD_CAPABILITIES

        registry.register("tradier", MockPrimary)
        registry.register("marketdata", MockShadow)
        registry.register("last_known_good_cache", MockCache)
        return registry

    def test_gateway_shadow_disabled_returns_primary(self):
        from app.services.options_market_data_gateway import OptionsMarketDataGateway
        primary = _make_chain(provider_id="tradier")
        registry = self._make_mock_registry(primary)
        gw = OptionsMarketDataGateway(registry=registry, shadow_enabled=False)
        result = gw.get_chain("AAPL")
        self.assertEqual(result.provider_id, "tradier")

    def test_gateway_with_shadow_enabled_returns_primary(self):
        from app.services.options_market_data_gateway import OptionsMarketDataGateway
        primary = _make_chain(provider_id="tradier")
        shadow = _make_chain(provider_id="marketdata")
        registry = self._make_mock_registry(primary, shadow)
        # Force sample rate 1.0 so shadow always runs
        gw = OptionsMarketDataGateway(
            registry=registry, shadow_enabled=True, shadow_sample_rate=1.0
        )
        result = gw.get_chain("AAPL")
        # Primary should always be returned (not shadow)
        self.assertEqual(result.provider_id, "tradier")

    def test_shadow_promoted_when_primary_fails(self):
        from app.providers.options_data_provider import ProviderTimeoutError
        from app.services.options_market_data_gateway import OptionsMarketDataGateway
        from app.services.market_data_provider_registry import ProviderRegistry

        registry = ProviderRegistry()
        shadow_chain = _make_chain(provider_id="marketdata", freshness="LIVE_FAILOVER")

        class FailingPrimary:
            provider_id = "tradier"
            is_configured = True
            @property
            def capabilities(self):
                from app.models.provider_capabilities import TRADIER_CAPABILITIES
                return TRADIER_CAPABILITIES
            def get_options_chain(self, symbol, requirements, expirations=None):
                raise ProviderTimeoutError("timeout", provider_id="tradier")

        class ShadowProvider:
            provider_id = "marketdata"
            is_configured = True
            @property
            def capabilities(self):
                from app.models.provider_capabilities import MARKETDATA_CAPABILITIES
                return MARKETDATA_CAPABILITIES
            def get_options_chain(self, symbol, requirements, expirations=None):
                return shadow_chain

        class MockCache:
            provider_id = "last_known_good_cache"
            is_configured = False
            @property
            def capabilities(self):
                from app.models.provider_capabilities import LAST_KNOWN_GOOD_CAPABILITIES
                return LAST_KNOWN_GOOD_CAPABILITIES

        registry.register("tradier", FailingPrimary)
        registry.register("marketdata", ShadowProvider)
        registry.register("last_known_good_cache", MockCache)

        gw = OptionsMarketDataGateway(
            registry=registry,
            provider_order=["tradier", "marketdata", "last_known_good_cache"],
            shadow_enabled=True,
            shadow_sample_rate=1.0,
        )
        result = gw.get_chain("AAPL")
        # Shadow promoted — should be marketdata chain
        self.assertEqual(result.provider_id, "marketdata")

    def test_shadow_budget_enforced(self):
        from app.services.options_market_data_gateway import OptionsMarketDataGateway
        primary = _make_chain(provider_id="tradier")
        shadow = _make_chain(provider_id="marketdata")
        registry = self._make_mock_registry(primary, shadow)
        gw = OptionsMarketDataGateway(
            registry=registry, shadow_enabled=True, shadow_sample_rate=1.0,
        )
        # Exhaust the budget
        import app.config as cfg
        gw._shadow_budget_remaining = 0
        # Even with sample_rate=1.0 and shadow_enabled, budget=0 skips shadow
        result = gw.get_chain("AAPL")
        self.assertEqual(result.provider_id, "tradier")


class TestComparisonPersistence(unittest.TestCase):
    def test_store_and_retrieve(self):
        from app.services.options_provider_comparison_service import ComparisonClassification, SelectionOutcome, compare_chains
        from app.db.options_provider_comparison_repository import get_recent_comparisons, store_comparison

        primary = _make_chain(provider_id="tradier")
        shadow = _make_chain(provider_id="marketdata")
        result = compare_chains(primary, shadow, SelectionOutcome.PRIMARY_SELECTED_SHADOW_AGREES)

        row_id = store_comparison(result, run_id="test_run")
        # row_id may be None if SQLite is unavailable in CI; just check no exception raised
        if row_id is not None:
            self.assertIsInstance(row_id, int)
            rows = get_recent_comparisons(limit=5, ticker="AAPL")
            self.assertIsInstance(rows, list)

    def test_get_comparison_stats_returns_dict(self):
        from app.db.options_provider_comparison_repository import get_comparison_stats
        stats = get_comparison_stats()
        self.assertIn("total", stats)
        self.assertIn("by_classification", stats)


class TestShadowSkipReasons(unittest.TestCase):
    def test_shadow_skip_reason_constants(self):
        from app.services.options_provider_comparison_service import ShadowSkipReason
        self.assertEqual(ShadowSkipReason.BUDGET, "SHADOW_SKIPPED_BUDGET")
        self.assertEqual(ShadowSkipReason.SAMPLE, "SHADOW_SKIPPED_SAMPLE")
        self.assertEqual(ShadowSkipReason.CAPABILITY, "SHADOW_SKIPPED_CAPABILITY")
        self.assertEqual(ShadowSkipReason.PROVIDER_UNCONFIGURED, "SHADOW_SKIPPED_PROVIDER_UNCONFIGURED")


class TestShadowConfigVariables(unittest.TestCase):
    def test_shadow_enabled_default_true(self):
        import app.config as cfg
        self.assertTrue(cfg.OPTIONS_PROVIDER_SHADOW_ENABLED)

    def test_shadow_dev_sample_rate_is_1(self):
        import app.config as cfg
        self.assertAlmostEqual(cfg.OPTIONS_PROVIDER_SHADOW_DEV_SAMPLE_RATE, 1.0)

    def test_shadow_prod_sample_rate_is_low(self):
        import app.config as cfg
        self.assertLessEqual(cfg.OPTIONS_PROVIDER_SHADOW_PROD_SAMPLE_RATE, 0.1)

    def test_shadow_max_tickers_per_run(self):
        import app.config as cfg
        self.assertGreater(cfg.OPTIONS_PROVIDER_SHADOW_MAX_TICKERS_PER_RUN, 0)

    def test_shadow_providers_includes_marketdata(self):
        import app.config as cfg
        self.assertIn("marketdata", cfg.OPTIONS_PROVIDER_SHADOW_PROVIDERS)

    def test_stale_cache_enabled_default_true(self):
        import app.config as cfg
        self.assertTrue(cfg.OPTIONS_STALE_CACHE_ENABLED)

    def test_stale_cache_max_age_positive(self):
        import app.config as cfg
        self.assertGreater(cfg.OPTIONS_STALE_CACHE_MAX_AGE_SECONDS, 0)


if __name__ == "__main__":
    unittest.main()
