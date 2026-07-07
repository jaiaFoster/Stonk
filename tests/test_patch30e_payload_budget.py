"""
ASA Patch 30E — Payload Budget Tests

Verifies:
  - Compact manifest is sub-50KB with realistic data
  - summary_json col targets sub-50KB (was ~1MB)
  - daily_opportunity_api still reads full blob and returns actions
  - open_positions_api still returns positions
  - API reads use include_full=True / load_summary(full=True)
  - No raw provider data in compact summary_json
"""
from __future__ import annotations

import json
import py_compile
from unittest.mock import MagicMock, patch


class TestCompile:
    def test_daily_opportunity_api_compiles(self):
        py_compile.compile("app/api/daily_opportunity_api.py", doraise=True)

    def test_open_positions_api_compiles(self):
        py_compile.compile("app/api/open_positions_api.py", doraise=True)

    def test_strategy_api_compiles(self):
        py_compile.compile("app/api/strategy_api.py", doraise=True)


# ─── Compact manifest size guard ──────────────────────────────────────────────

class TestCompactManifestSize:
    def _build_compact(self) -> dict:
        from app.services.report_snapshot_service import build_compact_manifest_summary
        return build_compact_manifest_summary(_large_fake_summary())

    def test_compact_manifest_under_50kb(self):
        result = self._build_compact()
        size = len(json.dumps(result, default=str))
        assert size < 50_000, f"Compact manifest {size} bytes >= 50KB limit"

    def test_compact_manifest_under_500kb(self):
        result = self._build_compact()
        size = len(json.dumps(result, default=str))
        assert size < 500_000, f"Compact manifest {size} bytes >= 500KB budget"

    def test_compact_has_no_raw_row_arrays(self):
        result = self._build_compact()
        serialized = json.dumps(result, default=str)
        assert '"items"' not in serialized
        assert '"rows"' not in serialized

    def test_compact_has_no_tradier_snapshot(self):
        result = self._build_compact()
        assert "tradier_snapshot" not in json.dumps(result, default=str)

    def test_compact_schema_version_2(self):
        result = self._build_compact()
        assert result["schema_version"] == 2

    def test_compact_is_much_smaller_than_source(self):
        from app.services.report_snapshot_service import build_compact_manifest_summary
        summary = _large_fake_summary()
        compact = build_compact_manifest_summary(summary)
        source_size = len(json.dumps(summary, default=str))
        compact_size = len(json.dumps(compact, default=str))
        assert compact_size < source_size / 10, (
            f"Compact ({compact_size}) should be <10% of source ({source_size})"
        )


# ─── daily_opportunity_api uses full=True ─────────────────────────────────────

class TestDailyOpportunityApiFullRead:
    def test_daily_opportunity_api_calls_include_full_true(self):
        import inspect
        from app.api import daily_opportunity_api
        source = inspect.getsource(daily_opportunity_api)
        assert "include_full=True" in source, "daily_opportunity_api must use include_full=True"

    def test_daily_opportunity_api_calls_load_summary_full_true(self):
        import inspect
        from app.api import daily_opportunity_api
        source = inspect.getsource(daily_opportunity_api)
        assert "full=True" in source, "daily_opportunity_api must use load_summary(full=True)"

    def test_daily_opportunity_returns_dict_when_no_snapshot(self):
        from app.api.daily_opportunity_api import build_daily_opportunity_response
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = build_daily_opportunity_response()
        assert isinstance(result, dict)
        assert result.get("provider_calls_triggered") is False

    def test_daily_opportunity_has_actions_key(self):
        from app.api.daily_opportunity_api import build_daily_opportunity_response
        fake_snapshot = {"run_id": "run-do-001"}
        fake_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_daily_opportunity_engine": {
                        "enabled": True,
                        "actions": [
                            {"ticker": "AAPL", "action": "open", "type": "spread",
                             "source": "skew", "priority_score": 72.0, "actionability_score": 72.0}
                        ]
                    }
                }
            }
        }
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.latest_success.return_value = fake_snapshot
            instance.load_summary.return_value = fake_summary
            result = build_daily_opportunity_response()
        assert "actions" in result
        assert isinstance(result["actions"], list)


# ─── open_positions_api uses full=True ────────────────────────────────────────

class TestOpenPositionsApiFullRead:
    def test_open_positions_api_calls_include_full_true(self):
        import inspect
        from app.api import open_positions_api
        source = inspect.getsource(open_positions_api)
        assert "include_full=True" in source, "open_positions_api must use include_full=True"

    def test_open_positions_api_calls_load_summary_full_true(self):
        import inspect
        from app.api import open_positions_api
        source = inspect.getsource(open_positions_api)
        assert "full=True" in source, "open_positions_api must use load_summary(full=True)"

    def test_open_positions_returns_dict_when_no_snapshot(self):
        from app.api.open_positions_api import build_open_positions_response
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            MockRepo.return_value.latest_success.return_value = None
            result = build_open_positions_response()
        assert isinstance(result, dict)
        assert result.get("provider_calls_triggered") is False

    def test_open_positions_has_options_positions_key(self):
        from app.api.open_positions_api import build_open_positions_response
        fake_snapshot = {"run_id": "run-pos-001"}
        fake_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_open_options_positions": {
                        "options_positions": [
                            {"ticker": "NVDA", "type": "calendar", "expiration": "2026-08-15"}
                        ],
                        "has_open_verticals": False,
                        "has_open_calendars": True,
                    }
                }
            }
        }
        with patch("app.services.report_snapshot_service.ReportSnapshotRepository") as MockRepo:
            instance = MockRepo.return_value
            instance.latest_success.return_value = fake_snapshot
            instance.load_summary.return_value = fake_summary
            result = build_open_positions_response()
        assert "options_positions" in result


# ─── CAVEMAN config invariants ────────────────────────────────────────────────

class TestCavemanConfigInvariants:
    def test_forward_factor_dry_run_is_true(self):
        from app import config
        assert config.FORWARD_FACTOR_DRY_RUN is True

    def test_report_full_debug_payload_enabled_is_false(self):
        from app import config
        assert config.REPORT_FULL_DEBUG_PAYLOAD_ENABLED is False

    def test_broker_debug_raw_logs_enabled_is_false(self):
        from app import config
        assert config.BROKER_DEBUG_RAW_LOGS_ENABLED is False


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _large_fake_summary() -> dict:
    many_rows = [
        {
            "ticker": f"T{i}",
            "verdict": "PASS",
            "score": float(i),
            "details": {"x": "y" * 500, "legs": [{"strike": 100 + i, "bid": 1.0, "ask": 1.1}] * 10},
        }
        for i in range(50)
    ]
    return {
        "report_quality": "complete",
        "report_data": {
            "tradier_snapshot": {
                "_run_manifest": {
                    "run_id": "run-large-001",
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
                    "alpha_vantage": {"success": True, "configured": True},
                },
                "_strategy_results": {
                    "skew_momentum_vertical": {
                        "pass_count": 5,
                        "watch_count": 8,
                        "fail_count": 37,
                        "skipped_count": 0,
                        "items": many_rows,
                    },
                    "earnings_calendar": {
                        "pass_count": 3,
                        "watch_count": 2,
                        "fail_count": 20,
                        "skipped_count": 0,
                        "rows": many_rows[:25],
                    },
                    "forward_factor_calendar": {
                        "pass_count": 1,
                        "watch_count": 3,
                        "fail_count": 15,
                        "skipped_count": 0,
                        "items": many_rows[:10],
                    },
                },
                "_daily_opportunity_engine": {
                    "enabled": True,
                    "actions": [
                        {"ticker": "AAPL", "action": "open", "type": "spread", "source": "skew", "priority_score": 72.0},
                    ],
                },
                "_open_options_positions": {
                    "options_positions": [],
                    "has_open_verticals": False,
                    "has_open_calendars": False,
                },
                "_payload_size_profile": {
                    "sections_bytes": {
                        "payload_text": 50000,
                        "report_summary_json": 980000,
                    }
                },
            }
        },
    }
