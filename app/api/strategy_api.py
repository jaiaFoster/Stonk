"""Strategy API builder functions — read-only, no provider calls.

All functions return plain dicts suitable for jsonify(). None of them trigger
provider calls, write to brokers, or execute user-supplied code.

CAVEMAN MODE: validate_draft() must NEVER eval, exec, or call any expression
evaluator — it performs static vocabulary and structure checks only.
"""

from __future__ import annotations

from typing import Any

from app.strategies.schema import REQUIRED_CORE_FIELDS, SCHEMA_VERSION, VALID_ROW_TYPES

_SAFE_OPERATORS: frozenset[str] = frozenset({
    "gt", "gte", "lt", "lte", "eq", "neq", "in", "not_in",
})

_SAFE_METRICS: frozenset[str] = frozenset({
    "score", "verdict_tier", "momentum_score", "iv_ratio", "debit", "spread_pct",
    "open_interest", "volume", "price_return_pct", "relative_strength",
    "days_to_earnings", "forward_factor",
})

_FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "eval", "exec", "import", "__", "os.", "sys.", "subprocess",
    "open(", "socket", "requests", "http",
)

_READ_ONLY_BASE: dict[str, Any] = {"provider_calls_triggered": False, "read_only": True}


def list_strategies() -> dict[str, Any]:
    """Return all registered strategy specs."""
    from app.strategies.registry import STRATEGY_SPEC_REGISTRY
    return {
        **_READ_ONLY_BASE,
        "strategies": list(STRATEGY_SPEC_REGISTRY.values()),
        "count": len(STRATEGY_SPEC_REGISTRY),
        "schema_version": SCHEMA_VERSION,
    }


def get_strategy(strategy_id: str) -> dict[str, Any] | None:
    """Return a single strategy spec by ID, or None if not found."""
    from app.strategies.registry import STRATEGY_SPEC_REGISTRY
    return STRATEGY_SPEC_REGISTRY.get(str(strategy_id or ""))


def get_strategy_schema() -> dict[str, Any]:
    """Return the universal row schema definition."""
    return {
        **_READ_ONLY_BASE,
        "schema_version": SCHEMA_VERSION,
        "required_core_fields": list(REQUIRED_CORE_FIELDS),
        "valid_row_types": sorted(VALID_ROW_TYPES),
        "valid_gate_statuses": sorted(
            ("pass", "watch", "fail", "unknown", "skipped", "dry_run")
        ),
    }


def get_test_rows(
    strategy_id: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """Return normalized test rows from the StockMomentumUnifiedTest clone.

    Reads from the latest stored snapshot — no provider calls.
    """
    from app.strategies.test_stock_momentum_unified import StockMomentumUnifiedTest
    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=False)
        if not snapshot:
            return {
                **_READ_ONLY_BASE,
                "rows": [], "count": 0,
                "note": "No snapshot available.",
            }
        summary = repo.load_summary(snapshot, full=False)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}
        sm = strategies.get("stock_momentum") or {}
        raw_rows = sm.get("items") or sm.get("rows") or sm.get("canonical_opportunities") or []
        clone = StockMomentumUnifiedTest()
        rows = clone.test_rows(list(raw_rows), limit=min(int(limit or 20), 50))
        return {
            **_READ_ONLY_BASE,
            "strategy_id": clone.strategy_id,
            "rows": rows,
            "count": len(rows),
            "source_run_id": snapshot.get("run_id"),
        }
    except Exception as exc:
        return {**_READ_ONLY_BASE, "rows": [], "count": 0, "error": str(exc)}


def validate_draft(draft: Any) -> dict[str, Any]:
    """Validate a draft strategy DSL object.

    Static analysis only — no code execution of any kind.
    Returns {valid, errors, warnings}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(draft, dict):
        return {
            **_READ_ONLY_BASE,
            "valid": False,
            "errors": ["Draft must be a JSON object."],
            "warnings": [],
        }

    name = str(draft.get("name") or "").strip()
    if not name:
        errors.append("Draft must include a non-empty 'name' field.")
    elif len(name) > 100:
        errors.append("Draft name must be 100 characters or fewer.")

    gates_def = draft.get("gates")
    if gates_def is None:
        warnings.append("No 'gates' defined — draft will always pass.")
    elif not isinstance(gates_def, list):
        errors.append("'gates' must be a list of gate objects.")
    else:
        for i, gate in enumerate(gates_def):
            if not isinstance(gate, dict):
                errors.append(f"gates[{i}]: must be an object, got {type(gate).__name__}.")
                continue
            metric = str(gate.get("metric") or "")
            operator = str(gate.get("operator") or "")
            if metric and metric not in _SAFE_METRICS:
                warnings.append(
                    f"gates[{i}]: metric {metric!r} is not in the recognized safe metric list. "
                    f"Known: {sorted(_SAFE_METRICS)}"
                )
            if operator and operator not in _SAFE_OPERATORS:
                errors.append(
                    f"gates[{i}]: operator {operator!r} is not allowed. "
                    f"Use one of: {sorted(_SAFE_OPERATORS)}"
                )
            for key, val in gate.items():
                if isinstance(val, str):
                    for pat in _FORBIDDEN_PATTERNS:
                        if pat in val:
                            errors.append(
                                f"gates[{i}].{key}: forbidden pattern {pat!r} detected."
                            )

    weight = draft.get("weight")
    if weight is not None and not isinstance(weight, (int, float)):
        errors.append("'weight' must be a number.")

    return {
        **_READ_ONLY_BASE,
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }
