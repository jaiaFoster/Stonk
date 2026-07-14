"""Universal row enrichment for production Earnings Calendar — ASA Patch 30C.

build_earnings_calendar_universal_row() adds universal fields to candidate rows.
build_earnings_lifecycle_universal_row() adds universal fields to lifecycle/open-position rows.

All legacy fields are preserved untouched so existing consumers continue to work.

CAVEMAN MODE: read-only, no broker writes, no provider calls.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.strategies.schema import SCHEMA_VERSION, VALID_ROW_TYPES


def build_earnings_calendar_universal_row(
    row: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Enrich an earnings_calendar candidate row with universal fields.

    Works in-place (also returns the row). Idempotent — safe to call twice.
    """
    if row.get("schema_version") == SCHEMA_VERSION:
        return row

    ticker = str(row.get("ticker") or "unknown").upper().strip()
    action = str(row.get("action") or "").upper()
    score = float(row.get("score") or 0)

    # ── row_type ──────────────────────────────────────────────────────────────
    if "row_type" not in row:
        row["row_type"] = _infer_candidate_row_type(action)

    # ── schema_version ────────────────────────────────────────────────────────
    row["schema_version"] = SCHEMA_VERSION

    # ── row_id ────────────────────────────────────────────────────────────────
    if "row_id" not in row:
        _run = str(run_id or "")
        row["row_id"] = _stable_row_id("earnings_calendar", ticker, _run)

    # ── details.earnings_calendar ─────────────────────────────────────────────
    if "details" not in row:
        row["details"] = {"earnings_calendar": _build_details(row)}

    # ── display ───────────────────────────────────────────────────────────────
    if "display" not in row:
        friendly = str(row.get("friendly_verdict") or row.get("action") or "")
        reasons = list(row.get("reasons") or [])
        primary = str(row.get("primary_reason") or (reasons[0] if reasons else ""))
        row["display"] = {
            "title": ticker,
            "subtitle": "Earnings Calendar",
            "badge": friendly,
            "sort_key": score,
            "public_reason": primary,
            "detail_lines": _candidate_detail_lines(row),
        }

    # ── gate_groups ───────────────────────────────────────────────────────────
    if "gate_groups" not in row:
        row["gate_groups"] = _build_candidate_gate_groups(row)

    # ── daily_opportunity dict ────────────────────────────────────────────────
    if "daily_opportunity" not in row:
        row_type = str(row.get("row_type") or "observation")
        # Semantic precedence: rejected_candidate rows must never be eligible,
        # regardless of calendar_entry_allowed (that field reflects pre-rejection
        # entry logic and may be stale when row_type is overridden to rejected).
        if row_type == "rejected_candidate":
            do_eligible = False
            row["daily_opportunity_eligible"] = False
            do_reason = "Rejected candidate excluded from Daily Opportunity."
        else:
            do_eligible = bool(row.get("daily_opportunity_eligible"))
            do_reason = str(row.get("daily_opportunity_reason") or "")
        row["daily_opportunity"] = {
            "eligible": do_eligible,
            "priority": round(score, 1) if do_eligible else None,
            "bucket": "earnings_calendar",
            "reason": do_reason if do_eligible else "",
            "exclusion_reason": "" if do_eligible else do_reason,
        }

    return row


def build_earnings_lifecycle_universal_row(
    check: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Enrich a calendar lifecycle (open-position) check with universal fields.

    Works in-place (also returns the check). Idempotent.
    """
    if check.get("schema_version") == SCHEMA_VERSION:
        return check

    ticker = str(check.get("ticker") or check.get("underlying") or "unknown").upper().strip()
    action = str(check.get("action") or "").upper()
    score = float(check.get("lifecycle_priority_score") or 0)

    # ── row_type ──────────────────────────────────────────────────────────────
    if "row_type" not in check:
        check["row_type"] = _infer_lifecycle_row_type(action)

    check.setdefault("strategy_id", "earnings_calendar")

    # ── schema_version ────────────────────────────────────────────────────────
    check["schema_version"] = SCHEMA_VERSION

    # ── row_id ────────────────────────────────────────────────────────────────
    if "row_id" not in check:
        _run = str(run_id or "")
        check["row_id"] = _stable_row_id("earnings_calendar_lifecycle", ticker, _run)

    # ── details.earnings_calendar ─────────────────────────────────────────────
    if "details" not in check:
        check["details"] = {"earnings_calendar": _build_lifecycle_details(check)}

    # ── display ───────────────────────────────────────────────────────────────
    if "display" not in check:
        reasons = list(check.get("reasons") or [])
        primary = reasons[0] if reasons else str(check.get("action") or "")
        check["display"] = {
            "title": ticker,
            "subtitle": "Earnings Calendar",
            "badge": str(check.get("action") or ""),
            "sort_key": score,
            "public_reason": primary,
            "detail_lines": _lifecycle_detail_lines(check),
        }

    # ── gate_groups ───────────────────────────────────────────────────────────
    if "gate_groups" not in check:
        check["gate_groups"] = _build_lifecycle_gate_groups(check)

    # ── daily_opportunity dict ────────────────────────────────────────────────
    if "daily_opportunity" not in check:
        check["daily_opportunity"] = {
            "eligible": False,
            "priority": None,
            "bucket": "earnings_calendar",
            "reason": "",
            "exclusion_reason": "Open position / lifecycle rows are excluded from Daily Opportunity.",
        }

    return check


# ─── Details builders ──────────────────────────────────────────────────────────

def _build_details(row: dict[str, Any]) -> dict[str, Any]:
    """Build details.earnings_calendar for candidate rows."""
    front_iv = _num(row.get("front_iv"))
    back_iv = _num(row.get("back_iv"))
    iv_edge = _num(row.get("iv_edge"))
    return {
        # Earnings event
        "earnings_date": row.get("earnings_date"),
        "earnings_time": row.get("earnings_time"),
        "earnings_source": row.get("earnings_source"),
        "earnings_sources_seen": list(row.get("earnings_sources_seen") or []),
        "earnings_trust_label": str(row.get("earnings_trust_label") or "unknown"),
        "date_confidence": str(row.get("date_confidence") or row.get("earnings_date_confidence") or "unknown"),
        "event_window_status": _event_window_status_label(row),
        # Expiration / structure
        "strategy_definition_id": row.get("strategy_definition_id"),
        "strategy_definition_version": row.get("strategy_definition_version"),
        "structure_template_id": row.get("structure_template_id"),
        "enumeration_policy_version": row.get("enumeration_policy_version"),
        "coverage_accounting": row.get("coverage_accounting"),
        "underlying_price": row.get("underlying_price"),
        "option_type": row.get("option_type"),
        "front_expiration": row.get("front_expiration"),
        "back_expiration": row.get("back_expiration"),
        "front_dte": row.get("front_dte"),
        "back_dte": row.get("back_dte"),
        "expiration_pair_status": _expiration_pair_status(row),
        "entry_window_status": row.get("entry_window_status"),
        "entry_window_open": row.get("entry_window_open"),
        "entry_window_reason": row.get("entry_window_reason"),
        "short_leg_expires_before_earnings": row.get("short_leg_expires_before_earnings"),
        "short_leg_dte_minimum": row.get("short_leg_dte_minimum"),
        "short_leg_time_value_minimum": row.get("short_leg_time_value_minimum"),
        "short_leg_does_not_span_event": row.get("short_leg_does_not_span_event"),
        "current_dte_to_earnings": row.get("current_dte_to_earnings"),
        "ideal_entry_window": row.get("ideal_entry_window"),
        "estimated_entry_date": row.get("estimated_entry_date"),
        "days_until_entry_window": row.get("days_until_entry_window"),
        "minimum_short_dte": row.get("short_leg_dte_minimum"),
        "available_expirations": row.get("available_expirations"),
        "available_pre_earnings_expirations": row.get("available_pre_earnings_expirations"),
        "rejected_expirations": row.get("rejected_expirations"),
        "proposed_short_expiration": row.get("proposed_short_expiration"),
        "proposed_long_expiration": row.get("proposed_long_expiration"),
        "short_leg_status": row.get("entry_window_status"),
        "long_leg_status": "preview" if row.get("proposed_long_expiration") else "not_available",
        "blocker_code": row.get("blocker_code") or row.get("entry_window_status"),
        "blocker_detail": row.get("blocker_detail") or row.get("entry_window_reason"),
        # Strike
        "strike": row.get("strike"),
        "strike_selection_status": "not_evaluated",
        "moneyness": "not_calculated",
        "moneyness_status": "not_evaluated",
        # IV
        "front_iv": front_iv,
        "back_iv": back_iv,
        "iv_relationship_status": str(row.get("iv_relationship_status") or "unavailable"),
        "iv_edge_label": _iv_edge_label(iv_edge),
        # Pricing
        "estimated_debit": row.get("conservative_debit") or row.get("mid_debit"),
        "estimated_max_risk": row.get("conservative_debit") or row.get("mid_debit"),
        "target_profit_pct": None,
        "stop_loss_pct": None,
        "reward_risk_status": "not_calculated",
        # Liquidity
        "liquidity_status": str(row.get("liquidity_status") or "unknown"),
        "spread_status": str(row.get("spread_status") or "unknown"),
        "open_interest_status": _oi_status(row),
        "volume_status": _vol_status(row),
        # Structure
        "structure_type": "call_calendar",
        "structure_status": str(row.get("structure_status") or row.get("earnings_relation") or "unknown"),
        "calendar_entry_allowed": bool(row.get("calendar_entry_allowed")),
    }


def _build_lifecycle_details(check: dict[str, Any]) -> dict[str, Any]:
    """Build details.earnings_calendar for lifecycle/open-position rows."""
    return {
        # Earnings event context
        "earnings_date": check.get("earnings_date"),
        "earnings_time": check.get("earnings_session"),
        "earnings_source": None,
        "earnings_sources_seen": [],
        "earnings_trust_label": "not_applicable",
        "date_confidence": "not_applicable",
        "event_window_status": "open_position",
        # Expiration / structure
        "strategy_definition_id": check.get("strategy_definition_id"),
        "strategy_definition_version": check.get("strategy_definition_version"),
        "structure_template_id": check.get("structure_template_id"),
        "enumeration_policy_version": check.get("enumeration_policy_version"),
        "underlying_price": check.get("underlying_price"),
        "option_type": check.get("option_type"),
        "front_expiration": check.get("front_expiration"),
        "back_expiration": check.get("back_expiration"),
        "front_dte": check.get("front_dte"),
        "back_dte": check.get("back_dte"),
        "expiration_pair_status": "open",
        # Strike
        "strike": check.get("strike"),
        "strike_selection_status": "open_position",
        "moneyness": check.get("short_leg_moneyness_pct"),
        "moneyness_status": _moneyness_status_from_check(check),
        # IV
        "front_iv": None,
        "back_iv": None,
        "iv_relationship_status": "not_evaluated",
        "iv_edge_label": "not_evaluated",
        # Pricing
        "estimated_debit": check.get("current_mid_debit"),
        "estimated_max_risk": check.get("entry_debit_estimate"),
        "target_profit_pct": check.get("target_profit_pct"),
        "stop_loss_pct": check.get("max_loss_pct"),
        "reward_risk_status": "not_calculated",
        # Liquidity (not evaluated for open positions)
        "liquidity_status": "not_evaluated",
        "spread_status": "not_evaluated",
        "open_interest_status": "not_evaluated",
        "volume_status": "not_evaluated",
        # Structure
        "structure_type": "call_calendar",
        "structure_status": "open",
        "calendar_entry_allowed": False,
        # P&L and assignment
        "pnl_pct": check.get("estimated_pnl_pct"),
        "assignment_risk": check.get("assignment_risk_level"),
        "assignment_risk_reason": (list(check.get("assignment_risk_reasons") or []) + [None])[0],
        "current_debit": check.get("current_mid_debit"),
        "entry_debit": check.get("entry_debit_estimate"),
    }


# ─── Gate group builders ───────────────────────────────────────────────────────

def _build_candidate_gate_groups(row: dict[str, Any]) -> dict[str, Any]:
    """Build nested gate groups for an Earnings Calendar candidate row."""
    trust = str(row.get("earnings_trust_label") or "unknown")
    date_conf = str(row.get("date_confidence") or "unknown")
    relation = str(row.get("earnings_relation") or row.get("structure_status") or "unknown")
    conflict = bool(row.get("date_conflict") or row.get("earnings_source_conflict"))
    sources_seen = list(row.get("earnings_sources_seen") or [])
    do_eligible = bool(row.get("daily_opportunity_eligible"))
    calendar_entry_allowed = bool(row.get("calendar_entry_allowed"))

    has_earnings = bool(row.get("earnings_date"))
    has_quote = row.get("underlying_price") is not None
    has_chain = row.get("front_expiration") is not None or row.get("front_iv") is not None

    trust_status, trust_reason = _trust_gate_status(trust, date_conf)
    pair_status, pair_reason = _pair_gate_status(relation)
    ev_status = _event_window_gate_status(relation)
    entry_status = str(row.get("entry_window_status") or "")
    iv_rel = str(row.get("iv_relationship_status") or "unavailable")
    iv_status = _iv_gate_status(iv_rel)
    front_iv = _num(row.get("front_iv"))
    back_iv = _num(row.get("back_iv"))
    max_spread = _num(row.get("max_leg_spread_pct"))
    min_oi = row.get("min_leg_open_interest")
    min_vol = row.get("min_leg_volume")
    est_debit = row.get("conservative_debit") or row.get("mid_debit")
    debit_ok = str(row.get("debit_status") or "unknown") == "pass"

    data_group = {
        "quote": _gate(
            label="Underlying quote",
            status="pass" if has_quote else "unknown",
            reason="Underlying price available." if has_quote else "Underlying price unavailable.",
            custom={"underlying_price": row.get("underlying_price")},
        ),
        "options_chain": _gate(
            label="Options chain",
            status="pass" if has_chain else "unknown",
            reason="Option chain data available." if has_chain else "Option chain data unavailable.",
            custom={"front_expiration": row.get("front_expiration"), "back_expiration": row.get("back_expiration")},
        ),
        "earnings_event": _gate(
            label="Earnings event",
            status="pass" if has_earnings else "fail",
            reason="Earnings date available." if has_earnings else "No earnings date found.",
            custom={"earnings_date": row.get("earnings_date"), "earnings_source": row.get("earnings_source")},
        ),
        "underlying_price": _gate(
            label="Underlying price",
            status="pass" if has_quote else "unknown",
            reason=(f"Price: {row['underlying_price']}" if has_quote else "Underlying price unavailable."),
            custom={"underlying_price": row.get("underlying_price")},
        ),
    }

    event_group = {
        "earnings_date_available": _gate(
            label="Earnings date available",
            status="pass" if has_earnings else "fail",
            reason="Earnings date confirmed." if has_earnings else "Earnings date missing.",
            custom={"earnings_date": row.get("earnings_date")},
        ),
        "earnings_source_quality": _gate(
            label="Earnings source quality",
            status=trust_status,
            reason=trust_reason,
            blocking=(trust_status == "fail"),
            custom={"sources_seen": sources_seen, "earnings_trust_label": trust},
        ),
        "earnings_conflict": _gate(
            label="Earnings date conflict",
            status="fail" if conflict else "pass",
            reason=("Earnings date disputed between providers." if conflict else "No provider date conflict."),
            blocking=conflict,
            custom={"date_conflict": conflict, "sources_seen": sources_seen},
        ),
        "event_window": _gate(
            label="Event window",
            status=ev_status,
            reason=_event_window_reason(relation),
            custom={"earnings_relation": relation},
        ),
        "calendar_entry_window": _gate(
            label="Calendar entry window",
            status=_calendar_entry_window_gate_status(entry_status),
            reason=str(row.get("entry_window_reason") or entry_status or "Entry window not evaluated."),
            blocking=entry_status in {
                "ENTRY_WINDOW_CLOSED", "NO_PRE_EARNINGS_SHORT_EXPIRY",
                "SHORT_LEG_SPANS_EARNINGS", "SHORT_DTE_TOO_LOW", "FRONT_LEG_TOO_DECAYED",
                "DATE_CONFLICT_REVIEW",
            },
            custom={
                "entry_window_status": row.get("entry_window_status"),
                "entry_window_open": row.get("entry_window_open"),
                "short_leg_dte_minimum": row.get("short_leg_dte_minimum"),
                "entry_window_front_dte": row.get("entry_window_front_dte"),
            },
        ),
    }

    setup_group = {
        "expiration_pair": _gate(
            label="Expiration pair",
            status=pair_status,
            reason=pair_reason,
            custom={
                "earnings_relation": relation,
                "front_expiration": row.get("front_expiration"),
                "back_expiration": row.get("back_expiration"),
            },
        ),
        "strike_selection": _gate(
            label="Strike selection",
            status="unknown",
            reason="Strike selection not evaluated in this version.",
            custom={"strike": row.get("strike")},
            blocking=False,
        ),
        "moneyness": _gate(
            label="Moneyness",
            status="unknown",
            reason="Moneyness not calculated in this version.",
            custom={"strike": row.get("strike"), "underlying_price": row.get("underlying_price")},
            blocking=False,
        ),
        "iv_relationship": _gate(
            label="IV relationship",
            status=iv_status,
            reason=_iv_reason(iv_rel, row.get("iv_edge")),
            custom={
                "iv_relationship_status": iv_rel,
                "front_iv": front_iv,
                "back_iv": back_iv,
                "iv_edge": row.get("iv_edge"),
            },
        ),
    }

    structure_group = {
        "calendar_structure": _gate(
            label="Calendar structure",
            status=pair_status,
            reason=f"Structure status: {relation}.",
            custom={"structure_status": relation},
        ),
        "legs_complete": _gate(
            label="Legs complete",
            status=("pass" if (row.get("front_expiration") and row.get("back_expiration")) else "unknown"),
            reason=("Both legs identified." if (row.get("front_expiration") and row.get("back_expiration")) else "Expiration data incomplete."),
            custom={"front_expiration": row.get("front_expiration"), "back_expiration": row.get("back_expiration")},
        ),
        "estimated_debit": _gate(
            label="Estimated debit",
            status=("pass" if debit_ok else ("watch" if est_debit is None else "fail")),
            reason=(f"Debit: {est_debit}" if est_debit is not None else "Debit not available."),
            custom={"estimated_debit": est_debit, "debit_pct_underlying": row.get("debit_pct_underlying")},
        ),
    }

    risk_group = {
        "max_debit": _gate(
            label="Max debit check",
            status=("pass" if debit_ok else ("unknown" if row.get("debit_pct_underlying") is None else "fail")),
            reason="Debit within limit." if debit_ok else "Debit over threshold or unknown.",
            custom={"debit_pct_underlying": row.get("debit_pct_underlying")},
        ),
        "assignment": _gate(
            label="Assignment risk",
            status="unknown",
            reason="Assignment risk not evaluated for candidate rows.",
            custom={},
            blocking=False,
        ),
        "event_gap": _gate(
            label="Event gap risk",
            status=_event_gap_status(relation),
            reason=_event_window_reason(relation),
            custom={"earnings_relation": relation},
        ),
        "account_guardrail": _gate(
            label="Account guardrail",
            status="unknown",
            reason="Account data not evaluated at this layer.",
            custom={},
            blocking=False,
        ),
    }

    liquidity_group = {
        "bid_ask_spread": _gate(
            label="Bid/ask spread",
            status=("pass" if str(row.get("spread_status") or "") == "pass" else ("unknown" if max_spread is None else "fail")),
            reason=(f"Max leg spread: {max_spread:.1f}%." if max_spread is not None else "Spread data unavailable."),
            custom={"max_leg_spread_pct": max_spread},
        ),
        "open_interest": _gate(
            label="Open interest",
            status=("pass" if (min_oi is not None and min_oi >= 10) else ("unknown" if min_oi is None else "watch")),
            reason=(f"Min leg OI: {min_oi}." if min_oi is not None else "Open interest data unavailable."),
            custom={"min_leg_open_interest": min_oi},
        ),
        "volume": _gate(
            label="Volume",
            status=("pass" if (min_vol is not None and min_vol >= 5) else ("unknown" if min_vol is None else "watch")),
            reason=(f"Min leg volume: {min_vol}." if min_vol is not None else "Volume data unavailable."),
            custom={"min_leg_volume": min_vol},
        ),
    }

    daily_opp_group = {
        "eligible": _gate(
            label="Daily Opportunity eligible",
            status="pass" if do_eligible else "fail",
            reason=("Eligible for Daily Opportunity." if do_eligible else "Not eligible for Daily Opportunity."),
            blocking=False,
            custom={"calendar_entry_allowed": calendar_entry_allowed, "action": str(row.get("action") or "")},
        ),
    }

    return {
        "data": data_group,
        "event": event_group,
        "setup": setup_group,
        "structure": structure_group,
        "risk": risk_group,
        "liquidity": liquidity_group,
        "daily_opportunity": daily_opp_group,
    }


def _build_lifecycle_gate_groups(check: dict[str, Any]) -> dict[str, Any]:
    """Build gate groups for lifecycle/open-position rows (simplified)."""
    assignment_risk = str(check.get("assignment_risk_level") or "unknown")
    pnl_pct = _num(check.get("estimated_pnl_pct"))
    short_itm = check.get("short_leg_itm")

    risk_group = {
        "assignment": _gate(
            label="Assignment risk",
            status=_assignment_gate_status(assignment_risk),
            reason=f"Assignment risk level: {assignment_risk}.",
            blocking=(assignment_risk in ("High", "Elevated")),
            custom={"assignment_risk_level": assignment_risk, "short_leg_itm": short_itm},
        ),
        "event_gap": _gate(
            label="Event gap risk",
            status="unknown",
            reason="Event gap not evaluated for open positions.",
            custom={},
            blocking=False,
        ),
        "account_guardrail": _gate(
            label="Account guardrail",
            status="unknown",
            reason="Account data not evaluated at this layer.",
            custom={},
            blocking=False,
        ),
        "max_debit": _gate(
            label="P&L status",
            status=("pass" if (pnl_pct is not None and pnl_pct >= 0) else ("watch" if pnl_pct is None else "fail")),
            reason=(f"Estimated P&L: {pnl_pct:.1f}%." if pnl_pct is not None else "P&L unavailable."),
            custom={"pnl_pct": pnl_pct},
            blocking=False,
        ),
    }

    daily_opp_group = {
        "eligible": _gate(
            label="Daily Opportunity eligible",
            status="skipped",
            reason="Open position / lifecycle rows are excluded from Daily Opportunity.",
            blocking=False,
            custom={},
        ),
    }

    return {
        "risk": risk_group,
        "daily_opportunity": daily_opp_group,
    }


# ─── Display helpers ───────────────────────────────────────────────────────────

def _candidate_detail_lines(row: dict[str, Any]) -> list[str]:
    lines = []
    if row.get("earnings_date"):
        lines.append(f"Earnings: {row['earnings_date']}")
    if row.get("front_expiration") and row.get("back_expiration"):
        lines.append(f"Expirations: {row['front_expiration']} / {row['back_expiration']}")
    if row.get("strike") is not None:
        lines.append(f"Strike: {row['strike']}")
    iv_rel = str(row.get("iv_relationship_status") or "")
    if iv_rel and iv_rel not in ("unavailable", "unknown"):
        lines.append(f"IV: {iv_rel}")
    est_debit = row.get("conservative_debit") or row.get("mid_debit")
    if est_debit is not None:
        lines.append(f"Est. debit: {est_debit:.2f}")
    return lines[:5]


def _lifecycle_detail_lines(check: dict[str, Any]) -> list[str]:
    lines = []
    if check.get("front_expiration") and check.get("back_expiration"):
        lines.append(f"Expirations: {check['front_expiration']} / {check['back_expiration']}")
    if check.get("strike") is not None:
        lines.append(f"Strike: {check['strike']}")
    if check.get("current_mid_debit") is not None:
        lines.append(f"Current debit: {check['current_mid_debit']:.2f}")
    pnl = _num(check.get("estimated_pnl_pct"))
    if pnl is not None:
        lines.append(f"Est. P&L: {pnl:.1f}%")
    return lines[:5]


# ─── Row type inference ────────────────────────────────────────────────────────

def _infer_candidate_row_type(action_upper: str) -> str:
    if action_upper in (
        "EARNINGS CALENDAR CANDIDATE",
        "URGENT REVIEW / EARNINGS SOON",
        "URGENT REVIEW / TIMING-SENSITIVE",
    ):
        return "new_candidate"
    if (
        "AVOID" in action_upper
        or "FAIL" in action_upper
        or "NOT AN EARNINGS SETUP" in action_upper
        or "REGULAR CALENDAR ONLY" in action_upper
        or "BAD DATE DATA" in action_upper
    ):
        return "rejected_candidate"
    if "NEAR_MISS" in action_upper or "WATCH" in action_upper or "MANUAL REVIEW" in action_upper:
        return "observation"
    return "observation"


def _infer_lifecycle_row_type(action_upper: str) -> str:
    if any(x in action_upper for x in ("TAKE PROFIT", "CUT", "URGENT REVIEW / EXIT")):
        return "lifecycle_check"
    return "open_position"


# ─── Gate status helpers ───────────────────────────────────────────────────────

def _trust_gate_status(trust: str, date_conf: str) -> tuple[str, str]:
    if trust == "conflict_do_not_trade":
        return "fail", "Earnings date is disputed between providers; trading blocked."
    if trust == "unknown_research_only":
        return "fail", "Earnings date is unknown; research required before trading."
    if trust == "single_source_verify":
        return "watch", "Earnings date from single source only — verify before entry."
    if trust == "multi_source_confirmed":
        return "pass", "Earnings date confirmed from multiple sources."
    return "unknown", f"Earnings trust status: {trust}."


def _pair_gate_status(relation: str) -> tuple[str, str]:
    if relation == "long_leg_captures_earnings":
        return "pass", "Preferred structure: short leg expires before earnings, back leg captures event."
    if relation == "near_miss_expiry_gap":
        return "watch", "Near-miss structure: expiry gap near earnings date."
    if relation in ("missing_expiration",):
        return "unknown", "Expiration data missing; cannot validate structure."
    if relation == "earnings_unknown":
        return "unknown", "Earnings date unknown; cannot validate structure."
    if relation in ("short_leg_spans_earnings", "earnings_on_front_expiration"):
        return "fail", "Short leg spans or coincides with earnings event."
    if relation in ("already_reported", "earnings_after_back_leg", "unclassified"):
        return "fail", f"Structure not valid for earnings calendar: {relation.replace('_', ' ')}."
    return "unknown", f"Structure: {relation}."


def _event_window_gate_status(relation: str) -> str:
    if relation == "long_leg_captures_earnings":
        return "pass"
    if relation in ("near_miss_expiry_gap", "earnings_on_front_expiration", "unclassified"):
        return "watch"
    if relation in ("short_leg_spans_earnings", "already_reported", "earnings_after_back_leg"):
        return "fail"
    return "unknown"


def _calendar_entry_window_gate_status(status: str) -> str:
    if status == "ENTRY_WINDOW_OPEN":
        return "pass"
    if status in {"ENTRY_WINDOW_CLOSING", "MONITOR_PRE_WINDOW", "DATA_NEEDED"}:
        return "watch"
    if status in {
        "ENTRY_WINDOW_CLOSED", "NO_PRE_EARNINGS_SHORT_EXPIRY",
        "SHORT_LEG_SPANS_EARNINGS", "SHORT_DTE_TOO_LOW", "FRONT_LEG_TOO_DECAYED",
        "DATE_CONFLICT_REVIEW",
    }:
        return "fail"
    return "unknown"


def _event_gap_status(relation: str) -> str:
    if relation == "long_leg_captures_earnings":
        return "pass"
    if relation in ("near_miss_expiry_gap",):
        return "watch"
    if relation in ("short_leg_spans_earnings", "earnings_on_front_expiration"):
        return "fail"
    return "unknown"


def _event_window_reason(relation: str) -> str:
    _reasons = {
        "long_leg_captures_earnings": "Back leg captures the earnings event window.",
        "short_leg_spans_earnings": "Short leg spans earnings; this is not the preferred structure.",
        "earnings_on_front_expiration": "Earnings occur on the front expiration date.",
        "near_miss_expiry_gap": "Expiration gap near earnings; manual evaluation recommended.",
        "already_reported": "Earnings are in the past; not an earnings calendar setup.",
        "earnings_after_back_leg": "Earnings are after both legs; not an earnings catalyst setup.",
        "missing_expiration": "Could not determine expiration dates.",
        "earnings_unknown": "Earnings date unknown.",
        "unclassified": "Could not classify earnings/expiration relationship.",
    }
    return _reasons.get(relation, f"Structure: {relation.replace('_', ' ')}.")


def _event_window_status_label(row: dict[str, Any]) -> str:
    rel = str(row.get("earnings_relation") or row.get("structure_status") or "unknown")
    _map = {
        "long_leg_captures_earnings": "captured",
        "short_leg_spans_earnings": "spans_event",
        "earnings_on_front_expiration": "on_front_expiration",
        "near_miss_expiry_gap": "near_miss",
        "already_reported": "already_reported",
        "earnings_after_back_leg": "after_back_leg",
        "missing_expiration": "missing_expiration",
        "earnings_unknown": "unknown",
    }
    return _map.get(rel, rel)


def _expiration_pair_status(row: dict[str, Any]) -> str:
    rel = str(row.get("earnings_relation") or "unknown")
    if rel == "long_leg_captures_earnings":
        return "preferred"
    if rel == "near_miss_expiry_gap":
        return "near_miss"
    if rel == "missing_expiration":
        return "missing"
    if rel == "earnings_unknown":
        return "unknown"
    if rel in ("short_leg_spans_earnings", "earnings_on_front_expiration", "already_reported", "earnings_after_back_leg"):
        return "invalid"
    return "unclassified"


def _iv_edge_label(iv_edge: float | None) -> str:
    if iv_edge is None:
        return "unavailable"
    if iv_edge > 0.02:
        return "favorable"
    if iv_edge >= -0.02:
        return "neutral"
    return "unfavorable"


def _iv_gate_status(iv_rel: str) -> str:
    if iv_rel == "favorable":
        return "pass"
    if iv_rel == "neutral":
        return "watch"
    if iv_rel == "unfavorable":
        return "fail"
    return "unknown"


def _iv_reason(iv_rel: str, iv_edge: Any) -> str:
    edge = _num(iv_edge)
    if iv_rel == "favorable":
        return (f"IV relationship favorable; edge: {edge:.3f}." if edge is not None else "IV relationship favorable.")
    if iv_rel == "neutral":
        return "IV relationship neutral; minimal IV edge."
    if iv_rel == "unfavorable":
        return "IV relationship unfavorable; front IV exceeds back IV."
    return "IV relationship data unavailable."


def _oi_status(row: dict[str, Any]) -> str:
    min_oi = row.get("min_leg_open_interest")
    if min_oi is None:
        return "unknown"
    return "pass" if min_oi >= 10 else "watch"


def _vol_status(row: dict[str, Any]) -> str:
    min_vol = row.get("min_leg_volume")
    if min_vol is None:
        return "unknown"
    return "pass" if min_vol >= 5 else "watch"


def _moneyness_status_from_check(check: dict[str, Any]) -> str:
    short_itm = check.get("short_leg_itm")
    if short_itm is True:
        return "itm"
    if short_itm is False:
        return "otm"
    return "unknown"


def _assignment_gate_status(risk_level: str) -> str:
    if risk_level in ("Low",):
        return "pass"
    if risk_level in ("Moderate",):
        return "watch"
    if risk_level in ("Elevated", "High"):
        return "fail"
    return "unknown"


# ─── Gate dict builder ─────────────────────────────────────────────────────────

def _gate(
    label: str,
    status: str,
    *,
    reason: str = "",
    custom: dict[str, Any] | None = None,
    blocking: bool | None = None,
) -> dict[str, Any]:
    canonical = _canonical_status(status)
    is_blocking = blocking if blocking is not None else (canonical == "fail")
    return {
        "status": canonical,
        "label": label,
        "reason": reason,
        "blocking": is_blocking,
        "custom": custom or {},
    }


def _canonical_status(s: str) -> str:
    clean = str(s or "").lower().strip()
    if clean in ("pass", "ok", "green", "true", "yes", "passed"):
        return "pass"
    if clean in ("watch", "warn", "warning", "yellow"):
        return "watch"
    if clean in ("fail", "failed", "no", "false", "red", "block"):
        return "fail"
    if clean in ("skipped", "skip", "excluded", "not_applicable", "na"):
        return "skipped"
    if clean in ("dry_run", "dry-run"):
        return "dry_run"
    return "unknown"


def _stable_row_id(strategy_id: str, ticker: str, run_id: str) -> str:
    raw = f"{strategy_id}:{ticker}:{run_id}"
    return f"ec:{ticker}:{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
