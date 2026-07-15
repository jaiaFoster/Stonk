"""
app/services/options_provider_comparison_service.py — Shadow comparison engine.

Patch 33B: Compares primary and shadow provider chains on canonical contract
identity (ticker, expiration, option_type, strike). Does NOT blend values.
Does NOT influence strategy verdicts. Produces structured comparison metrics.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.models.market_data_contracts import NormalizedOptionContract, NormalizedOptionsChain


# ─── Classification constants ─────────────────────────────────────────────────

class ComparisonClassification:
    MATCH = "MATCH"
    ACCEPTABLE_VARIANCE = "ACCEPTABLE_VARIANCE"
    WARNING = "WARNING"
    MATERIAL_DIVERGENCE = "MATERIAL_DIVERGENCE"
    NOT_COMPARABLE = "NOT_COMPARABLE"


class SelectionOutcome:
    PRIMARY_SELECTED = "PRIMARY_SELECTED"
    PRIMARY_SELECTED_SHADOW_AGREES = "PRIMARY_SELECTED_SHADOW_AGREES"
    PRIMARY_SELECTED_SHADOW_DIVERGES = "PRIMARY_SELECTED_SHADOW_DIVERGES"
    FAILOVER_SELECTED_PRIMARY_UNAVAILABLE = "FAILOVER_SELECTED_PRIMARY_UNAVAILABLE"
    FAILOVER_SELECTED_PRIMARY_INVALID = "FAILOVER_SELECTED_PRIMARY_INVALID"
    SHADOW_PROMOTED_PRIMARY_FAILED = "SHADOW_PROMOTED_PRIMARY_FAILED"
    STALE_CACHE_SELECTED_ALL_PROVIDERS_FAILED = "STALE_CACHE_SELECTED_ALL_PROVIDERS_FAILED"
    UNAVAILABLE = "UNAVAILABLE"


class ShadowSkipReason:
    BUDGET = "SHADOW_SKIPPED_BUDGET"
    SAMPLE = "SHADOW_SKIPPED_SAMPLE"
    CAPABILITY = "SHADOW_SKIPPED_CAPABILITY"
    PROVIDER_UNCONFIGURED = "SHADOW_SKIPPED_PROVIDER_UNCONFIGURED"
    SHADOW_DISABLED = "SHADOW_SKIPPED_DISABLED"


# ─── Thresholds ───────────────────────────────────────────────────────────────

_UNDERLYING_WARN_PCT = 0.0025          # 0.25%
_UNDERLYING_MATERIAL_PCT = 0.005       # 0.5%

_MID_WARN_ABS = 0.05
_MID_WARN_PCT = 0.03                   # 3%
_MID_MATERIAL_ABS = 0.10
_MID_MATERIAL_PCT = 0.08               # 8%

_COVERAGE_WARN_PCT = 0.95              # warn < 95%
_COVERAGE_MATERIAL_PCT = 0.90          # material < 90%

_OI_WARN_REL = 0.10                    # 10% relative difference
_OI_MATERIAL_REL = 0.25                # 25%

_IV_WARN_ABS = 0.03
_IV_MATERIAL_ABS = 0.07

_DELTA_WARN_ABS = 0.05
_DELTA_MATERIAL_ABS = 0.10


# ─── Result types ─────────────────────────────────────────────────────────────

@dataclass
class ContractComparisonResult:
    canonical_key: tuple[str, date, str, float]   # (ticker, exp, option_type, strike)
    classification: str
    mid_diff_abs: float | None = None
    mid_diff_pct: float | None = None
    iv_diff_abs: float | None = None
    delta_diff_abs: float | None = None
    oi_diff_rel: float | None = None
    primary_mid: float | None = None
    shadow_mid: float | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class ChainComparisonResult:
    ticker: str
    primary_provider: str
    shadow_provider: str
    selection_outcome: str
    classification: str

    # Coverage
    primary_contract_count: int = 0
    shadow_contract_count: int = 0
    matched_contract_count: int = 0
    coverage_pct: float = 0.0

    # Aggregated metrics over matched contracts
    mid_median_diff_pct: float | None = None
    mid_max_diff_abs: float | None = None
    iv_median_diff_abs: float | None = None
    delta_median_diff_abs: float | None = None

    # Underlying price divergence
    underlying_diff_pct: float | None = None
    underlying_classification: str = ComparisonClassification.NOT_COMPARABLE

    # Material divergences (top contracts only, max 10)
    material_divergences: list[dict[str, Any]] = field(default_factory=list)

    # All per-contract results (not persisted in full; used for audit logging)
    contract_results: list[ContractComparisonResult] = field(default_factory=list)

    shadow_skip_reason: str | None = None
    notes: list[str] = field(default_factory=list)


# ─── Comparison logic ─────────────────────────────────────────────────────────

def compare_chains(
    primary: NormalizedOptionsChain,
    shadow: NormalizedOptionsChain,
    selection_outcome: str = SelectionOutcome.PRIMARY_SELECTED,
) -> ChainComparisonResult:
    """
    Compare primary and shadow chains on canonical identity.
    Never blends or averages values. Returns comparison metrics only.
    """
    ticker = primary.underlying

    result = ChainComparisonResult(
        ticker=ticker,
        primary_provider=primary.provider_id,
        shadow_provider=shadow.provider_id,
        selection_outcome=selection_outcome,
        classification=ComparisonClassification.NOT_COMPARABLE,
        primary_contract_count=len(primary.contracts),
        shadow_contract_count=len(shadow.contracts),
    )

    # Underlying price comparison
    if primary.underlying_price is not None and shadow.underlying_price is not None:
        p_und = primary.underlying_price
        s_und = shadow.underlying_price
        if p_und > 0:
            diff_pct = abs(p_und - s_und) / p_und
            result.underlying_diff_pct = diff_pct
            if diff_pct <= _UNDERLYING_WARN_PCT:
                result.underlying_classification = ComparisonClassification.MATCH
            elif diff_pct <= _UNDERLYING_MATERIAL_PCT:
                result.underlying_classification = ComparisonClassification.WARNING
            else:
                result.underlying_classification = ComparisonClassification.MATERIAL_DIVERGENCE

    # Build shadow lookup by canonical key
    shadow_index: dict[tuple[str, date, str, float], NormalizedOptionContract] = {}
    for c in shadow.contracts:
        key = (ticker, c.expiration, c.option_type, c.strike)
        shadow_index[key] = c

    contract_results: list[ContractComparisonResult] = []
    for pc in primary.contracts:
        key = (ticker, pc.expiration, pc.option_type, pc.strike)
        sc = shadow_index.get(key)
        cr = _compare_contract(key, pc, sc)
        contract_results.append(cr)

    result.matched_contract_count = sum(
        1 for cr in contract_results if cr.classification != ComparisonClassification.NOT_COMPARABLE
    )
    if result.primary_contract_count > 0:
        result.coverage_pct = result.matched_contract_count / result.primary_contract_count

    result.contract_results = contract_results

    # Aggregate metrics over matched contracts
    matched = [cr for cr in contract_results if cr.classification != ComparisonClassification.NOT_COMPARABLE]
    if matched:
        mid_pcts = [cr.mid_diff_pct for cr in matched if cr.mid_diff_pct is not None]
        mid_abs = [cr.mid_diff_abs for cr in matched if cr.mid_diff_abs is not None]
        iv_abs = [cr.iv_diff_abs for cr in matched if cr.iv_diff_abs is not None]
        delta_abs = [cr.delta_diff_abs for cr in matched if cr.delta_diff_abs is not None]

        result.mid_median_diff_pct = _median(mid_pcts)
        result.mid_max_diff_abs = max(mid_abs) if mid_abs else None
        result.iv_median_diff_abs = _median(iv_abs)
        result.delta_median_diff_abs = _median(delta_abs)

    # Top material divergences (up to 10)
    material = [
        cr for cr in contract_results
        if cr.classification == ComparisonClassification.MATERIAL_DIVERGENCE
    ]
    material.sort(key=lambda cr: cr.mid_diff_abs or 0.0, reverse=True)
    result.material_divergences = [_contract_result_to_dict(cr) for cr in material[:10]]

    # Chain-level classification
    result.classification = _classify_chain(result)
    return result


def _compare_contract(
    key: tuple[str, date, str, float],
    pc: NormalizedOptionContract,
    sc: NormalizedOptionContract | None,
) -> ContractComparisonResult:
    if sc is None:
        return ContractComparisonResult(key, ComparisonClassification.NOT_COMPARABLE, notes=["no shadow match"])

    notes: list[str] = []
    max_class = ComparisonClassification.MATCH

    # Mid price
    mid_diff_abs: float | None = None
    mid_diff_pct: float | None = None
    if pc.mid is not None and sc.mid is not None:
        mid_diff_abs = abs(pc.mid - sc.mid)
        if pc.mid > 0:
            mid_diff_pct = mid_diff_abs / pc.mid
        warn_threshold = max(_MID_WARN_ABS, pc.mid * _MID_WARN_PCT) if pc.mid else _MID_WARN_ABS
        mat_threshold = max(_MID_MATERIAL_ABS, pc.mid * _MID_MATERIAL_PCT) if pc.mid else _MID_MATERIAL_ABS
        if mid_diff_abs > mat_threshold:
            max_class = _max_class(max_class, ComparisonClassification.MATERIAL_DIVERGENCE)
            notes.append(f"mid material diff {mid_diff_abs:.3f}")
        elif mid_diff_abs > warn_threshold:
            max_class = _max_class(max_class, ComparisonClassification.WARNING)
            notes.append(f"mid warning diff {mid_diff_abs:.3f}")

    # IV
    iv_diff_abs: float | None = None
    if pc.implied_volatility is not None and sc.implied_volatility is not None:
        iv_diff_abs = abs(pc.implied_volatility - sc.implied_volatility)
        if iv_diff_abs > _IV_MATERIAL_ABS:
            max_class = _max_class(max_class, ComparisonClassification.MATERIAL_DIVERGENCE)
            notes.append(f"iv material diff {iv_diff_abs:.4f}")
        elif iv_diff_abs > _IV_WARN_ABS:
            max_class = _max_class(max_class, ComparisonClassification.WARNING)

    # Delta
    delta_diff_abs: float | None = None
    if pc.delta is not None and sc.delta is not None:
        delta_diff_abs = abs(pc.delta - sc.delta)
        if delta_diff_abs > _DELTA_MATERIAL_ABS:
            max_class = _max_class(max_class, ComparisonClassification.MATERIAL_DIVERGENCE)
            notes.append(f"delta material diff {delta_diff_abs:.4f}")
        elif delta_diff_abs > _DELTA_WARN_ABS:
            max_class = _max_class(max_class, ComparisonClassification.WARNING)

    # Open interest
    oi_diff_rel: float | None = None
    if pc.open_interest and sc.open_interest and pc.open_interest > 0:
        oi_diff_rel = abs(pc.open_interest - sc.open_interest) / pc.open_interest
        if oi_diff_rel > _OI_MATERIAL_REL:
            max_class = _max_class(max_class, ComparisonClassification.MATERIAL_DIVERGENCE)
        elif oi_diff_rel > _OI_WARN_REL:
            max_class = _max_class(max_class, ComparisonClassification.WARNING)

    if max_class == ComparisonClassification.MATCH:
        # Narrow match vs acceptable variance
        if mid_diff_abs is not None and mid_diff_abs <= (_MID_WARN_ABS / 2):
            max_class = ComparisonClassification.MATCH
        elif mid_diff_abs is not None and mid_diff_abs > 0:
            max_class = ComparisonClassification.ACCEPTABLE_VARIANCE

    return ContractComparisonResult(
        canonical_key=key,
        classification=max_class,
        mid_diff_abs=mid_diff_abs,
        mid_diff_pct=mid_diff_pct,
        iv_diff_abs=iv_diff_abs,
        delta_diff_abs=delta_diff_abs,
        oi_diff_rel=oi_diff_rel,
        primary_mid=pc.mid,
        shadow_mid=sc.mid,
        notes=notes,
    )


def _classify_chain(result: ChainComparisonResult) -> str:
    # Coverage check
    if result.primary_contract_count > 0 and result.coverage_pct < _COVERAGE_MATERIAL_PCT:
        return ComparisonClassification.MATERIAL_DIVERGENCE
    if result.primary_contract_count > 0 and result.coverage_pct < _COVERAGE_WARN_PCT:
        return ComparisonClassification.WARNING

    # Underlying divergence escalates chain
    if result.underlying_classification == ComparisonClassification.MATERIAL_DIVERGENCE:
        return ComparisonClassification.MATERIAL_DIVERGENCE

    # Any material contracts
    if result.material_divergences:
        mat_count = len(result.material_divergences)
        total = result.matched_contract_count or 1
        mat_pct = mat_count / total
        if mat_pct > 0.10:
            return ComparisonClassification.MATERIAL_DIVERGENCE
        return ComparisonClassification.WARNING

    # Mid median
    if result.mid_median_diff_pct is not None:
        if result.mid_median_diff_pct > _MID_MATERIAL_PCT:
            return ComparisonClassification.MATERIAL_DIVERGENCE
        if result.mid_median_diff_pct > _MID_WARN_PCT:
            return ComparisonClassification.WARNING

    if result.matched_contract_count == 0:
        return ComparisonClassification.NOT_COMPARABLE

    return ComparisonClassification.MATCH


# ─── Helpers ─────────────────────────────────────────────────────────────────

_CLASS_RANK = {
    ComparisonClassification.MATCH: 0,
    ComparisonClassification.ACCEPTABLE_VARIANCE: 1,
    ComparisonClassification.WARNING: 2,
    ComparisonClassification.MATERIAL_DIVERGENCE: 3,
    ComparisonClassification.NOT_COMPARABLE: 4,
}


def _max_class(a: str, b: str) -> str:
    return a if _CLASS_RANK.get(a, 0) >= _CLASS_RANK.get(b, 0) else b


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _contract_result_to_dict(cr: ContractComparisonResult) -> dict[str, Any]:
    ticker, exp, ot, strike = cr.canonical_key
    return {
        "ticker": ticker,
        "expiration": str(exp),
        "option_type": ot,
        "strike": strike,
        "classification": cr.classification,
        "mid_diff_abs": round(cr.mid_diff_abs, 4) if cr.mid_diff_abs is not None else None,
        "mid_diff_pct": round(cr.mid_diff_pct, 4) if cr.mid_diff_pct is not None else None,
        "iv_diff_abs": round(cr.iv_diff_abs, 4) if cr.iv_diff_abs is not None else None,
        "delta_diff_abs": round(cr.delta_diff_abs, 4) if cr.delta_diff_abs is not None else None,
        "primary_mid": cr.primary_mid,
        "shadow_mid": cr.shadow_mid,
        "notes": cr.notes,
    }
