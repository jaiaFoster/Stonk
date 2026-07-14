from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory


def test_rejected_calendar_rows_take_semantic_precedence():
    from app.services.strategy_row_normalization_service import normalize_strategy_row

    debit = normalize_strategy_row(
        {"ticker": "DPZ", "row_type": "rejected_candidate", "verdict": "FAIL / DEBIT TOO LARGE", "calendar_entry_allowed": True},
        "earnings_calendar",
    )
    assert debit["decision_class"] == "rejected"
    assert debit["action_type"] == "none"
    assert debit["actionability"] == "non_actionable"
    assert debit["eligibility_status"] == "excluded"
    assert debit["exclusion_reason"] == "debit_too_large"
    assert debit["daily_opportunity_eligible"] is False
    assert debit["semantic_source"] == "row"

    fail = normalize_strategy_row(
        {"ticker": "BKR", "verdict": "FAIL / UNTRADEABLE SPREAD", "calendar_entry_allowed": True},
        "earnings_calendar",
    )
    assert fail["decision_class"] == "rejected"
    assert fail["action_type"] == "none"
    assert fail["eligibility_status"] == "excluded"

    hard = normalize_strategy_row(
        {
            "ticker": "SBUX",
            "verdict": "WATCH / ENTRY WINDOW OPEN",
            "calendar_entry_allowed": True,
            "checks": [{"name": "Debit", "status": "FAIL", "is_hard_block": True}],
        },
        "earnings_calendar",
    )
    assert hard["decision_class"] == "rejected"
    assert hard["action_type"] == "none"


def test_calendar_entry_lifecycle_monitor_and_ff_regressions():
    from app.services.strategy_row_normalization_service import normalize_strategy_row

    entry = normalize_strategy_row({"ticker": "C", "verdict": "PASS / CALENDAR", "trade_verdict": "PASS", "entry_allowed": True, "recommended_action": "ENTER"}, "earnings_calendar")
    assert entry["decision_class"] == "entry"
    assert entry["action_type"] == "calendar_entry"
    assert entry["eligibility_status"] == "eligible"

    lifecycle = normalize_strategy_row({"ticker": "SBUX", "type": "open_calendar", "verdict": "HOLD / MONITOR"}, "earnings_calendar")
    assert lifecycle["decision_class"] == "lifecycle"
    assert lifecycle["action_type"] == "calendar_position_action"

    monitor = normalize_strategy_row({"ticker": "NFLX", "entry_window_status": "MONITOR_PRE_WINDOW"}, "earnings_calendar")
    assert monitor["decision_class"] == "monitor"
    assert monitor["action_type"] == "none"
    assert monitor["eligibility_status"] == "excluded"

    ff = normalize_strategy_row({"ticker": "ELF", "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE"}, "forward_factor_calendar")
    assert ff["decision_class"] == "diagnostic"
    assert ff["eligibility_status"] == "dry_run_excluded"
    assert ff["exclusion_reason"] == "dry_run"


def test_catalog_registry_integrity_and_builtin_coverage():
    from app.services.strategy_data_catalog_service import (
        ALLOWED_USES,
        AVAILABILITY_STAGES,
        BUILT_IN_STRATEGY_FIELDS,
        CATALOG_SCHEMA_VERSION,
        MISSING_BEHAVIORS,
        PROVIDER_COST_CLASSES,
        REQUIREMENT_TYPES,
        VALUE_TYPES,
        catalog_diagnostics,
        field_catalog,
    )

    catalog = field_catalog()
    assert len(catalog) >= 50
    assert len(catalog) == len(set(catalog))
    for field_id, field in catalog.items():
        assert field.field_id == field_id
        assert field.schema_version == CATALOG_SCHEMA_VERSION
        assert field.value_type in VALUE_TYPES
        assert field.availability_stage in AVAILABILITY_STAGES
        assert field.default_missing_behavior in MISSING_BEHAVIORS
        assert field.provider_cost_class in PROVIDER_COST_CLASSES
        assert field.source_path
        assert set(field.allowed_uses) <= ALLOWED_USES
        assert set(field.requirement_types) <= REQUIREMENT_TYPES
        assert field.allowed_operators

    for strategy_id, required_fields in BUILT_IN_STRATEGY_FIELDS.items():
        missing = sorted(required_fields - set(catalog))
        assert not missing, f"{strategy_id} missing catalog fields: {missing}"

    diagnostics = catalog_diagnostics()
    assert diagnostics["built_in_strategy_coverage"]["forward_factor_calendar"]["missing"] == []


def test_requirement_mapping_examples():
    from app.services.strategy_data_catalog_service import requirements_for_fields

    result = requirements_for_fields([
        "market.price.last",
        "technical.return.20d",
        "momentum.relative_strength_score",
        "earnings.days_until",
        "options.delta",
        "volatility.forward_factor",
        "portfolio.has_position",
    ])
    by_type = {item["requirement_type"]: item for item in result["requirements"]}
    assert by_type["quote"]["fields"] == ["market.price.last"]
    assert "technical.return.20d" in by_type["candles"]["fields"]
    assert "momentum.relative_strength_score" in by_type["candles"]["fields"]
    assert "momentum.relative_strength_score" in by_type["benchmark_candles"]["fields"]
    assert by_type["earnings_date"]["fields"] == ["earnings.days_until"]
    assert by_type["options_chain"]["fields"] == ["options.delta"]
    assert by_type["options_chain_set"]["fields"] == ["volatility.forward_factor"]
    assert by_type["broker_positions"]["fields"] == ["portfolio.has_position"]
    assert result["provider_cost_estimate"] == "very_high"


def test_rule_validation_valid_and_invalid_cases():
    from app.services.strategy_data_catalog_service import validate_rule_definition

    assert validate_rule_definition({"field_id": "options.delta", "operator": "between", "value": [0.45, 0.55], "use": "gate"})["valid"] is True
    assert validate_rule_definition({"field_id": "portfolio.has_position", "operator": "is_true", "use": "gate"})["valid"] is True
    assert validate_rule_definition({"field_id": "earnings.session", "operator": "in", "value": ["after_market_close"], "use": "gate"})["valid"] is True
    assert validate_rule_definition({"field_id": "earnings.date", "operator": "within_next_days", "value": 21, "use": "gate"})["valid"] is True
    assert validate_rule_definition({"field_id": "earnings.sources", "operator": "contains_any", "value": ["finnhub"], "use": "gate"})["valid"] is True

    bad_operator = validate_rule_definition({"field_id": "market.price.last", "operator": "contains", "value": "NVDA", "use": "gate"})
    assert bad_operator["valid"] is False
    assert bad_operator["errors"][0]["code"] == "OPERATOR_NOT_ALLOWED"

    bad_date = validate_rule_definition({"field_id": "earnings.date", "operator": "greater_than", "value": 12, "use": "gate"})
    assert bad_date["valid"] is False
    assert bad_date["errors"][0]["code"] == "OPERATOR_NOT_ALLOWED"

    circular = validate_rule_definition({"field_id": "strategy.verdict", "operator": "equal", "value": "PASS", "use": "gate"})
    assert circular["valid"] is False
    assert any(error["code"] == "USE_NOT_ALLOWED" or error["code"] == "CIRCULAR_FIELD_USE" for error in circular["errors"])


def test_catalog_api_helpers_and_security_scan():
    from app.api.strategy_builder_api import catalog, field, operators, requirements, validate_rule

    response = catalog({"category": "options", "allowed_use": "gate"})
    assert response["catalog_schema_version"] == "31A.v1"
    assert response["provider_calls_triggered"] is False
    assert response["read_only"] is True
    assert response["field_count"] > 10
    assert all(item["category"] == "options" for item in response["fields"])

    body, status = field("options.delta")
    assert status == 200
    assert body["field"]["field_id"] == "options.delta"
    assert "between" in body["allowed_operators"]

    missing, missing_status = field("not.real")
    assert missing_status == 404
    assert missing["error"]["code"] == "FIELD_NOT_FOUND"

    assert "number" in operators()["operator_sets"]
    assert requirements(["options.delta"])["requirements"][0]["requirement_type"] == "options_chain"
    assert validate_rule({"field_id": "options.delta", "operator": "between", "value": [0.45, 0.55], "use": "gate"})["valid"] is True

    serialized = str(response).lower()
    for forbidden in ("account_number", "access_token", "password", "secret", "raw_provider", "database_path", "user_id"):
        assert forbidden not in serialized


def test_catalog_flask_endpoints_are_read_only():
    from app.main import app

    client = app.test_client()
    catalog_response = client.get("/api/strategy-builder/catalog")
    assert catalog_response.status_code == 200
    catalog_body = catalog_response.get_json()
    assert catalog_body["provider_calls_triggered"] is False
    assert catalog_body["read_only"] is True

    filtered = client.get("/api/strategy-builder/catalog/fields?category=options")
    assert filtered.status_code == 200
    assert filtered.get_json()["field_count"] > 10

    single = client.get("/api/strategy-builder/catalog/fields/options.delta")
    assert single.status_code == 200
    assert single.get_json()["field"]["field_id"] == "options.delta"

    missing = client.get("/api/strategy-builder/catalog/fields/not.real")
    assert missing.status_code == 404
    assert missing.get_json()["error"]["code"] == "FIELD_NOT_FOUND"

    ops = client.get("/api/strategy-builder/catalog/operators")
    assert ops.status_code == 200
    assert ops.get_json()["provider_calls_triggered"] is False

    req = client.get("/api/strategy-builder/catalog/requirements?field_id=options.delta")
    assert req.status_code == 200
    assert req.get_json()["requirements"][0]["requirement_type"] == "options_chain"

    valid = client.post(
        "/api/strategy-builder/validate-rule",
        json={"field_id": "options.delta", "operator": "between", "value": [0.45, 0.55], "use": "gate"},
    )
    assert valid.status_code == 200
    assert valid.get_json()["valid"] is True
    assert valid.get_json()["provider_calls_triggered"] is False


def _seed_verification_run(db_path: str, run_id: str, *, bad_calendar_semantics: bool = False) -> None:
    from app.services.run_manifest_repository import RunManifestRepository
    from app.services.strategy_row_normalization_service import normalize_strategy_row
    from app.services.strategy_row_repository import StrategyRowRepository

    RunManifestRepository(db_path).save({
        "run_id": run_id,
        "completed_at": "2026-07-10T00:00:00+00:00",
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
    earnings_rows = [
        normalize_strategy_row({
            "ticker": "SBUX",
            "type": "open_calendar",
            "verdict": "HOLD / MONITOR",
            "opportunity_id": "earnings_calendar:SBUX:2026-08-21",
            "lifecycle_stage": "OPEN_POSITION",
            "evaluation_state": "FULLY_EVALUATED",
            "trade_verdict": "PASS",
            "recommended_action": "HOLD",
            "calendar_stage": "OPEN_POSITION",
            "surface_eligible": True,
            "entry_allowed": False,
        }, "earnings_calendar"),
        normalize_strategy_row({
            "ticker": "ABT",
            "row_type": "rejected_candidate",
            "verdict": "FAIL / ENTRY_WINDOW_CLOSED",
            "entry_window_status": "ENTRY_WINDOW_CLOSED",
            "opportunity_id": "earnings_calendar:ABT:2026-07-16",
            "lifecycle_stage": "ACTIONABLE",
            "evaluation_state": "STRUCTURE_UNAVAILABLE",
            "trade_verdict": "BLOCKED",
            "recommended_action": "NONE",
            "calendar_stage": "LATE_WINDOW",
            "surface_eligible": True,
            "entry_evaluation_eligible": True,
            "entry_allowed": False,
        }, "earnings_calendar"),
    ]
    if bad_calendar_semantics:
        earnings_rows.append({
            "strategy_id": "earnings_calendar",
            "ticker": "DPZ",
            "row_id": "bad-dpz",
            "row_type": "rejected_candidate",
            "verdict": "FAIL / DEBIT TOO LARGE",
            "decision_class": "entry",
            "action_type": "calendar_entry",
            "actionability": "review_only",
            "eligibility_status": "eligible",
            "evaluation_state": "STRUCTURE_UNAVAILABLE",
            "trade_verdict": "NOT_EVALUATED",
            "recommended_action": "MONITOR",
            "entry_evaluation_eligible": False,
            "entry_allowed": True,
            "semantic_source": "row",
            "semantic_fields_version": "30J.v1",
        })
    stock_rows = [
        normalize_strategy_row({"ticker": "GE", "action": "WATCH / CONFIRM TREND"}, "stock_momentum"),
    ]
    skew_rows = [
        normalize_strategy_row({"ticker": "ALGN", "verdict": "FAIL / OPTIONS ILLIQUID"}, "skew_momentum_vertical"),
    ]
    ff_rows = [
        normalize_strategy_row({"ticker": "ELF", "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE"}, "forward_factor_calendar"),
    ]
    StrategyRowRepository(db_path).write_run(run_id, {
        "earnings_calendar": {"canonical_opportunities": earnings_rows},
        "stock_momentum": {"canonical_opportunities": stock_rows},
        "skew_momentum_vertical": {"canonical_opportunities": skew_rows},
        "forward_factor_calendar": {"canonical_opportunities": ff_rows},
    })


def test_endpoint_verification_packet_passes_after_persistence(monkeypatch):
    from app.services.endpoint_verification_service import run_endpoint_verification

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "verify.sqlite3")
        monkeypatch.setattr("app.config.RUN_MANIFEST_DB_PATH", db)
        monkeypatch.setattr("app.config.STRATEGY_ROW_DB_PATH", db)
        _seed_verification_run(db, "run-verify")
        logs: list[str] = []
        result = run_endpoint_verification(
            completed_run_id="run-verify",
            report_quality="SUCCESS_COMPLETE",
            log_print=logs.append,
        )

    assert result["failed_count"] == 0
    assert result["passed_count"] >= 8
    assert any("ENDPOINT VERIFICATION START run=run-verify" in line for line in logs)
    assert any("[VERIFY][PASS] daily_opportunity" in line for line in logs)
    assert any("inferred=0" in line for line in logs)
    assert not any("FULL_RAW" in line or "account_number" in line for line in logs)


def test_endpoint_verification_detects_invalid_calendar_entry_permission(monkeypatch):
    from app.services.endpoint_verification_service import run_endpoint_verification

    with TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "verify.sqlite3")
        monkeypatch.setattr("app.config.RUN_MANIFEST_DB_PATH", db)
        monkeypatch.setattr("app.config.STRATEGY_ROW_DB_PATH", db)
        _seed_verification_run(db, "run-bad", bad_calendar_semantics=True)
        logs: list[str] = []
        result = run_endpoint_verification(
            completed_run_id="run-bad",
            report_quality="SUCCESS_COMPLETE",
            log_print=logs.append,
        )

    assert result["failed_count"] >= 1
    assert any("CALENDAR_ENTRY_PERMISSION_INVALID" in line for line in logs)
    assert any("[VERIFY][FAIL] earnings_calendar" in line for line in logs)


def test_endpoint_verification_respects_dev_mode_enablement(monkeypatch):
    from app.services.endpoint_verification_service import maybe_run_endpoint_verification

    monkeypatch.setattr("app.config.DEV_ENDPOINT_VERIFICATION_ENABLED", True)
    assert maybe_run_endpoint_verification(completed_run_id="run", run_mode="prod", report_quality="SUCCESS_COMPLETE", log_print=lambda _msg: None) is None
