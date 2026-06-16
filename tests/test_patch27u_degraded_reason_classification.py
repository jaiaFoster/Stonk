import unittest

from app.services.data_freshness_service import build_data_freshness_summary
from app.services.degraded_reason_service import classify_degraded_reason
from app.services.report_service import format_html
from app.services.run_manifest_repository import build_run_manifest


class Patch27UDegradedReasonClassificationTests(unittest.TestCase):
    def test_robinhood_approval_timeout_classifies_from_existing_pipeline_metadata(self):
        reason = classify_degraded_reason(
            {"status": "degraded", "report_quality": "SUCCESS_DEGRADED"},
            {
                "warnings": [{"step": "positions", "message": "Robinhood approval timed out waiting for device challenge."}],
                "steps": [{"key": "positions", "status": "warning", "message": "Robinhood approval timeout."}],
            },
            {},
        )

        self.assertEqual(reason["degraded_reason_code"], "ROBINHOOD_APPROVAL_TIMEOUT")
        self.assertEqual(reason["degraded_provider"], "robinhood")
        self.assertEqual(reason["reason_confidence"], "high")

    def test_broker_unavailable_classifies_from_manifest_flags(self):
        reason = classify_degraded_reason({"status": "degraded", "report_quality": "SUCCESS_DEGRADED", "has_broker_data": False})

        self.assertEqual(reason["degraded_reason_code"], "BROKER_DATA_UNAVAILABLE")
        self.assertEqual(reason["degraded_stage"], "positions")
        self.assertIn("manifest.has_broker_data=false", reason["degraded_evidence"])

    def test_market_or_options_unavailable_classifies_from_manifest_flags(self):
        reason = classify_degraded_reason({"status": "degraded", "report_quality": "SUCCESS_DEGRADED", "has_market_data": False, "has_options_data": False})

        self.assertEqual(reason["degraded_reason_code"], "MARKET_OR_OPTIONS_DATA_UNAVAILABLE")
        self.assertIn("Market and options", reason["degraded_reason_label"])

    def test_unknown_remains_when_evidence_is_insufficient(self):
        reason = classify_degraded_reason({"status": "degraded", "report_quality": "SUCCESS_DEGRADED"})

        self.assertEqual(reason["degraded_reason_code"], "UNKNOWN")
        self.assertEqual(reason["degraded_reason_label"], "Unknown degraded reason")

    def test_data_freshness_exposes_structured_reason_fields(self):
        result = build_data_freshness_summary(
            {"run_id": "complete", "status": "complete", "completed_at": "2026-06-16T12:00:00+00:00"},
            {"report_quality": "SUCCESS_COMPLETE", "report_data": {"tradier_snapshot": {"_pipeline_status": {"report_quality": "SUCCESS_COMPLETE"}}}},
            {"run_id": "degraded", "status": "degraded", "report_quality": "SUCCESS_DEGRADED", "has_broker_data": False},
        )

        self.assertEqual(result["degraded_reason_code"], "BROKER_DATA_UNAVAILABLE")
        self.assertEqual(result["latest_run_degraded_reason"], "Broker position data unavailable")

    def test_shell_uses_classified_reason_label(self):
        snapshot = {
            "_report_snapshot": {
                "freshness": {
                    "quality_label": "LATEST_RUN_DEGRADED",
                    "freshness_state": "FRESH",
                    "canonical_snapshot_preserved": True,
                    "canonical_snapshot_run_id": "complete",
                    "canonical_snapshot_quality": "SUCCESS_COMPLETE",
                    "latest_run_id": "degraded",
                    "latest_run_report_quality": "SUCCESS_DEGRADED",
                    "degraded_reason_code": "BROKER_DATA_UNAVAILABLE",
                    "latest_run_degraded_reason": "Broker position data unavailable",
                }
            }
        }
        html = format_html("payload", [], {}, [], snapshot, [], view="shell")

        self.assertIn("Reason: Broker position data unavailable", html)

    def test_new_run_manifest_stores_degraded_reason_fields(self):
        manifest = build_run_manifest(
            "run-1",
            "dev",
            "degraded",
            "SUCCESS_DEGRADED",
            {},
            {"sections_bytes": {}},
            {
                "warnings": [{"step": "positions", "message": "Robinhood approval timed out waiting for device challenge."}],
                "errors": [],
            },
            {},
            {},
            provider_fetch_count=0,
        )

        self.assertEqual(manifest["degraded_reason_code"], "ROBINHOOD_APPROVAL_TIMEOUT")
        self.assertEqual(manifest["degraded_provider"], "robinhood")


if __name__ == "__main__":
    unittest.main()
