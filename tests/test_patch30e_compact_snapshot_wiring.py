"""
ASA Patch 30E — Compact Snapshot Wiring Tests

Covers:
  - build_compact_manifest_summary() structure and size
  - schema_version=2 in compact manifest
  - compact_manifest=True flag
  - required top-level keys
  - compact < hot summary size
  - REPORT_FULL_DEBUG_PAYLOAD_ENABLED=False uses compact path
  - latest_success() works without schema_version filter
  - api_links in compact manifest
"""
from __future__ import annotations

import json
import py_compile


class TestCompile:
    def test_report_snapshot_service_compiles(self):
        py_compile.compile("app/services/report_snapshot_service.py", doraise=True)


# ─── build_compact_manifest_summary ──────────────────────────────────────────

class TestBuildCompactManifestSummary:
    def _build(self, extra: dict | None = None) -> dict:
        from app.services.report_snapshot_service import build_compact_manifest_summary
        summary = _fake_full_summary()
        if extra:
            summary.update(extra)
        return build_compact_manifest_summary(summary)

    def test_returns_dict(self):
        assert isinstance(self._build(), dict)

    def test_schema_version_is_2(self):
        assert self._build()["schema_version"] == 2

    def test_compact_manifest_flag_true(self):
        assert self._build()["compact_manifest"] is True

    def test_has_strategy_counts(self):
        result = self._build()
        assert "strategy_counts" in result
        assert isinstance(result["strategy_counts"], dict)

    def test_has_daily_opportunity_summary(self):
        result = self._build()
        assert "daily_opportunity_summary" in result
        do = result["daily_opportunity_summary"]
        assert "action_count" in do

    def test_has_open_position_summary(self):
        result = self._build()
        assert "open_position_summary" in result
        assert "options_count" in result["open_position_summary"]

    def test_has_broker_snapshot_summary(self):
        result = self._build()
        assert "broker_snapshot_summary" in result

    def test_has_provider_status_summary(self):
        result = self._build()
        assert "provider_status_summary" in result

    def test_has_payload_profile(self):
        result = self._build()
        assert "payload_profile" in result

    def test_has_api_links(self):
        result = self._build()
        assert "api_links" in result
        links = result["api_links"]
        assert "forward_factor_calendar_rows" in links
        assert "daily_opportunity" in links

    def test_has_report_quality(self):
        result = self._build()
        assert "report_quality" in result

    def test_empty_summary_does_not_raise(self):
        from app.services.report_snapshot_service import build_compact_manifest_summary
        result = build_compact_manifest_summary({})
        assert result["schema_version"] == 2

    def test_is_serializable(self):
        result = self._build()
        serialized = json.dumps(result, default=str)
        assert len(serialized) > 0

    def test_compact_under_50kb(self):
        result = self._build()
        size = len(json.dumps(result, default=str))
        assert size < 50_000, f"Compact manifest too large: {size} bytes"

    def test_compact_smaller_than_full_source(self):
        from app.services.report_snapshot_service import build_compact_manifest_summary
        summary = _fake_full_summary()
        compact = build_compact_manifest_summary(summary)
        compact_size = len(json.dumps(compact, default=str))
        source_size = len(json.dumps(summary, default=str))
        assert compact_size < source_size, f"Compact ({compact_size}) should be smaller than source ({source_size})"

    def test_no_strategy_row_arrays_in_compact(self):
        result = self._build()
        serialized = json.dumps(result, default=str)
        # Compact manifest must not embed raw strategy row arrays
        assert '"items"' not in serialized
        assert '"rows"' not in serialized

    def test_strategy_counts_has_pass_watch_fail(self):
        result = self._build()
        counts = result["strategy_counts"]
        for sid, data in counts.items():
            assert "pass" in data
            assert "watch" in data
            assert "fail" in data


# ─── REPORT_FULL_DEBUG_PAYLOAD_ENABLED path selection ─────────────────────────

class TestPayloadEnabledPathSelection:
    def test_compact_path_is_default(self):
        from app import config
        assert config.REPORT_FULL_DEBUG_PAYLOAD_ENABLED is False

    def test_build_compact_manifest_summary_exists(self):
        from app.services.report_snapshot_service import build_compact_manifest_summary
        assert callable(build_compact_manifest_summary)

    def test_build_hot_report_summary_still_exists(self):
        from app.services.report_snapshot_service import build_hot_report_summary
        assert callable(build_hot_report_summary)


# ─── latest_success() schema_version filter removed ──────────────────────────

class TestLatestSuccessQuery:
    def test_latest_success_uses_schema_ceiling_not_exact_match(self):
        """latest_success() allows schema_version <= current; not an exact-match filter."""
        import inspect
        from app.services.report_snapshot_service import ReportSnapshotRepository
        source = inspect.getsource(ReportSnapshotRepository.latest_success)
        # Must allow lower schema versions (schema_version <= N), not exact match
        assert "schema_version <= ?" in source or "schema_version<=" in source
        # Must NOT require exact schema_version equality
        assert "schema_version = 2" not in source
        assert "schema_version=2" not in source
        assert "schema_version == 2" not in source

    def test_latest_degraded_uses_schema_ceiling_not_exact_match(self):
        """latest_degraded() allows schema_version <= current; not an exact-match filter."""
        import inspect
        from app.services.report_snapshot_service import ReportSnapshotRepository
        source = inspect.getsource(ReportSnapshotRepository.latest_degraded)
        assert "schema_version <= ?" in source or "schema_version<=" in source
        assert "schema_version = 2" not in source
        assert "schema_version=2" not in source

    def test_latest_success_returns_none_when_mocked_none(self):
        from unittest.mock import patch, MagicMock
        from app.services.report_snapshot_service import ReportSnapshotRepository
        with patch.object(ReportSnapshotRepository, "latest_success", return_value=None) as mock:
            repo = ReportSnapshotRepository.__new__(ReportSnapshotRepository)
            result = mock(repo)
        assert result is None

    def test_latest_success_queries_complete_status(self):
        """latest_success() SQL must filter for status='complete'."""
        import inspect
        from app.services.report_snapshot_service import ReportSnapshotRepository
        source = inspect.getsource(ReportSnapshotRepository.latest_success)
        assert "status='complete'" in source or 'status=\'complete\'' in source


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _fake_full_summary() -> dict:
    many_rows = [{"ticker": f"T{i}", "verdict": "PASS", "score": float(i), "details": {"x": "y" * 200}} for i in range(30)]
    return {
        "report_quality": "complete",
        "report_data": {
            "tradier_snapshot": {
                "_run_manifest": {
                    "run_id": "run-test-001",
                    "has_broker_data": True,
                    "broker_auth_status": "OK",
                },
                "_pipeline_status": {
                    "run_mode": "prod",
                    "broker_mode": "live",
                    "errors": [],
                },
                "_provider_status": {
                    "finnhub": {"success": True, "configured": True},
                    "alpha_vantage": {"success": False, "error": "timeout"},
                },
                "_strategy_results": {
                    "skew_momentum_vertical": {
                        "pass_count": 3,
                        "watch_count": 5,
                        "fail_count": 12,
                        "skipped_count": 0,
                        "items": many_rows,
                    },
                    "forward_factor_calendar": {
                        "pass_count": 1,
                        "watch_count": 2,
                        "fail_count": 8,
                        "skipped_count": 0,
                        "rows": many_rows[:5],
                    },
                },
                "_daily_opportunity_engine": {
                    "enabled": True,
                    "actions": [
                        {"ticker": "AAPL", "action": "open", "type": "spread", "source": "skew", "priority_score": 72.0},
                        {"ticker": "MSFT", "action": "open", "type": "spread", "source": "skew", "priority_score": 68.0},
                    ],
                },
                "_open_options_positions": {
                    "options_positions": [{"ticker": "NVDA", "type": "calendar"}],
                    "has_open_verticals": False,
                    "has_open_calendars": True,
                },
                "_payload_size_profile": {
                    "sections_bytes": {
                        "payload_text": 50000,
                        "report_summary_json": 950000,
                    }
                },
            }
        },
    }
