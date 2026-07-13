"""
Patch 27Z — FF paper observation journal.

Tests:
1. Schema: ff_observations.db created, ff_journal table has correct columns
2. write_run: rows written per candidate, all stages captured
3. write_run: structure fields populated only when structure_built=true
4. write_run: failure isolation (bad db path swallowed, returns 0)
5. write_run: no-op when FF_JOURNAL_ENABLED=false
6. write_run: no-op when FORWARD_FACTOR_DRY_RUN=false (defensive guard)
7. journal_summary: correct aggregates after writes
8. journal_summary: safe return when db absent
9. build_forward_factor_strategy: ff_journal key present in result
10. build_forward_factor_strategy: row count matches candidate count in journal
11. FF absent from Daily Opportunity (dry_run=True unchanged)
12. Provider-free: journal path never triggers provider call
"""

import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from app.db.ff_journal import write_run, journal_summary, _ensure_schema


def _now():
    return datetime.now(timezone.utc).isoformat()


def _candidate(ticker, stage="no_pair", structure_built=False, gates=None):
    g = gates or {
        "cheap_eligible": stage not in {"cap_skip", "budget_skipped", "recent_fail_skip"},
        "chain_approved": stage not in {"cap_skip", "budget_skipped", "recent_fail_skip", "cheap_eligible"},
        "source_qualified": False,
        "diagnostic_model": False,
        "structure_built": structure_built,
        "gate_fail_reason": None if structure_built else "structure_built",
    }
    row = {
        "ticker": ticker,
        "ff_candidate_stage": stage,
        "ff_gates": g,
        "verdict": "FAIL / NO MATCHED DOUBLE CALENDAR" if not structure_built else "DRY RUN PASS",
        "signal_score": 0.0,
        "underlying_price": 150.0,
        "front_raw_iv": 0.42 if structure_built else None,
        "back_raw_iv": 0.38 if structure_built else None,
        "is_diagnostic_only": False,
        "dry_run": True,
    }
    if structure_built:
        row.update({
            "put_short_expiration": "2026-08-21",
            "put_long_expiration": "2026-09-18",
            "call_short_expiration": "2026-08-21",
            "call_long_expiration": "2026-09-18",
            "structure_legs": {
                "put_short": {"delta": -0.35, "strike": 140},
                "put_long": {"delta": -0.28, "strike": 140},
                "call_short": {"delta": 0.35, "strike": 160},
                "call_long": {"delta": 0.28, "strike": 160},
            },
        })
    return row


class TestSchema(unittest.TestCase):
    def test_schema_creates_ff_journal_table(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            _ensure_schema(path)
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            tables = [r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            self.assertIn("ff_journal", tables)
            cols = [r["name"] for r in conn.execute("PRAGMA table_info(ff_journal)").fetchall()]
            conn.close()
            for expected in ["run_id", "run_date", "ticker", "ff_candidate_stage", "cheap_eligible",
                             "chain_approved", "source_qualified", "diagnostic_model", "structure_built",
                             "gate_fail_reason", "verdict", "signal_score", "put_short_expiration",
                             "put_long_expiration", "call_short_expiration", "call_long_expiration",
                             "put_short_delta", "put_long_delta", "call_short_delta", "call_long_delta",
                             "front_iv", "back_iv", "underlying_price", "is_diagnostic_only", "created_at"]:
                self.assertIn(expected, cols, msg=f"Missing column: {expected}")

    def test_index_created_on_ticker_run_date(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            _ensure_schema(path)
            conn = sqlite3.connect(path)
            indexes = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()]
            conn.close()
            self.assertTrue(any("ticker" in idx or "date" in idx.lower() or "journal" in idx for idx in indexes))


def _tmpdb():
    """Return (tmpdir_ctx, path). Caller must use as context manager to ensure cleanup."""
    import os, tempfile
    d = tempfile.mkdtemp()
    return d, os.path.join(d, "ff.db")


class TestWriteRun(unittest.TestCase):
    def test_write_run_returns_row_count_matching_candidates(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            candidates = [_candidate("ELF"), _candidate("CRDO"), _candidate("LULU"), _candidate("METU")]
            written = write_run("run-abc", "2026-06-16", candidates, db_path=path)
            self.assertEqual(written, 4)
            conn = sqlite3.connect(path)
            count = conn.execute("SELECT COUNT(*) FROM ff_journal").fetchone()[0]
            conn.close()
            self.assertEqual(count, 4)

    def test_cap_skip_rows_written_with_correct_stage(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            candidates = [_candidate("AAPL", stage="cap_skip"), _candidate("MSFT", stage="cap_skip")]
            write_run("run-xyz", "2026-06-16", candidates, db_path=path)
            conn = sqlite3.connect(path)
            rows = conn.execute("SELECT ticker, ff_candidate_stage, cheap_eligible FROM ff_journal").fetchall()
            conn.close()
            stages = {r[0]: r[1] for r in rows}
            self.assertEqual(stages["AAPL"], "cap_skip")
            self.assertEqual(stages["MSFT"], "cap_skip")
            cheap = {r[0]: r[2] for r in rows}
            self.assertEqual(cheap["AAPL"], 0)

    def test_structure_fields_populated_when_structure_built_true(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            candidates = [_candidate("ELF", stage="selected", structure_built=True)]
            write_run("run-001", "2026-06-16", candidates, db_path=path)
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM ff_journal WHERE ticker='ELF'").fetchone()
            conn.close()
            self.assertEqual(r["put_short_expiration"], "2026-08-21")
            self.assertEqual(r["call_long_expiration"], "2026-09-18")
            self.assertIsNotNone(r["put_short_delta"])
            self.assertIsNotNone(r["front_iv"])

    def test_structure_fields_null_when_not_structure_built(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            candidates = [_candidate("CRDO", stage="no_pair", structure_built=False)]
            write_run("run-002", "2026-06-16", candidates, db_path=path)
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            r = conn.execute("SELECT * FROM ff_journal WHERE ticker='CRDO'").fetchone()
            conn.close()
            self.assertIsNone(r["put_short_expiration"])
            self.assertIsNone(r["put_short_delta"])

    def test_failure_isolation_bad_path_returns_zero(self):
        written = write_run("run-fail", "2026-06-16", [_candidate("ELF")], db_path="\x00/invalid/ff.db")
        self.assertEqual(written, 0)

    def test_no_op_when_journal_disabled(self):
        from app import config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            with patch.object(config, "FF_JOURNAL_ENABLED", False):
                written = write_run("run-003", "2026-06-16", [_candidate("ELF")], db_path=path)
            self.assertEqual(written, 0)
            if os.path.exists(path):
                conn = sqlite3.connect(path)
                has_table = conn.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='ff_journal'"
                ).fetchone()[0]
                count = conn.execute("SELECT COUNT(*) FROM ff_journal").fetchone()[0] if has_table else 0
                conn.close()
                self.assertEqual(count, 0)

    def test_writes_when_dry_run_false(self):
        from app import config
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            with patch.object(config, "FORWARD_FACTOR_DRY_RUN", False):
                written = write_run("run-004", "2026-06-16", [_candidate("ELF")], db_path=path)
            self.assertEqual(written, 1)

    def test_empty_candidates_returns_zero(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            written = write_run("run-005", "2026-06-16", [], db_path=path)
            self.assertEqual(written, 0)

    def test_multiple_runs_accumulate(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            write_run("run-A", "2026-06-15", [_candidate("ELF"), _candidate("CRDO")], db_path=path)
            write_run("run-B", "2026-06-16", [_candidate("ELF"), _candidate("CRDO"), _candidate("LULU")], db_path=path)
            conn = sqlite3.connect(path)
            count = conn.execute("SELECT COUNT(*) FROM ff_journal").fetchone()[0]
            conn.close()
            self.assertEqual(count, 5)


class TestJournalSummary(unittest.TestCase):
    def test_summary_correct_after_writes(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            write_run("run-A", "2026-06-15", [_candidate("ELF"), _candidate("CRDO")], db_path=path)
            write_run("run-B", "2026-06-16", [_candidate("ELF", stage="selected", structure_built=True)], db_path=path)
            summary = journal_summary(db_path=path)
        self.assertEqual(summary["total_observations"], 3)
        self.assertEqual(summary["tickers_observed"], 2)
        self.assertEqual(summary["runs_recorded"], 2)
        self.assertEqual(summary["latest_run_date"], "2026-06-16")
        self.assertEqual(summary["latest_structure_built"], "ELF")
        self.assertTrue(summary["enabled"])

    def test_summary_safe_when_db_absent(self):
        summary = journal_summary(db_path="/nonexistent/ff.db")
        self.assertIn("total_observations", summary)
        self.assertEqual(summary["total_observations"], 0)
        self.assertIsNone(summary["latest_structure_built"])

    def test_summary_disabled_returns_enabled_false(self):
        from app import config
        with patch.object(config, "FF_JOURNAL_ENABLED", False):
            summary = journal_summary(db_path="/nonexistent/ff.db")
        self.assertFalse(summary["enabled"])
        self.assertEqual(summary["total_observations"], 0)


class TestIntegrationWithBuildFF(unittest.TestCase):
    def _hub(self):
        class FakeHub:
            def __init__(self):
                now = _now()
                self.context = type("C", (), {"fetch_audit": []})()
            def get_quote(self, *a, **k):
                return {"payload": {"last": 150}, "fetched_at": _now(), "fresh": True, "provider": "tradier", "confidence": "high"}
            def get_daily_candles(self, *a, **k):
                return {"payload": {"bars": [{"close": 150, "volume": 8_000_000}] * 240}, "fetched_at": _now(), "fresh": True, "provider": "tradier", "confidence": "high"}
            def get_derived_metrics(self, *a, **k):
                return {"average_volume_30d": 8_000_000, "realized_volatility_30d": 0.30}
            def get_earnings_event(self, *a, **k):
                return None
            def get_options_chain_set(self, ticker, *a, **k):
                return {"payload": {"expirations": [], "chains": {}, "chains_by_expiration": {}}}
        return FakeHub()

    def _plan(self, tickers):
        return {"by_ticker": {t: {"state": "APPROVED"} for t in tickers}, "forward_factor_chain_reserve": 4}

    def test_ff_journal_key_present_in_result(self):
        from app import config
        from app.services.forward_factor_service import build_forward_factor_strategy
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            with patch.object(config, "FF_JOURNAL_ENABLED", True), \
                 patch.object(config, "FF_JOURNAL_DB_PATH", path):
                result = build_forward_factor_strategy(
                    ["ELF"], {"ELF": {"current_price": 150, "average_volume_30d": 8_000_000}},
                    self._hub(), run_mode="dev", requirement_plan=self._plan(["ELF"]),
                    run_id="test-run-001", run_date="2026-06-16",
                )
        self.assertIn("ff_journal", result)
        journal = result["ff_journal"]
        self.assertIn("total_observations", journal)
        self.assertIn("enabled", journal)

    def test_journal_row_count_matches_candidate_count(self):
        from app import config
        from app.services.forward_factor_service import build_forward_factor_strategy
        tickers = ["ELF", "CRDO"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            with patch.object(config, "FF_JOURNAL_ENABLED", True), \
                 patch.object(config, "FF_JOURNAL_DB_PATH", path):
                result = build_forward_factor_strategy(
                    tickers, {t: {"current_price": 150, "average_volume_30d": 8_000_000} for t in tickers},
                    self._hub(), run_mode="dev", requirement_plan=self._plan(tickers),
                    run_id="test-run-002", run_date="2026-06-16",
                )
            candidate_count = len(result["items"])
            conn = sqlite3.connect(path)
            db_count = conn.execute("SELECT COUNT(*) FROM ff_journal WHERE run_id='test-run-002'").fetchone()[0]
            conn.close()
        self.assertEqual(db_count, candidate_count, msg=f"Journal rows {db_count} != candidate count {candidate_count}")

    def test_journal_disabled_no_ff_journal_key(self):
        from app import config
        from app.services.forward_factor_service import build_forward_factor_strategy
        with patch.object(config, "FF_JOURNAL_ENABLED", False):
            result = build_forward_factor_strategy(
                ["ELF"], {"ELF": {"current_price": 150, "average_volume_30d": 8_000_000}},
                self._hub(), run_mode="dev", requirement_plan=self._plan(["ELF"]),
                run_id="test-run-003", run_date="2026-06-16",
            )
        self.assertNotIn("ff_journal", result)

    def test_ff_journal_survives_normalize_strategy_results(self):
        """Regression: normalize_strategy_results must not strip ff_journal from FF result."""
        from app.strategies.registry import normalize_strategy_results
        fake_raw = {
            "forward_factor_calendar": {
                "strategy_id": "forward_factor_calendar",
                "dry_run": True,
                "items": [],
                "rows": [],
                "errors": [],
                "ff_journal": {"enabled": True, "total_observations": 5, "runs_recorded": 2,
                               "tickers_observed": 3, "latest_structure_built": "ELF", "latest_run_date": "2026-06-16"},
            }
        }
        context = type("C", (), {"analysis_tickers": [], "analysis_positions": []})()
        result = normalize_strategy_results(context, fake_raw)
        ff = result.get("forward_factor_calendar", {})
        self.assertIn("ff_journal", ff, "ff_journal stripped by normalize_strategy_results")
        self.assertEqual(ff["ff_journal"]["total_observations"], 5)

    def test_dry_run_true_unchanged(self):
        from app import config
        # Legacy dry-run flag preserved for backwards compat
        self.assertTrue(config.FORWARD_FACTOR_DRY_RUN)
        # Patch 33A: live recommendations enabled by default; execution still gated off
        self.assertTrue(getattr(config, "FF_RECOMMENDATIONS_ENABLED", False))
        self.assertFalse(getattr(config, "FF_EXECUTION_ENABLED", True))

    def test_ff_absent_from_daily_opportunity_when_recommendations_disabled(self):
        """When FF_RECOMMENDATIONS_ENABLED=False FF rows are absent from Daily Opportunity."""
        from app import config
        from app.services.forward_factor_service import build_forward_factor_strategy
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "ff.db")
            with patch.object(config, "FF_JOURNAL_ENABLED", True), \
                 patch.object(config, "FF_JOURNAL_DB_PATH", path), \
                 patch.object(config, "FF_RECOMMENDATIONS_ENABLED", False):
                result = build_forward_factor_strategy(
                    ["ELF"], {"ELF": {"current_price": 150, "average_volume_30d": 8_000_000}},
                    self._hub(), run_mode="dev", requirement_plan=self._plan(["ELF"]),
                    run_id="test-run-004", run_date="2026-06-16",
                )
        self.assertTrue(result.get("dry_run"))
        for row in result["items"]:
            self.assertFalse(row.get("can_enter_daily_opportunity", False))
            self.assertEqual(row.get("actionability_score", 0), 0)
