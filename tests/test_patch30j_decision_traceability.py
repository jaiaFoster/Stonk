from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch


def test_normalizer_adds_canonical_semantics_for_representative_rows():
    from app.services.strategy_row_normalization_service import normalize_strategy_row

    lifecycle = normalize_strategy_row(
        {"ticker": "SBUX", "type": "open_calendar", "verdict": "HOLD / MONITOR"},
        "earnings_calendar",
    )
    assert lifecycle["decision_class"] == "lifecycle"
    assert lifecycle["action_type"] == "active_calendar"
    assert lifecycle["eligibility_status"] == "eligible"
    assert lifecycle["semantic_source"] == "row"

    rejected = normalize_strategy_row(
        {"ticker": "ABT", "verdict": "FAIL / ENTRY_WINDOW_CLOSED", "entry_window_status": "ENTRY_WINDOW_CLOSED"},
        "earnings_calendar",
    )
    assert rejected["decision_class"] == "rejected"
    assert rejected["action_type"] == "none"
    assert rejected["exclusion_reason"] == "entry_window_closed"

    stock_watch = normalize_strategy_row({"ticker": "GE", "action": "WATCH / CONFIRM TREND"}, "stock_momentum")
    assert stock_watch["decision_class"] == "watch"
    assert stock_watch["action_type"] == "stock_watch"
    assert stock_watch["actionability"] == "monitor_only"

    tactical = normalize_strategy_row({"ticker": "SOXL", "action": "TACTICAL ONLY / DO NOT CHASE"}, "stock_momentum")
    assert tactical["action_type"] == "tactical_stock_watch"

    stock_add = normalize_strategy_row({"ticker": "AMZN", "action": "CONSIDER ADDING"}, "stock_momentum")
    assert stock_add["decision_class"] == "add"
    assert stock_add["action_type"] == "stock_add"

    skew = normalize_strategy_row({"ticker": "MSFT", "verdict": "PASS / SKEW VERTICAL"}, "skew_momentum_vertical")
    assert skew["action_type"] == "vertical_entry"

    skew_fail = normalize_strategy_row({"ticker": "ELF", "verdict": "FAIL / OPTIONS ILLIQUID"}, "skew_momentum_vertical")
    assert skew_fail["decision_class"] == "rejected"

    ff = normalize_strategy_row({"ticker": "ELF", "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE"}, "forward_factor_calendar")
    assert ff["decision_class"] == "diagnostic"
    assert ff["eligibility_status"] == "dry_run_excluded"
    assert ff["dry_run"] is True


def test_strategy_row_repository_persists_semantic_fields_and_infers_old_rows():
    from app.services.strategy_row_normalization_service import normalize_strategy_row
    from app.services.strategy_row_repository import StrategyRowRepository

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        normalized = normalize_strategy_row({"ticker": "GE", "action": "WATCH / CONFIRM TREND"}, "stock_momentum")
        legacy = {"strategy_id": "stock_momentum", "ticker": "ALGN", "row_id": "legacy-algn", "verdict": "WATCH / CONFIRM TREND"}
        repo = StrategyRowRepository(db)
        repo.write_run("run-30j", {"stock_momentum": {"canonical_opportunities": [normalized, legacy]}})
        rows = repo.read_latest("stock_momentum", limit=10)["rows"]

    by_ticker = {row["ticker"]: row for row in rows}
    assert by_ticker["GE"]["semantic_source"] == "row"
    assert by_ticker["GE"]["action_type"] == "stock_watch"
    assert by_ticker["GE"]["source_run_id"] == "run-30j"
    assert by_ticker["ALGN"]["semantic_source"] == "legacy_verdict_inference"
    assert by_ticker["ALGN"]["action_type"] == "stock_watch"


def test_collect_results_persists_row_owned_semantics_through_write_path():
    from types import SimpleNamespace

    from app.services.strategy_execution_service import collect_strategy_results
    from app.services.strategy_row_repository import StrategyRowRepository

    raw_results = {
        "earnings_calendar": {
            "new_trade_rows": [
                {"ticker": "ABT", "verdict": "FAIL / ENTRY_WINDOW_CLOSED", "entry_window_status": "ENTRY_WINDOW_CLOSED"}
            ],
            "active_items": [
                {"ticker": "SBUX", "type": "open_calendar", "verdict": "HOLD / MONITOR"}
            ],
        },
        "stock_momentum": {
            "items": [
                {"ticker": "GE", "action": "WATCH / CONFIRM TREND", "score": 80}
            ],
        },
        "forward_factor_calendar": {
            "items": [
                {"ticker": "ELF", "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE"}
            ],
        },
    }

    context = SimpleNamespace(run_id="run-30j1", analysis_tickers=[])
    normalized_results = collect_strategy_results(context, raw_results)

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        repo = StrategyRowRepository(db)
        repo.write_run("run-30j1", normalized_results)
        earnings = repo.read_latest("earnings_calendar", limit=10)["rows"]
        stock = repo.read_latest("stock_momentum", limit=10)["rows"]
        ff = repo.read_latest("forward_factor_calendar", limit=10)["rows"]

    earnings_by_ticker = {row["ticker"]: row for row in earnings}
    assert earnings_by_ticker["SBUX"]["semantic_source"] == "row"
    assert earnings_by_ticker["SBUX"]["decision_class"] == "lifecycle"
    assert earnings_by_ticker["SBUX"]["action_type"] == "active_calendar"
    assert earnings_by_ticker["ABT"]["semantic_source"] == "row"
    assert earnings_by_ticker["ABT"]["decision_class"] == "rejected"
    assert earnings_by_ticker["ABT"]["eligibility_status"] == "excluded"
    assert earnings_by_ticker["ABT"]["exclusion_reason"] == "entry_window_closed"

    stock_by_ticker = {row["ticker"]: row for row in stock}
    assert stock_by_ticker["GE"]["semantic_source"] == "row"
    assert stock_by_ticker["GE"]["decision_class"] == "watch"
    assert stock_by_ticker["GE"]["action_type"] == "stock_watch"

    ff_by_ticker = {row["ticker"]: row for row in ff}
    assert ff_by_ticker["ELF"]["semantic_source"] == "row"
    assert ff_by_ticker["ELF"]["actionability"] == "dry_run_only"
    assert ff_by_ticker["ELF"]["eligibility_status"] == "dry_run_excluded"
    assert ff_by_ticker["ELF"]["exclusion_reason"] == "dry_run"


def _write_daily_rows(db: str):
    from app.services.strategy_row_normalization_service import normalize_strategy_row
    from app.services.strategy_row_repository import StrategyRowRepository

    rows = [
        normalize_strategy_row({"ticker": "SBUX", "type": "open_calendar", "verdict": "HOLD / MONITOR", "score": 95}, "earnings_calendar"),
        normalize_strategy_row({"ticker": "AMZN", "action": "CONSIDER ADDING", "score": 90}, "stock_momentum"),
        normalize_strategy_row({"ticker": "GE", "action": "WATCH / CONFIRM TREND", "score": 80}, "stock_momentum"),
        normalize_strategy_row({"ticker": "SOXL", "action": "TACTICAL ONLY / DO NOT CHASE", "score": 70}, "stock_momentum"),
        normalize_strategy_row({"ticker": "NFLX", "verdict": "FAIL / ENTRY_WINDOW_CLOSED", "entry_window_status": "ENTRY_WINDOW_CLOSED", "score": 35}, "earnings_calendar"),
        normalize_strategy_row({"ticker": "ELF", "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE", "score": 88}, "forward_factor_calendar"),
    ]
    StrategyRowRepository(db).write_run("run-daily", {
        "earnings_calendar": {"canonical_opportunities": [rows[0], rows[4]]},
        "stock_momentum": {"canonical_opportunities": rows[1:4]},
        "forward_factor_calendar": {"canonical_opportunities": [rows[5]]},
    })


def test_daily_opportunity_traceability_limit_and_exclusions():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        _write_daily_rows(db)
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db):
            from app.api.daily_opportunity_api import build_daily_opportunity_response

            response = build_daily_opportunity_response(limit=2, include_exclusions=True)

    assert response["source"] == "strategy_row_store"
    assert response["fallback_used"] is False
    assert response["provider_calls_triggered"] is False
    assert response["eligible_before_limit"] == 4
    assert response["returned_action_count"] == 2
    assert response["action_limit"] == 2
    assert response["truncated"] is True
    assert response["truncated_count"] == 2
    assert response["exclusion_counts"]["action_limit"] == 2
    assert response["exclusion_counts"]["entry_window_closed"] == 1
    assert response["exclusion_counts"]["dry_run"] == 1
    assert response["inferred_semantics_count"] == 0
    first = response["actions"][0]
    assert first["ticker"] == "SBUX"
    assert first["source_table"] == "strategy_rows"
    assert first["source_strategy_id"] == "earnings_calendar"
    assert first["decision_class"] == "lifecycle"
    assert first["action_type"] == "active_calendar"
    assert first["semantic_source"] == "row"
    assert first["strategy_row_url"].startswith("/api/strategies/earnings_calendar/rows?row_id=")
    assert "display" in first and "primary_reason" in first["display"]
    assert "passed_gate_count" in first
    assert len(json.dumps(response, default=str)) < 100_000


def test_strategy_schema_exposes_semantic_versions():
    from app.api.strategy_api import get_strategy_schema

    schema = get_strategy_schema()
    assert schema["canonical_strategy_row_schema_version"] == "30J.v1"
    assert schema["minimum_supported_schema_version"] == "30A.v1"
    assert schema["semantic_fields_version"] == "30J.v1"
