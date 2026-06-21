"""
tests/test_ff_graduation_analysis.py — FF graduation analysis service tests.

Verifies the read-only aggregation logic without touching any provider.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pytest


@pytest.fixture
def ff_db(tmp_path):
    db_path = str(tmp_path / "ff_journal_test.db")
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ff_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            run_date TEXT NOT NULL,
            ticker TEXT NOT NULL,
            ff_candidate_stage TEXT,
            cheap_eligible INTEGER,
            chain_approved INTEGER,
            source_qualified INTEGER,
            diagnostic_model INTEGER,
            structure_built INTEGER,
            gate_fail_reason TEXT,
            verdict TEXT,
            signal_score REAL,
            put_short_expiration TEXT,
            put_long_expiration TEXT,
            call_short_expiration TEXT,
            call_long_expiration TEXT,
            put_short_delta REAL,
            put_long_delta REAL,
            call_short_delta REAL,
            call_long_delta REAL,
            front_iv REAL,
            back_iv REAL,
            underlying_price REAL,
            is_diagnostic_only INTEGER,
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.executemany(
        "INSERT INTO ff_journal (run_id, run_date, ticker, structure_built, diagnostic_model, "
        "source_qualified, chain_approved, cheap_eligible, verdict, signal_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("run1", "2025-06-01", "AAPL", 1, 1, 1, 1, 1, "DRY RUN PASS / FORWARD FACTOR SETUP", 0.35),
            ("run1", "2025-06-01", "MSFT", 1, 1, 0, 1, 1, "FAIL / FORWARD FACTOR BELOW THRESHOLD", 0.12),
            ("run1", "2025-06-01", "GOOG", 0, 1, 0, 1, 1, "FAIL / OPTIONS ILLIQUID", None),
            ("run2", "2025-06-02", "AAPL", 1, 1, 1, 1, 1, "WATCH / LIQUIDITY DATA PARTIAL", 0.22),
            ("run2", "2025-06-02", "TSLA", 0, 0, 0, 0, 1, "FAIL / CHAIN DATA QUALITY", None),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


class TestBuildAnalysis:
    def test_returns_ok_with_data(self, ff_db):
        with patch("app.services.ff_graduation_analysis_service.config") as mock_cfg:
            mock_cfg.FF_JOURNAL_DB_PATH = ff_db
            mock_cfg.FF_JOURNAL_ENABLED = True
            mock_cfg.FORWARD_FACTOR_DRY_RUN = True
            mock_cfg.FF_MIN_FORWARD_FACTOR = 0.20
            from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
            result = build_ff_graduation_analysis()

        assert result["status"] == "ok"
        assert result["ff_dry_run"] is True
        assert result["totals"]["total_observations"] == 5
        assert result["totals"]["distinct_tickers"] == 4
        assert result["totals"]["distinct_runs"] == 2
        assert result["gate_stats"]["structure_built_count"] == 3
        assert result["gate_stats"]["diagnostic_model_count"] == 4
        assert result["graduation_signals"]["dry_run_pass_count"] == 1
        assert result["graduation_signals"]["any_candidate_crossed_threshold"] is True

    def test_closest_to_threshold(self, ff_db):
        with patch("app.services.ff_graduation_analysis_service.config") as mock_cfg:
            mock_cfg.FF_JOURNAL_DB_PATH = ff_db
            mock_cfg.FF_JOURNAL_ENABLED = True
            mock_cfg.FORWARD_FACTOR_DRY_RUN = True
            mock_cfg.FF_MIN_FORWARD_FACTOR = 0.20
            from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
            result = build_ff_graduation_analysis()

        closest = result["graduation_signals"]["closest_to_threshold"]
        assert closest is not None
        assert closest["ticker"] == "AAPL"
        assert closest["signal_score"] == 0.35

    def test_recent_runs(self, ff_db):
        with patch("app.services.ff_graduation_analysis_service.config") as mock_cfg:
            mock_cfg.FF_JOURNAL_DB_PATH = ff_db
            mock_cfg.FF_JOURNAL_ENABLED = True
            mock_cfg.FORWARD_FACTOR_DRY_RUN = True
            mock_cfg.FF_MIN_FORWARD_FACTOR = 0.20
            from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
            result = build_ff_graduation_analysis()

        runs = result["recent_runs"]
        assert len(runs) == 2
        assert runs[0]["run_date"] == "2025-06-02"

    def test_no_db_returns_no_data(self, tmp_path):
        with patch("app.services.ff_graduation_analysis_service.config") as mock_cfg:
            mock_cfg.FF_JOURNAL_DB_PATH = str(tmp_path / "nonexistent.db")
            from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
            result = build_ff_graduation_analysis()

        assert result["status"] == "no_data"
