"""Forward Factor Calendar v1 verdict assignment."""

from app import config


def apply_forward_factor_verdict(row: dict) -> dict:
    blocker = ""
    if row.get("liquidity_status") == "WATCH":
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
    return {**row, "verdict": verdict, "primary_blocker": blocker, "actionability_score": 0, "next_action": "MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT"}
