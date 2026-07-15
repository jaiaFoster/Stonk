"""Patch 33B — Provider contract tests.

Verifies that FreshnessState, OptionsDataRequirements, NormalizedOptionContract,
and NormalizedOptionsChain honour their documented invariants.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone


class TestFreshnessState(unittest.TestCase):
    def test_live_states_permit_entry(self):
        from app.models.market_data_contracts import FreshnessState
        self.assertTrue(FreshnessState.permits_entry(FreshnessState.LIVE_PRIMARY))
        self.assertTrue(FreshnessState.permits_entry(FreshnessState.LIVE_FAILOVER))

    def test_non_live_states_do_not_permit_entry(self):
        from app.models.market_data_contracts import FreshnessState
        for state in [
            FreshnessState.DELAYED,
            FreshnessState.STALE_CACHE,
            FreshnessState.INCOMPLETE,
            FreshnessState.UNAVAILABLE,
        ]:
            self.assertFalse(FreshnessState.permits_entry(state), f"Expected no entry for {state}")

    def test_is_live_matches_live_states_only(self):
        from app.models.market_data_contracts import FreshnessState
        self.assertTrue(FreshnessState.is_live(FreshnessState.LIVE_PRIMARY))
        self.assertTrue(FreshnessState.is_live(FreshnessState.LIVE_FAILOVER))
        self.assertFalse(FreshnessState.is_live(FreshnessState.DELAYED))
        self.assertFalse(FreshnessState.is_live(FreshnessState.STALE_CACHE))

    def test_delayed_is_not_live(self):
        from app.models.market_data_contracts import FreshnessState
        self.assertFalse(FreshnessState.is_live(FreshnessState.DELAYED))
        self.assertFalse(FreshnessState.permits_entry(FreshnessState.DELAYED))


class TestNormalizedOptionContract(unittest.TestCase):
    def _make_contract(self, bid=1.00, ask=1.20, mid=None, **kwargs):
        from app.models.market_data_contracts import FreshnessState, NormalizedOptionContract
        defaults = dict(
            symbol="SPY240119C00450000",
            underlying="SPY",
            expiration=date(2024, 1, 19),
            strike=450.0,
            option_type="call",
            bid=bid,
            ask=ask,
            mid=mid,
            last=1.10,
            volume=500,
            open_interest=1000,
            implied_volatility=0.20,
            delta=0.50,
            gamma=0.01,
            theta=-0.05,
            vega=0.10,
            rho=0.02,
            underlying_price=450.0,
            quote_timestamp=datetime.now(timezone.utc),
            provider_id="tradier",
            data_delay_seconds=0,
            freshness_state=FreshnessState.LIVE_PRIMARY,
        )
        defaults.update(kwargs)
        return NormalizedOptionContract(**defaults)

    def test_spread_is_ask_minus_bid(self):
        c = self._make_contract(bid=1.00, ask=1.20)
        self.assertAlmostEqual(c.spread, 0.20)

    def test_spread_none_when_bid_missing(self):
        c = self._make_contract(bid=None, ask=1.20)
        self.assertIsNone(c.spread)

    def test_spread_pct_when_mid_provided(self):
        c = self._make_contract(bid=1.00, ask=1.20, mid=1.10)
        self.assertAlmostEqual(c.spread_pct, 0.20 / 1.10, places=5)

    def test_spread_pct_none_when_mid_zero(self):
        c = self._make_contract(bid=0.00, ask=0.00, mid=0.00)
        self.assertIsNone(c.spread_pct)

    def test_to_compact_dict_has_required_keys(self):
        c = self._make_contract()
        d = c.to_compact_dict()
        for key in ["symbol", "underlying", "expiration_date", "strike", "option_type",
                    "bid", "ask", "mid", "iv", "delta", "freshness_state"]:
            self.assertIn(key, d, f"Missing key: {key}")

    def test_to_compact_dict_expiration_date_is_iso(self):
        c = self._make_contract()
        d = c.to_compact_dict()
        self.assertEqual(d["expiration_date"], "2024-01-19")


class TestNormalizedOptionsChain(unittest.TestCase):
    def _make_chain(self, n_calls=3, n_puts=3, freshness="LIVE_PRIMARY"):
        from app.models.market_data_contracts import (
            FreshnessState, NormalizedOptionContract, NormalizedOptionsChain, ProviderAttempt, ProviderOutcome,
        )
        exp = date(2024, 1, 19)
        contracts = []
        for i, ot in enumerate(["call"] * n_calls + ["put"] * n_puts):
            contracts.append(NormalizedOptionContract(
                symbol=f"SPY_{ot[:1].upper()}{i}",
                underlying="SPY",
                expiration=exp,
                strike=450.0 + i * 5,
                option_type=ot,
                bid=1.0, ask=1.2, mid=1.1, last=1.1,
                volume=100, open_interest=500,
                implied_volatility=0.20,
                delta=0.5, gamma=0.01, theta=-0.05, vega=0.10, rho=0.02,
                underlying_price=450.0,
                quote_timestamp=datetime.now(timezone.utc),
                provider_id="tradier",
                data_delay_seconds=0,
                freshness_state=freshness,
            ))
        attempt = ProviderAttempt(
            provider_id="tradier",
            outcome=ProviderOutcome.SUCCESS,
            duration_ms=100,
            freshness_state=freshness,
            contract_count=len(contracts),
        )
        return NormalizedOptionsChain(
            underlying="SPY",
            expirations=[exp],
            contracts=contracts,
            provider_id="tradier",
            provider_attempts=[attempt],
            retrieved_at=datetime.now(timezone.utc),
            quote_timestamp=datetime.now(timezone.utc),
            freshness_state=freshness,
            is_live=FreshnessState.is_live(freshness),
            is_complete=True,
            validation_errors=[],
            validation_warnings=[],
            underlying_price=450.0,
        )

    def test_call_contracts_filters_correctly(self):
        chain = self._make_chain(n_calls=3, n_puts=2)
        self.assertEqual(len(chain.call_contracts), 3)

    def test_put_contracts_filters_correctly(self):
        chain = self._make_chain(n_calls=3, n_puts=2)
        self.assertEqual(len(chain.put_contracts), 2)

    def test_contracts_for_expiration_by_date(self):
        chain = self._make_chain()
        exp = date(2024, 1, 19)
        self.assertEqual(len(chain.contracts_for_expiration(exp)), 6)

    def test_contracts_for_expiration_by_string(self):
        chain = self._make_chain()
        self.assertEqual(len(chain.contracts_for_expiration("2024-01-19")), 6)

    def test_to_legacy_chain_set_shape(self):
        chain = self._make_chain()
        legacy = chain.to_legacy_chain_set()
        self.assertIn("ticker", legacy)
        self.assertIn("chains", legacy)
        self.assertIn("expirations", legacy)
        self.assertIn("freshness_state", legacy)
        self.assertIn("is_live", legacy)
        self.assertEqual(legacy["ticker"], "SPY")

    def test_to_legacy_chain_set_chains_by_expiration(self):
        chain = self._make_chain()
        legacy = chain.to_legacy_chain_set()
        chains_by_exp = legacy.get("chains_by_expiration") or legacy.get("chains")
        self.assertIsNotNone(chains_by_exp)
        self.assertIn("2024-01-19", chains_by_exp)
        self.assertEqual(len(chains_by_exp["2024-01-19"]), 6)


class TestOptionsDataRequirements(unittest.TestCase):
    def test_default_requirements_bid_ask_true(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        req = OptionsDataRequirements()
        self.assertTrue(req.bid_ask_required)
        self.assertFalse(req.live_required)
        self.assertFalse(req.greeks_required)

    def test_to_dict_round_trips(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        req = OptionsDataRequirements(live_required=True, greeks_required=True)
        d = req.to_dict()
        self.assertTrue(d["live_required"])
        self.assertTrue(d["greeks_required"])


if __name__ == "__main__":
    unittest.main()
