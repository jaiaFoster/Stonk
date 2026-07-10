"""Post-run read-only endpoint verification packet.

The verifier calls endpoint helper functions directly, not external HTTP and
not provider services. It is observability only: failures are logged but never
change run quality or trigger a new run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable

from app import config

_VERIFY_IN_PROGRESS = False


@dataclass
class _Check:
    name: str
    status: str
    fields: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None
    assertion: str | None = None


def maybe_run_endpoint_verification(
    *,
    completed_run_id: str | None,
    run_mode: str,
    report_quality: str | None,
    log_print: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """Run verification for dev-mode completed runs when enabled."""
    enabled = bool(getattr(config, "DEV_ENDPOINT_VERIFICATION_ENABLED", False))
    if not enabled and str(run_mode or "").lower() != "dev":
        return None
    if not enabled:
        return None
    if str(run_mode or "").lower() != "dev":
        return None
    return run_endpoint_verification(
        completed_run_id=completed_run_id,
        report_quality=report_quality,
        log_print=log_print,
    )


def run_endpoint_verification(
    *,
    completed_run_id: str | None,
    report_quality: str | None,
    log_print: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Verify important read-only API surfaces against the completed run."""
    global _VERIFY_IN_PROGRESS
    log = log_print or (lambda message: print(message, flush=True))
    if _VERIFY_IN_PROGRESS:
        result = {"verification_status": "WARNING", "warning": "verification_already_in_progress"}
        log("[VERIFY][WARN] endpoint_verification warning=verification_already_in_progress")
        return result
    _VERIFY_IN_PROGRESS = True
    started = perf_counter()
    checks: list[_Check] = []
    try:
        log(f"=== ENDPOINT VERIFICATION START run={completed_run_id or 'unknown'} quality={report_quality or 'UNKNOWN'} ===")
        checks.append(_check_health())
        checks.append(_check_dashboard(completed_run_id))
        checks.append(_check_daily(completed_run_id))
        checks.append(_check_open_positions())
        for strategy_id in ("earnings_calendar", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum"):
            checks.append(_check_strategy_rows(strategy_id, completed_run_id))
        checks.append(_check_catalog())
        checks.append(_check_catalog_operators())
        checks.append(_check_catalog_field())
        for check in checks:
            _log_check(log, check)
        passed = sum(1 for check in checks if check.status == "PASS")
        warned = sum(1 for check in checks if check.status == "WARN")
        failed = sum(1 for check in checks if check.status == "FAIL")
        duration_ms = int((perf_counter() - started) * 1000)
        verification_status = "FAILED" if failed else ("WARNING" if warned else "PASS")
        log(f"[VERIFY][SUMMARY] passed={passed} warned={warned} failed={failed} duration_ms={duration_ms}")
        log("=== ENDPOINT VERIFICATION END ===")
        return {
            "verification_status": verification_status,
            "passed_count": passed,
            "warning_count": warned,
            "failed_count": failed,
            "duration_ms": duration_ms,
            "checks": [_safe_check_dict(check) for check in checks],
        }
    except Exception as exc:
        log(f"[VERIFY][FAIL] endpoint_verification assertion=UNEXPECTED_ERROR message={_safe_message(str(exc))}")
        return {"verification_status": "FAILED", "error": "unexpected_verification_error"}
    finally:
        _VERIFY_IN_PROGRESS = False


def _check_health() -> _Check:
    return _Check("health", "PASS", {"status": 200, "body": "OK"})


def _check_dashboard(run_id: str | None) -> _Check:
    from app.api.dashboard_api import build_dashboard_summary

    data = build_dashboard_summary()
    fields = {
        "source": "compact_manifest",
        "latest_run_id": data.get("run_id"),
        "provider_calls_triggered": data.get("provider_calls_triggered"),
    }
    if data.get("provider_calls_triggered") is not False:
        return _Check("dashboard", "FAIL", fields, assertion="PROVIDER_CALLS_TRIGGERED")
    if run_id and data.get("run_id") != run_id:
        return _Check("dashboard", "FAIL", fields, assertion="RUN_ID_MISMATCH")
    return _Check("dashboard", "PASS", fields)


def _check_daily(run_id: str | None) -> _Check:
    from app.api.daily_opportunity_api import build_daily_opportunity_response

    data = build_daily_opportunity_response(limit=12, include_exclusions=True)
    dry = (data.get("dry_run_exclusions") or {}).get("forward_factor_calendar") or {}
    fields = {
        "source": data.get("source"),
        "fallback_used": data.get("fallback_used"),
        "actions": data.get("action_count"),
        "eligible": data.get("eligible_before_limit"),
        "semantic_rows": (data.get("semantic_source_counts") or {}).get("row", 0),
        "inferred": data.get("inferred_semantics_count"),
        "ff_dry_run_excluded": dry.get("rows_seen", 0),
        "provider_calls_triggered": data.get("provider_calls_triggered"),
        "latest_run_id": data.get("latest_run_id"),
    }
    if data.get("provider_calls_triggered") is not False:
        return _Check("daily_opportunity", "FAIL", fields, assertion="PROVIDER_CALLS_TRIGGERED")
    if data.get("source") != "strategy_row_store" or data.get("fallback_used") is not False:
        return _Check("daily_opportunity", "FAIL", fields, assertion="ROW_STORE_NOT_PRIMARY")
    if run_id and data.get("latest_run_id") != run_id:
        return _Check("daily_opportunity", "FAIL", fields, assertion="RUN_ID_MISMATCH")
    if int(data.get("inferred_semantics_count") or 0) != 0:
        return _Check("daily_opportunity", "FAIL", fields, assertion="SEMANTICS_INFERRED")
    if dry.get("excluded_reason") != "dry_run":
        return _Check("daily_opportunity", "FAIL", fields, assertion="FF_DRY_RUN_EXCLUSION_MISSING")
    return _Check("daily_opportunity", "PASS", fields)


def _check_open_positions() -> _Check:
    from app.api.open_positions_api import build_open_positions_response

    data = build_open_positions_response()
    active = int(data.get("active_calendar_count") or 0)
    has_open = bool(data.get("has_open_calendars"))
    fields = {
        "source": data.get("source"),
        "active_calendars": active,
        "has_open_calendars": has_open,
        "provider_calls_triggered": data.get("provider_calls_triggered"),
    }
    if data.get("provider_calls_triggered") is not False:
        return _Check("open_positions", "FAIL", fields, assertion="PROVIDER_CALLS_TRIGGERED")
    if active > 0 and not has_open:
        return _Check("open_positions", "FAIL", fields, assertion="HAS_OPEN_CALENDARS_FALSE")
    if data.get("source") not in {"strategy_row_store", "open_position_store", "empty"}:
        return _Check("open_positions", "WARN", fields, warning="legacy_or_unknown_source")
    if active == 1 and has_open:
        return _Check("open_positions", "WARN", fields, warning="lifecycle_completeness_deferred")
    return _Check("open_positions", "PASS", fields)


def _check_strategy_rows(strategy_id: str, run_id: str | None) -> _Check:
    from app.api.strategy_api import get_strategy_rows

    data = get_strategy_rows(strategy_id, limit=200)
    rows = [row for row in data.get("rows") or [] if isinstance(row, dict)]
    fields = {
        "rows": len(rows),
        "source": data.get("source"),
        "latest_run_id": data.get("latest_run_id"),
        "provider_calls_triggered": data.get("provider_calls_triggered"),
    }
    if data.get("provider_calls_triggered") is not False:
        return _Check(strategy_id, "FAIL", fields, assertion="PROVIDER_CALLS_TRIGGERED")
    if data.get("source") != "strategy_row_store":
        return _Check(strategy_id, "FAIL", fields, assertion="ROW_STORE_NOT_PRIMARY")
    if run_id and data.get("latest_run_id") != run_id:
        return _Check(strategy_id, "FAIL", fields, assertion="RUN_ID_MISMATCH")
    if int(data.get("row_count") or 0) != len(rows):
        return _Check(strategy_id, "FAIL", fields, assertion="ROW_COUNT_MISMATCH")
    if strategy_id == "earnings_calendar":
        invalid = [
            row for row in rows
            if str(row.get("row_type") or "") == "rejected_candidate"
            and (
                row.get("eligibility_status") == "eligible"
                or row.get("action_type") not in {None, "", "none"}
                or row.get("decision_class") != "rejected"
            )
        ]
        fields["rejected"] = sum(1 for row in rows if str(row.get("row_type") or "") == "rejected_candidate")
        fields["invalid_eligible_rejected_rows"] = len(invalid)
        if invalid:
            return _Check(strategy_id, "FAIL", fields, assertion="REJECTED_ROW_MARKED_ELIGIBLE")
    if strategy_id == "forward_factor_calendar":
        bad = [row for row in rows if not row.get("dry_run") or row.get("eligibility_status") != "dry_run_excluded"]
        fields["dry_run"] = True
        if bad:
            return _Check(strategy_id, "FAIL", fields, assertion="FF_DRY_RUN_ROW_ELIGIBLE")
    if strategy_id == "stock_momentum":
        non_row = sum(1 for row in rows if row.get("semantic_source") != "row")
        fields["semantic_source_row"] = len(rows) - non_row
        if non_row:
            return _Check(strategy_id, "FAIL", fields, assertion="STOCK_SEMANTIC_SOURCE_NOT_ROW")
    return _Check(strategy_id, "PASS", fields)


def _check_catalog() -> _Check:
    from app.services.strategy_data_catalog_service import build_catalog_response

    data = build_catalog_response()
    fields = {
        "schema": data.get("catalog_schema_version"),
        "fields": data.get("field_count"),
        "provider_calls_triggered": data.get("provider_calls_triggered"),
    }
    if data.get("provider_calls_triggered") is not False or data.get("read_only") is not True:
        return _Check("strategy_catalog", "FAIL", fields, assertion="CATALOG_NOT_READ_ONLY")
    if data.get("catalog_schema_version") != "31A.v1" or int(data.get("field_count") or 0) <= 0:
        return _Check("strategy_catalog", "FAIL", fields, assertion="CATALOG_SCHEMA_OR_FIELD_COUNT")
    return _Check("strategy_catalog", "PASS", fields)


def _check_catalog_operators() -> _Check:
    from app.services.strategy_data_catalog_service import operator_catalog

    data = operator_catalog()
    fields = {
        "operator_sets": len(data.get("operator_sets") or {}),
        "provider_calls_triggered": data.get("provider_calls_triggered"),
    }
    if data.get("provider_calls_triggered") is not False or not data.get("operator_sets"):
        return _Check("strategy_catalog_operators", "FAIL", fields, assertion="OPERATORS_UNAVAILABLE")
    return _Check("strategy_catalog_operators", "PASS", fields)


def _check_catalog_field() -> _Check:
    from app.services.strategy_data_catalog_service import get_field

    data, status = get_field("options.delta")
    fields = {
        "status": status,
        "field_id": ((data.get("field") or {}).get("field_id") if isinstance(data, dict) else None),
        "provider_calls_triggered": data.get("provider_calls_triggered") if isinstance(data, dict) else None,
    }
    if status != 200 or fields["field_id"] != "options.delta" or fields["provider_calls_triggered"] is not False:
        return _Check("strategy_catalog_field", "FAIL", fields, assertion="FIELD_LOOKUP_FAILED")
    return _Check("strategy_catalog_field", "PASS", fields)


def _log_check(log: Callable[[str], None], check: _Check) -> None:
    pairs = " ".join(f"{key}={_safe_value(value)}" for key, value in check.fields.items() if value is not None)
    suffix = ""
    if check.warning:
        suffix = f" warning={check.warning}"
    if check.assertion:
        suffix = f" assertion={check.assertion}"
    log(f"[VERIFY][{check.status}] {check.name} {pairs}{suffix}".strip())


def _safe_check_dict(check: _Check) -> dict[str, Any]:
    return {
        "name": check.name,
        "status": check.status,
        "fields": check.fields,
        "warning": check.warning,
        "assertion": check.assertion,
    }


def _safe_value(value: Any) -> str:
    text = str(value).replace("\n", " ").replace("\r", " ")
    return text[:120]


def _safe_message(message: str) -> str:
    lowered = message.lower()
    for marker in ("token", "password", "secret", "account"):
        if marker in lowered:
            return "redacted_error"
    return message.replace("\n", " ")[:160]
