from __future__ import annotations

from datetime import date


class _FakeTradier:
    is_configured = True

    def get_quotes(self, tickers, greeks=False):
        return {ticker: {"last": 50, "volume": 2_000_000, "average_volume": 2_000_000} for ticker in tickers}

    def get_expirations(self, ticker):
        return ["2026-07-10", "2026-07-17", "2026-07-24", "2026-08-21"]


class _FrozenDate(date):
    @classmethod
    def today(cls):
        return cls(2026, 7, 9)


def test_expiration_normalization_classifies_weekly_monthly_quarterly_and_malformed():
    from app.services.expiration_enumeration_service import normalize_expiration_records

    records = normalize_expiration_records(
        ["2026-07-10", "2026-07-17", "2026-09-18", "bad-date", {"expiration_date": "2027-07-16"}],
        valuation_date=date(2026, 7, 1),
        provider="fixture",
    )
    by_exp = {row.expiration: row for row in records}
    assert by_exp["2026-07-10"].expiration_type == "WEEKLY"
    assert by_exp["2026-07-17"].expiration_type == "MONTHLY"
    assert by_exp["2026-09-18"].expiration_type == "QUARTERLY"
    assert by_exp["2027-07-16"].expiration_type == "LEAPS"
    assert any(row.rejection_code == "MALFORMED_EXPIRATION" for row in records)


def test_pair_enumeration_tries_later_valid_pair_when_nearest_is_invalid():
    from app.models.strategy_definition import ExpirationPairRule, ExpirationRequirement
    from app.services.expiration_enumeration_service import enumerate_expiration_pairs, normalize_expiration_records

    records = normalize_expiration_records(
        ["2026-07-10", "2026-07-17", "2026-07-24", "2026-08-21"],
        valuation_date=date(2026, 7, 9),
    )
    result = enumerate_expiration_pairs(
        records,
        {
            "front": ExpirationRequirement("front", min_dte=7, max_dte=21, relation_to_event="before"),
            "back": ExpirationRequirement("back", min_dte=14, max_dte=70, relation_to_event="after"),
        },
        ExpirationPairRule(
            min_gap_days=14,
            max_gap_days=60,
            front_must_expire_before_event=True,
            back_must_expire_after_event=True,
            event_must_be_between=True,
        ),
        event_date=date(2026, 7, 30),
    )
    assert any(row["front_expiration"] == "2026-07-24" and row["back_expiration"] == "2026-08-21" for row in result["valid_pairs"])
    assert result["coverage"]["failure_by_code"]["FRONT_BELOW_MIN_DTE"] >= 1


def test_pair_enumeration_blocks_post_event_short_leg_with_machine_code():
    from app.models.strategy_definition import ExpirationPairRule, ExpirationRequirement
    from app.services.expiration_enumeration_service import enumerate_expiration_pairs, normalize_expiration_records

    records = normalize_expiration_records(["2026-07-17", "2026-08-21"], valuation_date=date(2026, 7, 9))
    result = enumerate_expiration_pairs(
        records,
        {
            "front": ExpirationRequirement("front", min_dte=1, max_dte=30, relation_to_event="before"),
            "back": ExpirationRequirement("back", min_dte=14, max_dte=70, relation_to_event="after"),
        },
        ExpirationPairRule(front_must_expire_before_event=True, back_must_expire_after_event=True, event_must_be_between=True),
        event_date=date(2026, 7, 14),
    )
    assert result["valid_pairs"] == []
    assert "FRONT_AFTER_EVENT" in result["coverage"]["failure_by_code"]


def test_strategy_definition_loader_accepts_builtin_and_rejects_unsafe_calculation():
    from copy import deepcopy

    from app.services.strategy_definition_loader_service import (
        load_builtin_strategy_definitions,
        validate_strategy_definition,
    )

    definitions = load_builtin_strategy_definitions()
    assert "earnings_calendar" in definitions
    built_in = definitions["earnings_calendar"]
    assert built_in.schema_version == "34A.strategy_definition.v1"
    assert built_in.raw["runtime_policy"]["trade_execution_allowed"] is False

    bad = deepcopy(built_in.raw)
    bad["structures"][0]["calculations"].append("os.system")
    validation = validate_strategy_definition(bad)
    assert validation["valid"] is False
    assert any(error["code"] == "CALCULATION_ID_MUST_BE_ALLOWLIST_TOKEN" for error in validation["errors"])


def test_strategy_definition_loader_rejects_unknown_field_and_circular_legs():
    from copy import deepcopy

    from app.services.strategy_definition_loader_service import load_builtin_strategy_definitions, validate_strategy_definition

    raw = deepcopy(load_builtin_strategy_definitions()["earnings_calendar"].raw)
    raw["gates"].append({"field_id": "not.real", "operator": "equal", "value": 1, "use": "gate"})
    validation = validate_strategy_definition(raw)
    assert validation["valid"] is False
    assert any(error["code"] == "FIELD_NOT_FOUND" for error in validation["errors"])

    raw = deepcopy(load_builtin_strategy_definitions()["earnings_calendar"].raw)
    raw["structures"][0]["legs"][0]["strike_rule"] = {"same_strike_as": "long_back"}
    raw["structures"][0]["legs"][1]["strike_rule"] = {"same_strike_as": "short_front"}
    validation = validate_strategy_definition(raw)
    assert validation["valid"] is False
    assert any(error["code"] == "CIRCULAR_LEG_REFERENCE" for error in validation["errors"])


def test_calendar_projection_attaches_declarative_provenance_and_coverage():
    from app.models.calendar_evolution_policy import load_calendar_evolution_policy
    from app.services.calendar_opportunity_projection_service import build_calendar_canonical_projection

    result = build_calendar_canonical_projection(
        earnings_trade_discovery={"items": []},
        earnings_discovery_quality={"items": [{
            "ticker": "ABT",
            "event": {"ticker": "ABT", "earnings_date": "2026-07-16"},
            "checks": [],
            "entry_window_status": "ENTRY_WINDOW_CLOSED",
            "entry_window_reason": "No valid pre-earnings short expiration with sufficient DTE/time value.",
            "rejected_expirations": [
                {"expiration": "2026-07-10", "primary_rejection_code": "FRONT_BELOW_MIN_DTE"},
                {"expiration": "2026-07-17", "primary_rejection_code": "SHORT_LEG_SPANS_EARNINGS"},
            ],
        }]},
        calendar_candidates=[],
        earnings_calendar_strategy={"items": []},
        calendar_ranking={"items": []},
        account_context={},
        open_options={},
        lifecycle_checks={},
        policy=load_calendar_evolution_policy(),
        evaluation_date=date(2026, 7, 9),
        run_mode="prod",
    )
    row = result["new_trade_rows"][0]
    assert row["strategy_definition_id"] == "earnings_calendar"
    assert row["structure_template_id"] == "earnings_calendar_same_strike"
    assert row["enumeration_policy_version"] == "34A.expiration_enumeration.v1"
    assert row["coverage_accounting"]["rejected_expiration_count"] == 2


def test_calendar_coverage_funnel_reports_path_counts():
    from app.services.calendar_coverage_telemetry_service import build_calendar_coverage_funnel

    coverage = build_calendar_coverage_funnel(
        earnings_trade_discovery={"items": [{"ticker": "ABT"}, {"ticker": "GE"}]},
        earnings_discovery_quality={"items": [{"ticker": "ABT"}, {"ticker": "GE", "exit_reason": "budget"}]},
        calendar_candidates=[{"ticker": "ABT"}],
        calendar_projection={"new_trade_rows": [{
            "ticker": "GE",
            "entry_window_status": "ENTRY_WINDOW_CLOSED",
            "rejected_expirations": [{"primary_rejection_code": "FRONT_BELOW_MIN_DTE"}],
        }]},
    )
    assert coverage["raw_events"] == 2
    assert coverage["quality_eligible"] == 1
    assert coverage["quality_rejected"] == 1
    assert coverage["failure_by_code"]["FRONT_BELOW_MIN_DTE"] == 1


def test_quality_filter_runs_declarative_enumeration_without_structural_reject(monkeypatch):
    from app.services import earnings_discovery_quality_service as svc

    monkeypatch.setattr(svc, "TradierProvider", lambda: _FakeTradier())
    monkeypatch.setattr(svc, "date", _FrozenDate)
    monkeypatch.setattr(svc.config, "EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", False)

    logs: list[str] = []
    result = svc.filter_earnings_discovery_for_calendar_scan(
        {"items": [{
            "ticker": "ABT",
            "earnings_date": "2026-07-30",
            "date": "2026-07-30",
            "is_timestamp_confirmed": True,
            "sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_date_confidence": "confirmed",
        }]},
        log_print=logs.append,
        run_mode="prod",
    )

    assert result["tickers"] == ["ABT"]
    row = result["items"][0]
    assert row["passes_precheck"] is True
    assert row["expiration_enumeration_result"] == "POLICY_VALID_PAIRS"
    assert row["front_expiration"] == "2026-07-24"
    assert any("CALENDAR_EXPIRATION_AUDIT ticker=ABT" in line for line in logs)


def test_quality_filter_does_not_fail_stable_candidate_when_no_pair(monkeypatch):
    from app.services import earnings_discovery_quality_service as svc

    class NoPairTradier(_FakeTradier):
        def get_expirations(self, ticker):
            return ["2026-07-10", "2026-07-17"]

    monkeypatch.setattr(svc, "TradierProvider", lambda: NoPairTradier())
    monkeypatch.setattr(svc, "date", _FrozenDate)
    monkeypatch.setattr(svc.config, "EARNINGS_DISCOVERY_CONSTITUENT_PRESCREEN", False)

    result = svc.filter_earnings_discovery_for_calendar_scan(
        {"items": [{
            "ticker": "ABT",
            "earnings_date": "2026-07-30",
            "date": "2026-07-30",
            "is_timestamp_confirmed": True,
            "sources_seen": ["finnhub", "alpha_vantage"],
            "earnings_date_confidence": "confirmed",
        }]},
        log_print=lambda msg: None,
        run_mode="prod",
    )

    row = result["items"][0]
    assert row["passes_precheck"] is True
    assert result["tickers"] == ["ABT"]
    assert row["expiration_enumeration_result"] == "STRUCTURE_UNAVAILABLE"
    statuses = {check["name"]: check["status"] for check in row["checks"]}
    assert statuses["Option expirations"] == "WARN"


def test_open_position_projection_emits_double_calendar_parent():
    from app.models.calendar_evolution_policy import load_calendar_evolution_policy
    from app.services.calendar_opportunity_projection_service import build_calendar_canonical_projection

    lifecycle_checks = {"checks": [
        {
            "ticker": "SBUX",
            "option_type": "call",
            "strike": 110,
            "front_expiration": "2026-08-21",
            "back_expiration": "2026-09-18",
            "action": "HOLD / MONITOR",
        },
        {
            "ticker": "SBUX",
            "option_type": "put",
            "strike": 100,
            "front_expiration": "2026-08-21",
            "back_expiration": "2026-09-18",
            "action": "HOLD / MONITOR",
        },
    ]}
    result = build_calendar_canonical_projection(
        earnings_trade_discovery={"items": []},
        earnings_discovery_quality={"items": []},
        calendar_candidates=[],
        earnings_calendar_strategy={"items": []},
        calendar_ranking={"items": []},
        account_context={},
        open_options={},
        lifecycle_checks=lifecycle_checks,
        policy=load_calendar_evolution_policy(),
        evaluation_date=date(2026, 7, 9),
        run_mode="prod",
    )
    open_rows = result["open_trade_rows"]
    assert sum(1 for row in open_rows if row["row_model"] == "OPEN_POSITION_PARENT") == 1
    assert sum(1 for row in open_rows if row["row_model"] == "OPEN_POSITION_CHILD") == 2
    assert result["calendar_row_reconciliation"]["open_position_parents_generated"] == 1
    assert result["calendar_row_reconciliation"]["open_position_children_generated"] == 2


def test_data_confidence_failure_codes_share_canonical_failure_collection():
    from app.services.automated_data_validation_service import log_data_confidence_validation, run_validation_suite

    rows = [{"row_type": "generic_without_action_or_score", "ticker": "BROKEN"}]
    result = run_validation_suite(rows, "fixture_strategy")
    assert result["true_failures"] > 0
    assert result["failure_codes"]
    line = log_data_confidence_validation(result, log_print=lambda msg, **kwargs: None)
    assert "failed=2" in line
    assert "failure_codes=[]" not in line
