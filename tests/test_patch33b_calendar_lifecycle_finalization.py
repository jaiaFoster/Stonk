from __future__ import annotations

from datetime import date, timedelta

from app.models.calendar_evolution_policy import CalendarEvolutionPolicy
from app.services.automated_data_validation_service import _row_profile
from app.services.calendar_opportunity_projection_service import enrich_calendar_engine_rows
from app.services.strategy_execution_service import collect_strategy_results
from app.services.strategy_row_repository import StrategyRowRepository


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


def test_calendar_projection_assigns_stable_parent_and_structure_ids():
    today = date(2026, 7, 13)
    earnings_date = today + timedelta(days=10)
    engine = {
        "new_trade_rows": [{
            "ticker": "SBUX",
            "verdict": "PASS / POSSIBLE ENTRY SETUP",
            "score": 82,
            "calendar_entry_allowed": True,
            "possible_spread": {
                "option_type": "call",
                "strike": 110,
                "short_expiration": "2026-08-21",
                "long_expiration": "2026-09-18",
            },
            "earnings": {"earnings_date": earnings_date.isoformat()},
            "entry_window_status": "ENTRY_WINDOW_OPEN",
        }],
        "open_trade_rows": [],
    }

    enrich_calendar_engine_rows(engine, policy=_policy(), evaluation_date=today)
    row = engine["new_trade_rows"][0]

    assert row["opportunity_id"] == f"earnings_calendar:SBUX:{earnings_date.isoformat()}"
    assert row["current_structure_id"].endswith(":call:110:2026-08-21:2026-09-18")
    assert row["lifecycle_stage"] == "ACTIONABLE"
    assert row["calendar_stage"] == "ENTRY_WINDOW_OPEN"
    assert row["entry_allowed"] is True


def test_pre_window_row_is_not_terminal_fail():
    today = date(2026, 7, 13)
    earnings_date = today + timedelta(days=20)
    engine = {
        "new_trade_rows": [{
            "ticker": "ABT",
            "verdict": "MONITOR / PRE-WINDOW",
            "score": 40,
            "calendar_entry_allowed": False,
            "earnings": {"earnings_date": earnings_date.isoformat()},
            "entry_window_status": "MONITOR_PRE_WINDOW",
        }],
        "open_trade_rows": [],
    }

    enrich_calendar_engine_rows(engine, policy=_policy(), evaluation_date=today)
    row = engine["new_trade_rows"][0]

    assert row["lifecycle_stage"] == "DEVELOPING"
    assert row["evaluation_state"] in {"BUILDING", "STRUCTURE_COMPLETE", "STRUCTURE_UNAVAILABLE"}
    assert row["trade_verdict"] == "NOT_EVALUATED"
    assert row["entry_allowed"] is False


def test_row_store_persists_lifecycle_fields(tmp_path):
    db = tmp_path / "strategy_rows.sqlite3"
    repo = StrategyRowRepository(str(db))
    strategy_results = {
        "earnings_calendar": {
            "canonical_opportunities": [{
                "strategy_id": "earnings_calendar",
                "ticker": "SBUX",
                "row_id": "row-1",
                "row_type": "observation",
                "verdict": "WATCH / ENTRY WINDOW OPEN",
                "score": 70,
                "opportunity_id": "earnings_calendar:SBUX:2026-08-21",
                "lifecycle_stage": "ACTIONABLE",
                "evaluation_state": "FULLY_EVALUATED",
                "trade_verdict": "WATCH",
                "recommended_action": "MONITOR",
                "calendar_stage": "ENTRY_WINDOW_OPEN",
                "surface_eligible": True,
                "entry_evaluation_eligible": True,
                "entry_allowed": False,
                "policy_version": "test",
            }]
        }
    }

    repo.write_run("run-33b", strategy_results)
    stored = repo.read_latest("earnings_calendar", limit=10)
    row = stored["rows"][0]

    assert row["opportunity_id"] == "earnings_calendar:SBUX:2026-08-21"
    assert row["lifecycle_stage"] == "ACTIONABLE"
    assert row["evaluation_state"] == "FULLY_EVALUATED"
    assert row["trade_verdict"] == "WATCH"
    assert row["entry_allowed"] is False


def test_strategy_normalization_preserves_projected_lifecycle_fields():
    class Context:
        run_id = "run-33b"

    raw_results = {
        "earnings_calendar": {
            "new_trade_rows": [{
                "ticker": "SBUX",
                "row_id": "calendar-row-1",
                "row_type": "observation",
                "verdict": "WATCH / ENTRY WINDOW OPEN",
                "score": 70,
                "opportunity_id": "earnings_calendar:SBUX:2026-08-21",
                "lifecycle_stage": "ACTIONABLE",
                "evaluation_state": "FULLY_EVALUATED",
                "trade_verdict": "WATCH",
                "recommended_action": "MONITOR",
                "calendar_stage": "ENTRY_WINDOW_OPEN",
                "surface_eligible": True,
                "entry_evaluation_eligible": True,
                "entry_allowed": False,
                "policy_version": "33B.lifecycle.v1",
                "current_structure_id": "earnings_calendar:SBUX:calendar:call:110:2026-08-21:2026-09-18",
            }]
        }
    }

    normalized = collect_strategy_results(Context(), raw_results)
    row = normalized["earnings_calendar"]["canonical_opportunities"][0]

    assert row["opportunity_id"] == "earnings_calendar:SBUX:2026-08-21"
    assert row["lifecycle_stage"] == "ACTIONABLE"
    assert row["evaluation_state"] == "FULLY_EVALUATED"
    assert row["trade_verdict"] == "WATCH"
    assert row["entry_allowed"] is False
    assert row["current_structure_id"].endswith(":call:110:2026-08-21:2026-09-18")


def test_data_confidence_lifecycle_incomplete_rows_use_candidate_profile():
    row = {
        "ticker": "ABT",
        "lifecycle_stage": "DEVELOPING",
        "evaluation_state": "BUILDING",
        "trade_verdict": "NOT_EVALUATED",
    }

    assert _row_profile(row, "earnings_calendar") == "candidate"
