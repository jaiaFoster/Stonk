import tempfile
import unittest
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
        finally:
            config.CALENDAR_OPPORTUNITY_DB_PATH = old_path
            config.CALENDAR_OPPORTUNITY_CACHE_ENABLED = old_enabled


if __name__ == "__main__":
    unittest.main()
