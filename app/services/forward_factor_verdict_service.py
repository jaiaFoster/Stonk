"""Forward Factor Calendar v1 verdict assignment."""

from app import config


def apply_forward_factor_verdict(row: dict) -> dict:
    blocker = ""
    _fv = row.get("forward_variance")
    if _fv is not None and float(_fv) <= 0:
        verdict, blocker = "FAIL / IV_RELATIONSHIP_ADVERSE", "Implied forward variance is non-positive — front IV exceeds back IV, indicating adverse term structure."
    elif row.get("liquidity_status") == "WATCH":
        verdict, blocker = "WATCH / LIQUIDITY DATA PARTIAL", "Four-leg package has usable quotes but incomplete liquidity fields."
    elif not row.get("liquidity_pass"):
        verdict, blocker = "FAIL / OPTIONS ILLIQUID", "Four-leg package failed configured liquidity or execution-width gates."
    elif float(row.get("debit_at_risk") or 0) > config.FF_MAX_DEBIT_DOLLARS:
        verdict, blocker = "FAIL / DEBIT TOO LARGE", "Conservative package debit exceeds configured risk cap."
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
    result = {**row, "verdict": verdict, "primary_blocker": blocker, "actionability_score": 0, "next_action": "MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT"}
    if warnings:
        result["iv_derivation_warnings"] = warnings
    return result
