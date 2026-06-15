"""Shared risk-severity classification for report and Daily Opportunity rows."""

from __future__ import annotations

from typing import Any

from app import config


ACTIONABLE_RISK_SEVERITIES = {"URGENT_RISK", "MATERIAL_REVIEW"}
INCOMPLETE_PHRASES = (
    "metrics were not evaluated", "data incomplete", "data unavailable",
    "skipped by dev data cap", "provider budget", "missing market",
)


def classify_risk_severity(row: dict[str, Any] | None) -> str:
    item = row or {}
    value = _number(item.get("market_value") if item.get("market_value") is not None else item.get("position_value"))
    allocation = _number(item.get("allocation_pct"))
    action = str(item.get("action") or item.get("category") or item.get("status") or item.get("verdict") or "").upper()
    messages = _messages(item)
    text = " ".join([action, *messages]).upper()

    if _is_tiny(value, allocation):
        return "CLEANUP"
    if _data_incomplete_only(action, messages):
        return "DATA_INCOMPLETE"
    if action in {"OK", "NEAR TARGET", "NO ACTION", "HOLD"}:
        return "NO_ACTION_HOLD"
    if "URGENT" in text or "CUT" in text or "EXIT" in text or "ABOVE RISK TARGET" in text:
        return "URGENT_RISK"
    if any(token in text for token in ("AVOID", "REDUCE", "TRIM", "DO NOT ADD", "RISK REVIEW", "NEAR / SLIGHTLY ABOVE TARGET")):
        return "MATERIAL_REVIEW"
    if messages and not all(_is_incomplete(message) for message in messages):
        return "MATERIAL_REVIEW"
    return "NO_ACTION_HOLD"


def with_risk_severity(row: dict[str, Any]) -> dict[str, Any]:
    output = dict(row)
    output["risk_severity"] = classify_risk_severity(output)
    return output


def is_actionable_risk(row: dict[str, Any] | None) -> bool:
    return classify_risk_severity(row) in ACTIONABLE_RISK_SEVERITIES


def _is_tiny(value: float | None, allocation: float | None) -> bool:
    value_limit = float(getattr(config, "TINY_POSITION_VALUE_THRESHOLD", 50) or 50)
    allocation_limit = float(getattr(config, "TINY_POSITION_PORTFOLIO_PCT_THRESHOLD", 0.5) or 0.5)
    return bool(
        (value is not None and 0 < abs(value) < value_limit)
        or (allocation is not None and 0 < abs(allocation) < allocation_limit)
    )


def _data_incomplete_only(action: str, messages: list[str]) -> bool:
    explicit_risk = any(token in action for token in ("AVOID", "REDUCE", "CUT", "TRIM", "URGENT", "EXIT"))
    return bool(messages) and not explicit_risk and all(_is_incomplete(message) for message in messages)


def _is_incomplete(message: str) -> bool:
    lower = message.lower()
    return any(phrase in lower for phrase in INCOMPLETE_PHRASES)


def _messages(row: dict[str, Any]) -> list[str]:
    output = []
    for key in ("risks", "warnings", "errors"):
        value = row.get(key)
        if isinstance(value, list):
            output.extend(str(item) for item in value if item)
    for key in ("why", "reason", "detail", "guidance", "main_blocker"):
        if row.get(key):
            output.append(str(row[key]))
    return output


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None
