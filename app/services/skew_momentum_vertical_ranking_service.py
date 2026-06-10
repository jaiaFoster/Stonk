"""Transparent ranking for Strategy 2 skew momentum vertical candidates."""

from __future__ import annotations

from typing import Any

from app import config


def rank_skew_momentum_vertical(candidate: dict[str, Any]) -> dict[str, Any]:
    momentum = _clamp(float(candidate.get("momentum_score") or 0) / 100 * 25, 0, 25)
    iv_edge = float(candidate.get("short_iv_edge") or 0)
    financing = float(candidate.get("short_premium_financing_pct") or 0)
    skew = _clamp(iv_edge / max(float(config.SKEW_VERTICAL_MIN_SHORT_IV_EDGE), 0.001) * 12.5, 0, 12.5)
    skew += _clamp(financing / max(float(config.SKEW_VERTICAL_MIN_SHORT_PREMIUM_FINANCING_PCT), 1) * 12.5, 0, 12.5)
    rr = float(candidate.get("reward_risk") or 0)
    debit_pct = float(candidate.get("debit_pct_of_width") or 100)
    payoff = _clamp(rr / max(float(config.SKEW_VERTICAL_PREFERRED_REWARD_RISK), 0.1) * 12, 0, 12)
    payoff += _clamp((float(config.SKEW_VERTICAL_MAX_DEBIT_PCT_OF_WIDTH) - debit_pct) / max(float(config.SKEW_VERTICAL_MAX_DEBIT_PCT_OF_WIDTH), 1) * 8, 0, 8)
    leg_width = max(float(candidate.get("long_leg_spread_pct") or 100), float(candidate.get("short_leg_spread_pct") or 100))
    liquidity = _clamp((float(config.SKEW_VERTICAL_MAX_LEG_SPREAD_PCT) - leg_width) / max(float(config.SKEW_VERTICAL_MAX_LEG_SPREAD_PCT), 1) * 10, 0, 10)
    liquidity += 5 if candidate.get("liquidity_pass") else 0
    dte = int(candidate.get("dte") or 0)
    timing = _clamp(10 - abs(dte - int(config.SKEW_VERTICAL_TARGET_DTE)) * 0.5, 0, 10)
    data_quality = 5 if candidate.get("data_quality_pass") and not candidate.get("delta_approximated") else 3 if candidate.get("data_quality_pass") else 0
    total = round(momentum + skew + payoff + liquidity + timing + data_quality, 1)
    requirements = candidate.get("requirements") or []
    return {
        "total_score": total,
        "momentum_score": round(momentum, 1),
        "skew_score": round(skew, 1),
        "payoff_score": round(payoff, 1),
        "liquidity_score": round(liquidity, 1),
        "timing_score": round(timing, 1),
        "data_quality_score": round(data_quality, 1),
        "passed_requirements": sum(1 for item in requirements if item.get("status") == "PASS"),
        "failed_requirements": sum(1 for item in requirements if item.get("status") == "FAIL"),
    }


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
