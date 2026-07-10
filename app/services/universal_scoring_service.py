"""Universal Strategy Scoring Service — ASA Patch 31B.

Assigns a common 0–100 score to every strategy row regardless of strategy type.
All four built-in strategies are supported via dedicated adapters.

Score ceilings enforce verdict/score consistency:
    FAIL / hard-gate failure → max UNIVERSAL_SCORE_HARD_FAIL_CEILING (39)
    Non-actionable diagnostic  → max UNIVERSAL_SCORE_DIAGNOSTIC_CEILING (29)
    WATCH                      → max UNIVERSAL_SCORE_WATCH_MAX (74)
    PASS                       → max 100

Score confidence bands:
    HIGH             → >= 75% components present
    MEDIUM           → >= 50%
    LOW              → >= 25%
    INSUFFICIENT_DATA → < 25%
"""
from __future__ import annotations

from typing import Any

from app import config


_SCORE_VERSION = getattr(config, "UNIVERSAL_SCORE_VERSION", "31B.score.v1")
_HARD_FAIL_CEIL = int(getattr(config, "UNIVERSAL_SCORE_HARD_FAIL_CEILING", 39))
_DIAG_CEIL = int(getattr(config, "UNIVERSAL_SCORE_DIAGNOSTIC_CEILING", 29))
_WATCH_MAX = int(getattr(config, "UNIVERSAL_SCORE_WATCH_MAX", 74))
_PASS_MIN = int(getattr(config, "UNIVERSAL_SCORE_PASS_MIN", 75))

_WEIGHTS: dict[str, float] = {
    "return_score": float(getattr(config, "UNIVERSAL_WEIGHT_RETURN", 0.20)),
    "risk_score": float(getattr(config, "UNIVERSAL_WEIGHT_RISK", 0.15)),
    "confidence_score": float(getattr(config, "UNIVERSAL_WEIGHT_CONFIDENCE", 0.15)),
    "liquidity_score": float(getattr(config, "UNIVERSAL_WEIGHT_LIQUIDITY", 0.15)),
    "data_quality_score": float(getattr(config, "UNIVERSAL_WEIGHT_DATA_QUALITY", 0.10)),
    "capital_efficiency_score": float(getattr(config, "UNIVERSAL_WEIGHT_CAPITAL_EFFICIENCY", 0.10)),
    "timing_score": float(getattr(config, "UNIVERSAL_WEIGHT_TIMING", 0.05)),
    "historical_evidence_score": float(getattr(config, "UNIVERSAL_WEIGHT_HISTORICAL", 0.05)),
    "portfolio_fit_score": float(getattr(config, "UNIVERSAL_WEIGHT_PORTFOLIO_FIT", 0.05)),
}

_COMPONENT_KEYS = list(_WEIGHTS.keys())


def compute_universal_score(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Compute and return all universal score fields for the given row."""
    verdict_upper = str(row.get("verdict") or "").upper()
    is_hard_fail = _is_hard_gate_fail(row, verdict_upper)
    is_diagnostic = _is_pure_diagnostic(row, strategy_id, verdict_upper)

    components = _dispatch_adapter(row, strategy_id, verdict_upper)
    present = [k for k in _COMPONENT_KEYS if components.get(k) is not None]
    missing = [k for k in _COMPONENT_KEYS if components.get(k) is None]

    completeness = round(len(present) / len(_COMPONENT_KEYS) * 100, 1) if _COMPONENT_KEYS else 0.0
    if completeness >= 75:
        band = "HIGH"
    elif completeness >= 50:
        band = "MEDIUM"
    elif completeness >= 25:
        band = "LOW"
    else:
        band = "INSUFFICIENT_DATA"

    raw_weighted = _weighted_average(components)
    universal_score = _apply_ceiling(raw_weighted, row, strategy_id, verdict_upper, is_hard_fail, is_diagnostic)
    hard_gate_pass = not is_hard_fail

    strengths, risks, why_not_higher = _explanation(components, row, strategy_id, verdict_upper, universal_score, is_hard_fail)
    why_actionable = _why_actionable(row, strategy_id, verdict_upper, is_hard_fail, hard_gate_pass)

    # actionability_score: 31B.9 contract field — use existing if already set by strategy service
    _existing_act = row.get("actionability_score")
    if _existing_act is not None:
        actionability_score = int(_existing_act)
    elif not hard_gate_pass:
        actionability_score = 0
    elif "PASS" in verdict_upper and "FAIL" not in verdict_upper:
        actionability_score = 75
    elif verdict_upper.startswith("WATCH"):
        actionability_score = 50
    else:
        actionability_score = 20

    return {
        "universal_score": universal_score,
        "return_score": components.get("return_score"),
        "risk_score": components.get("risk_score"),
        "confidence_score": components.get("confidence_score"),
        "liquidity_score": components.get("liquidity_score"),
        "data_quality_score": components.get("data_quality_score"),
        "capital_efficiency_score": components.get("capital_efficiency_score"),
        "timing_score": components.get("timing_score"),
        "historical_evidence_score": components.get("historical_evidence_score"),
        "portfolio_fit_score": components.get("portfolio_fit_score"),
        "actionability_score": actionability_score,
        "hard_gate_pass": hard_gate_pass,
        "score_version": _SCORE_VERSION,
        "score_completeness_pct": completeness,
        "missing_score_components": missing,
        "score_confidence_band": band,
        "ranking_reason": _ranking_reason(components, universal_score, strategy_id, verdict_upper),
        "top_strengths": strengths,
        "top_risks": risks,
        "score_breakdown": {k: round(float(components[k]), 1) for k in _COMPONENT_KEYS if components.get(k) is not None},
        "why_not_higher": why_not_higher,
        "why_actionable_or_not": why_actionable,
    }


# ── adapters ──────────────────────────────────────────────────────────────────

def _dispatch_adapter(row: dict, strategy_id: str, verdict_upper: str) -> dict[str, float | None]:
    if strategy_id == "earnings_calendar":
        return _score_earnings_calendar(row, verdict_upper)
    if strategy_id == "skew_momentum_vertical":
        return _score_skew_momentum_vertical(row, verdict_upper)
    if strategy_id == "forward_factor_calendar":
        return _score_forward_factor_calendar(row, verdict_upper)
    if strategy_id == "stock_momentum":
        return _score_stock_momentum(row, verdict_upper)
    return {k: None for k in _COMPONENT_KEYS}


def _score_earnings_calendar(row: dict, verdict_upper: str) -> dict[str, float | None]:
    """31B.11: Earnings calendar adapter."""
    is_pass = verdict_upper.startswith("PASS")
    is_watch = verdict_upper.startswith("WATCH") or verdict_upper.startswith("HOLD")
    is_fail = verdict_upper.startswith("FAIL") or verdict_upper.startswith("AVOID")

    score = float(row.get("score") or row.get("rank_score") or 0)
    ranking = row.get("ranking") or {}

    # Return: projected edge from calendar IV crush
    debit = float(row.get("debit_at_risk") or row.get("max_debit") or 0)
    iv_relationship = bool(row.get("back_iv_over_front_iv") or (row.get("iv_ratio") and float(row.get("iv_ratio") or 0) > 1))
    return_score: float | None
    if is_pass:
        return_score = min(100.0, 55.0 + score * 0.4)
    elif is_watch:
        return_score = min(74.0, 35.0 + score * 0.35)
    elif is_fail:
        return_score = min(30.0, score * 0.3)
    else:
        return_score = None

    # Risk: timing certainty, short-leg safety
    entry_window = str(row.get("entry_window_status") or "")
    short_leg_ok = entry_window not in {"SHORT_LEG_SPANS_EARNINGS", "ENTRY_WINDOW_CLOSED"}
    risk_score: float | None
    if is_pass and short_leg_ok:
        risk_score = 70.0
    elif is_pass:
        risk_score = 45.0
    elif is_watch:
        risk_score = 50.0
    elif is_fail:
        risk_score = 20.0
    else:
        risk_score = None

    # Confidence: earnings source quality
    earnings_trust = str(row.get("earnings_trust_label") or row.get("earnings_confidence") or "")
    if "confirmed" in earnings_trust.lower() or "high" in earnings_trust.lower():
        confidence_score: float | None = 85.0
    elif "unconfirmed" in earnings_trust.lower() or "medium" in earnings_trust.lower():
        confidence_score = 60.0
    elif is_pass or is_watch:
        confidence_score = 50.0
    elif is_fail:
        confidence_score = 20.0
    else:
        confidence_score = None

    # Liquidity
    liquidity_score: float | None = None
    if row.get("liquidity_pass") is True:
        liquidity_score = 80.0
    elif row.get("liquidity_pass") is False:
        liquidity_score = 15.0
    elif is_pass or is_watch:
        liquidity_score = 50.0

    # Data quality
    dq = str(row.get("data_quality_status") or row.get("confidence") or "")
    if "high" in dq.lower() or "good" in dq.lower():
        data_quality_score: float | None = 90.0
    elif "medium" in dq.lower() or "ok" in dq.lower():
        data_quality_score = 65.0
    elif dq:
        data_quality_score = 40.0
    elif is_pass:
        data_quality_score = 60.0
    else:
        data_quality_score = None

    # Capital efficiency: low debit relative to opportunity
    if debit > 0:
        cap_eff = max(10.0, min(85.0, 85.0 - (debit - 100) * 0.08)) if is_pass or is_watch else 20.0
        capital_efficiency_score: float | None = cap_eff
    else:
        capital_efficiency_score = None

    # Timing: entry window state
    if entry_window in {"ENTRY_WINDOW_OPEN", "ENTRY_WINDOW_VALID"}:
        timing_score: float | None = 90.0
    elif entry_window == "MONITOR_PRE_WINDOW":
        timing_score = 50.0
    elif entry_window in {"ENTRY_WINDOW_CLOSED", "SHORT_DTE_TOO_LOW"}:
        timing_score = 5.0
    elif is_pass:
        timing_score = 70.0
    else:
        timing_score = None

    mini_bt = float(ranking.get("mini_backtest_score") or 0) if isinstance(ranking, dict) else 0.0
    historical_evidence_score: float | None = min(100.0, 40.0 + mini_bt * 0.5) if (is_pass or is_watch) else None

    portfolio_fit_score: float | None = 60.0 if (is_pass or is_watch) else None

    return {
        "return_score": return_score,
        "risk_score": risk_score,
        "confidence_score": confidence_score,
        "liquidity_score": liquidity_score,
        "data_quality_score": data_quality_score,
        "capital_efficiency_score": capital_efficiency_score,
        "timing_score": timing_score,
        "historical_evidence_score": historical_evidence_score,
        "portfolio_fit_score": portfolio_fit_score,
    }


def _score_skew_momentum_vertical(row: dict, verdict_upper: str) -> dict[str, float | None]:
    """31B.11: Skew momentum vertical adapter."""
    is_pass = verdict_upper.startswith("PASS")
    is_watch = verdict_upper.startswith("WATCH")
    is_fail = verdict_upper.startswith("FAIL")

    score = float(row.get("score") or row.get("signal_score") or 0)
    ranking = row.get("ranking") or {}
    rr = float(ranking.get("reward_risk_ratio") or row.get("reward_risk_ratio") or 0) if isinstance(ranking, dict) else 0.0

    # Return: reward/risk + skew richness + momentum
    if is_pass:
        return_score: float | None = min(100.0, 45.0 + rr * 5 + score * 0.3)
    elif is_watch:
        return_score = min(70.0, 30.0 + rr * 4 + score * 0.25)
    elif is_fail:
        return_score = min(30.0, score * 0.25)
    else:
        return_score = None

    # Risk: max loss, spread width
    max_loss = float(row.get("max_loss_pct") or row.get("max_loss") or 0)
    if is_pass:
        risk_score: float | None = max(30.0, 80.0 - max_loss * 0.5)
    elif is_watch:
        risk_score = max(25.0, 60.0 - max_loss * 0.5)
    elif is_fail:
        risk_score = 15.0
    else:
        risk_score = None

    # Confidence: skew z-score quality
    skew_z = float(row.get("skew_zscore") or row.get("iv_skew_zscore") or 0)
    if abs(skew_z) >= 2.0:
        confidence_score: float | None = 85.0
    elif abs(skew_z) >= 1.0:
        confidence_score = 65.0
    elif is_pass or is_watch:
        confidence_score = 50.0
    else:
        confidence_score = None

    # Liquidity: both-leg liquidity
    liq = str(row.get("liquidity_status") or "")
    if liq.upper() == "PASS" or row.get("liquidity_pass") is True:
        liquidity_score: float | None = 80.0
    elif liq.upper() == "WATCH":
        liquidity_score = 50.0
    elif liq.upper() == "FAIL" or row.get("liquidity_pass") is False:
        liquidity_score = 15.0
    else:
        liquidity_score = None

    data_quality_score: float | None = 70.0 if (is_pass or is_watch) else None

    # Capital efficiency: defined risk spread
    debit = float(row.get("debit") or row.get("net_debit") or 0)
    if debit > 0 and (is_pass or is_watch):
        capital_efficiency_score: float | None = min(85.0, 70.0 - debit * 0.02)
    else:
        capital_efficiency_score = None

    # Timing: DTE + momentum freshness
    dte = int(row.get("dte") or row.get("front_dte") or 0)
    if 20 <= dte <= 50:
        timing_score: float | None = 80.0
    elif dte > 0:
        timing_score = 50.0
    else:
        timing_score = None

    historical_evidence_score: float | None = 55.0 if (is_pass or is_watch) else None
    portfolio_fit_score: float | None = 65.0 if (is_pass or is_watch) else None

    return {
        "return_score": return_score,
        "risk_score": risk_score,
        "confidence_score": confidence_score,
        "liquidity_score": liquidity_score,
        "data_quality_score": data_quality_score,
        "capital_efficiency_score": capital_efficiency_score,
        "timing_score": timing_score,
        "historical_evidence_score": historical_evidence_score,
        "portfolio_fit_score": portfolio_fit_score,
    }


def _score_forward_factor_calendar(row: dict, verdict_upper: str) -> dict[str, float | None]:
    """31B.11: Forward Factor Calendar adapter."""
    is_pass = "PASS" in verdict_upper and "FAIL" not in verdict_upper
    is_watch = verdict_upper.startswith("WATCH") or "DRY RUN PASS" in verdict_upper or bool(row.get("watch_zone_ff"))
    is_fail = not (is_pass or is_watch)

    ff = float(row.get("forward_factor") or row.get("diagnostic_raw_iv_forward_factor") or 0)
    threshold = float(row.get("threshold") or getattr(config, "FF_MIN_FORWARD_FACTOR", 0.20))
    ranking = row.get("ranking") or {}
    total_score = float(ranking.get("total_score") or 0) if isinstance(ranking, dict) else 0.0

    # Return: forward factor magnitude and structural edge
    if is_pass:
        ff_edge = max(0.0, ff - threshold) / max(threshold, 0.001)
        return_score: float | None = min(95.0, 55.0 + ff_edge * 80)
    elif is_watch:
        return_score = min(65.0, 35.0 + ff * 100)
    elif is_fail:
        return_score = min(20.0, ff * 40)
    else:
        return_score = None

    # Risk: invalid variance protection, earnings cleanliness
    fv = float(row.get("forward_variance") or 0)
    contaminated = bool(row.get("earnings_contaminated"))
    valid_fv = fv > 0
    if is_pass and not contaminated and valid_fv:
        risk_score: float | None = 75.0
    elif is_pass and contaminated:
        risk_score = 50.0
    elif is_watch and valid_fv:
        risk_score = 55.0
    elif is_fail:
        risk_score = 15.0 if not valid_fv else 25.0
    else:
        risk_score = None

    # Confidence: source-qualified IV
    source_qual = str(row.get("source_qualification") or "")
    front_ex = row.get("front_ex_earnings_iv")
    back_ex = row.get("back_ex_earnings_iv")
    if source_qual == "clean" and front_ex is not None and back_ex is not None:
        confidence_score: float | None = 85.0
    elif front_ex is not None or back_ex is not None:
        confidence_score = 55.0
    elif is_watch:
        confidence_score = 35.0
    else:
        confidence_score = 20.0

    # Liquidity: multi-leg calendar
    liq = str(row.get("liquidity_status") or "")
    liq_pass = bool(row.get("liquidity_pass"))
    if liq.upper() == "PASS" or liq_pass:
        liquidity_score: float | None = 80.0
    elif liq.upper() == "WATCH":
        liquidity_score = 50.0
    elif liq.upper() == "FAIL":
        liquidity_score = 15.0
    elif row.get("structure_status") == "COMPLETE":
        liquidity_score = 40.0
    else:
        liquidity_score = None

    # Data quality: source spec version + IV completeness
    spec_v = int(row.get("source_spec_version") or 0)
    if spec_v >= 1 and front_ex is not None and back_ex is not None:
        data_quality_score: float | None = 85.0
    elif front_ex is not None or back_ex is not None:
        data_quality_score = 55.0
    else:
        data_quality_score = 30.0

    # Capital efficiency: debit vs edge
    debit = float(row.get("debit_at_risk") or 0)
    edge_pct = float(row.get("edge_on_margin") or 0)
    if debit > 0 and edge_pct > 0:
        capital_efficiency_score: float | None = min(85.0, edge_pct * 1.2)
    elif debit > 0 and (is_pass or is_watch):
        capital_efficiency_score = max(20.0, 65.0 - debit * 0.06)
    else:
        capital_efficiency_score = None

    # Timing: front/back DTE quality
    front_dte = int(row.get("front_dte") or 0)
    back_dte = int(row.get("back_dte") or 0)
    front_target_min = int(getattr(config, "FF_FRONT_TARGET_DTE_MIN", 45))
    front_target_max = int(getattr(config, "FF_FRONT_TARGET_DTE_MAX", 80))
    if front_target_min <= front_dte <= front_target_max and back_dte > front_dte:
        timing_score: float | None = 80.0
    elif front_dte > 0 and back_dte > front_dte:
        timing_score = 50.0
    else:
        timing_score = None

    iv_pct = float(row.get("iv_percentile") or 0)
    historical_evidence_score: float | None = min(80.0, iv_pct) if iv_pct > 0 else None

    portfolio_fit_score: float | None = 55.0 if (is_pass or is_watch) else None

    return {
        "return_score": return_score,
        "risk_score": risk_score,
        "confidence_score": confidence_score,
        "liquidity_score": liquidity_score,
        "data_quality_score": data_quality_score,
        "capital_efficiency_score": capital_efficiency_score,
        "timing_score": timing_score,
        "historical_evidence_score": historical_evidence_score,
        "portfolio_fit_score": portfolio_fit_score,
    }


def _score_stock_momentum(row: dict, verdict_upper: str) -> dict[str, float | None]:
    """31B.11: Stock momentum adapter."""
    is_add = "CONSIDER ADDING" in verdict_upper or "ADD ON" in verdict_upper
    is_watch = "WATCH" in verdict_upper or "HOLD" in verdict_upper
    is_tactical = "TACTICAL" in verdict_upper or "STARTER" in verdict_upper
    is_fail = not (is_add or is_watch or is_tactical)

    score = float(row.get("score") or row.get("signal_score") or row.get("momentum_score") or 0)
    ranking = row.get("ranking") or {}
    momentum = float(ranking.get("momentum_score") or row.get("momentum_strength") or 0) if isinstance(ranking, dict) else 0.0

    # Return: momentum strength + relative strength
    if is_add:
        return_score: float | None = min(100.0, 50.0 + score * 0.45)
    elif is_watch:
        return_score = min(70.0, 35.0 + score * 0.35)
    elif is_tactical:
        return_score = min(55.0, 25.0 + score * 0.3)
    else:
        return_score = None

    # Risk: extension + volatility
    extended = bool(row.get("is_extended") or row.get("price_extended"))
    if is_add and not extended:
        risk_score: float | None = 70.0
    elif is_add:
        risk_score = 45.0
    elif is_watch:
        risk_score = 55.0
    elif is_tactical:
        risk_score = 40.0
    else:
        risk_score = None

    # Confidence: multi-timeframe + data completeness
    if score >= 70:
        confidence_score: float | None = 80.0
    elif score >= 50:
        confidence_score = 60.0
    elif is_add or is_watch:
        confidence_score = 45.0
    else:
        confidence_score = None

    # Liquidity: average volume proxy
    avg_vol = float(row.get("average_volume") or row.get("avg_volume_30d") or 0)
    if avg_vol >= 5_000_000:
        liquidity_score: float | None = 90.0
    elif avg_vol >= 1_000_000:
        liquidity_score = 70.0
    elif avg_vol > 0:
        liquidity_score = 45.0
    else:
        liquidity_score = None

    data_quality_score: float | None = 70.0 if (is_add or is_watch) else None

    # Capital efficiency: stocks have no built-in cap
    capital_efficiency_score: float | None = 65.0 if (is_add or is_watch) else None

    # Timing: pullback state
    pullback = bool(row.get("on_pullback") or row.get("pullback_opportunity"))
    breakout = bool(row.get("breakout_confirmed") or row.get("trend_confirmed"))
    if is_add and (pullback or breakout):
        timing_score: float | None = 80.0
    elif is_add:
        timing_score = 55.0
    elif is_watch:
        timing_score = 40.0
    else:
        timing_score = None

    historical_evidence_score: float | None = min(80.0, score * 0.8) if score > 0 else None
    portfolio_fit_score: float | None = 60.0 if (is_add or is_watch) else None

    return {
        "return_score": return_score,
        "risk_score": risk_score,
        "confidence_score": confidence_score,
        "liquidity_score": liquidity_score,
        "data_quality_score": data_quality_score,
        "capital_efficiency_score": capital_efficiency_score,
        "timing_score": timing_score,
        "historical_evidence_score": historical_evidence_score,
        "portfolio_fit_score": portfolio_fit_score,
    }


# ── hard gate detection ───────────────────────────────────────────────────────

def _is_hard_gate_fail(row: dict, verdict_upper: str) -> bool:
    if verdict_upper.startswith("FAIL"):
        return True
    if row.get("hard_gate_pass") is False:
        return True
    row_type = str(row.get("row_type") or "")
    if row_type == "rejected_candidate":
        return True
    decision_class = str(row.get("decision_class") or "")
    if decision_class == "rejected":
        return True
    return False


def _is_pure_diagnostic(row: dict, strategy_id: str, verdict_upper: str) -> bool:
    if strategy_id == "forward_factor_calendar":
        is_pass = "PASS" in verdict_upper and "FAIL" not in verdict_upper
        is_watch = verdict_upper.startswith("WATCH") or "DRY RUN PASS" in verdict_upper or bool(row.get("watch_zone_ff"))
        return not (is_pass or is_watch)
    decision_class = str(row.get("decision_class") or "")
    return decision_class in {"diagnostic", "dry_run_excluded"}


# ── weighting + ceiling ───────────────────────────────────────────────────────

def _weighted_average(components: dict[str, float | None]) -> float:
    total_weight = 0.0
    total_score = 0.0
    for key, weight in _WEIGHTS.items():
        val = components.get(key)
        if val is None:
            continue
        total_weight += weight
        total_score += float(val) * weight
    if total_weight < 0.01:
        return 0.0
    raw = total_score / total_weight
    # Scale proportionally — missing components don't grant free points
    completeness_factor = min(1.0, total_weight / 1.0)
    return min(100.0, raw * (0.6 + 0.4 * completeness_factor))


def _apply_ceiling(
    raw_score: float,
    row: dict,
    strategy_id: str,
    verdict_upper: str,
    is_hard_fail: bool,
    is_diagnostic: bool,
) -> int:
    score = round(raw_score)
    if is_hard_fail:
        return min(score, _HARD_FAIL_CEIL)
    if is_diagnostic:
        return min(score, _DIAG_CEIL)
    is_watch = (
        verdict_upper.startswith("WATCH")
        or "DRY RUN PASS" in verdict_upper
        or bool(row.get("watch_zone_ff"))
    )
    if is_watch:
        return min(score, _WATCH_MAX)
    return min(score, 100)


# ── explanation ───────────────────────────────────────────────────────────────

def _explanation(
    components: dict,
    row: dict,
    strategy_id: str,
    verdict_upper: str,
    universal_score: int,
    is_hard_fail: bool,
) -> tuple[list[str], list[str], str]:
    scored = {k: float(v) for k, v in components.items() if v is not None}
    strengths, risks = [], []
    for k, v in sorted(scored.items(), key=lambda x: -x[1]):
        label = _friendly_component(k)
        if v >= 70:
            strengths.append(label)
        elif v <= 30:
            risks.append(label)
    strengths = strengths[:3]
    risks = risks[:3]

    why_not_higher = ""
    if is_hard_fail:
        why_not_higher = "Hard gate failure — score is capped at the FAIL ceiling."
    elif universal_score < 40:
        why_not_higher = "Multiple component deficiencies pull the score below the watch threshold."
    elif universal_score < 55:
        weak = [_friendly_component(k) for k, v in scored.items() if v < 50]
        why_not_higher = f"Score limited by: {', '.join(weak[:3]) or 'weak components'}."
    elif universal_score < 75:
        weak = [_friendly_component(k) for k, v in scored.items() if v < 60]
        why_not_higher = f"WATCH ceiling applied; would need: {', '.join(weak[:2]) or 'stronger components'} to reach PASS."
    return strengths, risks, why_not_higher


def _why_actionable(
    row: dict,
    strategy_id: str,
    verdict_upper: str,
    is_hard_fail: bool,
    hard_gate_pass: bool,
) -> str:
    dry_run = bool(row.get("dry_run") or strategy_id == "forward_factor_calendar")
    if is_hard_fail:
        return "Not actionable — hard gate failure prevents any trade entry."
    if not hard_gate_pass:
        return "Not actionable — required gates have not been cleared."
    is_pass = "PASS" in verdict_upper and "FAIL" not in verdict_upper
    is_watch = verdict_upper.startswith("WATCH") or "DRY RUN PASS" in verdict_upper
    if dry_run and (is_pass or is_watch):
        return "Research signal only — strategy is in dry-run mode. No execution permitted."
    if is_pass:
        return "Actionable — passed all hard gates. Manual review required before entry."
    if is_watch:
        return "Watch signal — passes soft gates but not all hard criteria. Monitor for improvement."
    return "Not actionable — verdict does not qualify for entry."


def _ranking_reason(
    components: dict,
    universal_score: int,
    strategy_id: str,
    verdict_upper: str,
) -> str:
    scored = {k: float(v) for k, v in components.items() if v is not None}
    strategy_label = {
        "earnings_calendar": "Earnings Calendar",
        "skew_momentum_vertical": "Skew Vertical",
        "forward_factor_calendar": "Forward Factor",
        "stock_momentum": "Stock Momentum",
    }.get(strategy_id, strategy_id)

    top = sorted(scored.items(), key=lambda x: -x[1])[:2]
    bottom = sorted(scored.items(), key=lambda x: x[1])[:1]
    top_str = " and ".join(_friendly_component(k) for k, _ in top)
    bottom_str = _friendly_component(bottom[0][0]) if bottom else "missing components"
    ceiling = "FAIL ceiling" if verdict_upper.startswith("FAIL") else "WATCH ceiling" if verdict_upper.startswith("WATCH") else ""
    suffix = f" Score capped by {ceiling}." if ceiling else ""
    return f"{strategy_label} score {universal_score}. Strengths: {top_str}. Limited by: {bottom_str}.{suffix}"


_COMPONENT_LABELS: dict[str, str] = {
    "return_score": "return potential",
    "risk_score": "risk quality",
    "confidence_score": "signal confidence",
    "liquidity_score": "liquidity",
    "data_quality_score": "data quality",
    "capital_efficiency_score": "capital efficiency",
    "timing_score": "timing",
    "historical_evidence_score": "historical evidence",
    "portfolio_fit_score": "portfolio fit",
}


def _friendly_component(key: str) -> str:
    return _COMPONENT_LABELS.get(key, key.replace("_score", "").replace("_", " "))
