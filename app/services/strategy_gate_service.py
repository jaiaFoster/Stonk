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
