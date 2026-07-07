"""Fatal-gate final verdicts for Strategy 2."""

from __future__ import annotations

from typing import Any

from app.services.strategy_row_normalization_service import normalize_strategy_row


def apply_skew_momentum_vertical_verdict(candidate: dict[str, Any]) -> dict[str, Any]:
    failures = [item for item in candidate.get("requirements", []) or [] if item.get("status") == "FAIL"]
    blocker = str(failures[0].get("detail") or failures[0].get("name") or "") if failures else ""
    code = str(failures[0].get("code") or "") if failures else ""
    verdicts = {
        "data_quality": ("FAIL / DATA QUALITY", "BLOCKED_DATA_QUALITY", "Fix provider/candle quality before review."),
        "dte": ("FAIL / DTE TOO SHORT", "BLOCKED_DTE_TOO_SHORT", "Wait for an expiration that clears the hard DTE minimum."),
        "no_chain": ("FAIL / NO VALID VERTICAL", "BLOCKED_NO_OPTIONS_CHAIN", "Retry when a usable options chain is available."),
        "no_vertical": ("FAIL / NO VALID VERTICAL", "BLOCKED_NO_VALID_VERTICAL", "A liquid short wing or valid width must appear."),
        "liquidity": ("FAIL / OPTIONS ILLIQUID", "BLOCKED_ILLIQUID_OPTIONS", "Wait for tighter bid/ask spreads and stronger volume/open interest."),
        "spread_width": ("FAIL / SPREAD TOO WIDE", "BLOCKED_SPREAD_TOO_WIDE", "Wait for tighter option markets."),
        "debit": ("FAIL / DEBIT TOO LARGE", "BLOCKED_DEBIT_TOO_LARGE", "A lower-debit structure must pass risk limits."),
        "account_risk": ("FAIL / ACCOUNT RISK TOO HIGH", "BLOCKED_ACCOUNT_RISK_TOO_HIGH", "Max risk exceeds configured percentage of estimated account value."),
        "reward_risk": ("FAIL / REWARD RISK TOO WEAK", "BLOCKED_REWARD_RISK_TOO_WEAK", "Reward/risk must improve after conservative pricing."),
        "earnings_trust": ("FAIL / EARNINGS DATE CONFLICT", "BLOCKED_EARNINGS_DATE_CONFLICT", "Resolve the earnings-date conflict before live review."),
    }
    if code in verdicts:
        verdict, state, next_action = verdicts[code]
    elif not candidate.get("momentum_confirmed"):
        verdict, state, next_action = "WATCH / MOMENTUM NOT CONFIRMED", "WATCH_MOMENTUM_NOT_CONFIRMED", "Wait for directional momentum confirmation."
        blocker = blocker or "Momentum is mixed or below the configured threshold."
    elif not candidate.get("skew_pass"):
        verdict, state, next_action = "WATCH / SKEW NOT RICH ENOUGH", "WATCH_SKEW_NOT_RICH_ENOUGH", "Wait for the short wing to provide meaningful financing."
        blocker = blocker or "Short-wing skew is not rich enough."
    elif candidate.get("event_risk") and not candidate.get("event_risk_allowed"):
        verdict, state, next_action = "WATCH / EVENT RISK", "WATCH_TOO_EARLY_OR_TOO_LATE", "Recheck after the nearby earnings event."
        blocker = "Selected expiration overlaps the configured earnings-risk window."
    elif failures:
        verdict, state, next_action = "FAIL / DATA QUALITY", "BLOCKED_DATA_QUALITY", "Resolve the failed requirement before review."
    else:
        verdict, state, next_action = "PASS / POSSIBLE ENTRY SETUP", "PASSED_ENTRY_REVIEW", "Recheck live bid/ask quotes and account risk before entry."
        blocker = ""
    tone = "good" if verdict.startswith("PASS") else "warn" if verdict.startswith("WATCH") else "bad"
    spread_data = candidate.get("possible_spread") or {}
    row = {
        **candidate,
        "verdict": verdict,
        "display_state": state,
        "display_state_label": state.replace("_", " ").title(),
        "display_tone": tone,
        "primary_blocker": blocker,
        "next_action": next_action,
        "recoverability_hint": next_action,
        # 29.8: normalized fields for pre-30A readiness
        "momentum_status": "confirmed" if candidate.get("momentum_confirmed") else ("unavailable" if candidate.get("direction") is None else "not_confirmed"),
        "skew_status": "pass" if candidate.get("skew_pass") else "fail",
        "spread_width": spread_data.get("width"),
        "estimated_debit": candidate.get("conservative_debit"),
        "structure_status": "complete" if verdict.startswith("PASS") else ("watch" if verdict.startswith("WATCH") else "fail"),
        "atm_iv": candidate.get("atm_iv"),
    }
    normalize_strategy_row(row, "skew_momentum_vertical")
    try:
        from app.strategies.skew_momentum_vertical_universal import build_skew_momentum_vertical_universal_row
        build_skew_momentum_vertical_universal_row(row)
    except Exception:
        pass  # universal enrichment is additive; never block legacy output
    return row
