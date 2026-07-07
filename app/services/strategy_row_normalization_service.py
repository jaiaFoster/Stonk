"""Universal strategy row field normalization for pre-30A readiness (TKT-29.8).

Adds a thin set of normalized fields to every strategy row so all four strategies
expose the same minimal surface before Strategy Unification in 30A.

Pattern: call normalize_strategy_row(row, strategy_id) at the end of each
strategy's row builder. The function mutates `row` in-place and returns it.
"""

from __future__ import annotations

from typing import Any


_DAILY_OPPORTUNITY_REASON: dict[str, str] = {
    "stock_momentum": "Stock-only signal — options execution not applicable.",
    "forward_factor_calendar": "Forward Factor is in signal-only mode — execution gated for all tickers.",
}

_STOCK_ACTION_LABEL: dict[str, str] = {
    "CONSIDER ADDING": "Momentum Pass",
    "ADD ON PULLBACK": "Momentum Pass",
    "WATCH / CONFIRM TREND": "Watch",
    "WATCH / RESEARCH": "Watch",
    "STARTER ONLY / WAIT FOR PULLBACK": "Watch",
    "TACTICAL ONLY / DO NOT CHASE": "Tactical Watch",
    "HOLD / DO NOT ADD": "Tactical Watch",
    "AVOID / WEAK TREND": "Rejected",
    "WATCH / DATA INCOMPLETE": "Watch",
}

_FF_SKIP_STAGES = frozenset({"cap_skip", "budget_skipped", "recent_fail_skip"})
_FF_SKIP_STATES = frozenset({"SKIPPED_DEV_CAP", "SKIPPED_STRATEGY_CAP", "SKIPPED_PROVIDER_BUDGET"})


def normalize_strategy_row(row: dict[str, Any], strategy_id: str) -> dict[str, Any]:
    """Add universal normalized fields to a strategy row in-place. Returns row."""
    row.setdefault("strategy_id", strategy_id)

    if "friendly_verdict" not in row:
        row["friendly_verdict"] = _friendly_verdict(row, strategy_id)

    if "primary_reason" not in row:
        row["primary_reason"] = _primary_reason(row, strategy_id)

    if strategy_id in _DAILY_OPPORTUNITY_REASON:
        row.setdefault("daily_opportunity_reason", _DAILY_OPPORTUNITY_REASON[strategy_id])

    if strategy_id == "forward_factor_calendar":
        row.setdefault("can_enter_daily_opportunity", False)
        row.setdefault("can_trade_live", False)

    if "gates" not in row:
        g = _gates(row, strategy_id)
        if g is not None:
            row["gates"] = g

    return row


# ─── friendly_verdict ──────────────────────────────────────────────────────────


def _friendly_verdict(row: dict[str, Any], strategy_id: str) -> str:
    if strategy_id == "stock_momentum":
        action = str(row.get("action") or "")
        label = _STOCK_ACTION_LABEL.get(action)
        if label:
            return label
        upper = action.upper()
        if "AVOID" in upper or "FAIL" in upper or "WEAK" in upper:
            return "Rejected"
        if "TACTICAL" in upper or "HOLD" in upper:
            return "Tactical Watch"
        if "WATCH" in upper or "CONFIRM" in upper or "RESEARCH" in upper or "STARTER" in upper:
            return "Watch"
        if "CONSIDER" in upper or "ADD" in upper:
            return "Momentum Pass"
        return action or "Unknown"

    if strategy_id == "skew_momentum_vertical":
        verdict = str(row.get("verdict") or "")
        if verdict.startswith("PASS"):
            return "Vertical candidate"
        if verdict.startswith("WATCH"):
            return "Near candidate"
        if verdict.startswith("FAIL"):
            return "Did not qualify"
        return verdict or "Unknown"

    if strategy_id == "forward_factor_calendar":
        stage = str(row.get("ff_candidate_stage") or "")
        data_state = str(row.get("data_state") or "")
        verdict = str(row.get("verdict") or "")
        if stage in _FF_SKIP_STAGES or data_state in _FF_SKIP_STATES:
            return "Skipped by limited scan"
        upper = verdict.upper()
        if "DEV CAP" in upper or "STRATEGY CAP" in upper or "PROVIDER BUDGET" in upper:
            return "Skipped by limited scan"
        if verdict.startswith("PASS"):
            return "Signal candidate"
        if verdict.startswith("WATCH"):
            return "Near candidate"
        if verdict.startswith("FAIL"):
            return "Did not qualify"
        return verdict or "Unknown"

    if strategy_id == "earnings_calendar":
        action = str(row.get("action") or "")
        upper = action.upper()
        if "EARNINGS CALENDAR CANDIDATE" in upper:
            return "Eligible"
        if "URGENT REVIEW" in upper:
            return "Urgent review"
        if "NEAR_MISS" in upper or "REGULAR CALENDAR" in upper:
            return "Watch"
        if "WATCH" in upper and "VERIFY" in upper:
            return "Watch — verify date"
        if "FAIL" in upper or "AVOID" in upper or "NOT AN EARNINGS" in upper:
            return "Did not qualify"
        if "MANUAL REVIEW" in upper:
            return "Manual review"
        return action or "Unknown"

    return str(row.get("verdict") or row.get("action") or "Unknown")


# ─── primary_reason ────────────────────────────────────────────────────────────


def _primary_reason(row: dict[str, Any], strategy_id: str) -> str:
    if strategy_id == "stock_momentum":
        reasons = row.get("reasons") or []
        if reasons:
            return str(reasons[0])
        blockers = row.get("add_blockers") or []
        if blockers:
            return str(blockers[0])
        return str(row.get("action") or "No data available")

    if strategy_id == "skew_momentum_vertical":
        existing = row.get("primary_reason")
        if existing:
            return str(existing)
        return str(row.get("momentum_reason") or row.get("verdict") or "No reason available")

    if strategy_id == "forward_factor_calendar":
        blocker = row.get("primary_blocker")
        if blocker:
            return str(blocker)
        verdict = str(row.get("verdict") or "")
        if verdict.startswith("PASS"):
            ff = row.get("source_forward_factor") or row.get("forward_factor")
            if ff is not None:
                return f"Forward Factor {float(ff):.4f} above threshold."
        return verdict or "No reason available"

    if strategy_id == "earnings_calendar":
        reasons = row.get("reasons") or []
        if reasons:
            return str(reasons[0])
        return str(row.get("action") or "No reason available")

    existing = row.get("primary_reason") or row.get("primary_blocker")
    if existing:
        return str(existing)
    return str(row.get("verdict") or row.get("action") or "No reason available")


# ─── gates ────────────────────────────────────────────────────────────────────


def _gates(row: dict[str, Any], strategy_id: str) -> list[dict[str, Any]] | None:
    if strategy_id == "skew_momentum_vertical":
        reqs = row.get("requirements")
        if isinstance(reqs, list):
            return list(reqs)
        return None

    if strategy_id == "forward_factor_calendar":
        fg = row.get("ff_gates")
        if not isinstance(fg, dict):
            return None
        cheap = bool(fg.get("cheap_eligible"))
        chain = bool(fg.get("chain_approved"))
        sq = bool(fg.get("source_qualified"))
        dm = bool(fg.get("diagnostic_model"))
        sb = bool(fg.get("structure_built"))
        contaminated = bool(fg.get("earnings_contaminated"))
        stage = str(row.get("ff_candidate_stage") or "")
        if stage in _FF_SKIP_STAGES:
            return [
                _gate("Coverage eligibility", "skipped", "Outside limited scan window"),
                _gate("Chain approved", "not_applicable"),
                _gate("Source qualified", "not_applicable"),
                _gate("Diagnostic model", "not_applicable"),
                _gate("Structure built", "not_applicable"),
                _gate("Execution", "dry_run", "Signal-only mode"),
            ]
        return [
            _gate("Coverage eligibility", "pass" if cheap else "fail"),
            _gate("Chain approved", "pass" if chain else ("not_applicable" if not cheap else "fail")),
            _gate("Source qualified",
                  "fail" if contaminated else ("pass" if sq else ("not_applicable" if not chain else "fail")),
                  "Earnings contamination" if contaminated else ""),
            _gate("Diagnostic model",
                  "pass" if dm else ("not_applicable" if not (chain or sq) else "fail")),
            _gate("Structure built",
                  "pass" if sb else ("not_applicable" if not (dm or sq) else "fail")),
            _gate("Execution", "dry_run", "Signal-only mode — trade gated"),
        ]

    if strategy_id == "stock_momentum":
        mm = row.get("market_metrics") or {}
        above50 = mm.get("above_sma_50")
        above200 = mm.get("above_sma_200")
        add_allowed = bool(row.get("add_allowed_boolean"))
        action = str(row.get("action") or "").upper()
        overall = "pass" if add_allowed else ("watch" if "WATCH" in action else "fail")
        blockers = row.get("add_blockers") or []
        gates: list[dict[str, Any]] = [
            _gate("Above 50-day MA", _bool_status(above50)),
            _gate("Above 200-day MA", _bool_status(above200)),
            _gate("Momentum verdict", overall, str(row.get("action") or "")),
        ]
        if blockers:
            gates.append(_gate("Entry blockers", "fail", str(blockers[0])))
        return gates

    if strategy_id == "earnings_calendar":
        relation = str(row.get("earnings_relation") or "unknown")
        trust_label = str(row.get("earnings_trust_label") or "unknown")
        calendar_entry_allowed = bool(row.get("calendar_entry_allowed"))
        action = str(row.get("action") or "").upper()
        gates = []
        # Expiration relationship gate
        if relation == "long_leg_captures_earnings":
            gates.append(_gate("Expiration pair", "pass", "Preferred structure"))
        elif relation in ("missing_expiration", "already_reported", "earnings_after_back_leg",
                          "short_leg_spans_earnings"):
            gates.append(_gate("Expiration pair", "fail", relation.replace("_", " ")))
        elif relation in ("near_miss_expiry_gap", "earnings_on_front_expiration"):
            gates.append(_gate("Expiration pair", "watch", relation.replace("_", " ")))
        else:
            gates.append(_gate("Expiration pair", "unknown", relation))
        # Earnings trust gate
        if trust_label == "conflict_do_not_trade":
            gates.append(_gate("Earnings date trust", "fail", "Conflict — do not trade"))
        elif trust_label == "single_source_verify":
            gates.append(_gate("Earnings date trust", "watch", "Single-source lower confidence"))
        elif trust_label in ("confirmed", "multi_source", "multi_source_confirmed"):
            gates.append(_gate("Earnings date trust", "pass", "Confirmed earnings date"))
        else:
            gates.append(_gate("Earnings date trust", "unknown", trust_label))
        # Calendar entry gate
        if calendar_entry_allowed:
            gates.append(_gate("Calendar entry", "pass"))
        elif "URGENT" in action or "NEAR_MISS" in action or "WATCH" in action:
            gates.append(_gate("Calendar entry", "watch"))
        else:
            gates.append(_gate("Calendar entry", "fail"))
        return gates

    return None


def _gate(name: str, status: str, detail: str = "") -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail}


def _bool_status(val: Any) -> str:
    if val is True:
        return "pass"
    if val is False:
        return "fail"
    return "unknown"
