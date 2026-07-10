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

# Top-level draft fields that indicate code execution attempts — rejected outright.
_FORBIDDEN_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "code", "python", "eval", "exec", "shell", "command",
    "sql", "url_fetch", "network", "broker_write", "order", "trade",
    "schedule", "cron", "callback", "webhook",
})

# Only these top-level draft keys are allowed.
_ALLOWED_TOP_LEVEL_KEYS: frozenset[str] = frozenset({
    "name", "description", "asset_class", "inputs", "rules",
    "gates", "weight", "reason_template", "version", "tags",
})

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
    from app.services.strategy_row_schema import (
        STRATEGY_ROW_SCHEMA_VERSION,
        MINIMUM_SUPPORTED_STRATEGY_ROW_SCHEMA_VERSION,
        SEMANTIC_FIELDS_VERSION,
    )
    return {
        **_READ_ONLY_BASE,
        "schema_version": SCHEMA_VERSION,
        "canonical_strategy_row_schema_version": STRATEGY_ROW_SCHEMA_VERSION,
        "minimum_supported_schema_version": MINIMUM_SUPPORTED_STRATEGY_ROW_SCHEMA_VERSION,
        "semantic_fields_version": SEMANTIC_FIELDS_VERSION,
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
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return {
                **_READ_ONLY_BASE,
                "rows": [], "count": 0,
                "note": "No snapshot available.",
            }
        summary = repo.load_summary(snapshot, full=True)
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


def get_strategy_rows(
    strategy_id: str,
    limit: int = 20,
    row_id: str | None = None,
) -> dict[str, Any]:
    """Return universalized rows for a registered strategy from the latest snapshot.

    Supports stock_momentum and stock_momentum_unified_test for 30B.
    Other strategies return an empty-state 200 until their lanes are implemented.
    No provider calls triggered — reads from stored snapshot only.
    """
    strategy_id = str(strategy_id or "").strip()
    from app.strategies.registry import STRATEGY_SPEC_REGISTRY
    if not strategy_id or strategy_id not in STRATEGY_SPEC_REGISTRY:
        return {
            **_READ_ONLY_BASE,
            "strategy_id": strategy_id,
            "rows": [],
            "count": 0,
            "error": "Unknown strategy_id.",
            "valid_ids": list(STRATEGY_SPEC_REGISTRY.keys()),
        }

    try:
        from app.services.strategy_row_repository import StrategyRowRepository
        row_store = StrategyRowRepository()
        stored = row_store.read_latest(strategy_id, limit=min(int(limit or 20), 50))
        if stored.get("rows"):
            rows = stored["rows"]
            if row_id:
                rows = [row for row in rows if str(row.get("row_id") or "") == str(row_id)]
            error_count = sum(1 for row in rows if row.get("normalization_status") != "ok")
            return {
                **_READ_ONLY_BASE,
                "strategy_id": strategy_id,
                "schema_version": rows[0].get("schema_version") if rows else None,
                "latest_run_id": stored.get("run_id"),
                "source_run_id": stored.get("run_id"),
                "row_count": len(rows),
                "rows": rows,
                "source": "strategy_row_store",
                "empty_state": None,
                "normalization_summary": {
                    "row_count": len(rows),
                    "error_count": error_count,
                    "ok_count": len(rows) - error_count,
                },
                "dry_run": True if strategy_id == "forward_factor_calendar" else None,
            }
    except Exception:
        pass

    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return {
                **_READ_ONLY_BASE,
                "strategy_id": strategy_id,
                "rows": [],
                "count": 0,
                "empty_state": "no_snapshot",
                "source": "empty",
                "note": "No successful snapshot available.",
            }
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}

        if strategy_id in ("stock_momentum", "stock_momentum_unified_test"):
            sm_key = "stock_momentum"
            sm = strategies.get(sm_key) or {}
            raw_rows = sm.get("items") or sm.get("rows") or sm.get("canonical_opportunities") or []
            cap = min(int(limit or 20), 50)
            rows = []
            for row in list(raw_rows)[:cap]:
                if not isinstance(row, dict):
                    continue
                enriched = dict(row)
                if strategy_id == "stock_momentum_unified_test":
                    enriched["strategy_id"] = "stock_momentum_unified_test"
                try:
                    from app.strategies.stock_momentum_universal import build_stock_momentum_universal_row
                    build_stock_momentum_universal_row(enriched, run_id=snapshot.get("run_id"))
                except Exception:
                    pass
                rows.append(enriched)
            return {
                **_READ_ONLY_BASE,
                "strategy_id": strategy_id,
                "rows": rows,
                "row_count": len(rows),
                "source_run_id": snapshot.get("run_id"),
                "schema_version": rows[0].get("schema_version") if rows else None,
                "source": "legacy_snapshot_fallback",
                "normalization_summary": {"row_count": len(rows), "error_count": 0},
            }

        if strategy_id == "earnings_calendar":
            ec = strategies.get("earnings_calendar") or {}
            raw_rows = ec.get("rows") or ec.get("items") or ec.get("canonical_opportunities") or []
            # Also collect lifecycle/open-position rows from the snapshot
            lifecycle = tradier.get("_calendar_lifecycle_checks") or {}
            lifecycle_checks = list(lifecycle.get("checks") or [])
            cap = min(int(limit or 20), 50)
            rows = []
            run_id = snapshot.get("run_id")
            for row in list(raw_rows)[:cap]:
                if not isinstance(row, dict):
                    continue
                enriched = dict(row)
                try:
                    from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
                    build_earnings_calendar_universal_row(enriched, run_id=run_id)
                except Exception:
                    pass
                rows.append(enriched)
            for check in lifecycle_checks[: max(0, cap - len(rows))]:
                if not isinstance(check, dict):
                    continue
                enriched = dict(check)
                try:
                    from app.strategies.earnings_calendar_universal import build_earnings_lifecycle_universal_row
                    build_earnings_lifecycle_universal_row(enriched, run_id=run_id)
                except Exception:
                    pass
                rows.append(enriched)
            return {
                **_READ_ONLY_BASE,
                "strategy_id": strategy_id,
                "rows": rows,
                "row_count": len(rows),
                "source_run_id": run_id,
                "schema_version": rows[0].get("schema_version") if rows else None,
                "source": "legacy_snapshot_fallback",
                "normalization_summary": {"row_count": len(rows), "error_count": 0},
            }

        if strategy_id == "skew_momentum_vertical":
            skew_data = strategies.get("skew_momentum_vertical") or tradier.get("_skew_momentum_vertical_strategy") or {}
            raw_rows = skew_data.get("items") or skew_data.get("rows") or skew_data.get("canonical_opportunities") or []
            cap = min(int(limit or 20), 50)
            rows = []
            run_id = snapshot.get("run_id")
            for row in list(raw_rows)[:cap]:
                if not isinstance(row, dict):
                    continue
                enriched = dict(row)
                try:
                    from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
                    build_skew_momentum_vertical_universal_row(enriched, run_id=run_id)
                except Exception:
                    pass
                rows.append(enriched)
            return {
                **_READ_ONLY_BASE,
                "strategy_id": strategy_id,
                "rows": rows,
                "row_count": len(rows),
                "source_run_id": run_id,
                "schema_version": rows[0].get("schema_version") if rows else None,
                "source": "legacy_snapshot_fallback",
                "normalization_summary": {"row_count": len(rows), "error_count": 0},
            }

        if strategy_id == "forward_factor_calendar":
            ff_data = strategies.get("forward_factor_calendar") or tradier.get("_forward_factor_strategy") or {}
            raw_rows = ff_data.get("items") or ff_data.get("rows") or []
            cap = min(int(limit or 20), 50)
            rows = []
            run_id = snapshot.get("run_id")
            for row in list(raw_rows)[:cap]:
                if not isinstance(row, dict):
                    continue
                enriched = dict(row)
                try:
                    from app.strategies.forward_factor_universal import build_forward_factor_universal_row
                    build_forward_factor_universal_row(enriched, run_id=run_id)
                except Exception:
                    pass
                rows.append(enriched)
            return {
                **_READ_ONLY_BASE,
                "strategy_id": strategy_id,
                "rows": rows,
                "row_count": len(rows),
                "source_run_id": run_id,
                "schema_version": rows[0].get("schema_version") if rows else None,
                "dry_run": True,
                "source": "legacy_snapshot_fallback",
                "normalization_summary": {"row_count": len(rows), "error_count": 0},
            }

        # Other strategies: return empty state — future lanes will implement them.
        return {
            **_READ_ONLY_BASE,
            "strategy_id": strategy_id,
            "rows": [],
            "row_count": 0,
            "empty_state": "strategy_not_yet_universalized",
            "note": f"Universal row output for {strategy_id!r} is not yet implemented.",
        }

    except Exception as exc:
        return {**_READ_ONLY_BASE, "strategy_id": strategy_id, "rows": [], "row_count": 0, "error": str(exc)}


def get_strategy_rankings(
    strategy_id: str | None = None,
    ticker: str | None = None,
    verdict: str | None = None,
    tier: str | None = None,
    actionable_only: bool = False,
    include_diagnostic: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Return universally ranked strategy rows from the current-run Strategy Row Store.

    31B.15: Reads only from persisted current-run data — no provider calls.
    Filters: strategy_id, ticker, verdict, tier, actionable_only, include_diagnostic.
    """
    from app import config as _cfg
    cap = min(int(limit or 20), 100)
    _STRATEGY_IDS = ["earnings_calendar", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum"]
    target_ids = [strategy_id] if strategy_id else _STRATEGY_IDS

    all_rows: list[dict[str, Any]] = []
    run_ids: dict[str, str | None] = {}
    try:
        from app.services.strategy_row_repository import StrategyRowRepository
        repo = StrategyRowRepository()
        for sid in target_ids:
            try:
                stored = repo.read_latest(sid, limit=50)
                run_ids[sid] = stored.get("run_id")
                for row in (stored.get("rows") or []):
                    if isinstance(row, dict):
                        row.setdefault("strategy_id", sid)
                        all_rows.append(row)
            except Exception:
                run_ids[sid] = None
    except Exception as exc:
        return {**_READ_ONLY_BASE, "rows": [], "count": 0, "error": str(exc)}

    try:
        from app.services.universal_ranking_service import rank_strategy_rows
        ranked = rank_strategy_rows(all_rows)
    except Exception:
        ranked = all_rows

    if ticker:
        _t = str(ticker).upper().strip()
        ranked = [r for r in ranked if str(r.get("ticker") or "").upper() == _t]
    if verdict:
        _v = str(verdict).upper()
        ranked = [r for r in ranked if _v in str(r.get("verdict") or "").upper()]
    if tier:
        ranked = [r for r in ranked if str(r.get("opportunity_tier") or "") == str(tier).upper()]
    if actionable_only:
        ranked = [r for r in ranked if bool(r.get("strategy_actionable"))]
    if not include_diagnostic:
        ranked = [r for r in ranked if str(r.get("opportunity_tier") or "") != "DIAGNOSTIC"]

    returned = ranked[:cap]
    score_versions = list({str(r.get("score_version") or "") for r in returned if r.get("score_version")})
    ranking_versions = list({str(r.get("ranking_version") or "") for r in returned if r.get("ranking_version")})
    completeness_avg = (
        round(sum(float(r.get("score_completeness_pct") or 0) for r in returned) / len(returned), 1)
        if returned else None
    )
    dry_run_sids = [sid for sid in target_ids if sid == "forward_factor_calendar"]

    return {
        **_READ_ONLY_BASE,
        "rows": returned,
        "count": len(returned),
        "total_before_filter": len(ranked),
        "latest_run_ids": run_ids,
        "score_version": score_versions[0] if len(score_versions) == 1 else score_versions,
        "ranking_version": ranking_versions[0] if len(ranking_versions) == 1 else ranking_versions,
        "source": "strategy_row_store",
        "score_completeness_summary": {"average_pct": completeness_avg},
        "dry_run_strategy_ids": dry_run_sids,
        "filters_applied": {
            "strategy_id": strategy_id,
            "ticker": ticker,
            "verdict": verdict,
            "tier": tier,
            "actionable_only": actionable_only,
            "include_diagnostic": include_diagnostic,
        },
    }


def get_ticker_comparison(ticker: str) -> dict[str, Any]:
    """Return all strategies evaluated for a single ticker with comparative ranking.

    31B.15: Optional compare endpoint — GET /api/strategies/compare?ticker=XYZ
    """
    ticker = str(ticker or "").upper().strip()
    if not ticker:
        return {**_READ_ONLY_BASE, "error": "ticker parameter required", "rows": [], "count": 0}
    result = get_strategy_rankings(ticker=ticker, include_diagnostic=True, limit=50)
    rows = result.get("rows") or []
    rows.sort(key=lambda r: (-(int(r.get("universal_score") or 0)), str(r.get("strategy_id") or "")))
    best = rows[0] if rows else None
    why_ranked = None
    if best and len(rows) > 1:
        second = rows[1]
        why_ranked = (
            f"{best.get('strategy_id')} ranked above {second.get('strategy_id')} "
            f"because score {best.get('universal_score', '?')} > {second.get('universal_score', '?')}"
            + (f" and tier {best.get('opportunity_tier')} vs {second.get('opportunity_tier')}" if best.get("opportunity_tier") != second.get("opportunity_tier") else "")
            + "."
        )
    return {
        **_READ_ONLY_BASE,
        "ticker": ticker,
        "rows": rows,
        "count": len(rows),
        "best_strategy": best.get("strategy_id") if best else None,
        "best_score": best.get("universal_score") if best else None,
        "best_tier": best.get("opportunity_tier") if best else None,
        "why_ranked": why_ranked,
        "latest_run_ids": result.get("latest_run_ids"),
        "source": "strategy_row_store",
    }


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

    # Reject forbidden top-level keys that signal code execution.
    for key in draft.keys():
        if str(key).lower() in _FORBIDDEN_TOP_LEVEL_KEYS:
            errors.append(
                f"Field {key!r} is not allowed in strategy drafts — "
                f"draft strategies cannot contain code, SQL, shell, or execution directives."
            )

    # Warn about unexpected top-level keys beyond the allowed set.
    for key in draft.keys():
        if str(key).lower() not in _ALLOWED_TOP_LEVEL_KEYS and str(key).lower() not in _FORBIDDEN_TOP_LEVEL_KEYS:
            warnings.append(f"Unrecognized field {key!r} will be ignored by the validator.")

    name = str(draft.get("name") or "").strip()
    if not name:
        errors.append("Draft must include a non-empty 'name' field.")
    elif len(name) > 100:
        errors.append("Draft name must be 100 characters or fewer.")

    # Scan all string values in the entire draft for forbidden patterns.
    _scan_for_forbidden_patterns(draft, "", errors)

    gates_def = draft.get("gates") or draft.get("rules")
    if gates_def is None:
        warnings.append("No 'gates' or 'rules' defined — draft will always pass.")
    elif not isinstance(gates_def, list):
        errors.append("'gates'/'rules' must be a list of gate objects.")
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

    weight = draft.get("weight")
    if weight is not None and not isinstance(weight, (int, float)):
        errors.append("'weight' must be a number.")

    return {
        **_READ_ONLY_BASE,
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def _scan_for_forbidden_patterns(
    obj: Any, path: str, errors: list[str]
) -> None:
    """Recursively scan any string value for forbidden execution patterns."""
    if isinstance(obj, str):
        for pat in _FORBIDDEN_PATTERNS:
            if pat in obj:
                errors.append(f"Forbidden pattern {pat!r} detected at {path!r}.")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _scan_for_forbidden_patterns(v, f"{path}.{k}" if path else k, errors)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_for_forbidden_patterns(item, f"{path}[{i}]", errors)
