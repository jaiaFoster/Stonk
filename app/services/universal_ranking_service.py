"""Universal Strategy Ranking Service — ASA Patch 31B.13.

Ranks rows from all strategies on a single comparable scale using
universal_score + hard gate status + actionability.

Tiers:
    A  — PASS, hard_gate_pass=True, universal_score >= 75
    B  — WATCH or score 55–74
    C  — borderline score 40–54
    D  — score < 40, not hard-failed, not purely diagnostic
    REJECTED   — hard_gate_pass=False
    DIAGNOSTIC — pure dry-run diagnostic rows (non-research)

Ranking order:
    1. hard_gate_pass (True first)
    2. actionable (True first)
    3. universal_score (desc)
    4. score_confidence_band (HIGH > MEDIUM > LOW > INSUFFICIENT_DATA)
    5. liquidity_score (desc)
    6. data_quality_score (desc)
    7. ticker + strategy_id (asc — deterministic tie-breaker)
"""
from __future__ import annotations

from typing import Any

from app import config

_RANKING_VERSION = getattr(config, "UNIVERSAL_RANKING_VERSION", "31B.rank.v1")
_TIER_A_MIN = int(getattr(config, "UNIVERSAL_TIER_A_MIN_SCORE", 75))
_TIER_B_MIN = int(getattr(config, "UNIVERSAL_TIER_B_MIN_SCORE", 55))
_TIER_C_MIN = int(getattr(config, "UNIVERSAL_TIER_C_MIN_SCORE", 40))

_BAND_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INSUFFICIENT_DATA": 3}


def rank_strategy_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ranking fields to each row and return the sorted list."""
    if not rows:
        return []
    annotated = [_annotate(row) for row in rows]
    annotated.sort(key=_sort_key)
    strategy_counters: dict[str, int] = {}
    ticker_counters: dict[str, int] = {}
    for global_rank, row in enumerate(annotated, 1):
        sid = str(row.get("strategy_id") or "")
        ticker = str(row.get("ticker") or "").upper()
        strategy_counters[sid] = strategy_counters.get(sid, 0) + 1
        ticker_counters[ticker] = ticker_counters.get(ticker, 0) + 1
        row["global_rank"] = global_rank
        row["strategy_rank"] = strategy_counters[sid]
        row["ticker_rank"] = ticker_counters[ticker]
        row["ranking_version"] = _RANKING_VERSION
    return annotated


def _annotate(row: dict[str, Any]) -> dict[str, Any]:
    """Compute tier and ranking metadata for a single row."""
    row = dict(row)
    hard_gate = row.get("hard_gate_pass")
    if hard_gate is None:
        verdict_upper = str(row.get("verdict") or "").upper()
        hard_gate = not verdict_upper.startswith("FAIL")
        row["hard_gate_pass"] = hard_gate

    universal_score = int(row.get("universal_score") or 0)
    strategy_id = str(row.get("strategy_id") or "")
    verdict_upper = str(row.get("verdict") or "").upper()
    decision_class = str(row.get("decision_class") or "")

    is_diagnostic = decision_class in {"diagnostic"} and strategy_id == "forward_factor_calendar" and not (
        "PASS" in verdict_upper or verdict_upper.startswith("WATCH")
    )
    is_actionable = _is_actionable(row, strategy_id, verdict_upper, hard_gate)

    tier = _assign_tier(universal_score, hard_gate, is_actionable, is_diagnostic, verdict_upper)
    row["opportunity_tier"] = tier
    row["strategy_actionable"] = row.get("strategy_actionable") if row.get("strategy_actionable") is not None else is_actionable
    if not row.get("ranking_reason"):
        row["ranking_reason"] = _build_ranking_reason(row, tier, universal_score, is_actionable)
    return row


def _assign_tier(
    score: int,
    hard_gate: bool,
    is_actionable: bool,
    is_diagnostic: bool,
    verdict_upper: str,
) -> str:
    if not hard_gate:
        return "REJECTED"
    if is_diagnostic:
        return "DIAGNOSTIC"
    if is_actionable and score >= _TIER_A_MIN:
        return "A"
    if score >= _TIER_B_MIN or verdict_upper.startswith("WATCH"):
        return "B"
    if score >= _TIER_C_MIN:
        return "C"
    if score > 0:
        return "D"
    return "DIAGNOSTIC"


def _is_actionable(row: dict, strategy_id: str, verdict_upper: str, hard_gate: bool) -> bool:
    if not hard_gate:
        return False
    if row.get("strategy_actionable") is not None:
        return bool(row["strategy_actionable"])
    is_pass = "PASS" in verdict_upper and "FAIL" not in verdict_upper
    is_watch = verdict_upper.startswith("WATCH")
    return is_pass or is_watch


def _sort_key(row: dict) -> tuple:
    hard_gate = int(not bool(row.get("hard_gate_pass", True)))
    is_actionable = int(not bool(row.get("strategy_actionable")))
    score = -(int(row.get("universal_score") or 0))
    band = _BAND_ORDER.get(str(row.get("score_confidence_band") or ""), 3)
    liq = -(float(row.get("liquidity_score") or 0))
    dq = -(float(row.get("data_quality_score") or 0))
    tie = (str(row.get("ticker") or ""), str(row.get("strategy_id") or ""))
    return (hard_gate, is_actionable, score, band, liq, dq) + tie


def _build_ranking_reason(row: dict, tier: str, score: int, is_actionable: bool) -> str:
    ticker = str(row.get("ticker") or "").upper()
    strategy_id = str(row.get("strategy_id") or "")
    strategy_label = {
        "earnings_calendar": "Earnings Calendar",
        "skew_momentum_vertical": "Skew Vertical",
        "forward_factor_calendar": "Forward Factor",
        "stock_momentum": "Stock Momentum",
    }.get(strategy_id, strategy_id)
    verdict = str(row.get("verdict") or "")
    if tier == "REJECTED":
        return f"{ticker} ({strategy_label}): Tier REJECTED — hard gate failure. Score {score}."
    if tier == "DIAGNOSTIC":
        return f"{ticker} ({strategy_label}): Tier DIAGNOSTIC — not actionable. Score {score}."
    action_str = "Actionable" if is_actionable else "Research only"
    return f"{ticker} ({strategy_label}): Tier {tier} — {action_str}. Score {score}. Verdict: {verdict[:60]}."
