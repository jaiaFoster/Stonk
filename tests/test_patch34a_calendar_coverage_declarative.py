from __future__ import annotations

from datetime import date


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
    assert result["coverage"]["failure_by_code"]["SHORT_DTE_TOO_LOW"] >= 1


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
    assert "SHORT_LEG_SPANS_EARNINGS" in result["coverage"]["failure_by_code"]


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
                {"expiration": "2026-07-10", "primary_rejection_code": "SHORT_DTE_TOO_LOW"},
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
            "rejected_expirations": [{"primary_rejection_code": "SHORT_DTE_TOO_LOW"}],
        }]},
    )
    assert coverage["raw_events"] == 2
    assert coverage["quality_eligible"] == 1
    assert coverage["quality_rejected"] == 1
    assert coverage["failure_by_code"]["SHORT_DTE_TOO_LOW"] == 1
