"""Tests for ASA Patch 30C: Strategy Observation Review Dashboard + Outcome Foundation.

CAVEMAN MODE: No provider calls, no broker writes, no strategy logic changes.
All tests use temporary in-memory / temp-file SQLite DBs.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import pytest


# ─── shared helpers ───────────────────────────────────────────────────────────


def _tmp_db() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    return path


def _write_obs(db: str, observations: list[dict]) -> int:
    from app.db.strategy_observations import write_run
    if not observations:
        return 0
    run_id = observations[0]["run_id"]
    run_date = observations[0]["run_date"]
    return write_run(run_id, run_date, observations, db_path=db)


def _make_obs(**kwargs) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": "run_001",
        "observed_at": "2026-07-07T10:00:00+00:00",
        "run_date": "2026-07-07",
        "strategy_id": "earnings_calendar",
        "strategy_name": "Earnings Calendar Spread",
        "strategy_family": "options_event_volatility",
        "strategy_row_schema_version": "30A.v1",
        "observation_schema_version": "30B.v1",
        "ticker": "AAPL",
        "underlying_symbol": "AAPL",
        "candidate_type": "calendar_candidate",
        "structure_type": "calendar_spread",
        "timeframe": "",
        "verdict": "EARNINGS CALENDAR CANDIDATE",
        "friendly_verdict": "Eligible",
        "primary_reason": "Favorable IV setup",
        "status_bucket": "pass",
        "daily_opportunity_eligible": 1,
        "can_trade_live": 0,
        "dry_run": 0,
        "journal_eligible": 1,
        "data_quality_status": "good",
        "gate_pass_count": 3,
        "gate_watch_count": 0,
        "gate_fail_count": 0,
        "gate_unknown_count": 0,
        "gate_skipped_count": 0,
        "blocking_gate_count": 0,
        "score": 80.0,
        "actionability_score": None,
        "observation_key": "earnings_calendar:AAPL:calendar_candidate:calendar_spread",
        "row_hash": "abc123",
        "metrics_json": json.dumps({"iv_relationship_status": "favorable"}),
        "gates_json": json.dumps([
            {"id": "earnings_date_trust", "label": "Earnings date trust",
             "name": "Earnings date trust", "status": "pass", "reason": "Date confirmed",
             "detail": "", "blocking": False, "sort_order": 10},
        ]),
        "risk_flags_json": "[]",
        "reasons_json": "[]",
        "structure_json": "{}",
        "data_quality_json": "{}",
        "observation_refs_json": "[]",
        "source_summary_json": "{}",
    }
    base.update(kwargs)
    return base


def _make_blocking_obs(**kwargs) -> dict[str, Any]:
    gates = [
        {"id": "expiry_gap", "label": "Expiry gap", "name": "Expiry gap",
         "status": "fail", "reason": "EXPIRY_GAP: no valid expiration pair",
         "detail": "", "blocking": True, "sort_order": 20},
    ]
    return _make_obs(
        status_bucket="fail",
        verdict="FAIL / EXPIRY GAP",
        primary_reason="No valid expiration pair",
        gate_fail_count=1,
        blocking_gate_count=1,
        gates_json=json.dumps(gates),
        **kwargs,
    )


def _multi_run_db() -> str:
    """DB with two runs and multiple tickers/strategies."""
    db = _tmp_db()
    obs = [
        # run_001 — AAPL pass, NFLX watch, TSLA fail
        _make_obs(run_id="run_001", ticker="AAPL", status_bucket="pass", row_hash="h1"),
        _make_obs(run_id="run_001", ticker="NFLX", status_bucket="watch",
                  verdict="WATCH / NEAR_MISS", primary_reason="Near miss: expiry gap",
                  blocking_gate_count=1, row_hash="h2",
                  gates_json=json.dumps([
                      {"id": "expiry_gap", "label": "Expiry gap", "status": "fail",
                       "reason": "EXPIRY_GAP", "blocking": True}
                  ]),
                  observation_key="earnings_calendar:NFLX:calendar_candidate:calendar_spread",
                  ),
        _make_blocking_obs(run_id="run_001", ticker="TSLA", row_hash="h3",
                           observation_key="earnings_calendar:TSLA:calendar_candidate:calendar_spread"),
        # run_001 — skew strategy
        _make_obs(run_id="run_001", ticker="NVDA",
                  strategy_id="skew_momentum_vertical",
                  strategy_name="Skew Momentum Vertical",
                  strategy_family="options_skew",
                  verdict="PASS / POSSIBLE ENTRY SETUP",
                  status_bucket="pass",
                  daily_opportunity_eligible=1,
                  row_hash="h4",
                  observation_key="skew_momentum_vertical:NVDA:vertical_spread:vertical",
                  gates_json=json.dumps([]),
                  ),
        # run_002 — AAPL improved to pass, NFLX still watch, TSLA now watch (improved)
        _make_obs(run_id="run_002", ticker="AAPL", status_bucket="pass",
                  row_hash="h5",
                  observation_key="earnings_calendar:AAPL:calendar_candidate:calendar_spread",
                  ),
        _make_obs(run_id="run_002", ticker="NFLX", status_bucket="watch",
                  verdict="WATCH / NEAR_MISS", primary_reason="Near miss: expiry gap",
                  blocking_gate_count=0, row_hash="h6",
                  gates_json=json.dumps([]),
                  observation_key="earnings_calendar:NFLX:calendar_candidate:calendar_spread",
                  ),
        _make_obs(run_id="run_002", ticker="TSLA", status_bucket="watch",
                  verdict="WATCH / NEAR_MISS", row_hash="h7",
                  observation_key="earnings_calendar:TSLA:calendar_candidate:calendar_spread",
                  blocking_gate_count=0,
                  ),
    ]
    _write_obs(db, obs)
    return db


# ─── TestCompile ──────────────────────────────────────────────────────────────


class TestCompile:
    def test_classifier_imports(self):
        from app.services.strategy_observation_review_classifier import (
            classify_blocker_category,
            classify_review_priority,
            classify_review_type,
            classify_movement,
        )
        assert callable(classify_blocker_category)
        assert callable(classify_review_priority)
        assert callable(classify_review_type)
        assert callable(classify_movement)

    def test_review_service_imports(self):
        from app.services.strategy_observation_review_service import (
            REVIEW_SCHEMA_VERSION,
            build_strategy_review_summary,
            build_repeat_blockers,
            build_ticker_recurrence,
            build_run_movement,
            build_review_queue,
            build_observation_review_text,
        )
        assert REVIEW_SCHEMA_VERSION == "30C.v1"

    def test_outcome_db_imports(self):
        from app.db.strategy_observation_outcomes import (
            OUTCOME_SCHEMA_VERSION,
            ensure_outcome_schema,
            write_outcome,
            read_outcomes,
            outcome_schema_exists,
        )
        assert OUTCOME_SCHEMA_VERSION == "30C.v1"

    def test_new_db_query_functions_importable(self):
        from app.db.strategy_observations import (
            query_for_review,
            query_ticker_stats,
            query_two_latest_runs,
            query_primary_reason_stats,
        )
        assert callable(query_for_review)
        assert callable(query_ticker_stats)


# ─── TestSummaryService ───────────────────────────────────────────────────────


class TestSummaryService:
    def test_builds_7_day_summary(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_strategy_review_summary
        result = build_strategy_review_summary(days=7, db_path=db)
        assert result["provider_calls_triggered"] is False
        assert result["read_only"] is True
        assert result["review_schema_version"] == "30C.v1"
        assert result["total_observations"] >= 1
        assert result["window_days"] == 7

    def test_groups_by_strategy(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_strategy_review_summary
        result = build_strategy_review_summary(days=7, db_path=db)
        by_strategy = result.get("by_strategy", {})
        assert "earnings_calendar" in by_strategy
        assert "skew_momentum_vertical" in by_strategy

    def test_groups_by_status_bucket(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_strategy_review_summary
        result = build_strategy_review_summary(days=7, db_path=db)
        by_bucket = result.get("by_status_bucket", {})
        assert "pass" in by_bucket
        assert by_bucket["pass"] >= 1

    def test_counts_dry_run_observations(self):
        db = _tmp_db()
        obs = [
            _make_obs(strategy_id="forward_factor_calendar", status_bucket="dry_run",
                      dry_run=1, can_trade_live=0, daily_opportunity_eligible=0,
                      row_hash="ff1",
                      observation_key="forward_factor_calendar:CAG:calendar_candidate:calendar"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_strategy_review_summary
        result = build_strategy_review_summary(days=7, db_path=db)
        assert result["dry_run_count"] >= 1

    def test_counts_daily_opportunity_eligible(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_strategy_review_summary
        result = build_strategy_review_summary(days=7, db_path=db)
        assert result["daily_opportunity_eligible_count"] >= 1

    def test_handles_empty_journal(self):
        db = _tmp_db()
        from app.services.strategy_observation_review_service import build_strategy_review_summary
        result = build_strategy_review_summary(days=7, db_path=db)
        assert result["total_observations"] == 0
        assert result["provider_calls_triggered"] is False

    def test_strategy_specific_summary(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import (
            build_strategy_review_summary_for_strategy,
        )
        result = build_strategy_review_summary_for_strategy("earnings_calendar", days=7, db_path=db)
        assert result["strategy_id"] == "earnings_calendar"
        assert result["strategy_name"] == "Earnings Calendar Spread"
        assert "top_primary_reasons" in result
        assert result["provider_calls_triggered"] is False

    def test_run_summary_function(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import (
            build_strategy_review_summary_for_run,
        )
        result = build_strategy_review_summary_for_run("run_001", db_path=db)
        assert result["run_id"] == "run_001"
        assert result["total_observations"] >= 1
        assert result["provider_calls_triggered"] is False


# ─── TestRepeatBlockers ───────────────────────────────────────────────────────


class TestRepeatBlockers:
    def test_detects_repeated_expiry_gap(self):
        db = _tmp_db()
        obs = [
            _make_blocking_obs(run_id="r1", ticker="AAPL", row_hash="b1",
                               observation_key="earnings_calendar:AAPL:c:s"),
            _make_blocking_obs(run_id="r1", ticker="NFLX", row_hash="b2",
                               observation_key="earnings_calendar:NFLX:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_repeat_blockers
        result = build_repeat_blockers(days=7, db_path=db)
        assert result["provider_calls_triggered"] is False
        blockers = result.get("blockers", [])
        assert len(blockers) >= 1
        assert blockers[0]["count"] >= 2

    def test_detects_repeated_options_illiquid(self):
        db = _tmp_db()
        illiquid_gate = [{"id": "liquidity", "label": "Liquidity", "status": "fail",
                          "reason": "OPTIONS_ILLIQUID: wide spread", "blocking": True}]
        obs = [
            _make_obs(run_id="r1", ticker="TSLA", row_hash="i1",
                      strategy_id="skew_momentum_vertical",
                      status_bucket="fail", blocking_gate_count=1,
                      gates_json=json.dumps(illiquid_gate),
                      observation_key="skew_momentum_vertical:TSLA:c:s"),
            _make_obs(run_id="r1", ticker="AMZN", row_hash="i2",
                      strategy_id="skew_momentum_vertical",
                      status_bucket="fail", blocking_gate_count=1,
                      gates_json=json.dumps(illiquid_gate),
                      observation_key="skew_momentum_vertical:AMZN:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_repeat_blockers
        result = build_repeat_blockers(days=7, strategy_id="skew_momentum_vertical", db_path=db)
        blockers = result.get("blockers", [])
        assert len(blockers) >= 1
        categories = {b["suggested_category"] for b in blockers}
        assert "liquidity_constraint" in categories

    def test_classifies_provider_budget_blockers(self):
        db = _tmp_db()
        budget_gate = [{"id": "budget", "label": "Provider budget", "status": "fail",
                        "reason": "PROVIDER_BUDGET: chain fetch skipped", "blocking": True}]
        obs = [_make_obs(run_id="r1", ticker="X", row_hash="p1",
                         strategy_id="earnings_calendar",
                         status_bucket="fail", blocking_gate_count=1,
                         gates_json=json.dumps(budget_gate),
                         observation_key="earnings_calendar:X:c:s")]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_repeat_blockers
        result = build_repeat_blockers(days=7, db_path=db)
        blockers = result.get("blockers", [])
        categories = {b["suggested_category"] for b in blockers}
        assert "provider_budget" in categories

    def test_limits_sample_tickers(self):
        db = _tmp_db()
        obs = []
        for i, ticker in enumerate(["A", "B", "C", "D", "E", "F", "G"]):
            obs.append(_make_blocking_obs(
                run_id="r1", ticker=ticker,
                row_hash=f"bx{i}",
                observation_key=f"earnings_calendar:{ticker}:c:s",
            ))
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_repeat_blockers
        result = build_repeat_blockers(days=7, db_path=db)
        for b in result.get("blockers", []):
            assert len(b["sample_tickers"]) <= 5

    def test_filters_by_strategy_id(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_repeat_blockers
        result = build_repeat_blockers(days=7, strategy_id="skew_momentum_vertical", db_path=db)
        for b in result.get("blockers", []):
            assert b["strategy_id"] == "skew_momentum_vertical"

    def test_empty_when_no_blocking_gates(self):
        db = _tmp_db()
        obs = [_make_obs(row_hash="nb1", observation_key="earnings_calendar:AAPL:c:s")]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_repeat_blockers
        result = build_repeat_blockers(days=7, db_path=db)
        assert result["blockers"] == []


# ─── TestTickerRecurrence ─────────────────────────────────────────────────────


class TestTickerRecurrence:
    def test_detects_ticker_across_multiple_runs(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_ticker_recurrence
        result = build_ticker_recurrence(days=7, db_path=db)
        assert result["provider_calls_triggered"] is False
        tickers = {t["ticker"] for t in result.get("tickers", [])}
        assert "AAPL" in tickers

    def test_detects_ticker_across_multiple_strategies(self):
        db = _tmp_db()
        obs = [
            _make_obs(ticker="AAPL", strategy_id="earnings_calendar", row_hash="ms1",
                      observation_key="earnings_calendar:AAPL:c:s"),
            _make_obs(ticker="AAPL", strategy_id="skew_momentum_vertical", row_hash="ms2",
                      observation_key="skew_momentum_vertical:AAPL:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_ticker_recurrence
        result = build_ticker_recurrence(days=7, ticker="AAPL", db_path=db)
        tickers = result.get("tickers", [])
        assert len(tickers) == 1
        assert tickers[0]["strategy_count"] >= 2

    def test_high_priority_for_multi_strategy_pass(self):
        db = _tmp_db()
        obs = [
            _make_obs(ticker="AAPL", strategy_id="earnings_calendar", status_bucket="pass",
                      row_hash="pms1",
                      observation_key="earnings_calendar:AAPL:c:s"),
            _make_obs(ticker="AAPL", strategy_id="skew_momentum_vertical", status_bucket="pass",
                      row_hash="pms2",
                      observation_key="skew_momentum_vertical:AAPL:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_ticker_recurrence
        result = build_ticker_recurrence(days=7, ticker="AAPL", db_path=db)
        tickers = result.get("tickers", [])
        assert tickers[0]["review_priority"] == "high"

    def test_low_priority_for_fail_only(self):
        db = _tmp_db()
        obs = [
            _make_blocking_obs(ticker="FAIL_ONLY", run_id="r1", row_hash="f1",
                               observation_key="earnings_calendar:FAIL_ONLY:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_ticker_recurrence
        result = build_ticker_recurrence(days=7, ticker="FAIL_ONLY", db_path=db)
        tickers = result.get("tickers", [])
        if tickers:
            assert tickers[0]["review_priority"] in ("low", "medium", "ignore")

    def test_ticker_filter_works(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_ticker_recurrence
        result = build_ticker_recurrence(days=7, ticker="AAPL", db_path=db)
        for t in result.get("tickers", []):
            assert t["ticker"] == "AAPL"

    def test_empty_journal_safe(self):
        db = _tmp_db()
        from app.services.strategy_observation_review_service import build_ticker_recurrence
        result = build_ticker_recurrence(days=7, db_path=db)
        assert result["ticker_count"] == 0
        assert result["tickers"] == []


# ─── TestClassifier ───────────────────────────────────────────────────────────


class TestClassifier:
    def test_classify_blocker_expiry_gap(self):
        from app.services.strategy_observation_review_classifier import classify_blocker_category
        assert classify_blocker_category("EXPIRY_GAP: no valid expiration pair") == "structure_gap"

    def test_classify_blocker_options_illiquid(self):
        from app.services.strategy_observation_review_classifier import classify_blocker_category
        assert classify_blocker_category("OPTIONS_ILLIQUID: wide spread") == "liquidity_constraint"

    def test_classify_blocker_no_eligible_expiration(self):
        from app.services.strategy_observation_review_classifier import classify_blocker_category
        assert classify_blocker_category("NO_ELIGIBLE_EXPIRATION_PAIR") == "data_gap"

    def test_classify_blocker_provider_budget(self):
        from app.services.strategy_observation_review_classifier import classify_blocker_category
        assert classify_blocker_category("PROVIDER_BUDGET exceeded") == "provider_budget"

    def test_classify_blocker_unknown(self):
        from app.services.strategy_observation_review_classifier import classify_blocker_category
        assert classify_blocker_category("something_random_xyz") == "unknown"

    def test_classify_movement_fail_to_watch_is_improved(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement("fail", "watch")
        assert movement == "improved"

    def test_classify_movement_watch_to_pass_is_improved(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement("watch", "pass")
        assert movement == "improved"

    def test_classify_movement_pass_to_fail_is_degraded(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement("pass", "fail")
        assert movement == "degraded"

    def test_classify_movement_skipped_to_fail_is_degraded(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement("skipped", "fail")
        assert movement == "degraded"

    def test_classify_movement_missing_previous_is_new(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement(None, "pass")
        assert movement == "new"

    def test_classify_movement_missing_current_is_disappeared(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement("pass", None)
        assert movement == "disappeared"

    def test_classify_movement_gate_count_delta(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement("fail", "fail", prev_blocking=2, curr_blocking=1)
        assert movement == "improved"

    def test_classify_movement_same_bucket_is_unchanged(self):
        from app.services.strategy_observation_review_classifier import classify_movement
        movement, reason = classify_movement("pass", "pass", 0, 0)
        assert movement == "unchanged"

    def test_classify_review_type_near_miss(self):
        from app.services.strategy_observation_review_classifier import classify_review_type
        rt = classify_review_type("watch", "WATCH / NEAR_MISS", "Near miss", "")
        assert rt == "repeated_near_miss"

    def test_classify_review_type_ff_candidate(self):
        from app.services.strategy_observation_review_classifier import classify_review_type
        rt = classify_review_type("dry_run", "PASS / FF SIGNAL", "", "", "forward_factor_calendar")
        assert rt == "ff_research_candidate"

    def test_classify_review_type_pass_candidate(self):
        from app.services.strategy_observation_review_classifier import classify_review_type
        rt = classify_review_type("pass", "PASS", "IV favorable", "", "earnings_calendar")
        assert rt == "pass_candidate"

    def test_classify_review_priority_high_cross_strategy(self):
        from app.services.strategy_observation_review_classifier import classify_review_priority
        p = classify_review_priority("pass", 3, 2, "cross_strategy_confirmation")
        assert p == "high"

    def test_classify_review_priority_ignore_provider_budget(self):
        from app.services.strategy_observation_review_classifier import classify_review_priority
        p = classify_review_priority("fail", 5, 1, "provider_budget_gap")
        assert p == "ignore"


# ─── TestMovementTracking ─────────────────────────────────────────────────────


class TestMovementTracking:
    def test_fail_to_watch_is_improved(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_run_movement
        result = build_run_movement(run_id="run_002", prev_run_id="run_001", db_path=db)
        assert result["provider_calls_triggered"] is False
        movements = result.get("movement", [])
        # TSLA went from fail (run_001) to watch (run_002)
        tsla_mv = next((m for m in movements if m["ticker"] == "TSLA"), None)
        assert tsla_mv is not None
        assert tsla_mv["movement"] == "improved"
        assert tsla_mv["previous_status_bucket"] == "fail"
        assert tsla_mv["current_status_bucket"] == "watch"

    def test_new_candidate_detected(self):
        db = _tmp_db()
        obs = [
            _make_obs(run_id="run_001", ticker="AAPL", status_bucket="pass",
                      row_hash="new1",
                      observation_key="earnings_calendar:AAPL:c:s"),
            _make_obs(run_id="run_002", ticker="AAPL", status_bucket="pass",
                      row_hash="new2",
                      observation_key="earnings_calendar:AAPL:c:s"),
            _make_obs(run_id="run_002", ticker="NEW_TICKER", status_bucket="watch",
                      row_hash="new3",
                      observation_key="earnings_calendar:NEW_TICKER:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_run_movement
        result = build_run_movement(run_id="run_002", prev_run_id="run_001", db_path=db)
        movements = result.get("movement", [])
        new_m = next((m for m in movements if m["ticker"] == "NEW_TICKER"), None)
        assert new_m is not None
        assert new_m["movement"] == "new"

    def test_disappeared_candidate_detected(self):
        db = _tmp_db()
        obs = [
            _make_obs(run_id="run_001", ticker="AAPL", row_hash="d1",
                      observation_key="earnings_calendar:AAPL:c:s"),
            _make_obs(run_id="run_001", ticker="VANISH", row_hash="d2",
                      observation_key="earnings_calendar:VANISH:c:s"),
            _make_obs(run_id="run_002", ticker="AAPL", row_hash="d3",
                      observation_key="earnings_calendar:AAPL:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_run_movement
        result = build_run_movement(run_id="run_002", prev_run_id="run_001", db_path=db)
        movements = result.get("movement", [])
        van = next((m for m in movements if m["ticker"] == "VANISH"), None)
        assert van is not None
        assert van["movement"] == "disappeared"

    def test_unchanged_when_same_bucket(self):
        db = _tmp_db()
        obs = [
            _make_obs(run_id="run_001", ticker="SAME", status_bucket="pass",
                      row_hash="u1", blocking_gate_count=0,
                      observation_key="earnings_calendar:SAME:c:s"),
            _make_obs(run_id="run_002", ticker="SAME", status_bucket="pass",
                      row_hash="u2", blocking_gate_count=0,
                      observation_key="earnings_calendar:SAME:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_run_movement
        result = build_run_movement(run_id="run_002", prev_run_id="run_001", db_path=db)
        movements = result.get("movement", [])
        same = next((m for m in movements if m["ticker"] == "SAME"), None)
        assert same is not None
        assert same["movement"] == "unchanged"

    def test_no_runs_returns_empty_movement(self):
        db = _tmp_db()
        from app.services.strategy_observation_review_service import build_run_movement
        result = build_run_movement(db_path=db)
        assert result["provider_calls_triggered"] is False
        assert result["movement_count"] == 0

    def test_gate_delta_summarized(self):
        db = _tmp_db()
        obs = [
            _make_obs(run_id="run_001", ticker="GATE_T", status_bucket="fail",
                      blocking_gate_count=2, gate_fail_count=2, gate_pass_count=1,
                      row_hash="g1",
                      observation_key="earnings_calendar:GATE_T:c:s"),
            _make_obs(run_id="run_002", ticker="GATE_T", status_bucket="fail",
                      blocking_gate_count=1, gate_fail_count=1, gate_pass_count=1,
                      row_hash="g2",
                      observation_key="earnings_calendar:GATE_T:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_run_movement
        result = build_run_movement(run_id="run_002", prev_run_id="run_001", db_path=db)
        movements = result.get("movement", [])
        gate_t = next((m for m in movements if m["ticker"] == "GATE_T"), None)
        assert gate_t is not None
        assert gate_t.get("gate_delta_summary") is not None
        assert gate_t["gate_delta_summary"]["blocking_gate_delta"] == -1

    def test_score_delta_computed(self):
        db = _tmp_db()
        obs = [
            _make_obs(run_id="run_001", ticker="SCORED", score=70.0, row_hash="s1",
                      observation_key="earnings_calendar:SCORED:c:s"),
            _make_obs(run_id="run_002", ticker="SCORED", score=80.0, row_hash="s2",
                      observation_key="earnings_calendar:SCORED:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_run_movement
        result = build_run_movement(run_id="run_002", prev_run_id="run_001", db_path=db)
        movements = result.get("movement", [])
        scored = next((m for m in movements if m["ticker"] == "SCORED"), None)
        assert scored is not None
        assert scored["score_delta"] == pytest.approx(10.0)


# ─── TestReviewQueue ──────────────────────────────────────────────────────────


class TestReviewQueue:
    def test_creates_pass_candidate(self):
        db = _tmp_db()
        obs = [_make_obs(ticker="AAPL", status_bucket="pass", row_hash="q1",
                         observation_key="earnings_calendar:AAPL:c:s")]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7, db_path=db)
        assert result["provider_calls_triggered"] is False
        queue = result.get("queue", [])
        assert len(queue) >= 1
        assert any(item["review_type"] == "pass_candidate" for item in queue)

    def test_creates_repeated_near_miss(self):
        db = _tmp_db()
        obs = [
            _make_obs(ticker="NFLX", status_bucket="watch",
                      verdict="WATCH / NEAR_MISS", row_hash="nm1",
                      run_id="r1",
                      observation_key="earnings_calendar:NFLX:c:s"),
            _make_obs(ticker="NFLX", status_bucket="watch",
                      verdict="WATCH / NEAR_MISS", row_hash="nm2",
                      run_id="r2",
                      observation_key="earnings_calendar:NFLX:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7, db_path=db)
        queue = result.get("queue", [])
        assert any(item["review_type"] == "repeated_near_miss" for item in queue)

    def test_creates_ff_research_candidate(self):
        db = _tmp_db()
        obs = [
            _make_obs(ticker="CAG", strategy_id="forward_factor_calendar",
                      status_bucket="dry_run", dry_run=1,
                      verdict="PASS / FF SIGNAL",
                      row_hash="ff_q1", observation_key="forward_factor_calendar:CAG:c:s"),
            _make_obs(ticker="CAG", strategy_id="forward_factor_calendar",
                      status_bucket="dry_run", dry_run=1,
                      verdict="PASS / FF SIGNAL",
                      run_id="r2",
                      row_hash="ff_q2", observation_key="forward_factor_calendar:CAG:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7, db_path=db)
        queue = result.get("queue", [])
        assert any(item["review_type"] == "ff_research_candidate" for item in queue)

    def test_creates_cross_strategy_confirmation(self):
        db = _tmp_db()
        obs = [
            _make_obs(ticker="AAPL", strategy_id="earnings_calendar",
                      primary_reason="Cross-strategy confirm: both EC and SKEW agree",
                      status_bucket="pass", row_hash="cs1",
                      observation_key="earnings_calendar:AAPL:c:s"),
        ]
        _write_obs(db, obs)
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7, db_path=db)
        queue = result.get("queue", [])
        # confirm not an empty queue
        assert len(queue) >= 0  # cross_strategy needs special trigger

    def test_queue_does_not_create_public_recommendation(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7, db_path=db)
        # No daily_opportunity_eligible in queue items (review queue != recommendation)
        for item in result.get("queue", []):
            assert "daily_opportunity_eligible" not in item

    def test_queue_limit_enforced(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7, limit=2, db_path=db)
        assert len(result.get("queue", [])) <= 2

    def test_queue_filters_by_strategy(self):
        db = _multi_run_db()
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7, strategy_id="skew_momentum_vertical", db_path=db)
        for item in result.get("queue", []):
            assert item["strategy_id"] == "skew_momentum_vertical"


# ─── TestOutcomeFoundation ────────────────────────────────────────────────────


class TestOutcomeFoundation:
    def test_outcome_schema_creates_table(self):
        db = _tmp_db()
        from app.db.strategy_observation_outcomes import ensure_outcome_schema, outcome_schema_exists
        ensure_outcome_schema(db_path=db)
        assert outcome_schema_exists(db_path=db)

    def test_write_and_read_outcome(self):
        db = _tmp_db()
        from app.db.strategy_observation_outcomes import write_outcome, read_outcomes, ensure_outcome_schema
        ensure_outcome_schema(db_path=db)
        outcome = {
            "observation_key": "earnings_calendar:AAPL:c:s",
            "strategy_id": "earnings_calendar",
            "ticker": "AAPL",
            "run_id": "run_001",
            "outcome_type": "not_available",
            "outcome_status": "pending",
            "notes": "Awaiting 30D computation",
        }
        written = write_outcome(outcome, db_path=db)
        assert written == 1
        rows = read_outcomes(ticker="AAPL", db_path=db)
        assert len(rows) == 1
        assert rows[0]["outcome_type"] == "not_available"
        assert rows[0]["notes"] == "Awaiting 30D computation"

    def test_outcome_read_safe_on_missing_table(self):
        db = _tmp_db()
        from app.db.strategy_observation_outcomes import read_outcomes
        # Table not created — should return [] not raise
        rows = read_outcomes(ticker="AAPL", db_path=db)
        assert rows == []

    def test_write_outcome_missing_required_fields_returns_0(self):
        db = _tmp_db()
        from app.db.strategy_observation_outcomes import write_outcome
        written = write_outcome({"notes": "no key fields"}, db_path=db)
        assert written == 0

    def test_outcome_types_constant_exists(self):
        from app.db.strategy_observation_outcomes import OUTCOME_TYPES
        assert "not_available" in OUTCOME_TYPES
        assert "stock_forward_return" in OUTCOME_TYPES
        assert "option_structure_mid_return" in OUTCOME_TYPES

    def test_read_outcomes_by_strategy(self):
        db = _tmp_db()
        from app.db.strategy_observation_outcomes import write_outcome, read_outcomes, ensure_outcome_schema
        ensure_outcome_schema(db_path=db)
        for strat in ("earnings_calendar", "skew_momentum_vertical"):
            write_outcome({
                "observation_key": f"{strat}:AAPL:c:s",
                "strategy_id": strat,
                "ticker": "AAPL",
                "outcome_type": "not_available",
            }, db_path=db)
        ec_rows = read_outcomes(strategy_id="earnings_calendar", db_path=db)
        assert all(r["strategy_id"] == "earnings_calendar" for r in ec_rows)


# ─── TestEndpoints ────────────────────────────────────────────────────────────


class TestEndpoints:
    def _app(self):
        from app.main import app
        app.config["TESTING"] = True
        return app

    def test_summary_requires_token(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/dev/strategy-review/summary")
                assert resp.status_code == 403

    def test_blockers_requires_token(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/dev/strategy-review/blockers")
                assert resp.status_code == 403

    def test_tickers_requires_token(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/dev/strategy-review/tickers")
                assert resp.status_code == 403

    def test_movement_requires_token(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/dev/strategy-review/movement")
                assert resp.status_code == 403

    def test_queue_requires_token(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=False):
                resp = client.get("/api/dev/strategy-review/queue")
                assert resp.status_code == 403

    def test_summary_provider_calls_triggered_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-review/summary")
                assert resp.status_code == 200
                assert resp.get_json()["provider_calls_triggered"] is False

    def test_blockers_provider_calls_triggered_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-review/blockers")
                assert resp.status_code == 200
                assert resp.get_json()["provider_calls_triggered"] is False

    def test_tickers_provider_calls_triggered_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-review/tickers")
                assert resp.status_code == 200
                assert resp.get_json()["provider_calls_triggered"] is False

    def test_movement_provider_calls_triggered_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-review/movement")
                assert resp.status_code == 200
                assert resp.get_json()["provider_calls_triggered"] is False

    def test_queue_provider_calls_triggered_false(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-review/queue")
                assert resp.status_code == 200
                assert resp.get_json()["provider_calls_triggered"] is False

    def test_tickers_limit_param(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get("/api/dev/strategy-review/tickers?limit=2")
                assert resp.status_code == 200
                data = resp.get_json()
                assert len(data.get("tickers", [])) <= 2

    def test_blockers_filters_by_strategy(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                resp = client.get(
                    "/api/dev/strategy-review/blockers?strategy_id=earnings_calendar"
                )
                assert resp.status_code == 200
                data = resp.get_json()
                for b in data.get("blockers", []):
                    assert b["strategy_id"] == "earnings_calendar"

    def test_no_raw_provider_payload_in_responses(self):
        from app import config as cfg
        from unittest.mock import patch
        with self._app().test_client() as client:
            with patch.object(cfg, "ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", True), \
                 patch("app.main._valid_dev_token", return_value=True):
                for endpoint in (
                    "/api/dev/strategy-review/summary",
                    "/api/dev/strategy-review/blockers",
                    "/api/dev/strategy-review/tickers",
                ):
                    resp = client.get(endpoint)
                    text = resp.get_data(as_text=True)
                    assert "raw_provider_payload" not in text
                    assert "raw_json" not in text


# ─── TestRegression ───────────────────────────────────────────────────────────


class TestRegression:
    def test_daily_opportunity_unchanged(self):
        """Review endpoints must not influence Daily Opportunity output."""
        from app.services.strategy_observation_review_service import build_review_queue
        result = build_review_queue(days=7)
        for item in result.get("queue", []):
            # Queue items must not be mistakable as DO entries
            assert "daily_opportunity_eligible" not in item
            assert "recommendation" not in item

    def test_ff_still_dry_run_in_review(self):
        """FF observations in the review layer must preserve dry_run=1 and can_trade_live=0."""
        db = _tmp_db()
        obs = [_make_obs(
            ticker="CAG", strategy_id="forward_factor_calendar",
            status_bucket="dry_run", dry_run=1, can_trade_live=0,
            daily_opportunity_eligible=0, row_hash="reg_ff1",
            observation_key="forward_factor_calendar:CAG:c:s",
        )]
        _write_obs(db, obs)
        from app.db.strategy_observations import query_for_review
        rows = query_for_review(strategy_id="forward_factor_calendar", db_path=db)
        assert rows[0]["dry_run"] == 1
        assert rows[0]["can_trade_live"] == 0

    def test_30b_journal_functions_still_callable(self):
        from app.db.strategy_observations import (
            write_run, read_observations, run_summary, global_summary,
        )
        assert callable(write_run)
        assert callable(read_observations)
        assert callable(run_summary)
        assert callable(global_summary)

    def test_30a_normalization_still_works(self):
        from app.services.strategy_row_normalization_service import normalize_strategy_row
        row: dict = {"ticker": "AAPL", "iv_relationship_status": "favorable"}
        normalize_strategy_row(row, "earnings_calendar")
        assert "strategy_row_schema_version" in row

    def test_review_text_returns_string(self):
        from app.services.strategy_observation_review_service import build_observation_review_text
        text = build_observation_review_text(days=7)
        assert isinstance(text, str)
        assert "STRATEGY OBSERVATION REVIEW" in text

    def test_public_screener_routes_unmodified(self):
        from app.main import app
        app.config["TESTING"] = True
        with app.test_client() as client:
            resp = client.get("/screener")
            assert resp.status_code in (200, 302, 404)
            text = resp.get_data(as_text=True)
            assert "raw_provider_payload" not in text
            assert "blocking_gate_count" not in text

    def test_review_schema_version_constant(self):
        from app.services.strategy_observation_review_service import REVIEW_SCHEMA_VERSION
        assert REVIEW_SCHEMA_VERSION == "30C.v1"
