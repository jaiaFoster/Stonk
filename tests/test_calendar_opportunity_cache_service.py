import tempfile
import unittest
import sqlite3
from pathlib import Path

from app import config
from app.services.calendar_opportunity_cache_service import cache_calendar_opportunities


class CalendarOpportunityCacheServiceTests(unittest.TestCase):
    def test_repeated_candidate_upserts_seen_count(self):
        old_path = config.CALENDAR_OPPORTUNITY_DB_PATH
        old_enabled = config.CALENDAR_OPPORTUNITY_CACHE_ENABLED
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                config.CALENDAR_OPPORTUNITY_DB_PATH = str(Path(temp_dir) / "opportunities.sqlite3")
                config.CALENDAR_OPPORTUNITY_CACHE_ENABLED = True
                row = {
                    "ticker": "AVGO",
                    "verdict": "WATCH / REVIEW",
                    "earnings": {"earnings_date": "2026-06-11", "session_label": "AMC"},
                    "possible_spread": {
                        "strike": 300,
                        "short_expiration": "2026-06-12",
                        "long_expiration": "2026-07-17",
                        "option_type": "call",
                    },
                }
                cache_calendar_opportunities([row], run_id="one")
                result = cache_calendar_opportunities([row], run_id="two")

                self.assertEqual(result["summary"]["write_count"], 1)
                self.assertEqual(result["recent"][0]["seen_count"], 2)
                self.assertEqual(result["recent"][0]["display_state"], "CACHED_RECENT")
                self.assertIn("opportunity", result["recent"][0])
                self.assertIn("recoverability_hint", result["recent"][0])
        finally:
            config.CALENDAR_OPPORTUNITY_DB_PATH = old_path
            config.CALENDAR_OPPORTUNITY_CACHE_ENABLED = old_enabled

    def test_existing_cache_schema_migrates_without_losing_rows(self):
        old_path = config.CALENDAR_OPPORTUNITY_DB_PATH
        old_enabled = config.CALENDAR_OPPORTUNITY_CACHE_ENABLED
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                path = Path(temp_dir) / "opportunities.sqlite3"
                conn = sqlite3.connect(path)
                conn.execute(
                    """
                    CREATE TABLE calendar_opportunities (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        natural_key TEXT NOT NULL UNIQUE,
                        run_id TEXT, created_at TEXT, as_of_date TEXT, source TEXT,
                        strategy TEXT, symbol TEXT, earnings_date TEXT, earnings_session TEXT,
                        confirmed_timestamp INTEGER, trade_type TEXT, final_verdict TEXT,
                        main_blocker TEXT, score REAL, ranking_score REAL, candidate_status TEXT,
                        short_expiration TEXT, long_expiration TEXT, strike REAL, option_type TEXT,
                        estimated_debit REAL, max_risk REAL, max_profit REAL, reward_risk REAL,
                        liquidity_status TEXT, candle_provider TEXT, candle_quality TEXT,
                        backtest_status TEXT, payload_json TEXT, first_seen_at TEXT,
                        last_seen_at TEXT, seen_count INTEGER NOT NULL DEFAULT 1
                    )
                    """
                )
                conn.execute(
                    "INSERT INTO calendar_opportunities (natural_key, symbol, final_verdict, seen_count) VALUES (?, ?, ?, ?)",
                    ("OLD|ROW", "OLD", "WATCH / REVIEW", 3),
                )
                conn.commit()
                conn.close()

                config.CALENDAR_OPPORTUNITY_DB_PATH = str(path)
                config.CALENDAR_OPPORTUNITY_CACHE_ENABLED = True
                result = cache_calendar_opportunities([], run_id="migration")

                self.assertEqual(result["recent"][0]["symbol"], "OLD")
                self.assertEqual(result["recent"][0]["seen_count"], 3)
                conn = sqlite3.connect(path)
                columns = {row[1] for row in conn.execute("PRAGMA table_info(calendar_opportunities)").fetchall()}
                conn.close()
                self.assertIn("display_state", columns)
                self.assertIn("recoverability_hint", columns)
        finally:
            config.CALENDAR_OPPORTUNITY_DB_PATH = old_path
            config.CALENDAR_OPPORTUNITY_CACHE_ENABLED = old_enabled


if __name__ == "__main__":
    unittest.main()
