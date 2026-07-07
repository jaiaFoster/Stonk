"""Universal row enrichment for production Skew Momentum Vertical — ASA Patch 30D.

build_skew_momentum_vertical_universal_row() adds universal fields to vertical
spread candidate rows. Works in-place (also returns the row). Idempotent.

All legacy fields are preserved untouched so existing consumers continue to work.

CAVEMAN MODE: read-only, no broker writes, no provider calls.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.strategies.schema import SCHEMA_VERSION, VALID_ROW_TYPES


def build_skew_momentum_vertical_universal_row(
    row: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Enrich a skew_momentum_vertical candidate row with universal fields.

    Works in-place (also returns the row). Idempotent — safe to call twice.
    """
    if row.get("schema_version") == SCHEMA_VERSION:
        return row

    ticker = str(row.get("ticker") or "unknown").upper().strip()
    verdict = str(row.get("verdict") or "").upper()
    score = float(row.get("score") or 0)

    # ── strategy_id ───────────────────────────────────────────────────────────
    row.setdefault("strategy_id", "skew_momentum_vertical")

    # ── row_type ──────────────────────────────────────────────────────────────
    if "row_type" not in row:
        row["row_type"] = _infer_row_type(verdict)

    # ── schema_version ────────────────────────────────────────────────────────
    row["schema_version"] = SCHEMA_VERSION

    # ── row_id ────────────────────────────────────────────────────────────────
    if "row_id" not in row:
        _run = str(run_id or "")
        row["row_id"] = _stable_row_id("skew_momentum_vertical", ticker, _run)

    # ── details.skew_momentum_vertical ────────────────────────────────────────
    if "details" not in row:
        row["details"] = {"skew_momentum_vertical": _build_details(row)}

    # ── display ───────────────────────────────────────────────────────────────
    if "display" not in row:
        friendly = str(row.get("friendly_verdict") or verdict or "")
        primary = str(row.get("primary_reason") or row.get("momentum_reason") or "")
        row["display"] = {
            "title": ticker,
            "subtitle": "Skew Momentum Vertical",
            "badge": friendly,
            "sort_key": score,
            "public_reason": primary,
            "detail_lines": _detail_lines(row),
        }

    # ── gate_groups ───────────────────────────────────────────────────────────
    if "gate_groups" not in row:
        row["gate_groups"] = _build_gate_groups(row)

    # ── daily_opportunity dict ────────────────────────────────────────────────
    if "daily_opportunity" not in row:
        do_eligible = bool(row.get("daily_opportunity_eligible"))
        do_reason = str(row.get("daily_opportunity_reason") or "")
        row["daily_opportunity"] = {
            "eligible": do_eligible,
            "priority": round(score, 1) if do_eligible else None,
            "bucket": "skew_momentum_vertical",
            "reason": do_reason if do_eligible else "",
            "exclusion_reason": "" if do_eligible else do_reason,
        }

    return row


# ─── Row type inference ───────────────────────────────────────────────────────

def _infer_row_type(verdict_upper: str) -> str:
    if verdict_upper.startswith("PASS"):
        return "new_candidate"
    if verdict_upper.startswith("FAIL"):
        return "rejected_candidate"
    if verdict_upper.startswith("WATCH"):
        return "observation"
    return "observation"


# ─── details.skew_momentum_vertical ──────────────────────────────────────────

_RAW_EXCLUDED_FIELDS = frozenset({
    "long_leg", "short_leg", "payload", "requirements",
    "risk_notes", "provider_notes",
})


def _build_details(row: dict[str, Any]) -> dict[str, Any]:
    """Build compact details.skew_momentum_vertical block.

    Only scalar / small values — never raw option legs or provider blobs.
    """
    spread = row.get("possible_spread") if isinstance(row.get("possible_spread"), dict) else {}
    ranking = row.get("ranking") if isinstance(row.get("ranking"), dict) else {}
    return {
        # Direction + momentum
        "direction": row.get("direction"),
        "option_type": spread.get("option_type") or row.get("option_type"),
        "momentum_confirmed": bool(row.get("momentum_confirmed")),
        "momentum_score": _safe_float(row.get("momentum_score")),
        "momentum_status": str(row.get("momentum_status") or ""),
        # Skew
        "skew_pass": bool(row.get("skew_pass")),
        "short_iv_edge": _safe_float(row.get("short_iv_edge")),
        "short_premium_financing_pct": _safe_float(row.get("short_premium_financing_pct")),
        "adjusted_skew_score": _safe_float(row.get("adjusted_skew_score")),
        "skew_gap_to_pass": _safe_float(row.get("skew_gap_to_pass")),
        "atm_iv": _safe_float(row.get("atm_iv")),
        # Position structure
        "expiration": spread.get("expiration") or row.get("expiration"),
        "dte": row.get("dte"),
        "long_strike": _safe_float(spread.get("long_strike")),
        "short_strike": _safe_float(spread.get("short_strike")),
        "width": _safe_float(spread.get("width")),
        "underlying_price": _safe_float(row.get("underlying_price")),
        "breakeven": _safe_float(row.get("breakeven")),
        # Debit / risk / payoff
        "conservative_debit": _safe_float(row.get("conservative_debit")),
        "mid_debit": _safe_float(row.get("mid_debit")),
        "max_risk": _safe_float(row.get("max_risk")),
        "max_profit": _safe_float(row.get("max_profit")),
        "reward_risk": _safe_float(row.get("reward_risk")),
        "debit_pct_of_width": _safe_float(row.get("debit_pct_of_width")),
        "account_risk_pct": _safe_float(row.get("account_risk_pct")),
        # Liquidity
        "liquidity_pass": bool(row.get("liquidity_pass")),
        "long_leg_spread_pct": _safe_float(row.get("long_leg_spread_pct")),
        "short_leg_spread_pct": _safe_float(row.get("short_leg_spread_pct")),
        "spread_market_width_pct": _safe_float(row.get("spread_market_width_pct")),
        # Earnings risk
        "event_risk": bool(row.get("event_risk")),
        "event_risk_allowed": bool(row.get("event_risk_allowed")),
        "earnings_trust_label": str(row.get("earnings_trust_label") or ""),
        # Stale structure
        "stale_structure": bool(row.get("stale_structure")),
        # Ranking sub-scores (compact — just the scalars)
        "ranking_total_score": _safe_float(ranking.get("total_score")),
        "ranking_momentum_score": _safe_float(ranking.get("momentum_score")),
        "ranking_skew_score": _safe_float(ranking.get("skew_score")),
        "ranking_liquidity_score": _safe_float(ranking.get("liquidity_score")),
        "ranking_payoff_score": _safe_float(ranking.get("payoff_score")),
    }


# ─── Gate groups ──────────────────────────────────────────────────────────────

def _build_gate_groups(row: dict[str, Any]) -> dict[str, Any]:
    verdict = str(row.get("verdict") or "").upper()
    momentum_confirmed = bool(row.get("momentum_confirmed"))
    skew_pass = bool(row.get("skew_pass"))
    liquidity_pass = bool(row.get("liquidity_pass"))
    data_quality_pass = bool(row.get("data_quality_pass"))
    event_risk = bool(row.get("event_risk"))
    event_risk_allowed = bool(row.get("event_risk_allowed"))
    earnings_trust_label = str(row.get("earnings_trust_label") or "")
    rr = _safe_float(row.get("reward_risk"))
    do_eligible = bool(row.get("daily_opportunity_eligible"))
    stale_structure = bool(row.get("stale_structure"))

    # ── data ──────────────────────────────────────────────────────────────────
    data_grp: dict[str, Any] = {
        "quote": _gate(
            "pass" if data_quality_pass else "fail",
            "Quote Data",
            "Option leg quotes present." if data_quality_pass else "IV or quote data missing.",
            blocking=not data_quality_pass,
        ),
        "options_chain": _gate(
            "pass" if row.get("possible_spread") else "fail",
            "Options Chain",
            "Valid options chain with eligible strikes." if row.get("possible_spread") else "No valid same-expiration vertical found.",
            blocking=not bool(row.get("possible_spread")),
        ),
        "underlying_price": _gate(
            "pass" if _safe_float(row.get("underlying_price")) else "fail",
            "Underlying Price",
            "Live underlying quote available." if _safe_float(row.get("underlying_price")) else "Underlying price unavailable.",
            blocking=not bool(_safe_float(row.get("underlying_price"))),
        ),
    }

    # ── setup (momentum) ──────────────────────────────────────────────────────
    direction = str(row.get("direction") or "")
    setup_grp: dict[str, Any] = {
        "momentum": _gate(
            "pass" if momentum_confirmed else "fail",
            "Momentum",
            f"Directional momentum confirmed ({direction})." if momentum_confirmed else str(row.get("momentum_reason") or "Momentum not confirmed."),
            blocking=not momentum_confirmed,
        ),
        "data_quality": _gate(
            "pass" if data_quality_pass else "watch",
            "Data Quality",
            "All required data fields present." if data_quality_pass else "Some data fields are approximate.",
            blocking=False,
        ),
    }

    # ── volatility (skew) ─────────────────────────────────────────────────────
    skew_score = _safe_float(row.get("adjusted_skew_score"))
    iv_edge = _safe_float(row.get("short_iv_edge"))
    volatility_grp: dict[str, Any] = {
        "skew_richness": _gate(
            "pass" if skew_pass else "fail",
            "Skew Richness",
            f"Adjusted skew score {skew_score} meets threshold." if skew_pass else f"Skew score {skew_score} is below threshold.",
            blocking=not skew_pass,
            custom={"adjusted_skew_score": skew_score, "short_iv_edge": iv_edge},
        ),
        "iv_edge": _gate(
            "pass" if (iv_edge or 0) > 0 else "watch",
            "IV Edge",
            f"Short-wing IV edge {iv_edge:.4f}." if iv_edge is not None else "IV edge not available.",
            blocking=False,
            custom={"short_iv_edge": iv_edge},
        ),
    }

    # ── structure ─────────────────────────────────────────────────────────────
    spread = row.get("possible_spread") if isinstance(row.get("possible_spread"), dict) else {}
    structure_status = str(row.get("structure_status") or "")
    has_spread = bool(spread)
    stale_status = "watch" if stale_structure else "pass"
    structure_grp: dict[str, Any] = {
        "vertical_legs": _gate(
            "pass" if has_spread else "fail",
            "Vertical Legs",
            "Long and short legs identified." if has_spread else "No valid vertical identified.",
            blocking=not has_spread,
        ),
        "structure_status": _gate(
            "pass" if verdict.startswith("PASS") else ("watch" if verdict.startswith("WATCH") else "fail"),
            "Structure Status",
            f"Overall structure: {structure_status}.",
            blocking=verdict.startswith("FAIL"),
            custom={"structure_status": structure_status},
        ),
        "stale_structure": _gate(
            stale_status,
            "Structure Freshness",
            str(row.get("stale_structure_note") or "Structure is current.") if stale_structure else "Structure is current.",
            blocking=False,
        ),
    }

    # ── risk ──────────────────────────────────────────────────────────────────
    rr_pass = (rr is not None and rr >= 0.5)
    risk_grp: dict[str, Any] = {
        "reward_risk": _gate(
            "pass" if rr_pass else "fail",
            "Reward/Risk",
            f"Conservative reward/risk {rr:.2f}." if rr is not None else "Reward/risk not available.",
            blocking=not rr_pass,
            custom={"reward_risk": rr},
        ),
        "account_guardrail": _gate(
            "pass",
            "Account Guardrail",
            "Defined-risk debit spread; max loss is capped.",
            blocking=False,
        ),
    }
    acc_risk = _safe_float(row.get("account_risk_pct"))
    if acc_risk is not None:
        risk_grp["account_risk_pct"] = _gate(
            "pass" if acc_risk < 5.0 else "watch",
            "Account Risk %",
            f"Max risk is {acc_risk:.2f}% of estimated account value.",
            blocking=False,
            custom={"account_risk_pct": acc_risk},
        )

    # ── liquidity ─────────────────────────────────────────────────────────────
    long_spread_pct = _safe_float(row.get("long_leg_spread_pct"))
    short_spread_pct = _safe_float(row.get("short_leg_spread_pct"))
    liquidity_grp: dict[str, Any] = {
        "bid_ask_spread": _gate(
            "pass" if liquidity_pass else "fail",
            "Bid/Ask Spread",
            f"Leg spreads {long_spread_pct}% / {short_spread_pct}%." if liquidity_pass else f"Bid/ask spread too wide: {long_spread_pct}% / {short_spread_pct}%.",
            blocking=not liquidity_pass,
            custom={
                "long_leg_spread_pct": long_spread_pct,
                "short_leg_spread_pct": short_spread_pct,
            },
        ),
    }

    # ── event (earnings risk) ─────────────────────────────────────────────────
    if earnings_trust_label == "conflict_do_not_trade":
        event_gate_status = "fail"
        event_gate_msg = "Earnings date conflict — do not trade."
        event_blocking = True
    elif event_risk and not event_risk_allowed:
        event_gate_status = "watch"
        event_gate_msg = "Expiration overlaps earnings window — risk present."
        event_blocking = False
    elif event_risk and event_risk_allowed:
        event_gate_status = "watch"
        event_gate_msg = "Earnings event risk present but allowed by config."
        event_blocking = False
    else:
        event_gate_status = "pass"
        event_gate_msg = "No earnings event risk in position window."
        event_blocking = False

    event_grp: dict[str, Any] = {
        "earnings_risk": _gate(
            event_gate_status,
            "Earnings Event Risk",
            event_gate_msg,
            blocking=event_blocking,
            custom={
                "event_risk": event_risk,
                "earnings_trust_label": earnings_trust_label,
            },
        ),
    }

    # ── daily_opportunity ─────────────────────────────────────────────────────
    do_grp: dict[str, Any] = {
        "eligible": _gate(
            "pass" if do_eligible else "fail",
            "Daily Opportunity",
            "Eligible for Daily Opportunity." if do_eligible else "Not eligible for Daily Opportunity.",
            blocking=False,
        ),
    }

    return {
        "data": data_grp,
        "setup": setup_grp,
        "volatility": volatility_grp,
        "structure": structure_grp,
        "risk": risk_grp,
        "liquidity": liquidity_grp,
        "event": event_grp,
        "daily_opportunity": do_grp,
    }


# ─── Display detail lines ─────────────────────────────────────────────────────

def _detail_lines(row: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    verdict = str(row.get("verdict") or "")
    spread = row.get("possible_spread") if isinstance(row.get("possible_spread"), dict) else {}
    direction = str(row.get("direction") or "")
    if direction:
        lines.append(f"Direction: {direction.title()}")
    if spread.get("expiration"):
        dte = row.get("dte")
        exp_str = spread["expiration"]
        lines.append(f"Expiration: {exp_str}" + (f" ({dte} DTE)" if dte else ""))
    if spread.get("long_strike") and spread.get("short_strike"):
        lines.append(f"Spread: {spread['long_strike']}/{spread['short_strike']} {spread.get('option_type', '').upper()}")
    cd = _safe_float(row.get("conservative_debit"))
    if cd is not None:
        lines.append(f"Debit: ${cd:.2f}")
    rr = _safe_float(row.get("reward_risk"))
    if rr is not None:
        lines.append(f"Reward/Risk: {rr:.2f}x")
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


def _stable_row_id(strategy_id: str, ticker: str, run_id: str) -> str:
    raw = f"{strategy_id}:{ticker}:{run_id}"
    digest = hashlib.sha1(raw.encode()).hexdigest()
    return f"smv:{ticker}:{digest[:8]}"


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
