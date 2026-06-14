import json
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app import config
from app.models.market_data_models import SKIPPED_DEV_CAP
from app.services.data_coverage_service import build_data_coverage
from app.services.data_requirement_planner import DataRequirementPlanner
from app.services.data_requirement_service import skew_vertical_requirement, stock_momentum_requirement
from app.services.derived_market_metrics_service import compute_derived_metrics
from app.services.market_data_hub_service import MarketDataHub
from app.services.market_data_repository import MarketDataRepository
from app.services.report_snapshot_service import ReportSnapshotRepository
from app.services.run_data_context_service import create_run_data_context
from app.services.shared_market_metrics_service import build_canonical_market_metrics
from app.services.data_state_message_service import data_state_message


class FakeTradier:
    is_configured = True

    def __init__(self):
        self.quote_calls = 0
        self.chain_calls = 0

    def get_quotes(self, tickers):
        self.quote_calls += 1
        return {tickers[0]: {"last": 100}}

    def get_expirations(self, ticker):
        return [(date.today() + timedelta(days=21)).isoformat()]

    def get_option_chain(self, ticker, expiration, greeks=True):
        self.chain_calls += 1
        return [{"symbol": ticker + "C", "strike": 100}]


class SharedMarketDataFoundationTests(unittest.TestCase):
    def test_repository_creates_schema_wal_and_reuses_fresh_record(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "market.sqlite3")
            repo = MarketDataRepository(path)
            repo.put("NVDA", "quote", {"last": 100}, "tradier", 900)
            self.assertEqual(repo.get("NVDA", "quote").payload["last"], 100)
            with sqlite3.connect(path) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            self.assertIn("equity_daily_candles", names)
            self.assertIn("option_chain_snapshots", names)
            self.assertEqual(mode.lower(), "wal")

    def test_repository_rejects_expired_record(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = MarketDataRepository(str(Path(temp) / "market.sqlite3"))
            repo.put("NVDA", "quote", {"last": 100}, "tradier", -1)
            self.assertIsNone(repo.get("NVDA", "quote"))
            self.assertFalse(repo.get("NVDA", "quote", allow_stale=True).fresh)

    def test_hub_reuses_run_context_then_sqlite_without_provider_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = MarketDataRepository(str(Path(temp) / "market.sqlite3"))
            provider = FakeTradier()
            context = create_run_data_context("dev")
            hub = MarketDataHub(context, repository=repo, provider=provider)
            self.assertEqual(hub.get_quote("NVDA")["payload"]["last"], 100)
            self.assertEqual(hub.get_quote("NVDA")["payload"]["last"], 100)
            self.assertEqual(provider.quote_calls, 1)
            hub2 = MarketDataHub(create_run_data_context("dev"), repository=repo, provider=provider)
            self.assertEqual(hub2.get_quote("NVDA")["payload"]["last"], 100)
            self.assertEqual(provider.quote_calls, 1)

    def test_force_refresh_bypasses_run_and_sqlite_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeTradier()
            hub = MarketDataHub(
                create_run_data_context("dev"),
                repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")),
                provider=provider,
            )
            hub.get_quote("NVDA")
            hub.get_quote("NVDA", force_refresh=True)
            self.assertEqual(provider.quote_calls, 2)

    def test_equivalent_default_candle_requests_share_run_key(self):
        calls = []

        def candles(ticker, log_print=None):
            calls.append(ticker)
            return {"bars": [{"date": "2030-01-01", "close": 100}] * 240}

        with tempfile.TemporaryDirectory() as temp:
            hub = MarketDataHub(
                create_run_data_context("dev"),
                repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")),
                candle_fetcher=candles,
            )
            hub.get_daily_candles("CCL")
            hub.get_daily_candles("CCL", min_bars=240, interval="daily")
            self.assertEqual(calls, ["CCL"])

    def test_option_chain_equivalent_and_narrower_requests_reuse_broad_record(self):
        with tempfile.TemporaryDirectory() as temp:
            provider = FakeTradier()
            hub = MarketDataHub(create_run_data_context("dev"), repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")), provider=provider)
            hub.get_options_chain("ALGN", min_dte=7, max_dte=45, expirations=3)
            hub.get_options_chain("ALGN", min_dte=14, max_dte=30, expirations=2)
            self.assertEqual(provider.chain_calls, 1)

    def test_option_chain_fetches_expirations_inside_requested_dte_range(self):
        class MultiExpiration(FakeTradier):
            def get_expirations(self, ticker):
                return [(date.today() + timedelta(days=dte)).isoformat() for dte in (7, 55, 90, 120)]
        with tempfile.TemporaryDirectory() as temp:
            provider = MultiExpiration()
            hub = MarketDataHub(create_run_data_context("dev"), repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")), provider=provider)
            record = hub.get_options_chain("SPY", min_dte=50, max_dte=105, expirations=6)
            payload = record["payload"]
            self.assertEqual(len(payload["expirations"]), 2)
            self.assertEqual(provider.chain_calls, 2)

    def test_broad_merged_chain_request_samples_full_dte_range(self):
        class ManyExpirations(FakeTradier):
            def get_expirations(self, ticker):
                return [(date.today() + timedelta(days=dte)).isoformat() for dte in range(7, 106, 7)]
        with tempfile.TemporaryDirectory() as temp:
            provider = ManyExpirations()
            hub = MarketDataHub(create_run_data_context("dev"), repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")), provider=provider)
            payload = hub.get_options_chain("SPY", min_dte=7, max_dte=105, expirations=6)["payload"]
            dtes = [(date.fromisoformat(value) - date.today()).days for value in payload["expirations"]]
            self.assertLessEqual(min(dtes), 14)
            self.assertGreaterEqual(max(dtes), 98)

    def test_options_chain_set_returns_normalized_multi_expiration_shape(self):
        class PairExpirations(FakeTradier):
            def get_expirations(self, ticker):
                return [(date.today() + timedelta(days=dte)).isoformat() for dte in (60, 90)]
        with tempfile.TemporaryDirectory() as temp:
            hub = MarketDataHub(create_run_data_context("dev"), repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")), provider=PairExpirations())
            payload = hub.get_options_chain_set("SPY", min_dte=50, max_dte=105, max_expirations=6)["payload"]
            self.assertEqual(payload["ticker"], "SPY")
            self.assertEqual(len(payload["expirations"]), 2)
            self.assertEqual(len(payload["chains"]), 2)
            self.assertEqual(set(payload["chains_by_expiration"]), set(payload["expirations"]))

    def test_options_chain_set_reuses_broad_run_and_persistent_cache(self):
        class ManyExpirations(FakeTradier):
            def get_expirations(self, ticker):
                return [(date.today() + timedelta(days=dte)).isoformat() for dte in (45, 55, 65, 85, 95, 110)]
        with tempfile.TemporaryDirectory() as temp:
            repo = MarketDataRepository(str(Path(temp) / "market.sqlite3"))
            provider = ManyExpirations()
            hub = MarketDataHub(create_run_data_context("dev"), repository=repo, provider=provider)
            hub.get_options_chain_set("SPY", min_dte=40, max_dte=120, max_expirations=6)
            hub.get_options_chain_set("SPY", min_dte=50, max_dte=105, max_expirations=4)
            self.assertEqual(provider.chain_calls, 6)
            second_run = MarketDataHub(create_run_data_context("dev"), repository=repo, provider=provider)
            second_run.get_options_chain_set("SPY", min_dte=50, max_dte=105, max_expirations=4)
            self.assertEqual(provider.chain_calls, 6)

    def test_short_dated_ordinary_chain_cannot_satisfy_ff_chain_set(self):
        class MixedExpirations(FakeTradier):
            def get_expirations(self, ticker):
                return [(date.today() + timedelta(days=dte)).isoformat() for dte in (14, 60, 90)]
        with tempfile.TemporaryDirectory() as temp:
            provider = MixedExpirations()
            hub = MarketDataHub(create_run_data_context("dev"), repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")), provider=provider)
            hub.get_options_chain("SPY", min_dte=7, max_dte=21, expirations=1)
            payload = hub.get_options_chain_set("SPY", min_dte=50, max_dte=105, max_expirations=6)["payload"]
            self.assertEqual(provider.chain_calls, 3)
            self.assertEqual(len(payload["chains_by_expiration"]), 2)

    def test_canonical_metrics_map_contains_shared_price_trend_and_liquidity(self):
        bars = [{"date": f"2025-{(i // 28) % 12 + 1:02d}-{i % 28 + 1:02d}", "close": 100 + i, "volume": 1000000 + i} for i in range(260)]
        with tempfile.TemporaryDirectory() as temp:
            hub = MarketDataHub(
                create_run_data_context("dev"),
                repository=MarketDataRepository(str(Path(temp) / "market.sqlite3")),
                provider=FakeTradier(),
                candle_fetcher=lambda ticker, log_print=None: {"provider": "tradier", "bars": bars, "quality": {"confidence": "high"}},
            )
            metrics = build_canonical_market_metrics(hub, ["NVDA"], {"by_ticker": {"NVDA": {"state": "APPROVED"}}})["NVDA"]
            self.assertTrue(metrics["required_market_data_complete"])
            self.assertEqual(metrics["current_price"], 100)
            self.assertIsNotNone(metrics["return_3m_pct"])
            self.assertIsNotNone(metrics["sma_200"])
            self.assertIsNotNone(metrics["avg_volume_30d"])

    def test_data_state_messages_are_provider_neutral(self):
        self.assertIn("dev data cap", data_state_message("SKIPPED_DEV_CAP"))
        self.assertIn("provider budget", data_state_message("SKIPPED_PROVIDER_BUDGET"))
        self.assertNotIn("Finnhub", data_state_message("MISSING_PROVIDER_FAILED"))

    def test_hub_suppresses_repeated_provider_failure(self):
        class Broken(FakeTradier):
            def get_quotes(self, tickers):
                self.quote_calls += 1
                raise RuntimeError("provider down")
        with tempfile.TemporaryDirectory() as temp:
            repo = MarketDataRepository(str(Path(temp) / "market.sqlite3"))
            provider = Broken()
            hub = MarketDataHub(create_run_data_context(), repository=repo, provider=provider)
            self.assertIsNone(hub.get_quote("ORCL"))
            self.assertIsNone(hub.get_quote("ORCL"))
            self.assertEqual(provider.quote_calls, 1)

    def test_planner_merges_requirements_and_marks_dev_cap(self):
        plan = DataRequirementPlanner("dev", dev_ticker_cap=1).merge([
            stock_momentum_requirement(["NVDA", "ORCL"]),
            skew_vertical_requirement(["NVDA", "GOOGL"]),
        ])
        self.assertEqual(plan["ticker_count"], 3)
        self.assertEqual(plan["allowed_tickers"], ["NVDA"])
        self.assertEqual(plan["by_ticker"]["ORCL"]["state"], SKIPPED_DEV_CAP)
        self.assertIn("options_chain", plan["by_ticker"]["NVDA"]["data_types"])

    def test_planner_consolidates_overlapping_strategy_requirements_per_ticker(self):
        plan = DataRequirementPlanner("prod").merge([
            stock_momentum_requirement(["NVDA"]),
            skew_vertical_requirement(["NVDA"]),
        ])
        self.assertEqual(len(plan["approved_requirements"]), 1)
        merged = plan["approved_requirements"][0]
        self.assertTrue(merged["needs_options_chain"])
        self.assertIn("momentum_3m", merged["required_derived_metrics"])

    def test_shared_metrics_compute_momentum_sma_volume_volatility(self):
        bars = [{"close": 100 + i, "volume": 1000 + i} for i in range(260)]
        result = compute_derived_metrics(bars, bars)
        self.assertIsNotNone(result["metrics"]["momentum_3m"])
        self.assertIsNotNone(result["metrics"]["momentum_6m"])
        self.assertIsNotNone(result["metrics"]["sma_50"])
        self.assertIsNotNone(result["metrics"]["sma_200"])
        self.assertIsNotNone(result["metrics"]["average_volume_30d"])
        self.assertIsNotNone(result["metrics"]["realized_volatility_30d"])
        self.assertEqual(result["metrics"]["relative_strength_vs_QQQ"], 0)

    def test_shared_metrics_explain_insufficient_bars(self):
        result = compute_derived_metrics([{"close": 100, "volume": 1}])
        self.assertIn("Insufficient bars", result["reason"])
        self.assertIsNone(result["metrics"]["sma_50"])

    def test_coverage_tracks_sources_states_and_strategy(self):
        context = create_run_data_context("dev")
        context.audit("NVDA", "candles", "provider", state="COMPLETE", strategy_id="skew_momentum_vertical")
        context.audit("ORCL", "requirements", "skipped", state="SKIPPED_DEV_CAP", strategy_id="skew_momentum_vertical")
        coverage = build_data_coverage(context)
        self.assertEqual(coverage["states"]["SKIPPED_DEV_CAP"], 1)
        self.assertEqual(coverage["per_strategy"]["skew_momentum_vertical"]["COMPLETE"], 1)
        self.assertEqual(coverage["counters"]["provider_fetches"], 1)
        self.assertEqual(coverage["counters"]["skipped_dev_cap"], 1)
        self.assertEqual(coverage["per_strategy_summary"]["skew_momentum_vertical"]["skipped"], 1)

    def test_completed_report_snapshot_survives_and_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload", {"report_data": {"positions": []}}, {"states": {}}, {})
            latest = repo.latest_success(include_full=True)
            self.assertEqual(repo.load_payload(latest, full=True), "payload")
            self.assertEqual(latest["status"], "complete")

    def test_failed_snapshot_does_not_replace_latest_success(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload", {}, {}, {})
            repo.record_failure("run-2", "dev", {"error": "provider failed"})
            self.assertEqual(repo.latest_success()["run_id"], "run-1")

    def test_snapshot_schema_mismatch_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload", {}, {}, {})
            with sqlite3.connect(repo.db_path) as conn:
                conn.execute("UPDATE report_snapshots SET schema_version=999")
            self.assertIsNone(repo.latest_success())

    def test_corrupt_repository_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.sqlite3"
            path.write_text("not sqlite")
            repo = MarketDataRepository(str(path))
            self.assertFalse(repo.enabled)


if __name__ == "__main__":
    unittest.main()
