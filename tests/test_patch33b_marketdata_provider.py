"""Patch 33B — MarketData.app provider tests (no live network calls)."""

from __future__ import annotations

import json
import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch


def _make_marketdata_response(n_calls=2, n_puts=2, delayed=False):
    """Build a minimal MarketData.app /options/chain parallel-array response."""
    symbols, sides, strikes, exps = [], [], [], []
    bids, asks, mids, lasts = [], [], [], []
    volumes, open_interests, ivs = [], [], []
    deltas, gammas, thetas, vegas, rhos = [], [], [], [], []
    underlying_prices, updated_times = [], []

    for i in range(n_calls):
        symbols.append(f"SPY240119C{450 + i * 5:08.0f}".replace(".", ""))
        sides.append("call")
        strikes.append(450.0 + i * 5)
        exps.append(1705622400)  # 2024-01-19 as Unix timestamp
        bids.append(1.00 + i * 0.1)
        asks.append(1.20 + i * 0.1)
        mids.append(1.10 + i * 0.1)
        lasts.append(1.10 + i * 0.1)
        volumes.append(100 + i * 10)
        open_interests.append(500 + i * 50)
        ivs.append(0.20 + i * 0.01)
        deltas.append(0.50 + i * 0.01)
        gammas.append(0.01)
        thetas.append(-0.05)
        vegas.append(0.10)
        rhos.append(0.02)
        underlying_prices.append(452.0)
        updated_times.append(1705700000)

    for i in range(n_puts):
        symbols.append(f"SPY240119P{445 - i * 5:08.0f}".replace(".", ""))
        sides.append("put")
        strikes.append(445.0 - i * 5)
        exps.append(1705622400)
        bids.append(0.80 + i * 0.1)
        asks.append(1.00 + i * 0.1)
        mids.append(0.90 + i * 0.1)
        lasts.append(0.90 + i * 0.1)
        volumes.append(80 + i * 10)
        open_interests.append(400 + i * 50)
        ivs.append(0.22 + i * 0.01)
        deltas.append(-0.45 - i * 0.01)
        gammas.append(0.01)
        thetas.append(-0.05)
        vegas.append(0.10)
        rhos.append(-0.02)
        underlying_prices.append(452.0)
        updated_times.append(1705700000)

    return {
        "s": "ok",
        "optionSymbol": symbols,
        "side": sides,
        "strike": strikes,
        "expiration": exps,
        "bid": bids,
        "ask": asks,
        "mid": mids,
        "last": lasts,
        "volume": volumes,
        "openInterest": open_interests,
        "iv": ivs,
        "delta": deltas,
        "gamma": gammas,
        "theta": thetas,
        "vega": vegas,
        "rho": rhos,
        "underlyingPrice": underlying_prices,
        "updated": updated_times,
        "delayed": delayed,
    }


class TestMarketDataProviderParsing(unittest.TestCase):
    def test_parse_valid_response_returns_contracts(self):
        from app.providers.marketdata_provider import _parse_chain_response
        data = _make_marketdata_response(n_calls=2, n_puts=2)
        contracts, errors, warnings = _parse_chain_response(data, "SPY", datetime.now(timezone.utc))
        self.assertEqual(len(contracts), 4)
        self.assertEqual(len(errors), 0)

    def test_call_contracts_have_correct_type(self):
        from app.providers.marketdata_provider import _parse_chain_response
        data = _make_marketdata_response(n_calls=2, n_puts=0)
        contracts, _, _ = _parse_chain_response(data, "SPY", datetime.now(timezone.utc))
        for c in contracts:
            self.assertEqual(c.option_type, "call")

    def test_put_contracts_have_correct_type(self):
        from app.providers.marketdata_provider import _parse_chain_response
        data = _make_marketdata_response(n_calls=0, n_puts=2)
        contracts, _, _ = _parse_chain_response(data, "SPY", datetime.now(timezone.utc))
        for c in contracts:
            self.assertEqual(c.option_type, "put")

    def test_no_data_status_returns_error(self):
        from app.providers.marketdata_provider import _parse_chain_response
        data = {"s": "no_data"}
        contracts, errors, _ = _parse_chain_response(data, "SPY", datetime.now(timezone.utc))
        self.assertEqual(len(contracts), 0)
        self.assertGreater(len(errors), 0)

    def test_delayed_flag_detected(self):
        from app.providers.marketdata_provider import _is_delayed_response
        self.assertTrue(_is_delayed_response({"delayed": True}))
        self.assertFalse(_is_delayed_response({"delayed": False}))
        self.assertFalse(_is_delayed_response({}))

    def test_unix_timestamp_expiration_parsed(self):
        from app.providers.marketdata_provider import _parse_exp
        dt = _parse_exp(1705622400)
        self.assertIsInstance(dt, date)
        self.assertEqual(dt.year, 2024)

    def test_iso_string_expiration_parsed(self):
        from app.providers.marketdata_provider import _parse_exp
        dt = _parse_exp("2024-01-19")
        self.assertEqual(dt, date(2024, 1, 19))

    def test_none_expiration_returns_none(self):
        from app.providers.marketdata_provider import _parse_exp
        self.assertIsNone(_parse_exp(None))


class TestMarketDataProviderProperties(unittest.TestCase):
    def test_provider_id_is_marketdata(self):
        from app.providers.marketdata_provider import MarketDataProvider
        p = MarketDataProvider(api_key="test_key_123")
        self.assertEqual(p.provider_id, "marketdata")

    def test_is_configured_true_when_key_provided(self):
        from app.providers.marketdata_provider import MarketDataProvider
        p = MarketDataProvider(api_key="test_key_123")
        self.assertTrue(p.is_configured)

    def test_is_configured_false_when_no_key(self):
        from app.providers.marketdata_provider import MarketDataProvider
        p = MarketDataProvider(api_key=None)
        # Patch out config.MARKETDATA_KEY to None too
        import app.config as cfg
        orig = cfg.MARKETDATA_KEY
        try:
            cfg.MARKETDATA_KEY = None
            p2 = MarketDataProvider(api_key=None)
            self.assertFalse(p2.is_configured)
        finally:
            cfg.MARKETDATA_KEY = orig

    def test_raises_not_configured_when_no_key(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.providers.marketdata_provider import MarketDataProvider
        from app.providers.options_data_provider import ProviderNotConfiguredError
        import app.config as cfg
        orig = cfg.MARKETDATA_KEY
        try:
            cfg.MARKETDATA_KEY = None
            p = MarketDataProvider(api_key=None)
            with self.assertRaises(ProviderNotConfiguredError):
                p.get_options_chain("SPY", OptionsDataRequirements())
        finally:
            cfg.MARKETDATA_KEY = orig

    def test_get_chain_with_mocked_http(self):
        from app.models.market_data_contracts import FreshnessState, OptionsDataRequirements
        from app.providers.marketdata_provider import MarketDataProvider

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_marketdata_response(n_calls=2, n_puts=2)

        p = MarketDataProvider(api_key="test_key_123")
        with patch("app.providers.marketdata_provider.requests.get", return_value=mock_resp):
            chain = p.get_options_chain("SPY", OptionsDataRequirements())

        self.assertEqual(chain.underlying, "SPY")
        self.assertEqual(len(chain.contracts), 4)
        self.assertTrue(FreshnessState.is_live(chain.freshness_state))

    def test_delayed_response_sets_delayed_freshness(self):
        from app.models.market_data_contracts import FreshnessState, OptionsDataRequirements
        from app.providers.marketdata_provider import MarketDataProvider

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = _make_marketdata_response(n_calls=2, n_puts=1, delayed=True)

        p = MarketDataProvider(api_key="test_key_123")
        with patch("app.providers.marketdata_provider.requests.get", return_value=mock_resp):
            chain = p.get_options_chain("SPY", OptionsDataRequirements())

        self.assertEqual(chain.freshness_state, FreshnessState.DELAYED)
        self.assertFalse(chain.is_live)
        self.assertFalse(FreshnessState.permits_entry(chain.freshness_state))

    def test_401_raises_auth_error(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.providers.marketdata_provider import MarketDataProvider
        from app.providers.options_data_provider import ProviderAuthError

        mock_resp = MagicMock()
        mock_resp.status_code = 401

        p = MarketDataProvider(api_key="bad_key")
        with patch("app.providers.marketdata_provider.requests.get", return_value=mock_resp):
            with self.assertRaises(ProviderAuthError):
                p.get_options_chain("SPY", OptionsDataRequirements())

    def test_429_raises_rate_limit_error(self):
        from app.models.market_data_contracts import OptionsDataRequirements
        from app.providers.marketdata_provider import MarketDataProvider
        from app.providers.options_data_provider import ProviderRateLimitError

        mock_resp = MagicMock()
        mock_resp.status_code = 429

        p = MarketDataProvider(api_key="test_key")
        with patch("app.providers.marketdata_provider.requests.get", return_value=mock_resp):
            with self.assertRaises(ProviderRateLimitError):
                p.get_options_chain("SPY", OptionsDataRequirements())

    def test_health_check_not_configured(self):
        from app.providers.marketdata_provider import MarketDataProvider
        import app.config as cfg
        orig = cfg.MARKETDATA_KEY
        try:
            cfg.MARKETDATA_KEY = None
            p = MarketDataProvider(api_key=None)
            result = p.health_check()
            self.assertEqual(result["status"], "NOT_CONFIGURED")
            self.assertFalse(result["configured"])
        finally:
            cfg.MARKETDATA_KEY = orig

    def test_api_key_never_in_health_check_output(self):
        from app.providers.marketdata_provider import MarketDataProvider
        secret_key = "super_secret_api_key_12345"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"s": "ok", "optionSymbol": []}
        p = MarketDataProvider(api_key=secret_key)
        with patch("app.providers.marketdata_provider.requests.get", return_value=mock_resp):
            result = p.health_check()
        result_str = json.dumps(result)
        self.assertNotIn(secret_key, result_str)


if __name__ == "__main__":
    unittest.main()
