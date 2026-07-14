"""Pure account-risk facts for earnings-calendar structures."""

from __future__ import annotations

from typing import Any

from app import config


def evaluate_account_risk(candidate: dict[str, Any], account_context: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return debit/account sizing facts only; no verdict or action ownership."""
    debit = _num(candidate.get("debit_total_estimate"))
    if debit is None:
        per_spread = _num(candidate.get("conservative_debit") or candidate.get("mid_debit"))
        debit = per_spread * 100.0 if per_spread is not None else None

    override = getattr(config, "CALENDAR_ACCOUNT_VALUE_OVERRIDE", None)
    if override:
        account_value = float(override)
    else:
        account_value = _num((account_context or {}).get("account_value_estimate"))

    pct_of_account = (debit / account_value * 100.0) if debit is not None and account_value and account_value > 0 else None
    max_debit_pct_of_account = float(getattr(config, "CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT", 0.02) or 0.02) * 100.0

    status = "OK"
    warning = ""
    if not bool(config.CALENDAR_ACCOUNT_GUARDRAILS_ENABLED):
        status = "OK"
    elif account_value is None:
        status = "UNKNOWN ACCOUNT VALUE"
        warning = "Account value unavailable; debit sizing cannot be fully checked."
    elif debit is not None and (debit > float(config.CALENDAR_MAX_DEBIT_DOLLARS) or (pct_of_account or 0) > max_debit_pct_of_account):
        status = "TOO LARGE"
        warning = "Debit is too large for configured account guardrails."
    elif debit is not None and (debit > float(config.CALENDAR_WARN_DEBIT_DOLLARS) or (pct_of_account or 0) > float(config.CALENDAR_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT)):
        status = "WATCH SIZE"
        warning = "Debit is elevated for account size; consider smaller risk or shorter back-expiration alternatives."

    return {
        "account_value_estimate": account_value,
        "account_value_source": "override" if override else str((account_context or {}).get("account_value_source") or "unknown"),
        "debit_total_estimate": debit,
        "debit_pct_of_account": None if pct_of_account is None else round(pct_of_account, 2),
        "max_loss_assumption": "debit" if bool(config.CALENDAR_ASSUME_MAX_LOSS_IS_DEBIT) else "unknown",
        "account_risk_status": status,
        "account_risk_warning": warning,
    }


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
