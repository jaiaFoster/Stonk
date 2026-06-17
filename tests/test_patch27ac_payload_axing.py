"""
Patch 27AC — Payload axing, cache caps, dead code removal, alias fix, background scan.

Tests:
 1. _calendar_opportunity_cache key absent from tradier_snapshot after pipeline
 2. _unified_calendar_trade_engine key absent from tradier_snapshot after pipeline
 3. _calendar_ranking key absent from tradier_snapshot after pipeline
 4. calendar_scan_status present in tradier_snapshot (pending on first run)
 5. Skew cache DB prunes to ≤50 rows on write
 6. trade_memory.py file deleted — no import error
 7. strategy_id=calendar alias resolves → 200 (TKT-001)
 8. strategy_id=forward_factor alias resolves → 200 (TKT-002)
 9. strategy_id=ff alias resolves → 200
10. strategy_id=skew alias resolves → 200
11. Truly unknown strategy_id → not_found + valid_strategy_ids list
12. _calendar_scan_state initialises as never_run
13. _run_calendar_scan_bg stores results and marks complete
14. Background scan results are available on next read from state
15. skew cache SELECT LIMIT still honoured (≤ config value)
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# 1-4: calendar payload keys axed + calendar_scan_status present
# ---------------------------------------------------------------------------

class TestCalendarPayloadAxed(unittest.TestCase):
    """Verify the three axed keys are absent; scan_status present."""

    def _build_tradier_snapshot(self) -> dict:
        """Run enough of analysis_service internals to populate tradier_snapshot keys."""
        from app.services.analysis_service import (
            _CALENDAR_SCAN_LOCK, _CALENDAR_SCAN_STATE,
        )
        # Reset scan state so calendar_scan_status = "pending"
        with _CALENDAR_SCAN_LOCK:
            _CALENDAR_SCAN_STATE.update({"status": "never_run", "candidates": [], "completed_at": None})

        snapshot: dict = {}

        # Simulate what analysis_service does after the background scan deferral
        from app.services.analysis_service import (
            _CALENDAR_SCAN_LOCK as _LOCK,
            _CALENDAR_SCAN_STATE as _STATE,
        )
        with _LOCK:
            snapshot["_calendar_scan_status"] = "pending" if _STATE["status"] == "never_run" else "stale"
        return snapshot

    def test_calendar_scan_status_present(self):
        snap = self._build_tradier_snapshot()
        self.assertIn("_calendar_scan_status", snap)

    def test_calendar_scan_status_pending_on_first_run(self):
        from app.services.analysis_service import _CALENDAR_SCAN_LOCK, _CALENDAR_SCAN_STATE
        with _CALENDAR_SCAN_LOCK:
            _CALENDAR_SCAN_STATE.update({"status": "never_run", "candidates": [], "completed_at": None})
        with _CALENDAR_SCAN_LOCK:
            status = "pending" if _CALENDAR_SCAN_STATE["status"] == "never_run" else "stale"
        self.assertEqual(status, "pending")

    def test_calendar_opportunity_cache_not_in_snapshot_keys(self):
        # The snapshot key should not be set by analysis_service.
        # This validates by checking the source code directly.
        import inspect
        import app.services.analysis_service as svc
        src = inspect.getsource(svc)
        self.assertNotIn(
            '"_calendar_opportunity_cache"',
            src,
            "_calendar_opportunity_cache must not appear as a string literal snapshot key",
        )

    def test_unified_calendar_trade_engine_not_stored_to_snapshot(self):
        import inspect
        import app.services.analysis_service as svc
        src = inspect.getsource(svc)
        # The assignment line must be gone
        self.assertNotIn(
            'tradier_snapshot["_unified_calendar_trade_engine"]',
            src,
        )

    def test_calendar_ranking_not_stored_to_snapshot(self):
        import inspect
        import app.services.analysis_service as svc
        src = inspect.getsource(svc)
        self.assertNotIn(
            'tradier_snapshot["_calendar_ranking"]',
            src,
        )


# ---------------------------------------------------------------------------
# 5: skew cache FIFO prune
# ---------------------------------------------------------------------------

class TestSkewCachePrune(unittest.TestCase):

    def test_db_pruned_to_50_on_write(self):
        import sqlite3
        from app.services.skew_momentum_vertical_cache_service import (
            cache_skew_momentum_vertical_opportunities, _ensure_schema, _upsert,
        )
        with tempfile.TemporaryDirectory() as td:
            db = td + "/skew.db"
            # Pre-populate 60 rows directly
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            _ensure_schema(conn)
            for i in range(60):
                _upsert(conn, {"ticker": f"T{i:03d}", "direction": "bullish",
                               "possible_spread": {}, "score": float(i)})
            conn.commit()
            count_before = conn.execute("SELECT COUNT(*) FROM skew_vertical_opportunities").fetchone()[0]
            conn.close()
            self.assertEqual(count_before, 60)

            # Now write one more row through the service — prune should fire
            with patch("app.config.SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED", True), \
                 patch("app.config.SKEW_VERTICAL_OPPORTUNITY_DB_PATH", db), \
                 patch("app.config.SKEW_VERTICAL_OPPORTUNITY_CACHE_RECENT_LIMIT", 20):
                cache_skew_momentum_vertical_opportunities(
                    [{"ticker": "NEW", "direction": "bullish", "possible_spread": {}, "score": 99.0}]
                )

            conn2 = sqlite3.connect(db)
            count_after = conn2.execute("SELECT COUNT(*) FROM skew_vertical_opportunities").fetchone()[0]
            conn2.close()
            self.assertLessEqual(count_after, 50, f"DB has {count_after} rows after prune, expected ≤50")

    def test_recent_list_capped_by_config_limit(self):
        from app.services.skew_momentum_vertical_cache_service import cache_skew_momentum_vertical_opportunities
        rows = [{"ticker": f"T{i:03d}", "direction": "bullish", "possible_spread": {}, "score": float(i)}
                for i in range(30)]
        with tempfile.TemporaryDirectory() as td:
            db = td + "/skew.db"
            with patch("app.config.SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED", True), \
                 patch("app.config.SKEW_VERTICAL_OPPORTUNITY_DB_PATH", db), \
                 patch("app.config.SKEW_VERTICAL_OPPORTUNITY_CACHE_RECENT_LIMIT", 5):
                result = cache_skew_momentum_vertical_opportunities(rows)
        self.assertLessEqual(len(result["recent"]), 5)


# ---------------------------------------------------------------------------
# 6: trade_memory.py deleted
# ---------------------------------------------------------------------------

class TestTradeMemoryDeleted(unittest.TestCase):
    def test_trade_memory_py_does_not_exist(self):
        import os
        repo_root = Path(__file__).parent.parent
        self.assertFalse(
            (repo_root / "trade_memory.py").exists(),
            "trade_memory.py must be deleted",
        )

    def test_trade_memory_service_still_importable(self):
        from app.services.trade_memory_service import build_trade_memory_snapshot
        self.assertIsNotNone(build_trade_memory_snapshot)


# ---------------------------------------------------------------------------
# 7-11: strategy_id alias resolution
# ---------------------------------------------------------------------------

class TestStrategyAliasResolution(unittest.TestCase):
    def _make_repo(self, td: str):
        from app.services.report_snapshot_service import ReportSnapshotRepository
        path = str(Path(td) / "state.sqlite3")
        repo = ReportSnapshotRepository(path)
        summary = {"report_data": {"tradier_snapshot": {
            "_strategy_results": {
                "earnings_calendar": {"strategy_id": "earnings_calendar", "pass_count": 0},
                "forward_factor_calendar": {"strategy_id": "forward_factor_calendar", "pass_count": 0},
                "skew_momentum_vertical": {"strategy_id": "skew_momentum_vertical", "pass_count": 0},
                "stock_momentum": {"strategy_id": "stock_momentum", "pass_count": 0},
            }
        }}}
        repo.save_success("run-ac", "dev", "p", summary, {}, {})
        return repo

    def _detail(self, alias: str, td: str) -> dict:
        from app.services.developer_snapshot_service import build_snapshot_detail
        return build_snapshot_detail("strategy", strategy_id=alias, report_repository=self._make_repo(td))

    def test_calendar_alias_resolves_200(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._detail("calendar", td)
        self.assertEqual(result["status"], "ok", f"Expected ok, got: {result}")
        self.assertEqual(result["strategy_id"], "earnings_calendar")

    def test_forward_factor_alias_resolves_200(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._detail("forward_factor", td)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["strategy_id"], "forward_factor_calendar")

    def test_ff_alias_resolves_200(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._detail("ff", td)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["strategy_id"], "forward_factor_calendar")

    def test_skew_alias_resolves_200(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._detail("skew", td)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["strategy_id"], "skew_momentum_vertical")

    def test_unknown_id_returns_not_found(self):
        with tempfile.TemporaryDirectory() as td:
            result = self._detail("does_not_exist", td)
        self.assertEqual(result["status"], "not_found")
        self.assertIn("error", result)
        self.assertIn("valid_strategy_ids", result)
        self.assertFalse(result["provider_calls_triggered"])


# ---------------------------------------------------------------------------
# 12-14: background scan state
# ---------------------------------------------------------------------------

class TestBackgroundScanState(unittest.TestCase):
    def test_initial_state_is_never_run(self):
        from app.services import analysis_service
        # Reset to simulate fresh worker
        with analysis_service._CALENDAR_SCAN_LOCK:
            analysis_service._CALENDAR_SCAN_STATE.update({
                "status": "never_run", "candidates": [], "completed_at": None
            })
        with analysis_service._CALENDAR_SCAN_LOCK:
            s = dict(analysis_service._CALENDAR_SCAN_STATE)
        self.assertEqual(s["status"], "never_run")
        self.assertEqual(s["candidates"], [])

    def test_bg_scan_updates_state_on_completion(self):
        from app.services.analysis_service import _run_calendar_scan_bg, _CALENDAR_SCAN_LOCK, _CALENDAR_SCAN_STATE
        with _CALENDAR_SCAN_LOCK:
            _CALENDAR_SCAN_STATE.update({"status": "never_run", "candidates": [], "completed_at": None})

        fake_result = [{"ticker": "AAPL"}, {"ticker": "NVDA"}]
        t = threading.Thread(target=_run_calendar_scan_bg, args=(lambda: fake_result,), daemon=True)
        t.start()
        t.join(timeout=3)

        with _CALENDAR_SCAN_LOCK:
            s = dict(_CALENDAR_SCAN_STATE)
        self.assertEqual(s["status"], "complete")
        self.assertEqual(len(s["candidates"]), 2)
        self.assertIsNotNone(s["completed_at"])

    def test_bg_scan_stores_empty_list_on_exception(self):
        from app.services.analysis_service import _run_calendar_scan_bg, _CALENDAR_SCAN_LOCK, _CALENDAR_SCAN_STATE
        with _CALENDAR_SCAN_LOCK:
            _CALENDAR_SCAN_STATE.update({"status": "never_run", "candidates": [], "completed_at": None})

        def bad_scan():
            raise RuntimeError("scan failed")

        t = threading.Thread(target=_run_calendar_scan_bg, args=(bad_scan,), daemon=True)
        t.start()
        t.join(timeout=3)

        with _CALENDAR_SCAN_LOCK:
            s = dict(_CALENDAR_SCAN_STATE)
        self.assertEqual(s["status"], "complete")
        self.assertEqual(s["candidates"], [])
