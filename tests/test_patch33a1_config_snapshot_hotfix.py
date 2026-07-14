"""ASA Patch 33A.1.1 — Config Snapshot Compatibility Hotfix Tests

Regression tests that would have caught the run-blocking AttributeError
caused by removing EARNINGS_DISCOVERY_END_DAYS_REQUESTED from config.py.

Every test here must be green before any patch removes or renames a config symbol.
"""

from __future__ import annotations

import unittest


class TestConfigCompatibilityAlias(unittest.TestCase):
    def test_requested_end_days_compatibility_alias_exists(self):
        """EARNINGS_DISCOVERY_END_DAYS_REQUESTED must exist on config (compatibility alias)."""
        from app import config
        self.assertTrue(
            hasattr(config, "EARNINGS_DISCOVERY_END_DAYS_REQUESTED"),
            "EARNINGS_DISCOVERY_END_DAYS_REQUESTED must exist on config for pipeline_helpers compatibility",
        )

    def test_requested_end_days_equals_effective(self):
        """With 33A.1 policy, requested == effective (no separate hardcoded override)."""
        from app import config
        self.assertEqual(
            config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED,
            config.EARNINGS_DISCOVERY_END_DAYS,
            "EARNINGS_DISCOVERY_END_DAYS_REQUESTED must equal EARNINGS_DISCOVERY_END_DAYS (no silent override)",
        )

    def test_effective_end_days_default_is_35(self):
        """Patch 33A.1: effective default must be 35, not 21."""
        from app import config
        self.assertEqual(
            config.EARNINGS_DISCOVERY_END_DAYS, 35,
            "EARNINGS_DISCOVERY_END_DAYS must default to 35 (Patch 33A.1)",
        )


class TestConfigSnapshotStartupSmoke(unittest.TestCase):
    """Startup smoke tests: config_snapshot() must not raise at import time."""

    def test_config_snapshot_imports_without_error(self):
        """config_snapshot('dev') must not raise AttributeError or any other exception."""
        from app.services.pipeline_helpers import config_snapshot
        try:
            snapshot = config_snapshot("dev")
        except AttributeError as exc:
            self.fail(f"config_snapshot raised AttributeError — a config symbol was removed: {exc}")
        except Exception as exc:
            self.fail(f"config_snapshot raised unexpected error: {exc}")
        self.assertIsInstance(snapshot, dict)

    def test_config_snapshot_has_effective_end_days(self):
        """Snapshot must expose the new 33A.1 earnings_discovery_end_days_effective key."""
        from app.services.pipeline_helpers import config_snapshot
        snapshot = config_snapshot("dev")
        self.assertIn(
            "earnings_discovery_end_days_effective", snapshot,
            "config_snapshot must expose earnings_discovery_end_days_effective",
        )
        self.assertEqual(snapshot["earnings_discovery_end_days_effective"], 35)

    def test_config_snapshot_has_requested_end_days(self):
        """Snapshot must still expose earnings_discovery_end_days_requested for backward compat."""
        from app.services.pipeline_helpers import config_snapshot
        snapshot = config_snapshot("dev")
        self.assertIn(
            "earnings_discovery_end_days_requested", snapshot,
            "config_snapshot must expose earnings_discovery_end_days_requested",
        )

    def test_config_snapshot_override_adjusted_is_false_when_no_override(self):
        """When effective == requested, override_adjusted must be False."""
        from app.services.pipeline_helpers import config_snapshot
        snapshot = config_snapshot("dev")
        self.assertFalse(
            snapshot.get("earnings_discovery_end_override_adjusted"),
            "earnings_discovery_end_override_adjusted must be False when no silent override exists",
        )

    def test_config_snapshot_has_lifecycle_policy_fields(self):
        """Snapshot must expose new 33A.1 lifecycle policy fields."""
        from app.services.pipeline_helpers import config_snapshot
        snapshot = config_snapshot("dev")
        self.assertIn("calendar_structure_build_start_event_dte", snapshot)
        self.assertIn("calendar_surface_start_event_dte", snapshot)
        self.assertEqual(snapshot["calendar_structure_build_start_event_dte"], 24)
        self.assertEqual(snapshot["calendar_surface_start_event_dte"], 14)

    def test_config_snapshot_resilient_when_alias_missing(self):
        """config_snapshot must not crash even if EARNINGS_DISCOVERY_END_DAYS_REQUESTED is absent."""
        import unittest.mock as mock
        from app import config
        from app.services.pipeline_helpers import config_snapshot

        original = getattr(config, "EARNINGS_DISCOVERY_END_DAYS_REQUESTED", None)
        try:
            if hasattr(config, "EARNINGS_DISCOVERY_END_DAYS_REQUESTED"):
                delattr(config, "EARNINGS_DISCOVERY_END_DAYS_REQUESTED")
            snapshot = config_snapshot("dev")
            # Must fall back to the effective value, not crash
            self.assertEqual(
                snapshot["earnings_discovery_end_days_requested"],
                config.EARNINGS_DISCOVERY_END_DAYS,
            )
        finally:
            if original is not None:
                config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED = original
            elif not hasattr(config, "EARNINGS_DISCOVERY_END_DAYS_REQUESTED"):
                config.EARNINGS_DISCOVERY_END_DAYS_REQUESTED = config.EARNINGS_DISCOVERY_END_DAYS


if __name__ == "__main__":
    unittest.main()
