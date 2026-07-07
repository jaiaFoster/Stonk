"""Reusable, provider-free gates for canonical opportunities."""

from __future__ import annotations

from datetime import datetime, timezone

from app.models.strategy_opportunity_models import StrategyGate


def _gate(gate_id: str, status: str, reason_code: str, label: str, value=None) -> StrategyGate:
    failed = status == "FAIL"
    warned = status in {"WARN", "UNKNOWN"}
    return StrategyGate(
        name=gate_id, gate_id=gate_id, status=status, detail=label,
        is_hard_block=failed, value=value, reason_code=reason_code, reason_label=label,
        blockers=[reason_code] if failed else [], warnings=[reason_code] if warned else [],
    )


class LiquidityGate:
    @staticmethod
    def evaluate(*, open_interest: int | None, volume: int | None, spread_pct: float | None,
                 min_open_interest: int = 1, min_volume: int = 0, max_spread_pct: float = 25.0) -> StrategyGate:
        value = {"open_interest": open_interest, "volume": volume, "spread_pct": spread_pct}
        if open_interest is None or spread_pct is None:
            return _gate("liquidity", "WARN", "LIQUIDITY_DATA_MISSING", "Liquidity data incomplete.", value)
        if open_interest < min_open_interest or (volume is not None and volume < min_volume) or spread_pct > max_spread_pct:
            return _gate("liquidity", "FAIL", "OPTIONS_ILLIQUID", "Option liquidity below configured bounds.", value)
        return _gate("liquidity", "PASS", "LIQUIDITY_OK", "Option liquidity passes configured bounds.", value)


class DebitGate:
    @staticmethod
    def evaluate(*, debit: float | None, max_debit: float) -> StrategyGate:
        if debit is None:
            return _gate("debit", "WARN", "DEBIT_UNKNOWN", "Package debit unavailable.")
        if debit > max_debit:
            return _gate("debit", "FAIL", "DEBIT_TOO_LARGE", "Package debit exceeds configured maximum.", debit)
        return _gate("debit", "PASS", "DEBIT_OK", "Package debit within configured maximum.", debit)


class AccountRiskGate:
    @staticmethod
    def evaluate(*, max_risk: float | None, account_value: float | None, max_risk_pct: float) -> StrategyGate:
        if account_value in (None, 0) or max_risk is None:
            return _gate("account_risk", "WARN", "ACCOUNT_VALUE_UNKNOWN", "Account risk cannot be confirmed.")
        risk_pct = max_risk / account_value * 100
        if risk_pct > max_risk_pct:
            return _gate("account_risk", "FAIL", "ACCOUNT_RISK_TOO_HIGH", "Structure risk exceeds account limit.", risk_pct)
        return _gate("account_risk", "PASS", "ACCOUNT_RISK_OK", "Structure risk within account limit.", risk_pct)


class EarningsConfidenceGate:
    @staticmethod
    def evaluate(confidence: str | None, conflict: bool = False) -> StrategyGate:
        if conflict:
            return _gate("earnings_confidence", "FAIL", "EARNINGS_DATE_CONFLICT", "Earnings sources conflict.")
        if confidence in {"high", "confirmed"}:
            return _gate("earnings_confidence", "PASS", "EARNINGS_DATE_CONFIRMED", "Earnings date confidence high.")
        return _gate("earnings_confidence", "WARN", "EARNINGS_DATE_UNCERTAIN", "Earnings date confidence limited.")


class SourceConfidenceGate:
    @staticmethod
    def evaluate(source_mode: str, strategy_id: str | None = None) -> StrategyGate:
        if source_mode == "source_qualified":
            return _gate("source_confidence", "PASS", "SOURCE_QUALIFIED", "Source data qualified.")
        if source_mode in {"diagnostic", "paper"}:
            return _gate("source_confidence", "FAIL", "SOURCE_NOT_LIVE", "Diagnostic or paper source cannot trade live.", {"source_mode": source_mode, "strategy_id": strategy_id})
        return _gate("source_confidence", "WARN", "SOURCE_UNKNOWN", "Source confidence unknown.")


class DataFreshnessGate:
    @staticmethod
    def evaluate(data_as_of: str | None, max_age_seconds: int, now: datetime | None = None) -> StrategyGate:
        try:
            parsed = datetime.fromisoformat(str(data_as_of).replace("Z", "+00:00"))
            parsed = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return _gate("data_freshness", "WARN", "DATA_FRESHNESS_UNKNOWN", "Data timestamp unavailable.")
        age = ((now or datetime.now(timezone.utc)) - parsed).total_seconds()
        if age > max_age_seconds:
            return _gate("data_freshness", "FAIL", "DATA_STALE", "Data exceeds freshness limit.", age)
        return _gate("data_freshness", "PASS", "DATA_FRESH", "Data within freshness limit.", age)


class ProviderBudgetGate:
    @staticmethod
    def evaluate(*, approved: bool, planner_state: str | None = None) -> StrategyGate:
        if approved:
            return _gate("provider_budget", "PASS", "PROVIDER_BUDGET_APPROVED", "Provider budget approved.")
        return _gate("provider_budget", "SKIP", planner_state or "PROVIDER_BUDGET_BLOCKED", "Provider planner did not approve request.")


def enforce_dry_run_actionability(strategy_id: str, source_mode: str, dry_run: bool) -> StrategyGate:
    if strategy_id == "forward_factor_calendar" and (dry_run or source_mode != "source_qualified"):
        return _gate("daily_opportunity", "FAIL", "FF_DRY_RUN_EXCLUDED", "Forward Factor remains dry-run and excluded from Daily Opportunity.")
    return _gate("daily_opportunity", "PASS", "DAILY_OPPORTUNITY_ALLOWED", "Strategy may apply its normal eligibility rules.")


# ─── 30A: Canonical normalized-row gate helpers ───────────────────────────────
#
# make_gate() produces a plain dict compatible with the existing {name, status, detail}
# shape used throughout the public screener and normalization layer. The dict is a
# superset: legacy consumers reading gate["name"] / gate["detail"] continue to work;
# new consumers can also read gate["id"], gate["label"], gate["reason"], etc.

from typing import Any  # noqa: E402 (below top-level, acceptable in this file)

GATE_STATUSES: frozenset[str] = frozenset({
    "pass", "watch", "fail", "unknown", "skipped", "not_applicable", "dry_run", "error",
})

_GATE_STATUS_RANK: dict[str, int] = {
    "error": 0, "fail": 1, "watch": 2, "dry_run": 3,
    "skipped": 4, "unknown": 5, "not_applicable": 6, "pass": 7,
}

_GATE_DEFAULT_SORT: dict[str, int] = {
    "data_quality": 10, "coverage_eligibility": 10, "cheap_filter": 10,
    "earnings_date": 20, "earnings_date_trust": 20, "earnings_trust": 20,
    "expiration_pair": 30, "chain_approved": 30,
    "source_qualified": 35, "source_qualification": 35,
    "iv_relationship": 40, "liquidity": 50, "structure": 50, "structure_built": 50,
    "diagnostic_model": 55, "momentum": 60, "trend": 60,
    "relative_strength": 65, "volume": 70, "risk": 70,
    "earnings_contamination": 70, "reward_risk": 75,
    "add_blockers": 75, "entry_blockers": 75,
    "calendar_entry": 80, "vertical_criteria": 80, "execution": 90,
}


def make_gate(
    label: str,
    status: str,
    *,
    id: str = "",
    value: str | None = None,
    reason: str = "",
    blocking: bool | None = None,
    sort_order: int | None = None,
) -> dict[str, Any]:
    """Create a canonical normalized-row gate dict.

    The dict is backward compatible with consumers that read gate["name"] and
    gate["detail"]. New consumers can additionally read gate["id"], gate["label"],
    gate["reason"], gate["blocking"], gate["sort_order"].
    """
    gate_id = id or label.lower().replace(" ", "_").replace("-", "_")
    canonical = normalize_gate_status(status)
    is_blocking = blocking if blocking is not None else (canonical == "fail")
    order = sort_order if sort_order is not None else _GATE_DEFAULT_SORT.get(gate_id, 50)
    return {
        "id": gate_id,
        "label": label,
        "name": label,      # backward compat
        "status": canonical,
        "value": value,
        "reason": reason,
        "detail": reason,   # backward compat
        "blocking": is_blocking,
        "sort_order": order,
    }


def normalize_gate_status(status: str) -> str:
    """Map any status string to a canonical gate status value."""
    clean = str(status or "").lower().strip()
    if clean in ("pass", "ok", "green", "true", "yes", "passed"):
        return "pass"
    if clean in ("watch", "warn", "warning", "yellow", "near"):
        return "watch"
    if clean in ("fail", "failed", "no", "false", "red", "block", "blocked", "fail / "):
        return "fail"
    if clean in ("not_applicable", "na", "n/a", "not applicable"):
        return "not_applicable"
    if clean in ("skipped", "skip", "excluded"):
        return "skipped"
    if clean in ("dry_run", "dry-run", "signal_only", "dryrun"):
        return "dry_run"
    if clean in ("error", "err"):
        return "error"
    if clean in GATE_STATUSES:
        return clean
    return "unknown"


def gate_status_rank(status: str) -> int:
    """Numeric rank for a gate status — lower is worse / higher priority."""
    return _GATE_STATUS_RANK.get(normalize_gate_status(status), 5)


def has_blocking_gate_failure(gates: list[dict[str, Any]]) -> bool:
    """True if any gate in the list is blocking with status 'fail' or 'error'."""
    return any(
        gate.get("blocking") and gate.get("status") in ("fail", "error")
        for gate in (gates or [])
    )


def summarize_gates(gates: list[dict[str, Any]]) -> dict[str, Any]:
    """Compact summary of a gate list: worst status, counts, blocking state."""
    if not gates:
        return {
            "total": 0, "worst_status": "unknown", "fail_count": 0,
            "pass_count": 0, "has_blocking_failure": False,
        }
    counts: dict[str, int] = {}
    for gate in gates:
        s = gate.get("status", "unknown")
        counts[s] = counts.get(s, 0) + 1
    worst = min(gates, key=lambda g: gate_status_rank(g.get("status", "unknown")))
    return {
        "total": len(gates),
        "worst_status": worst.get("status", "unknown"),
        "fail_count": counts.get("fail", 0) + counts.get("error", 0),
        "pass_count": counts.get("pass", 0),
        "watch_count": counts.get("watch", 0),
        "skipped_count": counts.get("skipped", 0) + counts.get("not_applicable", 0),
        "has_blocking_failure": has_blocking_gate_failure(gates),
        "status_counts": counts,
    }
