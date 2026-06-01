"""Hold-through scoring for broker-detected active calendar spreads."""

from __future__ import annotations

from typing import Any


def build_hold_through_score(check: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    blockers: list[str] = []
    score = 0.0

    pnl = _num(check.get("estimated_pnl_pct"))
    target = _num(check.get("target_profit_pct")) or 50.0
    stop = _num(check.get("max_loss_pct")) or -35.0
    if pnl is None:
        score += 8
        blockers.append("Current P/L is not reliable enough to support a hold-through decision.")
    elif pnl >= target:
        score += 8
        blockers.append("Current P/L is already near/above target; positive P/L does not by itself support holding through earnings.")
    elif pnl <= stop:
        score += 2
        blockers.append("Current P/L is near/below stop guardrail.")
    elif pnl > 0:
        score += 15
        reasons.append("Current P/L is positive but still below the configured target.")
    else:
        score += 11
        reasons.append("Current P/L is not yet near the stop guardrail.")

    hist = check.get("historical_move_summary") if isinstance(check.get("historical_move_summary"), dict) else {}
    implied = _num(check.get("implied_move_pct") or check.get("estimated_breakeven_pct") or check.get("breakeven_pct"))
    avg_move = _num(hist.get("avg_abs_event_move_pct"))
    max_move = _num(hist.get("max_abs_event_move_pct"))
    small_rate = _num(hist.get("small_move_rate_pct"))
    historical_warning = ""
    if avg_move is None:
        score += 20
        reasons.append("Historical earnings move data is not attached; use active review instead of automatic hold/close.")
    else:
        reference = implied if implied is not None and implied > 0 else 8.0
        if avg_move > reference or (max_move is not None and max_move > reference * 1.8):
            score += 15
            historical_warning = "Historical realized earnings moves are large relative to the estimated neutral-calendar range."
            blockers.append(historical_warning)
        elif small_rate is not None and small_rate >= 60:
            score += 45
            reasons.append("Historical earnings moves look contained relative to the neutral-calendar thesis.")
        else:
            score += 32
            reasons.append("Historical earnings movement is mixed but not an automatic blocker.")

    net_iv = _num(check.get("net_iv_estimate"))
    if net_iv is None:
        score += 7
        reasons.append("IV edge remaining is unavailable.")
    elif net_iv >= 0:
        score += 13
        reasons.append("Remaining IV profile does not show an obvious negative edge.")
    else:
        score += 5
        blockers.append("Remaining IV profile is weak or inverted.")

    pricing = check.get("pricing_quality") if isinstance(check.get("pricing_quality"), dict) else {}
    confidence = str(pricing.get("confidence") or "").lower()
    if confidence == "high":
        score += 10
        reasons.append("Liquidity/pricing confidence is high enough for cleaner exit review.")
    elif confidence == "medium":
        score += 7
    else:
        score += 3
        blockers.append("Liquidity/pricing quality is low or uncertain.")

    assignment = str(check.get("assignment_risk_level") or "Unknown")
    if assignment in {"High", "Elevated"}:
        score += 1
        blockers.append(f"Assignment risk is {assignment}.")
    elif assignment == "Moderate":
        score += 3
        blockers.append("Assignment risk is moderate.")
    else:
        score += 5
        reasons.append("Assignment risk is not the primary blocker.")

    score = max(0.0, min(100.0, score))
    if score >= 80:
        action = "HOLD-THROUGH SUPPORTED"
    elif score >= 60:
        action = "HOLD, BUT REDUCE RISK / STRICT EXIT"
    elif score >= 40:
        action = "CONSIDER CLOSING BEFORE EARNINGS"
    else:
        action = "CLOSE / AVOID HOLD-THROUGH"

    return {
        "hold_through_score": round(score, 1),
        "hold_through_action": action,
        "hold_through_reasons": _dedupe(reasons),
        "hold_through_blockers": _dedupe(blockers),
        "historical_move_warning": historical_warning,
    }


def _num(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out
