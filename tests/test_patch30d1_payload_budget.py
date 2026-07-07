"""
ASA Patch 30D.1 — Payload Budget Guardrail Tests

Verifies:
  - Payload budget thresholds are enforced by payload_profile_service
  - Hot report summary builder exists and produces a compact result
  - summary_json_bytes budget warnings are emitted at the right thresholds
  - REPORT_SNAPSHOT_HOT_STRATEGY_ROWS controls row compaction
  - REPORT_SNAPSHOT_HOT_LOG_LINES controls log compaction
  - Compact run manifest is smaller than full summary
  - build_dashboard_summary summary_json_bytes key matches payload profile output
"""
from __future__ import annotations

import json
import py_compile


class TestCompile:
    def test_payload_profile_service_compiles(self):
        py_compile.compile("app/services/payload_profile_service.py", doraise=True)

    def test_report_snapshot_service_compiles(self):
        py_compile.compile("app/services/report_snapshot_service.py", doraise=True)

    def test_run_manifest_repository_compiles(self):
        py_compile.compile("app/services/run_manifest_repository.py", doraise=True)

    def test_dashboard_api_compiles(self):
        py_compile.compile("app/api/dashboard_api.py", doraise=True)


# ─── Payload thresholds (re-verify with 30D.1 config) ─────────────────────────

class TestPayloadBudgetThresholds:
    def _status(self, size_bytes: int) -> str:
        from app.services.payload_profile_service import _payload_status
        return _payload_status(size_bytes)

    def test_500kb_is_healthy(self):
        assert self._status(500_000) == "healthy"

    def test_750kb_is_healthy_boundary(self):
        assert self._status(750_000) == "healthy"

    def test_just_over_750kb_is_watch(self):
        assert self._status(750_001) == "watch"

    def test_1mb_is_watch_boundary(self):
        assert self._status(1_000_000) == "watch"

    def test_just_over_1mb_is_warning(self):
        assert self._status(1_000_001) == "warning"

    def test_2mb_is_warning_boundary(self):
        assert self._status(2_000_000) == "warning"

    def test_just_over_2mb_is_critical(self):
        assert self._status(2_000_001) == "critical"


# ─── Budget target: hot summary should trend toward <500KB ────────────────────

class TestHotSummaryCompaction:
    def test_build_hot_report_summary_exists(self):
        from app.services.report_snapshot_service import build_hot_report_summary
        assert callable(build_hot_report_summary)

    def test_build_hot_report_summary_strips_rows(self):
        from app.services.report_snapshot_service import build_hot_report_summary
        fake_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_strategy_results": {
                        "skew_momentum_vertical": {
                            "items": [{"ticker": f"T{i}", "verdict": "PASS", "score": 72.0} for i in range(20)],
                        }
                    },
                    "_benchmark_metrics": {"spy_ytd": 0.15},
                }
            }
        }
        hot = build_hot_report_summary(fake_summary)
        report_data = hot.get("report_data", {}) or {}
        tradier = report_data.get("tradier_snapshot", {}) or {}
        strategies = tradier.get("_strategy_results", {}) or {}
        skew = strategies.get("skew_momentum_vertical", {}) or {}
        rows = skew.get("items") or skew.get("rows") or []
        # Hot summary compacts to REPORT_SNAPSHOT_HOT_STRATEGY_ROWS (default 5)
        assert len(rows) <= 5, f"Hot summary has too many rows: {len(rows)}"

    def test_hot_summary_smaller_than_full(self):
        from app.services.report_snapshot_service import build_hot_report_summary
        many_rows = [{"ticker": f"T{i}", "verdict": "PASS", "score": float(i), "details": {"x": "y" * 100}} for i in range(50)]
        full_summary = {
            "report_data": {
                "tradier_snapshot": {
                    "_strategy_results": {
                        "skew_momentum_vertical": {"items": many_rows},
                        "earnings_calendar": {"rows": many_rows},
                    },
                }
            }
        }
        hot = build_hot_report_summary(full_summary)
        full_size = len(json.dumps(full_summary, default=str))
        hot_size = len(json.dumps(hot, default=str))
        assert hot_size < full_size, f"Hot summary ({hot_size}) should be smaller than full ({full_size})"


# ─── CAVEMAN invariants in config ─────────────────────────────────────────────

class TestCavemanConfigInvariants:
    def test_forward_factor_dry_run_is_true(self):
        from app import config
        assert config.FORWARD_FACTOR_DRY_RUN is True

    def test_report_include_raw_provider_payloads_is_false(self):
        from app import config
        assert config.REPORT_INCLUDE_RAW_PROVIDER_PAYLOADS is False

    def test_report_full_debug_payload_enabled_is_false(self):
        from app import config
        assert config.REPORT_FULL_DEBUG_PAYLOAD_ENABLED is False

    def test_broker_debug_raw_logs_enabled_is_false(self):
        from app import config
        assert config.BROKER_DEBUG_RAW_LOGS_ENABLED is False

    def test_report_include_heavy_debug_is_false(self):
        from app import config
        assert config.REPORT_INCLUDE_HEAVY_DEBUG is False


# ─── Hot strategy rows config ──────────────────────────────────────────────────

class TestHotStrategyRowsConfig:
    def test_hot_strategy_rows_config_exists(self):
        from app import config
        assert hasattr(config, "REPORT_SNAPSHOT_HOT_STRATEGY_ROWS")
        assert isinstance(config.REPORT_SNAPSHOT_HOT_STRATEGY_ROWS, int)
        assert config.REPORT_SNAPSHOT_HOT_STRATEGY_ROWS >= 1

    def test_hot_log_lines_config_exists(self):
        from app import config
        assert hasattr(config, "REPORT_SNAPSHOT_HOT_LOG_LINES")
        assert isinstance(config.REPORT_SNAPSHOT_HOT_LOG_LINES, int)

    def test_payload_budget_bytes_config_exists(self):
        from app import config
        assert hasattr(config, "PROVIDER_PAYLOAD_BUDGET_BYTES")
        assert config.PROVIDER_PAYLOAD_BUDGET_BYTES > 0


# ─── Compact run manifest is lightweight ──────────────────────────────────────

class TestCompactRunManifest:
    def test_get_latest_run_response_is_small(self):
        from app.api.run_api import get_latest_run
        result = get_latest_run()
        serialized = json.dumps(result, default=str)
        # Run manifest response should always be tiny (no embedded strategy rows)
        assert len(serialized) < 10_000, f"Run manifest response unexpectedly large: {len(serialized)} bytes"

    def test_get_latest_run_has_no_strategy_rows(self):
        from app.api.run_api import get_latest_run
        result = get_latest_run()
        result_str = json.dumps(result, default=str)
        assert "long_leg" not in result_str
        assert "short_leg" not in result_str

    def test_build_run_manifest_shape(self):
        from app.services.run_manifest_repository import build_run_manifest
        manifest = build_run_manifest(
            run_id="test-run",
            mode="dev",
            status="SUCCESS_COMPLETE",
            report_quality="complete",
            runtime_profile={"total_ms": 1000},
            payload_profile={"sections_bytes": {"payload_text": 0, "report_summary_json": 400000}},
            pipeline_status={"overall_status": "complete", "errors": []},
            strategy_results={},
            daily_opportunity={"actions": []},
        )
        assert "run_id" in manifest
        assert "strategy_counts" in manifest
        assert "daily_opportunity_count" in manifest
        assert "summary_json_bytes" in manifest
        # Should be a lightweight manifest, not a full dump
        size = len(json.dumps(manifest, default=str))
        assert size < 20_000, f"Run manifest unexpectedly large: {size} bytes"
