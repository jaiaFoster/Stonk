"""Broker-leg-only option structure classification and common lifecycle envelope."""

from __future__ import annotations

from typing import Any
from hashlib import sha256


def classify_broker_option_structure(legs: list[dict[str, Any]]) -> str:
    clean = [leg for leg in legs or [] if isinstance(leg, dict)]
    if len(clean) == 1:
        return "unpaired_option_leg"
    if len(clean) != 2:
        return "unknown_multileg"
    expirations = {str(leg.get("expiration") or leg.get("expiration_date") or "") for leg in clean}
    strikes = {leg.get("strike") for leg in clean}
    types = {str(leg.get("option_type") or "").lower() for leg in clean}
    if len(types) == 1 and len(strikes) == 1 and len(expirations) == 2:
        return "calendar"
    if len(types) == 1 and len(strikes) == 2 and len(expirations) == 1:
        return "call_debit_vertical" if types == {"call"} else "put_debit_vertical" if types == {"put"} else "vertical"
    return "unknown_multileg"


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
        "structure_id": _structure_id(ticker, legs),
        "strategy_id": None,
        "structure_type": classify_broker_option_structure(legs),
        "ticker": ticker,
        "expiration": _shared_expiration(legs),
        "legs": legs,
        "entry_basis_status": "broker_detected",
        "current_value": current_value,
        "estimated_pl": estimated_pl,
        "dte": dte,
        "moneyness": moneyness,
        "assignment_risk": assignment_risk,
        "expiration_risk": expiration_risk,
        "confidence": "high" if classify_broker_option_structure(legs) not in {"unknown_multileg", "unpaired_option_leg"} else "low",
        "next_action": next_action,
        "urgency": urgency,
    }


def _shared_expiration(legs: list[dict[str, Any]]) -> str | None:
    values = {str(leg.get("expiration") or leg.get("expiration_date") or "") for leg in legs or []}
    values.discard("")
    return next(iter(values)) if len(values) == 1 else None


def _structure_id(ticker: str, legs: list[dict[str, Any]]) -> str:
    parts = sorted(
        f"{leg.get('option_type')}:{leg.get('strike')}:{leg.get('expiration') or leg.get('expiration_date')}:{leg.get('side') or leg.get('position_type')}"
        for leg in legs or []
    )
    return f"{ticker.upper()}:{sha256('|'.join(parts).encode()).hexdigest()[:12]}"
