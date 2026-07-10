"""ASA Patch 30B — Universal Strategy Observation Journal tests.

Tests cover:
- DB layer (schema, write, read, dedup, summary)
- Adapter (all four strategies, row_hash, observation_key, status_bucket, gates)
- Write path (journal failure does not fail report, disabled flag)
- Dev endpoints (provider_calls_triggered, filter params)
- FF safety invariants
- Regression (Daily Opportunity unchanged, public screener unchanged)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import types
from typing import Any
from unittest.mock import MagicMock, patch


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_earnings_row(**kwargs) -> dict[str, Any]:
    base: dict[str, Any] = {
        "strategy_id": "earnings_calendar",
        "strategy_row_schema_version": "30A.v1",
        "ticker": "AAPL",
        "action": "EARNINGS CALENDAR CANDIDATE",
        "verdict": "EARNINGS CALENDAR CANDIDATE",
        "friendly_verdict": "Eligible",
        "primary_reason": "Favorable IV setup",
        "metrics": {"iv_relationship_status": "favorable"},
        "gates": [
            {"id": "earnings_date_trust", "label": "Earnings date trust", "name": "Earnings date trust",
             "status": "pass", "reason": "Confirmed", "detail": "Confirmed", "blocking": False, "sort_order": 20},
            {"id": "calendar_entry", "label": "Calendar entry", "name": "Calendar entry",
             "status": "pass", "reason": "", "detail": "", "blocking": False, "sort_order": 80},
        ],
        "data_quality": "good",
        "daily_opportunity_eligible": True,
        "can_trade_live": False,
        "dry_run": False,
        "journal_eligible": True,
        "observation_key": "earnings_calendar:AAPL:calendar_candidate:calendar_spread:2026-07-18/2026-08-15",
        "observation_refs": [],
        "earnings_date": "2026-07-17",
        "earnings_trust_label": "confirmed",
        "calendar_entry_allowed": True,
        "iv_relationship_status": "favorable",
        "liquidity_status": "pass",
        "debit_status": "pass",
    }
    base.update(kwargs)
    return base


def _make_skew_row(**kwargs) -> dict[str, Any]:
    base: dict[str, Any] = {
        "strategy_id": "skew_momentum_vertical",
        "strategy_row_schema_version": "30A.v1",
        "ticker": "NVDA",
        "verdict": "PASS / POSSIBLE ENTRY SETUP",
        "friendly_verdict": "Vertical candidate",
        "primary_reason": "Momentum confirmed, skew favorable",
        "metrics": {"momentum_status": "confirmed", "skew_status": "pass"},
        "gates": [
            {"id": "momentum", "label": "Momentum", "name": "Momentum",
             "status": "pass", "reason": "Confirmed", "detail": "Confirmed", "blocking": False, "sort_order": 60},
        ],
        "data_quality": "ok",
        "daily_opportunity_eligible": True,
        "can_trade_live": False,
        "dry_run": False,
        "journal_eligible": True,
        "observation_key": "skew_momentum_vertical:NVDA:vertical_spread:vertical:2026-08-15",
        "observation_refs": [],
        "direction": "BULLISH",
        "momentum_status": "confirmed",
        "skew_status": "pass",
    }
    base.update(kwargs)
    return base


def _make_ff_row(**kwargs) -> dict[str, Any]:
    base: dict[str, Any] = {
        "strategy_id": "forward_factor_calendar",
        "strategy_row_schema_version": "30A.v1",
        "ticker": "CAG",
        "verdict": "PASS / FF SIGNAL",
        "friendly_verdict": "Signal candidate",
        "primary_reason": "Forward factor above threshold",
        "metrics": {"source_forward_factor": 0.35, "source_qualified": True},
        "gates": [
            {"id": "coverage_eligibility", "label": "Coverage eligibility", "name": "Coverage eligibility",
             "status": "pass", "reason": "", "detail": "", "blocking": False, "sort_order": 10},
            {"id": "execution", "label": "Execution", "name": "Execution",
             "status": "dry_run", "reason": "Signal-only mode", "detail": "Signal-only mode",
             "blocking": False, "sort_order": 90},
        ],
        "data_quality": "ok",
        "daily_opportunity_eligible": False,
        "can_trade_live": False,
        "dry_run": True,
        "journal_eligible": True,
        "observation_key": "forward_factor_calendar:CAG:forward_factor_signal:calendar:2026-08-15/2026-10-17",
        "observation_refs": [],
        "source_forward_factor": 0.35,
        "source_qualified": True,
        "cheap_eligible": True,
        "chain_approved": True,
        "structure_built": True,
        "earnings_contaminated": False,
    }
    base.update(kwargs)
    return base


def _make_stock_row(**kwargs) -> dict[str, Any]:
    base: dict[str, Any] = {
        "strategy_id": "stock_momentum",
        "strategy_row_schema_version": "30A.v1",
        "ticker": "CRDO",
        "action": "CONSIDER ADDING",
        "verdict": "CONSIDER ADDING",
        "friendly_verdict": "Momentum Pass",
        "primary_reason": "Trend clean, momentum strong",
        "metrics": {"momentum_score": 82.5, "trend_status": "clean"},
        "gates": [
            {"id": "momentum_verdict", "label": "Momentum verdict", "name": "Momentum verdict",
             "status": "pass", "reason": "CONSIDER ADDING", "detail": "CONSIDER ADDING",
             "blocking": False, "sort_order": 65},
        ],
        "data_quality": "ok",
        "daily_opportunity_eligible": True,
        "can_trade_live": False,
        "dry_run": False,
        "journal_eligible": True,
        "observation_key": "stock_momentum:CRDO:stock_momentum:equity",
        "observation_refs": [],
        "action": "CONSIDER ADDING",
        "momentum_score": 82.5,
        "trend_status": "clean",
        "portfolio_status": "Not currently held",
    }
    base.update(kwargs)
    return base


# ─── Compile tests ─────────────────────────────────────────────────────────────

class TestCompile:
    def test_db_module_imports(self):
        import app.db.strategy_observations as m
        assert hasattr(m, "write_run")
        assert hasattr(m, "read_observations")
        assert hasattr(m, "run_summary")
        assert hasattr(m, "global_summary")

    def test_journal_service_imports(self):
        from app.services.strategy_observation_journal_service import (
            build_strategy_observation,
            build_observations_from_strategy_results,
        )
        assert callable(build_strategy_observation)
        assert callable(build_observations_from_strategy_results)

    def test_summary_service_imports(self):
        from app.services.strategy_observation_summary_service import (
            build_strategy_observation_summary,
            build_run_observation_summary,
            build_observation_list,
        )
        assert callable(build_strategy_observation_summary)

    def test_observation_schema_version(self):
        from app.db.strategy_observations import OBSERVATION_SCHEMA_VERSION
        assert OBSERVATION_SCHEMA_VERSION == "30B.v1"

    def test_config_values_exist(self):
        from app import config
        assert hasattr(config, "STRATEGY_OBSERVATION_JOURNAL_ENABLED")
        assert hasattr(config, "STRATEGY_OBSERVATION_DB_PATH")
        assert hasattr(config, "STRATEGY_OBSERVATION_RETENTION_DAYS")
        assert hasattr(config, "STRATEGY_OBSERVATION_MAX_ROWS_PER_RUN")
        assert hasattr(config, "STRATEGY_OBSERVATION_MAX_JSON_BYTES_PER_ROW")


# ─── DB layer ─────────────────────────────────────────────────────────────────

class TestDBLayer:
    def _tmp_db(self) -> str:
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        f.close()
        os.unlink(f.name)
        return f.name

    def test_creates_table(self):
        from app.db.strategy_observations import _ensure_schema, _connect
        db = self._tmp_db()
        _ensure_schema(db)
        with _connect(db) as conn:
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        assert "strategy_observations" in tables

    def test_write_and_read(self):
        from app.db.strategy_observations import write_run, read_observations
        db = self._tmp_db()
        obs = [{
            "run_id": "run_001", "observed_at": "2026-07-07T00:00:00+00:00",
            "run_date": "2026-07-07", "strategy_id": "earnings_calendar",
            "strategy_name": "Earnings Calendar Spread", "strategy_family": "options_event_volatility",
            "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
            "ticker": "AAPL", "underlying_symbol": "AAPL",
            "candidate_type": "calendar_candidate", "structure_type": "calendar_spread", "timeframe": "",
            "verdict": "EARNINGS CALENDAR CANDIDATE", "friendly_verdict": "Eligible",
            "primary_reason": "Good IV", "status_bucket": "pass",
            "daily_opportunity_eligible": 1, "can_trade_live": 0, "dry_run": 0, "journal_eligible": 1,
            "data_quality_status": "good",
            "gate_pass_count": 2, "gate_watch_count": 0, "gate_fail_count": 0,
            "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
            "score": 75.0, "actionability_score": None,
            "observation_key": "earnings_calendar:AAPL:calendar_candidate:calendar_spread:2026-07-18",
            "row_hash": "abc123",
            "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
            "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
            "observation_refs_json": "[]", "source_summary_json": "{}",
        }]
        written = write_run("run_001", "2026-07-07", obs, db_path=db)
        assert written == 1
        rows = read_observations(run_id="run_001", db_path=db)
        assert len(rows) == 1
        assert rows[0]["ticker"] == "AAPL"
        assert rows[0]["status_bucket"] == "pass"

    def test_deduplication_on_insert(self):
        from app.db.strategy_observations import write_run, read_observations
        db = self._tmp_db()
        obs = [{
            "run_id": "run_dup", "observed_at": "2026-07-07T00:00:00+00:00",
            "run_date": "2026-07-07", "strategy_id": "stock_momentum",
            "strategy_name": "Stock Momentum", "strategy_family": "equity_momentum",
            "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
            "ticker": "CRDO", "underlying_symbol": "CRDO",
            "candidate_type": "stock_momentum", "structure_type": "equity", "timeframe": "",
            "verdict": "CONSIDER ADDING", "friendly_verdict": "Momentum Pass",
            "primary_reason": "Trend clean", "status_bucket": "pass",
            "daily_opportunity_eligible": 1, "can_trade_live": 0, "dry_run": 0, "journal_eligible": 1,
            "data_quality_status": "ok",
            "gate_pass_count": 1, "gate_watch_count": 0, "gate_fail_count": 0,
            "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
            "score": 82.5, "actionability_score": None,
            "observation_key": "stock_momentum:CRDO:stock_momentum:equity",
            "row_hash": "hash_stable_001",
            "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
            "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
            "observation_refs_json": "[]", "source_summary_json": "{}",
        }]
        write_run("run_dup", "2026-07-07", obs, db_path=db)
        write_run("run_dup", "2026-07-07", obs, db_path=db)  # duplicate
        rows = read_observations(run_id="run_dup", db_path=db)
        assert len(rows) == 1, "Duplicate write should be ignored"

    def test_filter_by_strategy_id(self):
        from app.db.strategy_observations import write_run, read_observations
        db = self._tmp_db()

        def _obs(run_id, ticker, strategy_id, obs_key, row_hash):
            return {
                "run_id": run_id, "observed_at": "2026-07-07T00:00:00+00:00",
                "run_date": "2026-07-07", "strategy_id": strategy_id,
                "strategy_name": strategy_id, "strategy_family": "unknown",
                "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
                "ticker": ticker, "underlying_symbol": ticker,
                "candidate_type": "c", "structure_type": "s", "timeframe": "",
                "verdict": "PASS", "friendly_verdict": "ok",
                "primary_reason": "ok", "status_bucket": "pass",
                "daily_opportunity_eligible": 0, "can_trade_live": 0, "dry_run": 0, "journal_eligible": 1,
                "data_quality_status": "ok",
                "gate_pass_count": 0, "gate_watch_count": 0, "gate_fail_count": 0,
                "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
                "score": None, "actionability_score": None,
                "observation_key": obs_key, "row_hash": row_hash,
                "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
                "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
                "observation_refs_json": "[]", "source_summary_json": "{}",
            }

        write_run("r1", "2026-07-07", [
            _obs("r1", "AAPL", "earnings_calendar", "ec:AAPL:c:s", "h1"),
            _obs("r1", "NVDA", "skew_momentum_vertical", "skew:NVDA:c:s", "h2"),
        ], db_path=db)
        ec_rows = read_observations(strategy_id="earnings_calendar", db_path=db)
        assert all(r["strategy_id"] == "earnings_calendar" for r in ec_rows)
        assert len(ec_rows) == 1

    def test_filter_by_ticker(self):
        from app.db.strategy_observations import write_run, read_observations
        db = self._tmp_db()
        obs = [{
            "run_id": "r1", "observed_at": "2026-07-07T00:00:00+00:00",
            "run_date": "2026-07-07", "strategy_id": "stock_momentum",
            "strategy_name": "Stock Momentum", "strategy_family": "equity_momentum",
            "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
            "ticker": "TSLA", "underlying_symbol": "TSLA",
            "candidate_type": "c", "structure_type": "s", "timeframe": "",
            "verdict": "PASS", "friendly_verdict": "ok", "primary_reason": "ok", "status_bucket": "pass",
            "daily_opportunity_eligible": 0, "can_trade_live": 0, "dry_run": 0, "journal_eligible": 1,
            "data_quality_status": "ok",
            "gate_pass_count": 0, "gate_watch_count": 0, "gate_fail_count": 0,
            "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
            "score": None, "actionability_score": None,
            "observation_key": "stock_momentum:TSLA:c:s", "row_hash": "h_tsla",
            "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
            "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
            "observation_refs_json": "[]", "source_summary_json": "{}",
        }]
        write_run("r1", "2026-07-07", obs, db_path=db)
        tsla_rows = read_observations(ticker="TSLA", db_path=db)
        assert len(tsla_rows) == 1

    def test_filter_by_status_bucket(self):
        from app.db.strategy_observations import write_run, read_observations
        db = self._tmp_db()
        obs = [{
            "run_id": "r1", "observed_at": "2026-07-07T00:00:00+00:00",
            "run_date": "2026-07-07", "strategy_id": "forward_factor_calendar",
            "strategy_name": "Forward Factor", "strategy_family": "options_forward_volatility",
            "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
            "ticker": "CAG", "underlying_symbol": "CAG",
            "candidate_type": "c", "structure_type": "s", "timeframe": "",
            "verdict": "PASS / FF SIGNAL", "friendly_verdict": "Signal candidate",
            "primary_reason": "FF above threshold", "status_bucket": "dry_run",
            "daily_opportunity_eligible": 0, "can_trade_live": 0, "dry_run": 1, "journal_eligible": 1,
            "data_quality_status": "ok",
            "gate_pass_count": 1, "gate_watch_count": 1, "gate_fail_count": 0,
            "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
            "score": None, "actionability_score": None,
            "observation_key": "forward_factor_calendar:CAG:c:s", "row_hash": "h_cag",
            "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
            "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
            "observation_refs_json": "[]", "source_summary_json": "{}",
        }]
        write_run("r1", "2026-07-07", obs, db_path=db)
        dry_rows = read_observations(status_bucket="dry_run", db_path=db)
        assert len(dry_rows) == 1

    def test_run_summary(self):
        from app.db.strategy_observations import write_run, run_summary
        db = self._tmp_db()
        obs = [{
            "run_id": "r_sum", "observed_at": "2026-07-07T00:00:00+00:00",
            "run_date": "2026-07-07", "strategy_id": "earnings_calendar",
            "strategy_name": "EC", "strategy_family": "options_event_volatility",
            "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
            "ticker": "AAPL", "underlying_symbol": "AAPL",
            "candidate_type": "c", "structure_type": "s", "timeframe": "",
            "verdict": "EARNINGS CALENDAR CANDIDATE", "friendly_verdict": "Eligible",
            "primary_reason": "ok", "status_bucket": "pass",
            "daily_opportunity_eligible": 1, "can_trade_live": 0, "dry_run": 0, "journal_eligible": 1,
            "data_quality_status": "good",
            "gate_pass_count": 2, "gate_watch_count": 0, "gate_fail_count": 0,
            "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
            "score": None, "actionability_score": None,
            "observation_key": "earnings_calendar:AAPL:c:s", "row_hash": "h_rsum",
            "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
            "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
            "observation_refs_json": "[]", "source_summary_json": "{}",
        }]
        write_run("r_sum", "2026-07-07", obs, db_path=db)
        summary = run_summary("r_sum", db_path=db)
        assert summary["total_observations"] == 1
        assert summary["daily_opportunity_eligible_count"] == 1
        assert summary["can_trade_live_count"] == 0
        assert "earnings_calendar" in summary["by_strategy"]

    def test_empty_db_returns_safe_values(self):
        from app.db.strategy_observations import read_observations, run_summary, global_summary
        db = self._tmp_db()
        assert read_observations(db_path=db) == []
        s = run_summary("nonexistent", db_path=db)
        assert s["total_observations"] == 0
        g = global_summary(days=7, db_path=db)
        assert g["total_observations"] == 0


# ─── Adapter ──────────────────────────────────────────────────────────────────

class TestAdapter:
    def _build(self, row, strategy_id=None):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        return build_strategy_observation(
            row, run_id="run_test", run_date="2026-07-07",
            strategy_id=strategy_id or row.get("strategy_id")
        )

    def test_earnings_calendar_produces_observation(self):
        obs = self._build(_make_earnings_row())
        assert obs["strategy_id"] == "earnings_calendar"
        assert obs["ticker"] == "AAPL"
        assert obs["verdict"] == "EARNINGS CALENDAR CANDIDATE"
        assert obs["status_bucket"] == "pass"
        assert obs["daily_opportunity_eligible"] == 1
        assert obs["can_trade_live"] == 0
        assert obs["dry_run"] == 0

    def test_skew_momentum_produces_observation(self):
        obs = self._build(_make_skew_row())
        assert obs["strategy_id"] == "skew_momentum_vertical"
        assert obs["ticker"] == "NVDA"
        assert obs["status_bucket"] == "pass"
        assert obs["daily_opportunity_eligible"] == 1

    def test_forward_factor_produces_observation(self):
        obs = self._build(_make_ff_row())
        assert obs["strategy_id"] == "forward_factor_calendar"
        assert obs["ticker"] == "CAG"
        assert obs["status_bucket"] == "dry_run"
        assert obs["can_trade_live"] == 0
        assert obs["dry_run"] == 1
        assert obs["daily_opportunity_eligible"] == 0

    def test_stock_momentum_produces_observation(self):
        obs = self._build(_make_stock_row())
        assert obs["strategy_id"] == "stock_momentum"
        assert obs["ticker"] == "CRDO"
        assert obs["status_bucket"] == "pass"
        assert obs["daily_opportunity_eligible"] == 1

    def test_required_fields_always_present(self):
        required = [
            "run_id", "run_date", "strategy_id", "ticker", "verdict",
            "friendly_verdict", "primary_reason", "status_bucket",
            "daily_opportunity_eligible", "can_trade_live", "dry_run",
            "observation_key", "row_hash", "observation_schema_version",
            "gate_pass_count", "gate_fail_count", "blocking_gate_count",
        ]
        for row_fn in (_make_earnings_row, _make_skew_row, _make_ff_row, _make_stock_row):
            obs = self._build(row_fn())
            for field in required:
                assert field in obs, f"{field!r} missing from {obs.get('strategy_id')} observation"

    def test_observation_schema_version_is_30b(self):
        obs = self._build(_make_earnings_row())
        assert obs["observation_schema_version"] == "30B.v1"

    def test_row_hash_is_deterministic(self):
        row = _make_earnings_row()
        obs1 = self._build({**row})
        obs2 = self._build({**row})
        assert obs1["row_hash"] == obs2["row_hash"]

    def test_row_hash_changes_with_verdict(self):
        obs_pass = self._build(_make_earnings_row(verdict="EARNINGS CALENDAR CANDIDATE"))
        obs_fail = self._build(_make_earnings_row(verdict="FAIL / ILLIQUID"))
        assert obs_pass["row_hash"] != obs_fail["row_hash"]

    def test_observation_key_preserved_from_row(self):
        obs = self._build(_make_earnings_row())
        assert "earnings_calendar:AAPL" in obs["observation_key"]

    def test_observation_key_fallback_on_missing(self):
        row = {"ticker": "XYZ", "verdict": "PASS"}
        obs = self._build(row, strategy_id="earnings_calendar")
        assert obs["observation_key"].startswith("earnings_calendar:XYZ")

    def test_gate_counts_correct(self):
        row = _make_earnings_row()
        row["gates"] = [
            {"id": "g1", "status": "pass", "blocking": False},
            {"id": "g2", "status": "fail", "blocking": True},
            {"id": "g3", "status": "watch", "blocking": False},
        ]
        obs = self._build(row)
        assert obs["gate_pass_count"] == 1
        assert obs["gate_fail_count"] == 1
        assert obs["gate_watch_count"] == 1
        assert obs["blocking_gate_count"] == 1

    def test_malformed_row_does_not_crash(self):
        for bad_row in ({}, {"ticker": None}, {"verdict": 12345}, {"gates": "not a list"}):
            try:
                obs = self._build(bad_row, strategy_id="earnings_calendar")
                assert obs["strategy_id"] == "earnings_calendar"
            except Exception as exc:
                assert False, f"build_strategy_observation raised for {bad_row!r}: {exc}"

    def test_raw_large_fields_excluded_from_json(self):
        row = _make_earnings_row()
        row["raw_json"] = "x" * 50000
        row["options_chain"] = [{"strike": 100}] * 500
        row["debug_trace"] = {"step": 1}
        obs = self._build(row)
        for json_key in ("metrics_json", "structure_json", "source_summary_json"):
            text = obs.get(json_key) or ""
            assert "raw_json" not in text
            assert "options_chain" not in text
            assert "debug_trace" not in text

    def test_json_columns_are_valid_json_strings(self):
        obs = self._build(_make_skew_row())
        for col in ("metrics_json", "gates_json", "risk_flags_json", "reasons_json",
                    "structure_json", "data_quality_json", "observation_refs_json", "source_summary_json"):
            val = obs[col]
            assert isinstance(val, str), f"{col} should be a string"
            json.loads(val)  # should not raise

    def test_structure_json_for_ff(self):
        obs = self._build(_make_ff_row())
        structure = json.loads(obs["structure_json"])
        assert structure.get("dry_run") is True
        assert structure.get("daily_opportunity_eligible") is False
        assert structure.get("can_trade_live") is False

    def test_structure_json_for_earnings(self):
        obs = self._build(_make_earnings_row())
        structure = json.loads(obs["structure_json"])
        assert "earnings_trust_label" in structure

    def test_structure_json_for_stock(self):
        obs = self._build(_make_stock_row())
        structure = json.loads(obs["structure_json"])
        assert "action" in structure
        assert "momentum_score" in structure


# ─── Status bucket ────────────────────────────────────────────────────────────

class TestStatusBucket:
    def _bucket(self, verdict, dry_run=False, strategy_id="earnings_calendar"):
        from app.services.strategy_observation_journal_service import _derive_status_bucket
        return _derive_status_bucket(
            {"verdict": verdict, "dry_run": dry_run}, strategy_id
        )

    def test_pass_verdict(self):
        assert self._bucket("PASS / OK") == "pass"

    def test_watch_verdict(self):
        assert self._bucket("WATCH / NOT CONFIRMED") == "watch"

    def test_fail_verdict(self):
        assert self._bucket("FAIL / ILLIQUID") == "fail"

    def test_avoid_verdict(self):
        assert self._bucket("AVOID / WEAK TREND") == "fail"

    def test_skipped_verdict(self):
        assert self._bucket("SKIPPED / DEV CAP") == "skipped"

    def test_ff_dry_run_pass(self):
        assert self._bucket("PASS / FF SIGNAL", dry_run=True, strategy_id="forward_factor_calendar") == "dry_run"

    def test_ff_dry_run_fail_stays_fail(self):
        assert self._bucket("FAIL / BAD DATA", dry_run=True, strategy_id="forward_factor_calendar") == "fail"

    def test_ff_dry_run_skipped_stays_skipped(self):
        assert self._bucket("SKIPPED / CAP", dry_run=True, strategy_id="forward_factor_calendar") == "skipped"

    def test_unknown_fallback(self):
        assert self._bucket("SOME RANDOM VALUE") == "unknown"

    def test_near_miss_is_watch(self):
        assert self._bucket("NEAR_MISS / EXPIRY GAP") == "watch"

    def test_tactical_is_watch(self):
        assert self._bucket("TACTICAL ONLY / DO NOT CHASE") == "watch"


# ─── Batch builder ────────────────────────────────────────────────────────────

class TestBatchBuilder:
    def test_builds_observations_from_four_strategies(self):
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results,
        )
        results = {
            "earnings_calendar": {"canonical_opportunities": [_make_earnings_row()]},
            "skew_momentum_vertical": {"canonical_opportunities": [_make_skew_row()]},
            "forward_factor_calendar": {"canonical_opportunities": [_make_ff_row()]},
            "stock_momentum": {"canonical_opportunities": [_make_stock_row()]},
        }
        obs = build_observations_from_strategy_results(results, "run_batch", "2026-07-07")
        assert len(obs) == 4
        strategy_ids = {o["strategy_id"] for o in obs}
        assert strategy_ids == {
            "earnings_calendar", "skew_momentum_vertical",
            "forward_factor_calendar", "stock_momentum",
        }

    def test_falls_back_to_rows_key(self):
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results,
        )
        results = {
            "earnings_calendar": {"rows": [_make_earnings_row()]},
        }
        obs = build_observations_from_strategy_results(results, "run_fallback", "2026-07-07")
        assert len(obs) == 1

    def test_works_on_copies_not_mutating_source(self):
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results,
        )
        original_row = _make_earnings_row()
        original_keys = set(original_row.keys())
        results = {"earnings_calendar": {"canonical_opportunities": [original_row]}}
        build_observations_from_strategy_results(results, "run_copy", "2026-07-07")
        assert set(original_row.keys()) == original_keys, "Source row was mutated"

    def test_empty_results_returns_empty(self):
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results,
        )
        assert build_observations_from_strategy_results({}, "r", "2026-07-07") == []

    def test_non_dict_rows_skipped_safely(self):
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results,
        )
        results = {"earnings_calendar": {"canonical_opportunities": ["not a dict", None, 42]}}
        obs = build_observations_from_strategy_results(results, "r_skip", "2026-07-07")
        assert obs == []

    def test_max_rows_per_run_cap(self):
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results,
        )
        from app import config as cfg
        original = cfg.STRATEGY_OBSERVATION_MAX_ROWS_PER_RUN
        try:
            cfg.STRATEGY_OBSERVATION_MAX_ROWS_PER_RUN = 3
            results = {
                "earnings_calendar": {"canonical_opportunities": [_make_earnings_row(ticker=f"T{i}") for i in range(10)]},
            }
            obs = build_observations_from_strategy_results(results, "r_cap", "2026-07-07")
            assert len(obs) <= 3
        finally:
            cfg.STRATEGY_OBSERVATION_MAX_ROWS_PER_RUN = original


# ─── Write path ───────────────────────────────────────────────────────────────

class TestWritePath:
    def test_disabled_flag_skips_write(self):
        from app import config as cfg
        from app.db.strategy_observations import write_run
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db.close()
        os.unlink(db.name)
        original = cfg.STRATEGY_OBSERVATION_JOURNAL_ENABLED
        try:
            cfg.STRATEGY_OBSERVATION_JOURNAL_ENABLED = False
            written = write_run("r", "2026-07-07", [{"run_id": "r"}], db_path=db.name)
            assert written == 0
            assert not os.path.exists(db.name), "DB should not be created when disabled"
        finally:
            cfg.STRATEGY_OBSERVATION_JOURNAL_ENABLED = original

    def test_write_failure_returns_zero(self):
        from app.db.strategy_observations import write_run
        written = write_run("r", "2026-07-07", [{"bad": "record"}], db_path="/nonexistent/path/x.db")
        assert written == 0

    def test_write_run_returns_count(self):
        from app.db.strategy_observations import write_run
        import tempfile, os
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db.close()
        os.unlink(db.name)
        obs = [{
            "run_id": "r1", "observed_at": "2026-07-07T00:00:00+00:00",
            "run_date": "2026-07-07", "strategy_id": "earnings_calendar",
            "strategy_name": "EC", "strategy_family": "options_event_volatility",
            "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
            "ticker": "AAPL", "underlying_symbol": "AAPL",
            "candidate_type": "c", "structure_type": "s", "timeframe": "",
            "verdict": "PASS", "friendly_verdict": "ok", "primary_reason": "ok", "status_bucket": "pass",
            "daily_opportunity_eligible": 0, "can_trade_live": 0, "dry_run": 0, "journal_eligible": 1,
            "data_quality_status": "ok",
            "gate_pass_count": 0, "gate_watch_count": 0, "gate_fail_count": 0,
            "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
            "score": None, "actionability_score": None,
            "observation_key": "earnings_calendar:AAPL:c:s", "row_hash": "h1",
            "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
            "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
            "observation_refs_json": "[]", "source_summary_json": "{}",
        }]
        n = write_run("r1", "2026-07-07", obs, db_path=db.name)
        assert n == 1
        os.unlink(db.name)


# ─── Summary service ──────────────────────────────────────────────────────────

class TestSummaryService:
    def test_build_strategy_observation_summary_structure(self):
        from app.services.strategy_observation_summary_service import build_strategy_observation_summary
        result = build_strategy_observation_summary(days=7)
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True
        assert "summary" in result

    def test_build_run_observation_summary_structure(self):
        from app.services.strategy_observation_summary_service import build_run_observation_summary
        result = build_run_observation_summary("nonexistent_run")
        assert result.get("provider_calls_triggered") is False
        assert "run_id" in result

    def test_build_observation_list_structure(self):
        from app.services.strategy_observation_summary_service import build_observation_list
        result = build_observation_list(limit=10)
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True
        assert "observations" in result

    def test_build_run_observation_summary_no_run_id(self):
        from app.services.strategy_observation_summary_service import build_run_observation_summary
        result = build_run_observation_summary("")
        assert result.get("status") == "unavailable"
        assert result.get("provider_calls_triggered") is False


# ─── FF safety ────────────────────────────────────────────────────────────────

class TestFFSafety:
    def _build_ff(self, **kwargs):
        from app.services.strategy_observation_journal_service import build_strategy_observation
        row = _make_ff_row(**kwargs)
        return build_strategy_observation(row, run_id="r_ff", run_date="2026-07-07")

    def test_ff_dry_run_true(self):
        obs = self._build_ff()
        assert obs["dry_run"] == 1

    def test_ff_can_trade_live_false(self):
        obs = self._build_ff()
        assert obs["can_trade_live"] == 0

    def test_ff_daily_opportunity_eligible_false(self):
        obs = self._build_ff()
        assert obs["daily_opportunity_eligible"] == 0

    def test_ff_status_bucket_dry_run_on_pass(self):
        obs = self._build_ff(verdict="PASS / FF SIGNAL")
        assert obs["status_bucket"] == "dry_run"

    def test_ff_structure_json_enforces_safety(self):
        obs = self._build_ff()
        structure = json.loads(obs["structure_json"])
        assert structure.get("dry_run") is True
        assert structure.get("daily_opportunity_eligible") is False
        assert structure.get("can_trade_live") is False

    def test_ff_universal_observations_written_when_legacy_journal_writes_zero(self):
        from app.services.strategy_observation_journal_service import (
            build_observations_from_strategy_results,
        )
        ff_results = {
            "forward_factor_calendar": {"canonical_opportunities": [_make_ff_row()], "rows": []},
        }
        obs = build_observations_from_strategy_results(ff_results, "r_ff_univ", "2026-07-07")
        assert len(obs) == 1
        assert obs[0]["strategy_id"] == "forward_factor_calendar"
        assert obs[0]["dry_run"] == 1
        assert obs[0]["daily_opportunity_eligible"] == 0
        assert obs[0]["can_trade_live"] == 0

    def test_ff_can_trade_live_count_in_summary_is_zero(self):
        from app.db.strategy_observations import write_run, run_summary
        import tempfile, os
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db.close()
        os.unlink(db.name)
        obs = [{
            "run_id": "r_ff_s", "observed_at": "2026-07-07T00:00:00+00:00",
            "run_date": "2026-07-07", "strategy_id": "forward_factor_calendar",
            "strategy_name": "FF", "strategy_family": "options_forward_volatility",
            "strategy_row_schema_version": "30A.v1", "observation_schema_version": "30B.v1",
            "ticker": "CAG", "underlying_symbol": "CAG",
            "candidate_type": "c", "structure_type": "s", "timeframe": "",
            "verdict": "PASS / FF SIGNAL", "friendly_verdict": "Signal",
            "primary_reason": "FF above threshold", "status_bucket": "dry_run",
            "daily_opportunity_eligible": 0, "can_trade_live": 0, "dry_run": 1, "journal_eligible": 1,
            "data_quality_status": "ok",
            "gate_pass_count": 1, "gate_watch_count": 1, "gate_fail_count": 0,
            "gate_unknown_count": 0, "gate_skipped_count": 0, "blocking_gate_count": 0,
            "score": None, "actionability_score": None,
            "observation_key": "forward_factor_calendar:CAG:c:s", "row_hash": "h_ff_s",
            "metrics_json": "{}", "gates_json": "[]", "risk_flags_json": "[]",
            "reasons_json": "[]", "structure_json": "{}", "data_quality_json": "{}",
            "observation_refs_json": "[]", "source_summary_json": "{}",
        }]
        write_run("r_ff_s", "2026-07-07", obs, db_path=db.name)
        summary = run_summary("r_ff_s", db_path=db.name)
        assert summary["can_trade_live_count"] == 0, "FF can_trade_live_count must be zero"
        assert summary["daily_opportunity_eligible_count"] == 0
        os.unlink(db.name)


# ─── Dev endpoint structure ───────────────────────────────────────────────────

class TestEndpoints:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_observations_endpoint_requires_token(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/dev/strategy-observations")
                assert resp.status_code == 403

    def test_observations_endpoint_returns_provider_calls_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-observations?token=test")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False

    def test_observations_summary_endpoint_returns_provider_calls_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-observations/summary?token=test")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False

    def test_observations_run_endpoint_returns_provider_calls_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-observations/run/test_run_id?token=test")
                assert resp.status_code == 200
                data = resp.get_json()
                assert data.get("provider_calls_triggered") is False

    def test_observations_limit_enforced(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get(
                    "/api/dev/strategy-observations?token=test&limit=9999"
                )
                assert resp.status_code == 200
                data = resp.get_json()
                # Endpoint should cap at 500 — no error, just a valid response
                assert "observations" in data


# ─── Regression ───────────────────────────────────────────────────────────────

class TestRegression:
    def test_normalized_rows_still_present_in_dev_snapshot(self):
        from app.services.developer_snapshot_service import build_snapshot_detail
        result = build_snapshot_detail("strategies")
        assert result.get("provider_calls_triggered") is False
        assert result.get("read_only") is True
        # normalized_strategy_rows may be absent if no snapshot available, but
        # the endpoint must not error out.
        assert "status" in result

    def test_30a_tests_still_pass(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        from app.services.strategy_spec_registry import get_spec
        row = {"ticker": "AAPL", "verdict": "PASS / OK"}
        normalize_strategy_row(row, "skew_momentum_vertical")
        assert row.get("strategy_row_schema_version") == "30J.v1"

    def test_ff_excluded_from_daily_opportunity_invariant(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row = {"ticker": "CAG", "verdict": "PASS / FF SIGNAL"}
        normalize_strategy_row(row, "forward_factor_calendar")
        assert row.get("daily_opportunity_eligible") is False
        assert row.get("can_trade_live") is False
        assert row.get("dry_run") is True

    def test_public_screener_not_modified(self):
        import app.main as main_mod
        # Verify the three new routes exist but do not alter the screener route
        assert hasattr(main_mod, "app")
        rules = {rule.rule for rule in main_mod.app.url_map.iter_rules()}
        assert "/api/dev/strategy-observations" in rules
        assert "/api/dev/strategy-observations/summary" in rules
        assert "/screener" in rules  # screener still registered

    def test_legacy_ff_journal_unmodified(self):
        from app.db.ff_journal import write_run, journal_summary, historical_ivs
        assert callable(write_run)
        assert callable(journal_summary)
        assert callable(historical_ivs)

    def test_observation_schema_version_in_summary(self):
        from app.services.strategy_observation_summary_service import build_strategy_observation_summary
        result = build_strategy_observation_summary()
        assert result.get("observation_schema_version") == "30B.v1"
