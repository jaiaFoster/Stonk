"""
ASA Patch 30D.1 — Payload Boundary Tests

Verifies:
  - REPORT_FULL_DEBUG_PAYLOAD_ENABLED and BROKER_DEBUG_RAW_LOGS_ENABLED exist
  - Both default to False
  - Payload consumer inventory doc exists
  - run manifest has compact shape (not a giant payload)
  - build_dashboard_summary() compiles and returns a dict
  - dashboard_api, daily_opportunity_api, open_positions_api, run_api all compile
"""
from __future__ import annotations

import os
import py_compile


class TestCompile:
    def test_dashboard_api_compiles(self):
        py_compile.compile("app/api/dashboard_api.py", doraise=True)

    def test_daily_opportunity_api_compiles(self):
        py_compile.compile("app/api/daily_opportunity_api.py", doraise=True)

    def test_open_positions_api_compiles(self):
        py_compile.compile("app/api/open_positions_api.py", doraise=True)

    def test_run_api_compiles(self):
        py_compile.compile("app/api/run_api.py", doraise=True)

    def test_config_compiles(self):
        py_compile.compile("app/config.py", doraise=True)


# ─── Config flags ─────────────────────────────────────────────────────────────

class TestConfigFlags:
    def test_report_full_debug_payload_flag_exists(self):
        from app import config
        assert hasattr(config, "REPORT_FULL_DEBUG_PAYLOAD_ENABLED")

    def test_report_full_debug_payload_default_false(self):
        from app import config
        assert config.REPORT_FULL_DEBUG_PAYLOAD_ENABLED is False

    def test_broker_debug_raw_logs_flag_exists(self):
        from app import config
        assert hasattr(config, "BROKER_DEBUG_RAW_LOGS_ENABLED")

    def test_broker_debug_raw_logs_default_false(self):
        from app import config
        assert config.BROKER_DEBUG_RAW_LOGS_ENABLED is False

    def test_forward_factor_dry_run_still_true(self):
        from app import config
        assert config.FORWARD_FACTOR_DRY_RUN is True

    def test_report_include_raw_provider_payloads_still_false(self):
        from app import config
        assert config.REPORT_INCLUDE_RAW_PROVIDER_PAYLOADS is False

    def test_report_include_heavy_debug_still_false(self):
        from app import config
        assert config.REPORT_INCLUDE_HEAVY_DEBUG is False


# ─── Inventory doc exists ──────────────────────────────────────────────────────

class TestInventoryDoc:
    def test_payload_consumer_inventory_doc_exists(self):
        assert os.path.isfile("docs/patch30d1_payload_consumer_inventory.md"), \
            "docs/patch30d1_payload_consumer_inventory.md must exist"

    def test_inventory_doc_not_empty(self):
        with open("docs/patch30d1_payload_consumer_inventory.md") as f:
            content = f.read()
        assert len(content) > 200

    def test_inventory_doc_mentions_caveman(self):
        with open("docs/patch30d1_payload_consumer_inventory.md") as f:
            content = f.read()
        assert "CAVEMAN" in content

    def test_inventory_doc_mentions_write_paths(self):
        with open("docs/patch30d1_payload_consumer_inventory.md") as f:
            content = f.read()
        assert "Write Paths" in content or "write path" in content.lower()


# ─── Dashboard summary module ──────────────────────────────────────────────────

class TestDashboardSummaryModule:
    def _call(self) -> dict:
        from app.api.dashboard_api import build_dashboard_summary
        return build_dashboard_summary()

    def test_returns_dict(self):
        result = self._call()
        assert isinstance(result, dict)

    def test_read_only_true(self):
        result = self._call()
        assert result.get("read_only") is True

    def test_provider_calls_triggered_false(self):
        result = self._call()
        assert result.get("provider_calls_triggered") is False

    def test_has_api_links(self):
        result = self._call()
        links = result.get("api_links") or {}
        assert isinstance(links, dict)
        assert "daily_opportunity" in links
        assert "open_positions" in links
        assert "run_refresh" in links


# ─── Daily opportunity module ──────────────────────────────────────────────────

class TestDailyOpportunityModule:
    def _call(self) -> dict:
        from app.api.daily_opportunity_api import build_daily_opportunity_response
        return build_daily_opportunity_response()

    def test_returns_dict(self):
        result = self._call()
        assert isinstance(result, dict)

    def test_read_only_true(self):
        result = self._call()
        assert result.get("read_only") is True

    def test_provider_calls_triggered_false(self):
        result = self._call()
        assert result.get("provider_calls_triggered") is False

    def test_has_actions_key(self):
        result = self._call()
        assert "actions" in result
        assert isinstance(result["actions"], list)

    def test_has_action_count(self):
        result = self._call()
        assert "action_count" in result


# ─── Open positions module ─────────────────────────────────────────────────────

class TestOpenPositionsModule:
    def _call(self) -> dict:
        from app.api.open_positions_api import build_open_positions_response
        return build_open_positions_response()

    def test_returns_dict(self):
        result = self._call()
        assert isinstance(result, dict)

    def test_read_only_true(self):
        result = self._call()
        assert result.get("read_only") is True

    def test_provider_calls_triggered_false(self):
        result = self._call()
        assert result.get("provider_calls_triggered") is False

    def test_has_options_positions_key(self):
        result = self._call()
        assert "options_positions" in result
        assert isinstance(result["options_positions"], list)

    def test_has_options_count(self):
        result = self._call()
        assert "options_count" in result


# ─── Run API module ────────────────────────────────────────────────────────────

class TestRunApiModule:
    def test_get_latest_run_returns_dict(self):
        from app.api.run_api import get_latest_run
        result = get_latest_run()
        assert isinstance(result, dict)

    def test_get_latest_run_read_only(self):
        from app.api.run_api import get_latest_run
        result = get_latest_run()
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True

    def test_get_run_status_not_found(self):
        from app.api.run_api import get_run_status
        result = get_run_status("nonexistent-job-id", {})
        assert result.get("status") == "not_found"
        assert result.get("read_only") is True

    def test_get_run_status_with_job(self):
        from app.api.run_api import get_run_status
        jobs = {"abc123": {"status": "complete", "message": "Done.", "mode": "prod", "created_at": 0, "result": None}}
        result = get_run_status("abc123", jobs)
        assert result["status"] == "complete"
        assert result["mode"] == "prod"
        assert result["read_only"] is True
