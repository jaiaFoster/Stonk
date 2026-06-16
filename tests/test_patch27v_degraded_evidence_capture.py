import unittest

from app.services.degraded_reason_service import build_degraded_evidence_fields
from app.services.run_manifest_repository import build_run_manifest


class Patch27VDegradedEvidenceCaptureTests(unittest.TestCase):
    def test_broker_auth_degraded_manifest_records_structured_evidence(self):
        manifest = build_run_manifest(
            "run-auth",
            "dev",
            "degraded",
            "SUCCESS_DEGRADED",
            {},
            {"sections_bytes": {}},
            {
                "warnings": [{"step": "positions", "message": "Robinhood approval timed out waiting for device challenge."}],
                "steps": [{"key": "positions", "status": "warning", "message": "Robinhood approval timeout."}],
            },
            {},
            {},
            provider_fetch_count=2,
            provider_status={
                "robinhood": {
                    "status": "auth_required",
                    "auth_required": True,
                    "error": "approval timed out",
                }
            },
        )

        self.assertEqual(manifest["degraded_reason_code"], "ROBINHOOD_APPROVAL_TIMEOUT")
        self.assertEqual(manifest["degraded_stage"], "positions")
        self.assertEqual(manifest["degraded_provider"], "robinhood")
        self.assertEqual(manifest["degraded_auth_status"], "auth_required")
        self.assertTrue(manifest["degraded_timeout"])
        self.assertIn("robinhood_approval_timeout", manifest["degraded_timeout_reason"])
        self.assertGreater(len(manifest["degraded_evidence"]), 0)

    def test_timeout_degraded_manifest_records_timeout_stage(self):
        manifest = build_run_manifest(
            "run-timeout",
            "dev",
            "timeout",
            "SUCCESS_DEGRADED",
            {},
            {"sections_bytes": {}},
            {
                "timeout_reason": "run_stale_timeout",
                "failed_stage": "format_payload",
                "warnings": [{"message": "Run timeout recovered from stale lock."}],
            },
            {},
            {},
            provider_fetch_count=2,
            provider_status={},
        )

        self.assertEqual(manifest["degraded_reason_code"], "RUN_TIMEOUT_OR_STALE_LOCK")
        self.assertEqual(manifest["degraded_stage"], "format_payload")
        self.assertEqual(manifest["degraded_timeout_reason"], "run_stale_timeout")
        self.assertTrue(manifest["degraded_timeout"])

    def test_provider_partial_failure_records_provider_evidence(self):
        manifest = build_run_manifest(
            "run-provider",
            "dev",
            "degraded",
            "SUCCESS_DEGRADED",
            {},
            {"sections_bytes": {}},
            {
                "warnings": [{"step": "calendar_spread_scan", "message": "Tradier provider partial failure: option chain unavailable."}],
                "steps": [{"key": "calendar_spread_scan", "status": "warning", "message": "Tradier partial provider error."}],
            },
            {"earnings_calendar": {"watch_count": 1}},
            {},
            provider_fetch_count=2,
            provider_status={"tradier": {"status": "partial", "error": "option chain unavailable"}},
        )

        self.assertEqual(manifest["degraded_reason_code"], "PROVIDER_PARTIAL_FAILURE")
        self.assertEqual(manifest["degraded_stage"], "calendar_spread_scan")
        self.assertEqual(manifest["degraded_provider"], "tradier")
        self.assertIn("tradier: option chain unavailable", manifest["degraded_provider_errors"])

    def test_missing_evidence_remains_unknown(self):
        manifest = build_run_manifest(
            "run-unknown",
            "dev",
            "degraded",
            "SUCCESS_DEGRADED",
            {},
            {"sections_bytes": {}},
            {},
            {"earnings_calendar": {"watch_count": 1}},
            {},
            provider_fetch_count=1,
            provider_status={},
        )

        self.assertEqual(manifest["degraded_reason_code"], "UNKNOWN")
        self.assertEqual(manifest["degraded_evidence"], [])

    def test_evidence_builder_is_provider_free_metadata_only(self):
        fields = build_degraded_evidence_fields(
            status="degraded",
            report_quality="SUCCESS_DEGRADED",
            pipeline_status={"warnings": [{"message": "Finnhub provider unavailable."}]},
            provider_status={"finnhub": {"status": "failed", "error": "403"}},
        )

        self.assertEqual(fields["degraded_provider"], "finnhub")
        self.assertFalse(fields.get("provider_calls_triggered", False))
        self.assertIn("finnhub: 403", fields["degraded_provider_errors"])


if __name__ == "__main__":
    unittest.main()
