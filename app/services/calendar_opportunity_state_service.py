"""Normalize calendar opportunities into a strategy-agnostic display shape."""

from __future__ import annotations

from typing import Any


DISPLAY_STATES = {
    "ACTIVE_OPEN",
    "PASSED_ENTRY_REVIEW",
    "WATCH_EARLY",
    "WATCH_LATE",
    "BLOCKED_PRECHECK",
    "BLOCKED_NO_STRUCTURE",
    "BLOCKED_RANKING",
    "BLOCKED_FINAL_VERDICT",
    "PROVIDER_LIMITED",
    "CACHED_RECENT",
    "UNKNOWN_REVIEW",
}


def normalize_calendar_opportunity_state(row: dict[str, Any] | None, *, cached: bool = False) -> dict[str, str]:
    row = row or {}
    final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
    quality = row.get("quality_precheck") if isinstance(row.get("quality_precheck"), dict) else {}
    candidate = row.get("candidate") if isinstance(row.get("candidate"), dict) else {}
    ranking = row.get("ranking") if isinstance(row.get("ranking"), dict) else {}
    verdict = _first(row.get("verdict"), final.get("final_verdict"), row.get("action"))
    blocker = _first(row.get("main_blocker"), final.get("main_blocker"), quality.get("primary_rejection_reason"))
    reason = _first(row.get("main_reason"), final.get("main_reason"), blocker, row.get("reasons"))
    next_action = _first(row.get("next_action"), row.get("entry_plan"), row.get("next_check"))
    text = " ".join(
        str(value)
        for value in (
            verdict,
            blocker,
            reason,
            row.get("backtest_status"),
            quality.get("primary_rejection_reason"),
            " ".join(str(item) for item in (row.get("provider_notes") or [])),
        )
        if value
    ).upper()
    row_type = str(row.get("type") or "").lower()
    entry_timing = str(row.get("entry_timing") or ranking.get("entry_timing") or "").upper()
    has_candidate = bool(candidate or row.get("possible_spread"))

    if row_type in {"open_calendar", "active_calendar", "open_trade"} or "OPEN /" in text or "LIFECYCLE" in text:
        state = "ACTIVE_OPEN"
        hint = next_action or "Reprice the broker-detected active calendar."
    elif _provider_limited(text):
        state = "PROVIDER_LIMITED"
        hint = "Provider/data issue; retry after the affected provider recovers."
    elif quality and quality.get("passes_precheck") is False:
        state = "BLOCKED_PRECHECK"
        hint = _recoverability_hint(text, "precheck")
    elif not has_candidate and ("NO VALID CALENDAR STRUCTURE" in text or "NO PROPOSED SPREAD" in text or quality):
        state = "BLOCKED_NO_STRUCTURE"
        hint = "Could become tradable if a valid expiration pair appears."
    elif str(ranking.get("action") or row.get("ranking_action") or "").upper().startswith("FAIL"):
        state = "BLOCKED_RANKING"
        hint = _recoverability_hint(text, "ranking")
    elif str(verdict).upper().startswith("FAIL"):
        state = "BLOCKED_FINAL_VERDICT"
        hint = _recoverability_hint(text, "final")
    elif str(verdict).upper().startswith("PASS"):
        state = "PASSED_ENTRY_REVIEW"
        hint = next_action or "Recheck live quotes and account guardrails before any entry."
    elif entry_timing == "EARLY" or "EARLY" in text:
        state = "WATCH_EARLY"
        hint = "Re-run closer to earnings."
    elif entry_timing == "LATE" or "LATE" in text:
        state = "WATCH_LATE"
        hint = "Late review only; avoid chasing unless liquidity and IV edge remain strong."
    elif cached:
        state = "CACHED_RECENT"
        hint = next_action or "Re-run the scanner to refresh this cached opportunity."
    else:
        state = "UNKNOWN_REVIEW"
        hint = next_action or "Review the attached requirements and provider notes."

    labels = {
        "ACTIVE_OPEN": "Active Open",
        "PASSED_ENTRY_REVIEW": "Passed Entry Review",
        "WATCH_EARLY": "Watch Early",
        "WATCH_LATE": "Watch Late",
        "BLOCKED_PRECHECK": "Blocked Precheck",
        "BLOCKED_NO_STRUCTURE": "Blocked No Structure",
        "BLOCKED_RANKING": "Blocked Ranking",
        "BLOCKED_FINAL_VERDICT": "Blocked Final Verdict",
        "PROVIDER_LIMITED": "Provider Limited",
        "CACHED_RECENT": "Cached Recent",
        "UNKNOWN_REVIEW": "Unknown Review",
    }
    tones = {
        "ACTIVE_OPEN": "warn",
        "PASSED_ENTRY_REVIEW": "good",
        "WATCH_EARLY": "neutral",
        "WATCH_LATE": "warn",
        "BLOCKED_PRECHECK": "bad",
        "BLOCKED_NO_STRUCTURE": "bad",
        "BLOCKED_RANKING": "bad",
        "BLOCKED_FINAL_VERDICT": "bad",
        "PROVIDER_LIMITED": "warn",
        "CACHED_RECENT": "neutral",
        "UNKNOWN_REVIEW": "neutral",
    }
    return {
        "display_state": state,
        "display_state_label": labels[state],
        "display_tone": tones[state],
        "primary_reason": reason or "No primary reason recorded.",
        "primary_blocker": blocker or "",
        "next_action": next_action or hint,
        "recoverability_hint": hint,
    }


def attach_calendar_display_fields(row: dict[str, Any], *, cached: bool = False) -> dict[str, Any]:
    normalized = dict(row)
    normalized.update(normalize_calendar_opportunity_state(normalized, cached=cached))
    normalized["opportunity"] = build_strategy_opportunity_row(normalized)
    return normalized


def build_strategy_opportunity_row(row: dict[str, Any]) -> dict[str, Any]:
    state = normalize_calendar_opportunity_state(row)
    final = row.get("final_verdict") if isinstance(row.get("final_verdict"), dict) else {}
    payload = {key: value for key, value in row.items() if key not in {"opportunity", "payload"}}
    return {
        "strategy_id": str(row.get("strategy_id") or row.get("strategy") or "earnings_calendar"),
        "strategy_label": str(row.get("strategy_label") or "Earnings Calendar"),
        "ticker": str(row.get("ticker") or row.get("symbol") or "UNKNOWN").upper(),
        "source": str(row.get("source") or "unified_calendar_trade_engine_v1"),
        "display_state": state["display_state"],
        "score": row.get("rank_score") if row.get("rank_score") is not None else row.get("score"),
        "priority": row.get("priority") if row.get("priority") is not None else row.get("score"),
        "verdict": _first(row.get("verdict"), final.get("final_verdict"), row.get("action")),
        "primary_reason": state["primary_reason"],
        "primary_blocker": state["primary_blocker"],
        "next_action": state["next_action"],
        "risk_notes": list(row.get("risks") or []),
        "provider_notes": list(row.get("provider_notes") or []),
        "payload": payload,
    }


def _provider_limited(text: str) -> bool:
    return any(token in text for token in ("PROVIDER", "HTTP 403", "HTTP 429", "RATE LIMIT", "DATA UNAVAILABLE", "CANDLE DATA"))


def _recoverability_hint(text: str, stage: str) -> str:
    if "TIMESTAMP" in text or "SESSION" in text:
        return "Needs confirmed earnings timestamp."
    if "SPREAD" in text or "BID/ASK" in text:
        return "Could become tradable if spreads tighten."
    if "LIQUID" in text or "OPEN INTEREST" in text or "VOLUME" in text:
        return "Rejected by liquidity; avoid unless volume/OI improves."
    if "DEBIT" in text or "TOO LARGE" in text:
        return "Needs a lower-debit structure that passes account guardrails."
    if stage == "precheck":
        return "Fix the precheck blocker, then re-run the calendar scanner."
    if stage == "ranking":
        return "Could become tradable if ranking criteria improve."
    return "Final verdict blocked entry; re-run only after the blocker changes."


def _first(*values: Any) -> str:
    for value in values:
        if isinstance(value, list) and value:
            return str(value[0])
        if value not in (None, "", []):
            return str(value)
    return ""
