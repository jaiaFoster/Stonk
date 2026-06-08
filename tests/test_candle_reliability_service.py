import unittest
from datetime import date, timedelta

from app.services.candle_service import build_candle_quality


class CandleReliabilityServiceTests(unittest.TestCase):
    def test_quality_records_provider_fallback_and_high_confidence(self):
        start = date.today() - timedelta(days=249)
        bars = [{"date": (start + timedelta(days=i)).isoformat(), "close": 100 + i} for i in range(250)]
        quality = build_candle_quality(
            "NVDA",
            "tradier",
            bars,
            ["finnhub", "tradier"],
            [{"provider": "finnhub", "error": "HTTP 403"}],
        )

        self.assertEqual(quality["selected_provider"], "tradier")
        self.assertEqual(quality["providers_attempted"], ["finnhub", "tradier"])
        self.assertEqual(quality["confidence"], "high")
        self.assertEqual(quality["status"], "ok")


if __name__ == "__main__":
    unittest.main()
