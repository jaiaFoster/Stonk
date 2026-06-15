import unittest

from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine
from app.services.report_service import format_html
from app.services.risk_severity_service import classify_risk_severity, is_actionable_risk


class Patch27TRiskSeverityTinyCleanupTests(unittest.TestCase):
    def test_tiny_position_is_cleanup_not_actionable_risk(self):
        row = {
            "ticker": "LEFT",
            "action": "AVOID ADDING / REDUCE RISK",
            "position_value": 20,
            "allocation_pct": 0.1,
            "risks": ["Weak trend."],
        }
        self.assertEqual(classify_risk_severity(row), "CLEANUP")
        self.assertFalse(is_actionable_risk(row))

    def test_missing_metrics_is_data_incomplete_not_risk(self):
        row = {
            "ticker": "MU",
            "action": "WATCH / REVIEW",
            "position_value": 500,
            "allocation_pct": 2,
            "risks": ["Market metrics were not evaluated in this dev run. Reason: skipped by dev data cap."],
        }
        self.assertEqual(classify_risk_severity(row), "DATA_INCOMPLETE")

    def test_meaningful_reduce_and_urgent_rows_are_actionable(self):
        material = {"action": "REDUCE RISK", "position_value": 500, "allocation_pct": 3}
        urgent = {"action": "URGENT CUT REVIEW", "position_value": 500, "allocation_pct": 3}
        self.assertEqual(classify_risk_severity(material), "MATERIAL_REVIEW")
        self.assertEqual(classify_risk_severity(urgent), "URGENT_RISK")
        self.assertTrue(is_actionable_risk(material))
        self.assertTrue(is_actionable_risk(urgent))

    def test_daily_opportunity_excludes_tiny_cleanup_risk(self):
        result = build_daily_opportunity_engine(
            {}, {}, {},
            [{
                "ticker": "LEFT",
                "action": "AVOID ADDING / REDUCE RISK",
                "score": 20,
                "position_value": 20,
                "allocation_pct": 0.1,
                "risks": ["Weak trend."],
            }],
            log_print=lambda message: None,
        )
        self.assertEqual(result["actions"], [])

    def test_daily_opportunity_excludes_tiny_gap_cleanup_risk(self):
        result = build_daily_opportunity_engine(
            {}, {},
            {"suggestions": [{
                "ticker": "LEFT",
                "category": "AVOID ADDING / REDUCE RISK",
                "score": 90,
                "position_value": 20,
                "allocation_pct": 0.1,
                "risks": ["Weak trend."],
            }]},
            [],
            log_print=lambda message: None,
        )
        self.assertEqual(result["actions"], [])

    def test_shell_counts_only_true_risk_full_detail_preserves_cleanup(self):
        recommendations = [
            {
                "ticker": "LEFT", "action": "AVOID ADDING / REDUCE RISK", "score": 20,
                "position_value": 20, "allocation_pct": 0.1, "risks": ["Weak trend."],
            },
            {
                "ticker": "MU", "action": "WATCH / REVIEW", "score": 50,
                "position_value": 600, "allocation_pct": 2,
                "risks": ["Market metrics were not evaluated in this dev run. Reason: skipped by dev data cap."],
            },
            {
                "ticker": "SOFI", "action": "REDUCE RISK", "score": 25,
                "position_value": 900, "allocation_pct": 4, "risks": ["Thesis weakened."],
            },
        ]
        snapshot = {"_pipeline_status": {"run_mode": "dev", "config_snapshot": {}}}
        shell = format_html("debug", [], {}, recommendations, snapshot, [], view="shell")
        full = format_html("debug", [], {}, recommendations, snapshot, [], view="full")
        shell_risk = shell[shell.index('id="risk-review"'):shell.index('id="strategy-summary"')]
        full_risk = full[full.index('id="risk-review"'):full.index('id="blocked-calendars"')]
        self.assertIn("SOFI", shell_risk)
        self.assertNotIn("LEFT", shell_risk)
        self.assertIn("Cleanup/tracking size: 1", shell_risk)
        self.assertIn("Data incomplete: 1", shell_risk)
        self.assertIn("CLEANUP", full_risk)
        self.assertIn("DATA_INCOMPLETE", full_risk)
        self.assertIn("MATERIAL_REVIEW", full_risk)


if __name__ == "__main__":
    unittest.main()
