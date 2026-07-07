"""Universal row enrichment for Forward Factor Calendar — ASA Patch 30E.

build_forward_factor_universal_row() adds universal schema fields to FF
candidate rows. Works in-place (also returns the row). Idempotent.

All legacy fields are preserved untouched so existing consumers still work.

CAVEMAN MODE: read-only, no broker writes, no provider calls.
FORWARD_FACTOR_DRY_RUN=True preserved — dry-run rows get row_type="observation".
FF excluded from Daily Opportunity — daily_opportunity.eligible always False.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.strategies.schema import SCHEMA_VERSION, VALID_ROW_TYPES


def build_forward_factor_universal_row(
    row: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Enrich a forward_factor_calendar candidate row with universal fields.

    Works in-place (also returns the row). Idempotent — safe to call twice.
    """
    if row.get("schema_version") == SCHEMA_VERSION:
        return row

    from app import config

    ticker = str(row.get("ticker") or "unknown").upper().strip()
    verdict = str(row.get("verdict") or "").upper()
    score = _safe_float(row.get("score") or row.get("actionability_score")) or 0.0
    is_dry_run = bool(getattr(config, "FORWARD_FACTOR_DRY_RUN", True))

    row.setdefault("strategy_id", "forward_factor_calendar")

    if "row_type" not in row:
        row["row_type"] = _infer_row_type(verdict, is_dry_run=is_dry_run)

    row["schema_version"] = SCHEMA_VERSION

    if "row_id" not in row:
        front = str(row.get("front_expiration") or "")
        back = str(row.get("back_expiration") or "")
        row["row_id"] = _stable_row_id("forward_factor_calendar", ticker, str(run_id or ""), front, back)

    if "details" not in row:
        row["details"] = {"forward_factor": _build_ff_details(row, is_dry_run=is_dry_run)}

    if "display" not in row:
        friendly = str(row.get("friendly_verdict") or verdict or "")
        primary = str(row.get("primary_blocker") or row.get("primary_reason") or "")
        row["display"] = {
            "title": ticker,
            "subtitle": "Forward Factor Calendar",
            "badge": friendly,
            "sort_key": score,
            "public_reason": primary,
            "detail_lines": _detail_lines(row),
        }

    if "gate_groups" not in row:
        row["gate_groups"] = _build_gate_groups(row, is_dry_run=is_dry_run)

    if "daily_opportunity" not in row:
        row["daily_opportunity"] = {
            "eligible": False,
            "priority": None,
            "bucket": "forward_factor_calendar",
            "reason": "",
            "exclusion_reason": "Forward Factor Calendar is excluded from Daily Opportunity (dry-run / research mode).",
        }

    return row


# ─── Row type inference ───────────────────────────────────────────────────────

def _infer_row_type(verdict_upper: str, *, is_dry_run: bool) -> str:
    if is_dry_run:
        return "observation"
    if verdict_upper.startswith("PASS"):
        return "new_candidate"
    if verdict_upper.startswith("FAIL"):
        return "rejected_candidate"
    if verdict_upper.startswith("SKIPPED") or verdict_upper.startswith("WATCH"):
        return "observation"
    return "observation"


# ─── details.forward_factor ───────────────────────────────────────────────────

def _build_ff_details(row: dict[str, Any], *, is_dry_run: bool) -> dict[str, Any]:
    from app import config
    formula = row.get("diagnostic_raw_iv_formula") or {}
    return {
        "forward_factor": _safe_float(row.get("forward_factor") or (formula.get("forward_factor") if isinstance(formula, dict) else None)),
        "front_dte": row.get("front_dte"),
        "back_dte": row.get("back_dte"),
        "front_expiration": row.get("front_expiration"),
        "back_expiration": row.get("back_expiration"),
        "front_iv": _safe_float(row.get("front_ex_earnings_iv") or row.get("front_raw_iv")),
        "back_iv": _safe_float(row.get("back_ex_earnings_iv") or row.get("back_raw_iv")),
        "front_ex_earnings_iv": _safe_float(row.get("front_ex_earnings_iv")),
        "back_ex_earnings_iv": _safe_float(row.get("back_ex_earnings_iv")),
        "earnings_contaminated": bool(row.get("earnings_contaminated")),
        "iv_derivation_method": str(row.get("front_iv_derivation_method") or "unknown"),
        "formula_version": str(getattr(config, "FF_FORMULA_VERSION", "volvibes_v1")),
        "source_spec_version": int(getattr(config, "FF_SOURCE_SPEC_VERSION", 1)),
        "is_dry_run": is_dry_run,
        "conservative_debit": _safe_float(row.get("conservative_debit")),
        "mid_debit": _safe_float(row.get("mid_debit")),
        "debit_at_risk": _safe_float(row.get("debit_at_risk")),
        "liquidity_pass": bool(row.get("liquidity_pass")),
        "liquidity_status": str(row.get("liquidity_status") or ""),
        "package_slippage_pct": _safe_float(row.get("package_slippage_pct")),
        "near_miss_ff": bool(row.get("near_miss_ff")),
        "source_qualification": str(row.get("source_qualification") or ""),
        "ff_candidate_stage": str(row.get("ff_candidate_stage") or ""),
    }


# ─── Gate groups ──────────────────────────────────────────────────────────────

def _build_gate_groups(row: dict[str, Any], *, is_dry_run: bool) -> dict[str, Any]:
    verdict = str(row.get("verdict") or "").upper()
    ff_value = _safe_float(row.get("forward_factor"))
    from app import config
    ff_threshold = float(getattr(config, "FF_MIN_FORWARD_FACTOR", 0.20))
    liquidity_pass = bool(row.get("liquidity_pass"))
    earnings_contaminated = bool(row.get("earnings_contaminated"))
    structure_status = str(row.get("structure_status") or "")
    front_ex = _safe_float(row.get("front_ex_earnings_iv"))
    back_ex = _safe_float(row.get("back_ex_earnings_iv"))
    data_eligible = bool((row.get("data_eligibility") or {}).get("eligible") if isinstance(row.get("data_eligibility"), dict) else False)
    debit_at_risk = _safe_float(row.get("debit_at_risk"))
    max_debit = float(getattr(config, "FF_MAX_DEBIT_DOLLARS", 500))
    candidate_stage = str(row.get("ff_candidate_stage") or "")

    data_grp: dict[str, Any] = {
        "eligibility": _gate(
            "pass" if data_eligible else "fail",
            "Data Eligibility",
            "Price, candles, and derived metrics available." if data_eligible else "Required market data unavailable.",
            blocking=not data_eligible,
        ),
        "iv_source": _gate(
            "pass" if (front_ex is not None and back_ex is not None) else "watch",
            "IV Source",
            "Ex-earnings IV available for both expirations." if (front_ex is not None and back_ex is not None) else "Source IV unavailable; diagnostic formula only.",
            blocking=False,
        ),
    }

    candidate_grp: dict[str, Any] = {
        "selection": _gate(
            "fail" if candidate_stage in ("cap_skip", "budget_skipped") else "pass",
            "Candidate Selection",
            f"Stage: {candidate_stage}." if candidate_stage else "Candidate was selected for evaluation.",
            blocking=candidate_stage in ("cap_skip", "budget_skipped"),
        ),
    }

    forward_vol_grp: dict[str, Any] = {
        "forward_factor": _gate(
            "pass" if (ff_value is not None and ff_value >= ff_threshold) else (
                "watch" if ff_value is not None else "fail"
            ),
            "Forward Factor",
            f"FF={ff_value:.4f} ≥ threshold {ff_threshold:.2f}." if (ff_value is not None and ff_value >= ff_threshold)
            else (f"FF={ff_value:.4f} below threshold {ff_threshold:.2f}." if ff_value is not None else "Forward factor unavailable."),
            blocking=(ff_value is not None and ff_value < ff_threshold),
            custom={"forward_factor": ff_value, "threshold": ff_threshold},
        ),
    }

    earnings_grp: dict[str, Any] = {
        "contamination": _gate(
            "watch" if earnings_contaminated else "pass",
            "Earnings Contamination",
            str(row.get("earnings_contamination_reason") or "Expiry within earnings window.") if earnings_contaminated else "No earnings contamination in expiration window.",
            blocking=False,
            custom={"earnings_contaminated": earnings_contaminated},
        ),
    }

    liquidity_grp: dict[str, Any] = {
        "package_liquidity": _gate(
            "pass" if liquidity_pass else ("watch" if str(row.get("liquidity_status") or "").upper() == "WATCH" else "fail"),
            "Package Liquidity",
            "Four-leg package passes liquidity gates." if liquidity_pass else str(row.get("primary_blocker") or "Liquidity gates failed."),
            blocking=not liquidity_pass and str(row.get("liquidity_status") or "").upper() != "WATCH",
        ),
    }

    setup_grp: dict[str, Any] = {
        "structure": _gate(
            "pass" if structure_status == "COMPLETE" else "fail",
            "Double Calendar Structure",
            "Matched-strike ±35-delta double calendar constructed." if structure_status == "COMPLETE" else str(row.get("structure_reason") or "Structure unavailable."),
            blocking=structure_status != "COMPLETE",
            custom={"structure_status": structure_status},
        ),
    }

    budget_grp: dict[str, Any] = {
        "debit_cap": _gate(
            "pass" if (debit_at_risk is None or debit_at_risk <= max_debit) else "fail",
            "Debit Risk Cap",
            f"Conservative debit ${debit_at_risk:.0f} within cap ${max_debit:.0f}." if debit_at_risk is not None else "Debit not yet calculated.",
            blocking=debit_at_risk is not None and debit_at_risk > max_debit,
            custom={"debit_at_risk": debit_at_risk, "max_debit_dollars": max_debit},
        ),
    }

    risk_grp: dict[str, Any] = {
        "dry_run": _gate(
            "dry_run" if is_dry_run else "pass",
            "Dry Run Mode",
            "FORWARD_FACTOR_DRY_RUN=True — signals are research-only." if is_dry_run else "Live signal mode.",
            blocking=False,
        ),
    }

    do_grp: dict[str, Any] = {
        "eligible": _gate(
            "fail",
            "Daily Opportunity",
            "Forward Factor Calendar is excluded from Daily Opportunity.",
            blocking=False,
        ),
    }

    return {
        "data": data_grp,
        "candidate": candidate_grp,
        "forward_vol": forward_vol_grp,
        "earnings": earnings_grp,
        "liquidity": liquidity_grp,
        "setup": setup_grp,
        "budget": budget_grp,
        "risk": risk_grp,
        "daily_opportunity": do_grp,
    }


# ─── Display detail lines ─────────────────────────────────────────────────────

def _detail_lines(row: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    ff_value = _safe_float(row.get("forward_factor"))
    if ff_value is not None:
        lines.append(f"Forward Factor: {ff_value:.4f}")
    front_ex = _safe_float(row.get("front_ex_earnings_iv"))
    back_ex = _safe_float(row.get("back_ex_earnings_iv"))
    if front_ex is not None and back_ex is not None:
        lines.append(f"IV: front {front_ex:.2%} / back {back_ex:.2%}")
    front = row.get("front_expiration")
    back = row.get("back_expiration")
    if front and back:
        lines.append(f"Pair: {front} / {back}")
    debit = _safe_float(row.get("conservative_debit"))
    if debit is not None:
        lines.append(f"Debit: ${debit:.2f}")
    verdict = str(row.get("verdict") or "")
    if verdict:
        lines.append(f"Verdict: {verdict}")
    return lines[:6]


# ─── Shared utilities ─────────────────────────────────────────────────────────

def _gate(
    status: str,
    label: str,
    reason: str,
    *,
    blocking: bool = False,
    custom: dict[str, Any] | None = None,
) -> dict[str, Any]:
    g: dict[str, Any] = {"status": status, "label": label, "reason": reason, "blocking": blocking}
    if custom:
        g["custom"] = custom
    return g


def _stable_row_id(strategy_id: str, ticker: str, run_id: str, front: str, back: str) -> str:
    raw = f"{strategy_id}:{ticker}:{run_id}:{front}:{back}"
    digest = hashlib.sha1(raw.encode()).hexdigest()
    return f"ffc:{ticker}:{digest[:8]}"


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
