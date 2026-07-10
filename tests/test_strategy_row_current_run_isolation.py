"""TKT-STRATEGY-ROW-CURRENT-RUN-ISOLATION — run isolation tests.

When an enabled strategy crashes and writes 0 rows for the current run,
`read_latest()` must return an empty/failed response for that run rather than
stale rows from the previous successful run.

The fix adds a `strategy_run_log` table that records (run_id, strategy_id,
row_count, execution_status) for every write_run() call.  read_latest() consults
the log first to determine the most recent run, then returns rows only if
row_count > 0.
"""
from __future__ import annotations

import sys
import types

# ── pyo3 panic guard ──────────────────────────────────────────────────────────
_rh_stub = types.ModuleType("robin_stocks")
_rh_stub.robinhood = types.ModuleType("robin_stocks.robinhood")
sys.modules.setdefault("robin_stocks", _rh_stub)
sys.modules.setdefault("robin_stocks.robinhood", _rh_stub.robinhood)

import os
import tempfile

import pytest


def _make_row(run_id: str, strategy_id: str, ticker: str = "AAPL") -> dict:
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "row_id": f"{strategy_id}-{ticker}-{run_id}",
        "ticker": ticker,
        "symbol": ticker,
        "verdict": "WATCH / SIGNAL",
        "score": 0.75,
        "row_type": "candidate",
        "normalization_status": "ok",
    }


def _make_result(rows: list, errors: list | None = None, execution_failed: bool = False) -> dict:
    return {
        "canonical_opportunities": rows,
        "errors": errors or [],
        "execution_failed": execution_failed,
    }


@pytest.fixture
def tmp_repo(tmp_path):
    from app.services.strategy_row_repository import StrategyRowRepository
    db_path = str(tmp_path / "test_rows.db")
    return StrategyRowRepository(db_path=db_path)


class TestWriteRunLog:
    """write_run() must insert entries into strategy_run_log for every strategy."""

    def test_write_creates_log_entry_with_rows(self, tmp_repo):
        row = _make_row("run-001", "earnings_calendar")
        result = tmp_repo.write_run("run-001", {"earnings_calendar": _make_result([row])})
        assert result["write_count"] == 1
        # Verify log entry exists.
        with tmp_repo._connect() as conn:
            log = conn.execute(
                "SELECT row_count, execution_status FROM strategy_run_log WHERE run_id=? AND strategy_id=?",
                ("run-001", "earnings_calendar"),
            ).fetchone()
        assert log is not None
        assert log["row_count"] == 1
        assert log["execution_status"] == "ok"

    def test_write_creates_failed_log_entry_for_crash(self, tmp_repo):
        """Crashed strategy (0 rows + errors) → execution_status='failed'."""
        result = tmp_repo.write_run(
            "run-002",
            {"forward_factor_calendar": _make_result([], errors=["some crash"], execution_failed=True)},
        )
        assert result["write_count"] == 0
        with tmp_repo._connect() as conn:
            log = conn.execute(
                "SELECT row_count, execution_status FROM strategy_run_log WHERE run_id=? AND strategy_id=?",
                ("run-002", "forward_factor_calendar"),
            ).fetchone()
        assert log is not None
        assert log["row_count"] == 0
        assert log["execution_status"] == "failed"

    def test_write_creates_empty_log_entry_for_no_rows_no_errors(self, tmp_repo):
        """Strategy returned no rows and no errors → execution_status='empty'."""
        result = tmp_repo.write_run(
            "run-003",
            {"stock_momentum": _make_result([])},
        )
        assert result["write_count"] == 0
        with tmp_repo._connect() as conn:
            log = conn.execute(
                "SELECT row_count, execution_status FROM strategy_run_log WHERE run_id=? AND strategy_id=?",
                ("run-003", "stock_momentum"),
            ).fetchone()
        assert log is not None
        assert log["row_count"] == 0
        assert log["execution_status"] == "empty"

    def test_write_creates_log_entries_for_multiple_strategies(self, tmp_repo):
        row = _make_row("run-004", "earnings_calendar")
        tmp_repo.write_run(
            "run-004",
            {
                "earnings_calendar": _make_result([row]),
                "forward_factor_calendar": _make_result([], errors=["crash"]),
                "stock_momentum": _make_result([]),
            },
        )
        with tmp_repo._connect() as conn:
            logs = {
                row["strategy_id"]: dict(row)
                for row in conn.execute("SELECT strategy_id, row_count, execution_status FROM strategy_run_log WHERE run_id=?", ("run-004",)).fetchall()
            }
        assert logs["earnings_calendar"]["row_count"] == 1
        assert logs["earnings_calendar"]["execution_status"] == "ok"
        assert logs["forward_factor_calendar"]["row_count"] == 0
        assert logs["forward_factor_calendar"]["execution_status"] == "failed"
        assert logs["stock_momentum"]["row_count"] == 0
        assert logs["stock_momentum"]["execution_status"] == "empty"


class TestReadLatestIsolation:
    """read_latest() must return empty/failed state for crashed strategies."""

    def test_returns_rows_from_current_run_when_ok(self, tmp_repo):
        row = _make_row("run-A", "earnings_calendar")
        tmp_repo.write_run("run-A", {"earnings_calendar": _make_result([row])})
        result = tmp_repo.read_latest("earnings_calendar")
        assert result["run_id"] == "run-A"
        assert result["row_count"] == 1
        assert len(result["rows"]) == 1

    def test_returns_empty_when_strategy_failed_current_run(self, tmp_repo):
        """After successful run A and failed run B, read_latest returns empty for run B."""
        row = _make_row("run-B1", "forward_factor_calendar")
        tmp_repo.write_run("run-B1", {"forward_factor_calendar": _make_result([row])})
        # Crash run.
        tmp_repo.write_run(
            "run-B2",
            {"forward_factor_calendar": _make_result([], errors=["TypeError: multiple values"])},
        )
        result = tmp_repo.read_latest("forward_factor_calendar")
        assert result["run_id"] == "run-B2"
        assert result["rows"] == []
        assert result["row_count"] == 0
        assert result["execution_status"] == "failed"
        assert result["fallback_used"] is False

    def test_does_not_return_stale_rows_from_previous_run(self, tmp_repo):
        """When current run has 0 rows (crash), must NOT return stale rows from old run."""
        row = _make_row("run-C1", "forward_factor_calendar")
        tmp_repo.write_run("run-C1", {"forward_factor_calendar": _make_result([row])})
        # Current run: crash.
        tmp_repo.write_run(
            "run-C2",
            {"forward_factor_calendar": _make_result([], errors=["crash"])},
        )
        result = tmp_repo.read_latest("forward_factor_calendar")
        # Must NOT contain stale rows from run-C1.
        assert result["rows"] == [], f"Expected empty rows, got {len(result['rows'])} stale row(s)"
        assert result["run_id"] == "run-C2"

    def test_empty_strategy_no_log_falls_back_to_row_lookup(self, tmp_repo):
        """When strategy_run_log has no entry, fall back to row-based latest_run_id."""
        row = _make_row("run-D", "stock_momentum")
        # Write rows without going through write_run (simulates legacy data).
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        with tmp_repo._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO strategy_rows
                (run_id, strategy_id, row_id, ticker, symbol, verdict, score, row_type, normalization_status, created_at, schema_version)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                ("run-D", "stock_momentum", "row-d-1", "AAPL", "AAPL", "WATCH", 0.5, "candidate", "ok", now, 1),
            )
        result = tmp_repo.read_latest("stock_momentum")
        assert result["run_id"] == "run-D"
        assert len(result["rows"]) == 1

    def test_no_data_returns_none_run_id(self, tmp_repo):
        result = tmp_repo.read_latest("skew_momentum_vertical")
        assert result["run_id"] is None
        assert result["rows"] == []
        assert result["row_count"] == 0


class TestRunLogUpsert:
    """Repeated writes to same (run_id, strategy_id) replace the log entry."""

    def test_upsert_updates_existing_log_entry(self, tmp_repo):
        tmp_repo.write_run("run-E", {"earnings_calendar": _make_result([], errors=["crash"])})
        row = _make_row("run-E", "earnings_calendar")
        # Second write with same run_id (e.g. after retry).
        tmp_repo.write_run("run-E", {"earnings_calendar": _make_result([row])})
        with tmp_repo._connect() as conn:
            log = conn.execute(
                "SELECT row_count, execution_status FROM strategy_run_log WHERE run_id=? AND strategy_id=?",
                ("run-E", "earnings_calendar"),
            ).fetchone()
        assert log["row_count"] == 1
        assert log["execution_status"] == "ok"
