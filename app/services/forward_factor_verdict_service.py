"""Forward Factor Calendar v1 verdict assignment."""

from app import config


def apply_forward_factor_verdict(row: dict) -> dict:
    """Assign verdict, actionability, and strategy_actionable for a Forward Factor row.

    31B.6: WATCH zone — liquidity-partial, debit-warn, or near-threshold with complete structure.
    31B.8: strategy_actionable=True only for PASS/WATCH; execution_enabled always False (dry-run).
    """
    blocker = ""
    _fv = row.get("forward_variance")
    if _fv is not None and float(_fv) <= 0:
        verdict, blocker = "FAIL / IV_RELATIONSHIP_ADVERSE", "Implied forward variance is non-positive — front IV exceeds back IV, indicating adverse term structure."
    elif row.get("liquidity_status") == "WATCH":
        verdict, blocker = "WATCH / LIQUIDITY DATA PARTIAL", "Four-leg package has usable quotes but incomplete liquidity fields."
    elif not row.get("liquidity_pass"):
        verdict, blocker = "FAIL / OPTIONS ILLIQUID", "Four-leg package failed configured liquidity or execution-width gates."
    elif float(row.get("debit_at_risk") or 0) > (getattr(config, "FF_WARN_DEBIT_DOLLARS", 250) or 250):
        _debit = float(row.get("debit_at_risk") or 0)
        if _debit > config.FF_MAX_DEBIT_DOLLARS:
            verdict, blocker = "FAIL / DEBIT TOO LARGE", "Conservative package debit exceeds configured risk cap."
        else:
            _warn = float(getattr(config, "FF_WARN_DEBIT_DOLLARS", 250))
            verdict, blocker = "WATCH / DEBIT ELEVATED", f"Debit ${_debit:.0f} exceeds warning threshold ${_warn:.0f}."
    elif float(row.get("forward_factor") or 0) + 1e-12 < config.FF_MIN_FORWARD_FACTOR:
        verdict, blocker = "FAIL / FORWARD FACTOR BELOW THRESHOLD", "Forward Factor is below source-reported 0.20 threshold."
    elif row.get("earnings_contaminated"):
        verdict = "DRY RUN PASS / FORWARD FACTOR SETUP"
        blocker = row.get("earnings_contamination_reason") or "Earnings contamination detected."
    else:
        verdict = "PASS / FORWARD FACTOR SETUP"
    warnings = []
    front_method = row.get("front_iv_derivation_method") or ""
    back_method = row.get("back_iv_derivation_method") or ""
    if "straddle_strip" in front_method or "straddle_strip" in back_method:
        warnings.append("Ex-earnings IV derived via ATM straddle variance stripping — verify implied move is reasonable.")
    if "haircut_fallback" in front_method or "haircut_fallback" in back_method:
        warnings.append(f"Ex-earnings IV derived via fixed {config.FF_EARNINGS_IV_HAIRCUT_PCT:.0%} haircut — straddle data was unavailable.")
    _upper = verdict.upper()
    is_pass = "PASS" in _upper and "FAIL" not in _upper
    is_watch = _upper.startswith("WATCH") or ("DRY RUN PASS" in _upper)
    is_actionable = is_pass or is_watch
    ff_candidate_stage = row.get("ff_candidate_stage") or ("actionable" if is_pass else "watch" if is_watch else "fetched")
    result = {
        **row,
        "verdict": verdict,
        "primary_blocker": blocker,
        "actionability_score": 0,
        "next_action": "MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT",
        "strategy_actionable": is_actionable,
        "execution_enabled": False,
        "ff_candidate_stage": ff_candidate_stage,
        "ff_structure_status": row.get("structure_status"),
        "ff_liquidity_status": row.get("liquidity_status"),
        "ff_actionability_status": "actionable" if is_pass else "watch" if is_watch else "non_actionable",
    }
    if warnings:
        result["iv_derivation_warnings"] = warnings
    return result
