from __future__ import annotations

import inspect
from datetime import date, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
from app.models.strategy_opportunity_lifecycle import EvaluationState, LifecycleStage, RecommendedAction, Verdict
from app.services.calendar_decision_service import decide_calendar_opportunity
from app.services.calendar_opportunity_projection_service import enrich_calendar_engine_rows
from app.services.open_options_position_reconciliation_service import reconcile_open_calendar_positions


def _policy() -> CalendarEvolutionPolicy:
    return CalendarEvolutionPolicy(
        discovery_start_event_dte=0,
        discovery_end_event_dte=35,
        build_start_event_dte=24,
        surface_start_event_dte=14,
        ideal_entry_min_event_dte=6,
        ideal_entry_max_event_dte=12,
        late_entry_event_dte=4,
    )


def test_pre_window_structure_blockers_do_not_create_fail_verdict():
    decision = decide_calendar_opportunity(
        {"verdict": "FAIL / DEBIT TOO LARGE", "main_blocker": "Debit too large."},
        lifecycle_stage=LifecycleStage.SURFACED,
        lifecycle_evaluation_state=EvaluationState.STRUCTURE_COMPLETE,
        lifecycle_recommended_action=RecommendedAction.PREPARE,
        entry_evaluation_eligible=False,
        structure_available=True,
    )

    assert decision.evaluation_state == EvaluationState.STRUCTURE_COMPLETE
    assert decision.trade_verdict == Verdict.NOT_EVALUATED
    assert decision.recommended_action == RecommendedAction.PREPARE
    assert decision.entry_allowed is False
    assert "Debit too large." in decision.blockers


def test_actionable_structure_unavailable_blocks_entry():
    decision = decide_calendar_opportunity(
        {"entry_window_status": "SHORT_LEG_SPANS_EARNINGS", "entry_window_reason": "Short leg spans earnings."},
        lifecycle_stage=LifecycleStage.ACTIONABLE,
        lifecycle_evaluation_state=EvaluationState.STRUCTURE_UNAVAILABLE,
        lifecycle_recommended_action=RecommendedAction.REVIEW,
        entry_evaluation_eligible=True,
        structure_available=False,
    )

    assert decision.evaluation_state == EvaluationState.STRUCTURE_UNAVAILABLE
    assert decision.trade_verdict == Verdict.NOT_EVALUATED
    assert decision.recommended_action == RecommendedAction.NONE
    assert decision.entry_allowed is False


def test_dev_budget_maps_to_deferred_not_optionability_failure():
    decision = decide_calendar_opportunity(
        {"exit_reason": "DEV_MODE_BUDGET_NOT_SELECTED"},
        lifecycle_stage=LifecycleStage.DEVELOPING,
        lifecycle_evaluation_state=EvaluationState.NOT_REQUESTED,
        lifecycle_recommended_action=RecommendedAction.MONITOR,
        entry_evaluation_eligible=False,
        structure_available=False,
    )

    assert decision.evaluation_state == EvaluationState.DEFERRED_BUDGET
    assert decision.trade_verdict == Verdict.NOT_EVALUATED
    assert decision.recommended_action == RecommendedAction.NONE
    assert decision.entry_allowed is False


def test_one_parent_row_keeps_many_structure_attempts_nested():
    today = date(2026, 7, 13)
    earnings_date = today + timedelta(days=10)
    rows = []
    for idx, (strike, option_type, verdict) in enumerate(
        [
            (110, "call", "FAIL / DEBIT TOO LARGE"),
            (105, "call", "FAIL / NO MATCHING STRIKE"),
            (100, "put", "PASS / POSSIBLE ENTRY SETUP"),
        ]
    ):
        rows.append({
            "ticker": "SBUX",
            "row_id": f"attempt-{idx}",
            "verdict": verdict,
            "score": 50 + idx,
            "calendar_entry_allowed": verdict.startswith("PASS"),
            "possible_spread": {
                "option_type": option_type,
                "strike": strike,
                "short_expiration": "2026-08-21",
                "long_expiration": "2026-09-18",
            },
            "earnings": {"earnings_date": earnings_date.isoformat()},
            "entry_window_status": "ENTRY_WINDOW_OPEN",
        })
    engine = {"new_trade_rows": rows, "open_trade_rows": []}

    enrich_calendar_engine_rows(engine, policy=_policy(), evaluation_date=today)

    assert len(engine["new_trade_rows"]) == 1
    parent = engine["new_trade_rows"][0]
    assert parent["row_id"] == f"earnings_calendar:SBUX:{earnings_date.isoformat()}"
    assert parent["structure_attempt_summary"]["attempt_count"] == 3
    assert parent["duplicate_parent_rows_collapsed"] == 2


def test_sbux_double_calendar_groups_two_children_into_one_parent():
    lifecycle_rows = [
        {
            "ticker": "SBUX",
            "row_id": "call-calendar",
            "verdict": "HOLD / MONITOR",
            "details": {
                "earnings_calendar": {
                    "structure": "110.0 CALL | short 2026-08-21 / long 2026-09-18",
                    "value": "current debit 0.82 | entry debit est. 0.84",
                }
            },
        },
        {
            "ticker": "SBUX",
            "row_id": "put-calendar",
            "verdict": "HOLD / MONITOR",
            "details": {
                "earnings_calendar": {
                    "structure": "100.0 PUT | short 2026-08-21 / long 2026-09-18",
                    "value": "current debit 0.61 | entry debit est. 0.68",
                }
            },
        },
    ]

    result = reconcile_open_calendar_positions(lifecycle_rows)

    assert len(result["child_calendars"]) == 2
    assert len(result["double_calendar_parents"]) == 1
    assert len(result["unmatched_child_calendars"]) == 0
    parent = result["double_calendar_parents"][0]
    assert parent["position_structure_type"] == "double_calendar"
    assert parent["child_count"] == 2
    assert parent["unmatched_leg_count"] == 0


def test_open_positions_api_projects_parent_and_child_counts_from_row_store():
    from app.services.strategy_row_repository import StrategyRowRepository

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        StrategyRowRepository(db).write_run("run-33c", {
            "earnings_calendar": {
                "active_rows": [
                    {
                        "type": "open_calendar",
                        "ticker": "SBUX",
                        "row_id": "call-calendar",
                        "verdict": "HOLD / MONITOR",
                        "score": 60,
                        "details": {"earnings_calendar": {"structure": "110.0 CALL | short 2026-08-21 / long 2026-09-18"}},
                    },
                    {
                        "type": "open_calendar",
                        "ticker": "SBUX",
                        "row_id": "put-calendar",
                        "verdict": "HOLD / MONITOR",
                        "score": 60,
                        "details": {"earnings_calendar": {"structure": "100.0 PUT | short 2026-08-21 / long 2026-09-18"}},
                    },
                ]
            }
        })
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db):
            from app.api.open_positions_api import build_open_positions_response

            response = build_open_positions_response()

    assert response["source"] == "strategy_row_store"
    assert response["fallback_used"] is False
    assert response["child_calendar_count"] == 2
    assert response["parent_double_calendar_count"] == 1
    assert response["active_parent_calendar_count"] == 1
    assert len(response["double_calendar_structures"]) == 1
    assert response["unmatched_leg_count"] == 0


def test_strategy_rows_endpoint_does_not_legacy_rebuild_when_store_empty():
    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "rows.sqlite3")
        with patch("app.services.strategy_row_repository.config.STRATEGY_ROW_DB_PATH", db), \
             patch("app.services.report_snapshot_service.ReportSnapshotRepository") as snapshot_repo:
            from app.api.strategy_api import get_strategy_rows

            response = get_strategy_rows("earnings_calendar", limit=20)

    assert response["source"] == "empty"
    assert response["empty_state"] == "row_store_empty"
    snapshot_repo.assert_not_called()


def test_calendar_apis_do_not_import_legacy_business_builders():
    import app.api.daily_opportunity_api as daily
    import app.api.open_positions_api as open_positions
    import app.api.strategy_api as strategy

    for module in (daily, open_positions, strategy):
        source = inspect.getsource(module)
        assert "calendar_verdict_service" not in source
        assert "calendar_ranking_service" not in source
        assert "unified_calendar_trade_engine_service" not in source


def test_no_calendar_verdict_service_finalizer_live_callers():
    from app.services import calendar_ranking_service

    assert "attach_final_verdicts_to_ranking" not in inspect.getsource(calendar_ranking_service)
    import pathlib
    assert not pathlib.Path("app/services/unified_calendar_trade_engine_service.py").exists()
    assert not pathlib.Path("app/services/calendar_verdict_service.py").exists()
