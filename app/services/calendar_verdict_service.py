"""
Final calendar verdict service.

This module is deliberately stateless: it takes already-discovered candidates,
ranking rows, optional backtest context, and optional account context, then
returns conservative user-facing fields. It never creates or tracks trades.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Any

from app import config


@dataclass
class CalendarFinalVerdict:
    final_verdict: str
    status: str
    main_blocker: str
    main_reason: str
    hard_fail_reason: str | None
    trade_type: str
    trade_type_label: str
    backtest_status: str
    account_risk_status: str
    account_risk_warning: str
    raw_scanner_verdict: str
    ranking_verdict: str
    can_show_as_entry: bool
    reasons: list[str]
    blockers: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_calendar_trade_type(candidate: dict[str, Any], ranking: dict[str, Any] | None = None) -> dict[str, Any]:
    earnings = _first_dict(
        candidate.get("earnings_event"),
        candidate.get("earnings"),
        ((ranking or {}).get("strategy") or {}).get("earnings") if isinstance((ranking or {}).get("strategy"), dict) else None,
    )
    event_date = _parse_date(earnings.get("earnings_date") or earnings.get("date"))
    front = _parse_date(candidate.get("front_expiration") or candidate.get("short_expiration"))
    back = _parse_date(candidate.get("back_expiration") or candidate.get("long_expiration"))
    session = _normalize_session(earnings.get("session_label") or earnings.get("session") or earnings.get("earnings_session"))
    confirmed = bool(earnings.get("is_timestamp_confirmed")) and session != "unknown"
    max_front_days = int(getattr(config, "CALENDAR_TRUE_IV_FRONT_MAX_DAYS_AFTER_EVENT", 7) or 7)
    detail = ""

    if not event_date or not front or not back or (not confirmed and not bool(getattr(config, "CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS", False))):
        key = "unknown_event_timing"
        detail = "Earnings date/session is missing or unconfirmed."
    elif session == "unknown":
        key = "unknown_event_timing"
        detail = "Earnings session is unknown, so event inclusion cannot be trusted."
    elif not _expiration_includes_event(front, event_date, session):
        if _expiration_includes_event(back, event_date, session):
            key = "pre_earnings_financing_or_directional_long_vol"
            detail = "Short leg expires before the earnings release; long leg carries the event."
        else:
            key = "not_an_earnings_calendar"
            detail = "Neither expiration cleanly carries the earnings event."
    elif (front - event_date).days > max_front_days:
        key = "invalid_for_earnings_strategy"
        detail = f"Front short expiration is {(front - event_date).days} days after earnings, beyond the {max_front_days}-day event-IV window."
    elif not back or back <= front:
        key = "invalid_for_earnings_strategy"
        detail = "Long expiration must be after the short expiration."
    elif front < event_date:
        key = "pre_earnings_financing_or_directional_long_vol"
        detail = "Short leg expires before earnings; this is not a true event-IV short calendar."
    else:
        key = "true_earnings_iv_crush_calendar"
        detail = "Short/front leg includes the earnings event and long leg remains open after it."

    labels = {
        "true_earnings_iv_crush_calendar": "TRUE EARNINGS IV-CRUSH CALENDAR",
        "pre_earnings_financing_or_directional_long_vol": "PRE-EARNINGS FINANCING / LONG-VOL TRADE",
        "not_an_earnings_calendar": "NOT AN EARNINGS CALENDAR",
        "invalid_for_earnings_strategy": "INVALID FOR STRATEGY",
        "unknown_event_timing": "TIMESTAMP UNKNOWN",
    }
    return {
        "trade_type": key,
        "trade_type_label": labels.get(key, "TIMESTAMP UNKNOWN"),
        "trade_type_detail": detail,
        "event_session": session,
        "front_days_after_event": None if not event_date or not front else (front - event_date).days,
    }


def classify_trade_type(candidate: dict[str, Any], ranking: dict[str, Any] | None = None) -> dict[str, Any]:
    return classify_calendar_trade_type(candidate, ranking)


def apply_hard_fail_overrides(
    candidate: dict[str, Any],
    ranking: dict[str, Any] | None = None,
    account_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    blockers: list[str] = []
    reasons: list[str] = []
    verdict = None
    status = "PASS"
    main_blocker = ""

    max_spread = _num(candidate.get("max_leg_spread_pct"))
    min_oi = _num(candidate.get("min_leg_open_interest"))
    min_vol = _num(candidate.get("min_leg_volume"))
    iv_edge = _num(candidate.get("iv_edge"))

    if max_spread is not None and max_spread > float(config.CALENDAR_HARD_FAIL_MAX_LEG_SPREAD_PCT):
        verdict = "FAIL / UNTRADEABLE SPREAD"
        status = "FAIL"
        main_blocker = "options market untradeable"
        blockers.append(f"Max leg spread {max_spread:.1f}% exceeds {config.CALENDAR_HARD_FAIL_MAX_LEG_SPREAD_PCT}%.")
    if min_oi is not None and min_oi < float(config.CALENDAR_HARD_FAIL_MIN_OPEN_INTEREST):
        verdict = "FAIL / NO OPEN INTEREST"
        status = "FAIL"
        main_blocker = main_blocker or "options market untradeable"
        blockers.append(f"Min open interest {min_oi:.0f} is below {config.CALENDAR_HARD_FAIL_MIN_OPEN_INTEREST}.")
    if (
        min_vol is not None
        and min_vol < float(config.CALENDAR_HARD_FAIL_MIN_VOLUME_IF_LOW_OI)
        and (min_oi is None or min_oi <= float(config.CALENDAR_HARD_FAIL_LOW_OI_THRESHOLD))
    ):
        verdict = "FAIL / NO LIVE LIQUIDITY"
        status = "FAIL"
        main_blocker = main_blocker or "options market untradeable"
        blockers.append(f"Min volume {min_vol:.0f} with low OI does not show live liquidity.")
    if iv_edge is not None and iv_edge < -float(config.CALENDAR_HARD_FAIL_BACK_IV_OVER_FRONT_IV_PCT):
        verdict = verdict or "FAIL / IV EDGE NOT PRESENT"
        status = "FAIL"
        main_blocker = main_blocker or "IV edge not present"
        blockers.append(f"Back IV exceeds front IV by {abs(iv_edge):.1f} points.")

    timestamp_confirmed = _timestamp_confirmed(candidate, ranking)
    if not timestamp_confirmed and bool(config.CALENDAR_REQUIRE_CONFIRMED_EARNINGS_TIMESTAMP_FOR_ENTRY) and status != "FAIL":
        verdict = "WATCH ONLY / TIMESTAMP UNCONFIRMED"
        status = "WATCH"
        main_blocker = "earnings timestamp unconfirmed"
        blockers.append("Earnings timestamp/session is unconfirmed; entry label is capped at watch.")

    account = evaluate_account_risk(candidate, account_context)
    if account["account_risk_status"] == "TOO LARGE":
        verdict = "FAIL / DEBIT TOO LARGE"
        status = "FAIL"
        main_blocker = "debit too large for account"
        blockers.append(account["account_risk_warning"])
    elif account["account_risk_warning"]:
        reasons.append(account["account_risk_warning"])

    return {
        "override_verdict": verdict,
        "status": status,
        "main_blocker": main_blocker,
        "hard_fail_reason": blockers[0] if status == "FAIL" and blockers else None,
        "blockers": _dedupe(blockers),
        "reasons": _dedupe(reasons),
        **account,
    }


def build_final_calendar_verdict(
    candidate: dict[str, Any],
    ranking: dict[str, Any] | None = None,
    backtest: dict[str, Any] | None = None,
    account_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ranking = ranking or {}
    trade = classify_trade_type(candidate, ranking)
    override = apply_hard_fail_overrides(candidate, ranking, account_context)
    raw_scanner = _raw_scanner_verdict(candidate)
    ranking_action = str(ranking.get("action") or "").strip()
    status = override.get("status") or "PASS"
    verdict = override.get("override_verdict")
    blockers = list(override.get("blockers") or [])
    reasons = list(override.get("reasons") or [])

    if not verdict and bool(config.CALENDAR_FINAL_VERDICT_USE_RANKING) and ranking_action:
        if ranking_action.upper().startswith("FAIL"):
            verdict = "FAIL / DO NOT ENTER"
            status = "FAIL"
            blockers.append(_ranking_blocker(ranking))
        elif ranking_action.upper().startswith("PASS"):
            verdict = "PASS / POSSIBLE ENTRY SETUP"
            status = "PASS"
        else:
            verdict = ranking_action
            status = "WATCH"

    if not verdict:
        verdict = raw_scanner
        status = "PASS" if verdict.upper().startswith("PASS") else "FAIL" if verdict.upper().startswith("FAIL") else "WATCH"

    trade_type = trade["trade_type"]
    if trade_type == "true_earnings_iv_crush_calendar" and not bool(config.CALENDAR_TRUE_IV_CRUSH_CAN_PASS) and status == "PASS":
        verdict, status = "WATCH / TRUE CALENDAR DISABLED", "WATCH"
    if trade_type == "pre_earnings_financing_or_directional_long_vol" and not bool(config.CALENDAR_PRE_EARNINGS_FINANCING_CAN_PASS) and status == "PASS":
        verdict, status = "WATCH / RESEARCH ONLY", "WATCH"
        blockers.append("Pre-earnings financing/long-vol structures are research-only by default.")
    if trade_type in {"not_an_earnings_calendar", "invalid_for_earnings_strategy"} and status == "PASS":
        verdict = "FAIL / INVALID FOR STRATEGY" if trade_type == "invalid_for_earnings_strategy" else "FAIL / NOT AN EARNINGS CALENDAR"
        status = "FAIL"
        blockers.append(trade.get("trade_type_detail") or "Structure does not match the earnings-calendar strategy.")
    if trade_type == "unknown_event_timing" and status == "PASS" and not bool(getattr(config, "CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS", False)):
        verdict, status = "WATCH ONLY / TIMESTAMP UNCONFIRMED", "WATCH"
        blockers.append("Trade type cannot be confirmed without earnings timing.")

    bt_status = _backtest_status(ranking, backtest, status, blockers)
    main_blocker = str(override.get("main_blocker") or "").strip()
    if not main_blocker and blockers:
        main_blocker = blockers[0]
    main_reason = _main_reason(status, trade["trade_type_label"], ranking, candidate, reasons, blockers)
    can_show = status == "PASS" and str(verdict).upper().startswith("PASS")

    return CalendarFinalVerdict(
        final_verdict=verdict,
        status=status,
        main_blocker=main_blocker,
        main_reason=main_reason,
        hard_fail_reason=override.get("hard_fail_reason"),
        trade_type=trade_type,
        trade_type_label=trade["trade_type_label"],
        backtest_status=bt_status,
        account_risk_status=override.get("account_risk_status") or "OK",
        account_risk_warning=override.get("account_risk_warning") or "",
        raw_scanner_verdict=raw_scanner,
        ranking_verdict=ranking_action or "not ranked",
        can_show_as_entry=can_show,
        reasons=_dedupe(reasons),
        blockers=_dedupe(blockers),
    ).to_dict()


def attach_final_verdicts_to_ranking(
    ranking: dict[str, Any],
    account_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    items = [i for i in (ranking.get("items", []) or []) if isinstance(i, dict)]
    for item in items:
        candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else {}
        final = build_final_calendar_verdict(candidate, item, None, account_context)
        item["final_verdict"] = final
        item["trade_type"] = final["trade_type"]
        item["trade_type_label"] = final["trade_type_label"]
        item["main_blocker"] = final["main_blocker"]
        item["main_reason"] = final["main_reason"]
        item["backtest_status"] = final["backtest_status"]
        item["account_risk_status"] = final["account_risk_status"]
        item["account_risk_warning"] = final["account_risk_warning"]
    ranking["eligible_for_backtest"] = [
        item for item in items
        if item.get("backtest_eligible") and (item.get("final_verdict") or {}).get("can_show_as_entry")
    ]
    summary = ranking.get("summary", {}) if isinstance(ranking.get("summary"), dict) else {}
    summary["final_pass_count"] = sum(1 for item in items if (item.get("final_verdict") or {}).get("status") == "PASS")
    summary["final_fail_count"] = sum(1 for item in items if (item.get("final_verdict") or {}).get("status") == "FAIL")
    summary["hard_fail_count"] = sum(1 for item in items if (item.get("final_verdict") or {}).get("hard_fail_reason"))
    ranking["summary"] = summary
    return ranking


def evaluate_account_risk(candidate: dict[str, Any], account_context: dict[str, Any] | None = None) -> dict[str, Any]:
    debit = _num(candidate.get("debit_total_estimate"))
    if debit is None:
        per_spread = _num(candidate.get("conservative_debit") or candidate.get("mid_debit"))
        debit = per_spread * 100.0 if per_spread is not None else None

    # TKT-024: check override first, then estimate from positions.
    override = getattr(config, "CALENDAR_ACCOUNT_VALUE_OVERRIDE", None)
    if override:
        account_value = float(override)
    else:
        account_value = _num((account_context or {}).get("account_value_estimate"))

    pct_of_account = (debit / account_value * 100.0) if debit is not None and account_value and account_value > 0 else None
    max_debit_pct_of_account = float(getattr(config, "CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT", 0.02) or 0.02) * 100.0

    status = "OK"
    warning = ""
    if not bool(config.CALENDAR_ACCOUNT_GUARDRAILS_ENABLED):
        status = "OK"
    elif account_value is None:
        status = "UNKNOWN ACCOUNT VALUE"
        warning = "Account value unavailable; debit sizing cannot be fully checked."
    elif debit is not None and (debit > float(config.CALENDAR_MAX_DEBIT_DOLLARS) or (pct_of_account or 0) > max_debit_pct_of_account):
        status = "TOO LARGE"
        warning = "Debit is too large for configured account guardrails."
    elif debit is not None and (debit > float(config.CALENDAR_WARN_DEBIT_DOLLARS) or (pct_of_account or 0) > float(config.CALENDAR_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT)):
        status = "WATCH SIZE"
        warning = "Debit is elevated for account size; consider smaller risk or shorter back-expiration alternatives."

    return {
        "account_value_estimate": account_value,
        "debit_total_estimate": debit,
        "debit_pct_of_account": None if pct_of_account is None else round(pct_of_account, 2),
        "max_loss_assumption": "debit" if bool(config.CALENDAR_ASSUME_MAX_LOSS_IS_DEBIT) else "unknown",
        "account_risk_status": status,
        "account_risk_warning": warning,
    }


def _raw_scanner_verdict(candidate: dict[str, Any]) -> str:
    if not candidate:
        return "FAIL / NO VALID CALENDAR STRUCTURE"
    verdict = str(candidate.get("verdict") or candidate.get("action") or "").strip()
    return verdict or "PASS / POSSIBLE ENTRY SETUP"


def _ranking_blocker(ranking: dict[str, Any]) -> str:
    failures = [c for c in (ranking.get("criteria", []) or []) if str(c.get("status") or "").upper() == "FAIL"]
    if failures:
        return str(failures[0].get("detail") or failures[0].get("name") or "Ranking rejected this setup.")
    return str(ranking.get("next_check") or "Calendar ranking rejected this setup.")


def _main_reason(status: str, trade_label: str, ranking: dict[str, Any], candidate: dict[str, Any], reasons: list[str], blockers: list[str]) -> str:
    if status == "FAIL":
        return blockers[0] if blockers else "Final verdict rejected this setup before any PASS label."
    if status == "WATCH":
        return blockers[0] if blockers else f"{trade_label}; monitor/research only until stricter requirements pass."
    if reasons:
        return reasons[0]
    if ranking.get("reasons"):
        return str((ranking.get("reasons") or [""])[0])
    return f"{trade_label}; candidate passed final hard-fail checks."


def _backtest_status(ranking: dict[str, Any], backtest: dict[str, Any] | None, status: str, blockers: list[str]) -> str:
    if "insufficient_historical_candle_data" in (ranking.get("backtest_blockers") or []):
        return "skipped_insufficient_candles"
    if status != "PASS":
        blocker_text = " ".join(blockers).lower()
        if blockers and any(term in blocker_text for term in ("untradeable", "spread", "liquidity", "open interest", "volume")):
            return "skipped_untradeable"
        return "diagnostic_available"
    if ranking.get("backtest_eligible"):
        return "eligibility_queued"
    return "not_eligible"


def _timestamp_confirmed(candidate: dict[str, Any], ranking: dict[str, Any] | None) -> bool:
    for payload in (
        candidate.get("earnings_event"),
        candidate.get("earnings"),
        ((ranking or {}).get("strategy") or {}).get("earnings") if isinstance((ranking or {}).get("strategy"), dict) else None,
    ):
        if isinstance(payload, dict) and payload.get("is_timestamp_confirmed"):
            return True
    return False


def _normalize_session(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return "unknown"
    if text in {"bmo", "before open", "before market open", "before-market-open", "pre-market", "premarket"}:
        return "bmo"
    if text in {"amc", "after close", "after market close", "after-market-close", "post-market", "postmarket"}:
        return "amc"
    if "before" in text and "market" in text:
        return "bmo"
    if "after" in text and ("market" in text or "close" in text):
        return "amc"
    return "unknown"


def _expiration_includes_event(expiration: date, event_date: date, session: str) -> bool:
    if session == "bmo":
        return expiration >= event_date
    if session == "amc":
        return expiration > event_date
    return False


def _first_dict(*items: Any) -> dict[str, Any]:
    for item in items:
        if isinstance(item, dict) and item:
            return item
    return {}


def _parse_date(value: Any) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _num(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[Any]) -> list[str]:
    out: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out
