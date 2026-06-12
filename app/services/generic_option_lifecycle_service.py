"""Broker-leg-only option structure classification and common lifecycle envelope."""

from __future__ import annotations

from typing import Any


def classify_broker_option_structure(legs: list[dict[str, Any]]) -> str:
    clean = [leg for leg in legs or [] if isinstance(leg, dict)]
    if len(clean) != 2:
        return "unknown_multi_leg" if clean else "unknown"
    expirations = {str(leg.get("expiration") or leg.get("expiration_date") or "") for leg in clean}
    strikes = {leg.get("strike") for leg in clean}
    types = {str(leg.get("option_type") or "").lower() for leg in clean}
    if len(types) == 1 and len(strikes) == 1 and len(expirations) == 2:
        return "calendar"
    if len(types) == 1 and len(strikes) == 2 and len(expirations) == 1:
        return "call_debit_vertical" if types == {"call"} else "put_debit_vertical" if types == {"put"} else "vertical"
    return "unknown_multi_leg"


def build_lifecycle_envelope(
    ticker: str,
    legs: list[dict[str, Any]],
    *,
    current_value: float | None = None,
    estimated_pl: float | None = None,
    dte: int | None = None,
    moneyness: float | None = None,
    assignment_risk: str = "Unknown",
    expiration_risk: str = "Unknown",
    next_action: str = "Review",
    urgency: str = "MONITOR",
) -> dict[str, Any]:
    return {
        "strategy_id": None,
        "structure_type": classify_broker_option_structure(legs),
        "ticker": ticker,
        "legs": legs,
        "entry_basis_status": "broker_detected",
        "current_value": current_value,
        "estimated_pl": estimated_pl,
        "dte": dte,
        "moneyness": moneyness,
        "assignment_risk": assignment_risk,
        "expiration_risk": expiration_risk,
        "next_action": next_action,
        "urgency": urgency,
    }
