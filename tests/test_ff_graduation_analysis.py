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
            created_at TEXT DEFAULT (datetime('now')),
            ticker TEXT NOT NULL,
            verdict TEXT,
            signal_tier TEXT,
            is_positive_signal INTEGER,
            is_pass INTEGER,
            structure_built INTEGER,
            source_qualified INTEGER,
            diagnostic_model INTEGER,
            chain_approved INTEGER,
            cheap_eligible INTEGER,
            signal_score REAL,
            gate_fail_reason TEXT,
            forward_factor REAL,
            diagnostic_raw_iv_forward_factor REAL,
            source_forward_factor REAL,
            front_expiration TEXT,
            back_expiration TEXT,
            liquidity_status TEXT,
            primary_blocker TEXT,
            earnings_contaminated INTEGER
        );
    """)
    conn.executemany(
        "INSERT INTO ff_journal (run_id, run_date, ticker, structure_built, diagnostic_model, "
        "source_qualified, chain_approved, cheap_eligible, verdict, signal_tier, is_positive_signal, is_pass, "
        "signal_score, forward_factor, diagnostic_raw_iv_forward_factor, source_forward_factor, liquidity_status, primary_blocker, earnings_contaminated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("run1", "2025-06-01", "AAPL", 1, 1, 1, 1, 1, "DRY RUN PASS / FORWARD FACTOR SETUP", "SOURCE_QUALIFIED_POSITIVE", 1, 1, 0.35, 0.35, None, 0.35, "PASS", None, 0),
            ("run1", "2025-06-01", "MSFT", 1, 1, 0, 1, 1, "FAIL / FORWARD FACTOR BELOW THRESHOLD", "NEGATIVE_OR_BLOCKED", 0, 0, 0.12, 0.12, None, None, "PASS", "Below threshold", 0),
            ("run1", "2025-06-01", "GOOG", 0, 1, 0, 1, 1, "FAIL / OPTIONS ILLIQUID", "NEGATIVE_OR_BLOCKED", 0, 0, None, None, None, None, "FAIL", "Illiquid", 1),
            ("run2", "2025-06-02", "AAPL", 1, 1, 1, 1, 1, "WATCH / LIQUIDITY DATA PARTIAL", "WATCH_NEAR_POSITIVE", 0, 0, 0.22, None, 0.22, None, "WATCH", "Liquidity partial", 0),
            ("run2", "2025-06-02", "TSLA", 0, 0, 0, 0, 1, "FAIL / CHAIN DATA QUALITY", "NEGATIVE_OR_BLOCKED", 0, 0, None, None, None, None, None, "Chain data", 0),
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
        assert result["dry_run"] is True
        assert result["total_observations"] == 5
        assert result["pass_observations"] == 1
        assert result["source_qualified_positive_count"] == 1
        assert result["diagnostic_positive_count"] == 0
        assert result["near_positive_count"] == 1
        assert result["structure_complete_count"] == 3
        assert result["liquidity_pass_count"] == 2
        assert result["provider_calls_triggered"] is False

    def test_closest_to_threshold(self, ff_db):
        with patch("app.services.ff_graduation_analysis_service.config") as mock_cfg:
            mock_cfg.FF_JOURNAL_DB_PATH = ff_db
            mock_cfg.FF_JOURNAL_ENABLED = True
            mock_cfg.FORWARD_FACTOR_DRY_RUN = True
            mock_cfg.FF_MIN_FORWARD_FACTOR = 0.20
            mock_cfg.FF_GRAD_MIN_CALC_COMPLETE = 20
            mock_cfg.FF_GRAD_MIN_POSITIVE = 5
            mock_cfg.FF_GRAD_MIN_SOURCE_QUALIFIED = 3
            mock_cfg.FF_GRAD_MIN_STRUCTURE_COMPLETE = 3
            mock_cfg.FF_GRAD_MIN_MANUAL_REVIEWS = 1
            from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
            result = build_ff_graduation_analysis()

        assert result["eligible_for_review"] is False
        assert "Needs at least 20 FF calculation-complete observations" in result["readiness"]["reasons"][0]

    def test_recent_runs(self, ff_db):
        with patch("app.services.ff_graduation_analysis_service.config") as mock_cfg:
            mock_cfg.FF_JOURNAL_DB_PATH = ff_db
            mock_cfg.FF_JOURNAL_ENABLED = True
            mock_cfg.FORWARD_FACTOR_DRY_RUN = True
            mock_cfg.FF_MIN_FORWARD_FACTOR = 0.20
            mock_cfg.FF_GRAD_MIN_CALC_COMPLETE = 20
            mock_cfg.FF_GRAD_MIN_POSITIVE = 5
            mock_cfg.FF_GRAD_MIN_SOURCE_QUALIFIED = 3
            mock_cfg.FF_GRAD_MIN_STRUCTURE_COMPLETE = 3
            mock_cfg.FF_GRAD_MIN_MANUAL_REVIEWS = 1
            from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
            result = build_ff_graduation_analysis()

        rows = result["recent_passes"]
        assert len(rows) >= 1
        assert rows[0]["ticker"] in {"AAPL", "MSFT"}

    def test_no_db_returns_no_data(self, tmp_path):
        with patch("app.services.ff_graduation_analysis_service.config") as mock_cfg:
            mock_cfg.FF_JOURNAL_DB_PATH = str(tmp_path / "nonexistent.db")
            mock_cfg.FORWARD_FACTOR_DRY_RUN = True
            from app.services.ff_graduation_analysis_service import build_ff_graduation_analysis
            result = build_ff_graduation_analysis()

        assert result["status"] == "no_data"
