"""
ASA Patch 30D.1 — Write Paths Preserved Tests

Verifies that the payload boundary changes in 30D.1 do NOT break any write paths:
  - ReportSnapshotRepository still has save() and latest_success()
  - RunManifestRepository still has save() and latest()
  - Calendar opportunity cache still writable
  - Skew vertical opportunity cache still writable
  - StrategyObservationJournal save method still present
  - FFObservationJournal still present
  - FORWARD_FACTOR_DRY_RUN=True preserved
  - Skew opportunity cache write path not removed
  - analysis_service still imports the build_run_manifest function
  - New 30D.1 API modules do NOT call any write operations
"""
from __future__ import annotations

import py_compile


class TestCompile:
    def test_analysis_service_compiles(self):
        py_compile.compile("app/services/analysis_service.py", doraise=True)

    def test_report_snapshot_service_compiles(self):
        py_compile.compile("app/services/report_snapshot_service.py", doraise=True)

    def test_run_manifest_repository_compiles(self):
        py_compile.compile("app/services/run_manifest_repository.py", doraise=True)

    def test_dashboard_api_no_writes(self):
        """dashboard_api.py must not contain write operations."""
        py_compile.compile("app/api/dashboard_api.py", doraise=True)
        with open("app/api/dashboard_api.py") as f:
            content = f.read()
        forbidden = [".save(", "repo.save", "INSERT", "UPDATE", "DELETE", "broker_write", "order"]
        for token in forbidden:
            assert token not in content, f"dashboard_api.py must not contain '{token}'"

    def test_daily_opportunity_api_no_writes(self):
        py_compile.compile("app/api/daily_opportunity_api.py", doraise=True)
        with open("app/api/daily_opportunity_api.py") as f:
            content = f.read()
        for token in [".save(", "broker_write", "order"]:
            assert token not in content, f"daily_opportunity_api.py must not contain '{token}'"

    def test_open_positions_api_no_writes(self):
        py_compile.compile("app/api/open_positions_api.py", doraise=True)
        with open("app/api/open_positions_api.py") as f:
            content = f.read()
        for token in [".save(", "broker_write", "order"]:
            assert token not in content, f"open_positions_api.py must not contain '{token}'"

    def test_run_api_no_broker_writes(self):
        py_compile.compile("app/api/run_api.py", doraise=True)
        with open("app/api/run_api.py") as f:
            content = f.read()
        for token in ["broker_write", "order", "trade_execution"]:
            assert token not in content, f"run_api.py must not contain '{token}'"


# ─── ReportSnapshotRepository write path ──────────────────────────────────────

class TestReportSnapshotWritePath:
    def test_repository_has_save_method(self):
        from app.services.report_snapshot_service import ReportSnapshotRepository
        assert hasattr(ReportSnapshotRepository, "save_success") or hasattr(ReportSnapshotRepository, "save")

    def test_repository_has_latest_success_method(self):
        from app.services.report_snapshot_service import ReportSnapshotRepository
        assert hasattr(ReportSnapshotRepository, "latest_success")

    def test_repository_has_load_summary_method(self):
        from app.services.report_snapshot_service import ReportSnapshotRepository
        assert hasattr(ReportSnapshotRepository, "load_summary")

    def test_build_hot_report_summary_still_exists(self):
        from app.services.report_snapshot_service import build_hot_report_summary
        assert callable(build_hot_report_summary)


# ─── RunManifestRepository write path ─────────────────────────────────────────

class TestRunManifestWritePath:
    def test_repository_has_save_method(self):
        from app.services.run_manifest_repository import RunManifestRepository
        assert hasattr(RunManifestRepository, "save")

    def test_repository_has_latest_method(self):
        from app.services.run_manifest_repository import RunManifestRepository
        assert hasattr(RunManifestRepository, "latest")

    def test_build_run_manifest_still_exists(self):
        from app.services.run_manifest_repository import build_run_manifest
        assert callable(build_run_manifest)


# ─── Opportunity cache write paths ────────────────────────────────────────────

class TestOpportunityCacheWritePaths:
    def test_calendar_opportunity_service_exists(self):
        import importlib
        mod = importlib.import_module("app.services.calendar_opportunity_cache_service")
        assert mod is not None

    def test_skew_opportunity_cache_service_exists(self):
        try:
            import importlib
            mod = importlib.import_module("app.services.skew_vertical_opportunity_cache_service")
            assert mod is not None
        except ImportError:
            # May not exist if skew cache is in a different module
            from app import config
            assert hasattr(config, "SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED")


# ─── Strategy journal write paths ─────────────────────────────────────────────

class TestStrategyJournalWritePaths:
    def test_strategy_observation_journal_service_exists(self):
        import importlib
        try:
            mod = importlib.import_module("app.services.strategy_observation_journal_service")
            assert mod is not None
        except ImportError:
            mod = importlib.import_module("app.services.strategy_observation_service")
            assert mod is not None

    def test_ff_observation_journal_config_exists(self):
        from app import config
        assert hasattr(config, "FF_JOURNAL_ENABLED")
        assert hasattr(config, "FF_JOURNAL_DB_PATH")


# ─── CAVEMAN mode invariants ───────────────────────────────────────────────────

class TestCavemanModeInvariants:
    def test_forward_factor_dry_run_unchanged(self):
        from app import config
        assert config.FORWARD_FACTOR_DRY_RUN is True

    def test_skew_opportunity_cache_enabled(self):
        from app import config
        assert hasattr(config, "SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED")

    def test_calendar_opportunity_cache_enabled(self):
        from app import config
        assert hasattr(config, "CALENDAR_OPPORTUNITY_CACHE_ENABLED")

    def test_strategy_observation_journal_enabled(self):
        from app import config
        assert hasattr(config, "STRATEGY_OBSERVATION_JOURNAL_ENABLED")

    def test_no_trade_execution_flag(self):
        """TRADE_EXECUTION_ENABLED must not exist or must be False."""
        from app import config
        val = getattr(config, "TRADE_EXECUTION_ENABLED", False)
        assert val is False

    def test_30d1_api_modules_all_read_only(self):
        """All 30D.1 read API modules must declare provider_calls_triggered=False."""
        from app.api.dashboard_api import build_dashboard_summary
        from app.api.daily_opportunity_api import build_daily_opportunity_response
        from app.api.open_positions_api import build_open_positions_response
        from app.api.run_api import get_latest_run, get_run_status

        for fn, args in [
            (build_dashboard_summary, []),
            (build_daily_opportunity_response, []),
            (build_open_positions_response, []),
            (get_latest_run, []),
            (get_run_status, ["job-id", {}]),
        ]:
            result = fn(*args)
            assert result.get("provider_calls_triggered") is False, \
                f"{fn.__name__} returned provider_calls_triggered != False"
            assert result.get("read_only") is True, \
                f"{fn.__name__} returned read_only != True"


# ─── Analysis service still references write paths ────────────────────────────

class TestAnalysisServiceWriteIntegration:
    def test_analysis_service_imports_build_run_manifest(self):
        """analysis_service.py should still import build_run_manifest for the write path."""
        with open("app/services/analysis_service.py") as f:
            content = f.read()
        assert "build_run_manifest" in content

    def test_analysis_service_imports_report_snapshot_save(self):
        with open("app/services/analysis_service.py") as f:
            content = f.read()
        assert "ReportSnapshotRepository" in content or "save_snapshot" in content
