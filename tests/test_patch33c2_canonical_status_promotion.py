from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.models.calendar_evolution_policy import CalendarEvolutionPolicy, load_calendar_evolution_policy
from app.models.strategy_opportunity_lifecycle import EvaluationState, Verdict
from app.services.automated_data_validation_service import (
    LEVEL_ERROR,
    LEVEL_WARNING,
    ValidationReport,
    ValidationResult,
    log_data_confidence_validation,
)
from app.services.calendar_opportunity_projection_service import (
    enrich_calendar_engine_rows,
    validate_calendar_canonical_rows,
)
from app.services.endpoint_verification_service import run_endpoint_verification
from app.services.run_finalization_coordinator import persist_strategy_artifacts
from app.strategies.adapters import EarningsCalendarStrategy


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


def test_strategy_registry_not_evaluated_calendar_rows_are_not_failures():
    rows = [
        {
            "ticker": f"T{i}",
            "row_id": f"row-{i}",
            "row_type": "OPPORTUNITY_PARENT",
            "evaluation_state": EvaluationState.STRUCTURE_UNAVAILABLE,
            "trade_verdict": Verdict.NOT_EVALUATED,
            "recommended_action": "MONITOR",
            "entry_evaluation_eligible": False,
            "entry_allowed": False,
        }
        for i in range(53)
    ]

    result = EarningsCalendarStrategy().normalize_result({"new_trade_rows": rows}, object()).to_dict()

    assert result["summary"]["not_evaluated"] == 53
    assert result["summary"]["fail"] == 0
    assert result["fail_count"] == 0


def test_structure_unavailable_row_can_surface_but_never_entry_allowed():
    today = date(2026, 7, 13)
    earnings_date = today + timedelta(days=20)
    engine = {
        "new_trade_rows": [{
            "ticker": "AXSM",
            "verdict": "NOT_EVALUATED / NO VALID CALENDAR STRUCTURE",
            "score": 35,
            "calendar_entry_allowed": False,
            "earnings": {"earnings_date": earnings_date.isoformat()},
            "entry_window_status": "MONITOR_PRE_WINDOW",
            "entry_window_reason": "Structure not available yet.",
        }],
        "open_trade_rows": [],
    }

    enrich_calendar_engine_rows(engine, policy=_policy(), evaluation_date=today)
    row = engine["new_trade_rows"][0]

    assert row["evaluation_state"] in {EvaluationState.BUILDING, EvaluationState.STRUCTURE_UNAVAILABLE}
    assert row["trade_verdict"] == Verdict.NOT_EVALUATED
    assert row["entry_evaluation_eligible"] is False
    assert row["entry_allowed"] is False


def test_canonical_validator_rejects_entry_allowed_without_pass_enter():
    row = {
        "ticker": "BAD",
        "row_id": "bad-row",
        "row_model": "OPPORTUNITY_PARENT",
        "opportunity_id": "earnings_calendar:BAD:2026-08-01",
        "evaluation_state": EvaluationState.STRUCTURE_UNAVAILABLE,
        "trade_verdict": Verdict.NOT_EVALUATED,
        "recommended_action": "MONITOR",
        "entry_evaluation_eligible": False,
        "entry_allowed": True,
    }

    result = validate_calendar_canonical_rows([row])
    codes = {item["code"] for item in result["invariant_violations"]}

    assert "ENTRY_ALLOWED_WITHOUT_ENTRY_EVALUATION" in codes
    assert "STRUCTURE_UNAVAILABLE_ENTRY_ALLOWED" in codes
    assert result["violation_count"] > 0


@dataclass
class _RunContext:
    run_id: str = "run-bad"
    created_at: str = "2026-07-13T00:00:00+00:00"
    fetch_audit: tuple = ()


def test_pre_persistence_semantic_validation_blocks_row_store_write(monkeypatch):
    bad_row = {
        "strategy_id": "earnings_calendar",
        "ticker": "BAD",
        "row_id": "bad-row",
        "row_model": "OPPORTUNITY_PARENT",
        "opportunity_id": "earnings_calendar:BAD:2026-08-01",
        "evaluation_state": EvaluationState.STRUCTURE_UNAVAILABLE,
        "trade_verdict": Verdict.NOT_EVALUATED,
        "recommended_action": "MONITOR",
        "entry_evaluation_eligible": False,
        "entry_allowed": True,
    }
    called = {"write": False}

    class _Repo:
        def write_run(self, *_args, **_kwargs):
            called["write"] = True
            return {"write_count": 1}

    monkeypatch.setattr("app.services.strategy_row_repository.StrategyRowRepository", lambda: _Repo())
    monkeypatch.setattr("app.config.OPPORTUNITY_HISTORY_ENABLED", False)
    monkeypatch.setattr("app.config.DATA_CONFIDENCE_VALIDATION_LOG_ENABLED", False)

    result = persist_strategy_artifacts(
        run_context=_RunContext(),
        run_mode="dev",
        normalized_strategy_results={"earnings_calendar": {"canonical_opportunities": [bad_row]}},
        tradier_snapshot={},
        earnings_events={},
        positions=[],
        log_print=lambda _msg: None,
    )

    assert called["write"] is False
    assert result["required_failures"]
    assert result["status"] == "failed"


def test_endpoint_verifier_checks_entry_permission_not_surface_eligibility(monkeypatch):
    from app.services.run_manifest_repository import RunManifestRepository
    from app.services.strategy_row_normalization_service import normalize_strategy_row
    from app.services.strategy_row_repository import StrategyRowRepository

    with TemporaryDirectory() as tmp:
        db = f"{tmp}/verify.sqlite3"
        monkeypatch.setattr("app.config.RUN_MANIFEST_DB_PATH", db)
        monkeypatch.setattr("app.config.STRATEGY_ROW_DB_PATH", db)
        RunManifestRepository(db).save({
            "run_id": "run-good",
            "completed_at": "2026-07-13T00:00:00+00:00",
            "mode": "dev",
            "status": "complete",
            "report_quality": "SUCCESS_COMPLETE",
            "strategy_counts": {},
            "daily_opportunity_count": 0,
            "has_broker_data": True,
            "has_market_data": True,
            "has_options_data": True,
            "has_errors": False,
            "error_count": 0,
        })
        StrategyRowRepository(db).write_run("run-good", {
            "earnings_calendar": {"canonical_opportunities": [
                normalize_strategy_row({
                    "ticker": "ABT",
                    "row_type": "OPPORTUNITY_PARENT",
                    "verdict": "NOT_EVALUATED / MONITOR",
                    "opportunity_id": "earnings_calendar:ABT:2026-08-01",
                    "lifecycle_stage": "SURFACED",
                    "evaluation_state": "STRUCTURE_UNAVAILABLE",
                    "trade_verdict": "NOT_EVALUATED",
                    "recommended_action": "MONITOR",
                    "surface_eligible": True,
                    "entry_evaluation_eligible": False,
                    "entry_allowed": False,
                }, "earnings_calendar")
            ]},
            "stock_momentum": {"canonical_opportunities": [normalize_strategy_row({"ticker": "GE", "action": "WATCH / CONFIRM TREND"}, "stock_momentum")]},
            "skew_momentum_vertical": {"canonical_opportunities": [normalize_strategy_row({"ticker": "ALGN", "verdict": "FAIL / OPTIONS ILLIQUID"}, "skew_momentum_vertical")]},
            "forward_factor_calendar": {"canonical_opportunities": [normalize_strategy_row({"ticker": "ELF", "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE"}, "forward_factor_calendar")]},
        })
        result = run_endpoint_verification(
            completed_run_id="run-good",
            report_quality="SUCCESS_COMPLETE",
            log_print=lambda _msg: None,
        )

    assert result["failed_count"] == 0
    assert result["required_failed_count"] == 0


def test_data_confidence_failed_count_uses_hard_failures_only():
    report = ValidationReport("regression", "row")
    report.add(ValidationResult("warn.only", False, LEVEL_WARNING, "warning"))
    suite = {
        "total_reports": 1,
        "passed_reports": 0,
        "failed_reports": 1,
        "true_failures": 0,
        "total_warnings": 1,
        "not_applicable": 0,
        "expected_missing": 0,
        "reports": [report.to_dict()],
    }
    lines: list[str] = []

    log_data_confidence_validation(suite, log_print=lines.append)

    assert "failed=0" in lines[0]
    assert "failure_codes=[]" in lines[0]


def test_policy_source_prefers_environment_even_when_value_equals_default():
    with patch.dict("os.environ", {"CALENDAR_SURFACE_START_EVENT_DTE": "14"}, clear=False):
        policy = load_calendar_evolution_policy()

    assert policy.source_by_field["surface_start_event_dte"] == "railway_env:CALENDAR_SURFACE_START_EVENT_DTE"
