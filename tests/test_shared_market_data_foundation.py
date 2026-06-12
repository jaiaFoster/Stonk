import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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


class FakeTradier:
    is_configured = True

    def __init__(self):
        self.quote_calls = 0

    def get_quotes(self, tickers):
        self.quote_calls += 1
        return {tickers[0]: {"last": 100}}

    def get_expirations(self, ticker):
        return ["2030-01-18"]

    def get_option_chain(self, ticker, expiration, greeks=True):
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

    def test_completed_report_snapshot_survives_and_loads(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = ReportSnapshotRepository(str(Path(temp) / "reports.sqlite3"))
            repo.save_success("run-1", "dev", "payload", {"report_data": {"positions": []}}, {"states": {}}, {})
            latest = repo.latest_success()
            self.assertEqual(json.loads(latest["payload_json"]), "payload")
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
