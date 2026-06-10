"""Fatal-gate final verdicts for Strategy 2."""

from __future__ import annotations

from typing import Any


def apply_skew_momentum_vertical_verdict(candidate: dict[str, Any]) -> dict[str, Any]:
    failures = [item for item in candidate.get("requirements", []) or [] if item.get("status") == "FAIL"]
    blocker = str(failures[0].get("detail") or failures[0].get("name") or "") if failures else ""
    code = str(failures[0].get("code") or "") if failures else ""
    verdicts = {
        "data_quality": ("FAIL / DATA QUALITY", "BLOCKED_DATA_QUALITY", "Fix provider/candle quality before review."),
        "no_chain": ("FAIL / NO VALID VERTICAL", "BLOCKED_NO_OPTIONS_CHAIN", "Retry when a usable options chain is available."),
        "no_vertical": ("FAIL / NO VALID VERTICAL", "BLOCKED_NO_VALID_VERTICAL", "A liquid short wing or valid width must appear."),
        "liquidity": ("FAIL / OPTIONS ILLIQUID", "BLOCKED_ILLIQUID_OPTIONS", "Wait for tighter bid/ask spreads and stronger volume/open interest."),
        "spread_width": ("FAIL / SPREAD TOO WIDE", "BLOCKED_SPREAD_TOO_WIDE", "Wait for tighter option markets."),
        "debit": ("FAIL / DEBIT TOO LARGE", "BLOCKED_DEBIT_TOO_LARGE", "A lower-debit structure must pass risk limits."),
        "reward_risk": ("FAIL / REWARD RISK TOO WEAK", "BLOCKED_REWARD_RISK_TOO_WEAK", "Reward/risk must improve after conservative pricing."),
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
    return {
        **candidate,
        "verdict": verdict,
        "display_state": state,
        "display_state_label": state.replace("_", " ").title(),
        "display_tone": tone,
        "primary_blocker": blocker,
        "next_action": next_action,
        "recoverability_hint": next_action,
    }
