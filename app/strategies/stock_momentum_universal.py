"""Universal row enrichment for production Stock Momentum — ASA Patch 30B.

build_stock_momentum_universal_row() adds the universal fields that distinguish
a "30B universal row" from a plain legacy row:

  row_type        — one of VALID_ROW_TYPES
  schema_version  — 30A.v2
  row_id          — stable key for this ticker+strategy within a run
  details         — {stock_momentum: {…}} namespace
  display         — {title, subtitle, badge, sort_key, public_reason, detail_lines}
  gate_groups     — nested {group: {gate: {status, label, reason, custom}}} dict
  daily_opportunity — expanded dict (in addition to existing daily_opportunity_eligible bool)

All legacy fields (action, score, gates flat list, etc.) are preserved untouched
so existing consumers continue to work.

CAVEMAN MODE: This module is read-only. No broker writes, no provider calls.
"""

from __future__ import annotations

import hashlib
from typing import Any

from app.strategies.schema import SCHEMA_VERSION, VALID_ROW_TYPES


def build_stock_momentum_universal_row(
    row: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Enrich an already-normalized stock_momentum row with universal fields.

    Works in-place (also returns the row). Idempotent — safe to call twice.
    """
    if row.get("schema_version") == SCHEMA_VERSION:
        return row  # already universalized

    mm = row.get("market_metrics") or {}
    ticker = str(row.get("ticker") or "unknown").upper().strip()
    action = str(row.get("action") or "").upper()
    score = float(row.get("score") or row.get("momentum_score") or 0)

    # ── row_type ──────────────────────────────────────────────────────────────
    if "row_type" not in row:
        row["row_type"] = _infer_row_type(action)

    # ── schema_version ────────────────────────────────────────────────────────
    row["schema_version"] = SCHEMA_VERSION

    # ── row_id ────────────────────────────────────────────────────────────────
    if "row_id" not in row:
        _run = str(run_id or "")
        row["row_id"] = _stable_row_id("stock_momentum", ticker, _run)

    # ── details.stock_momentum ────────────────────────────────────────────────
    if "details" not in row:
        already_held = str(row.get("portfolio_status") or "").lower().startswith("already")
        row["details"] = {
            "stock_momentum": {
                "momentum_score": row.get("momentum_score") or row.get("score"),
                "relative_strength": row.get("relative_strength"),
                "trend_status": row.get("trend_status"),
                "volume_status": row.get("volume_status"),
                "price_action_status": row.get("price_action_status"),
                "risk_status": row.get("risk_status"),
                "watchlist_source": "portfolio" if already_held else "watchlist",
                "already_held": already_held,
                "current_price": mm.get("current_price"),
                "benchmark": "QQQ",
                "lookback_window": "3m/6m/12m",
                "entry_quality": row.get("entry_quality"),
                "add_allowed": row.get("add_allowed_boolean"),
                "extension_vs_50d_pct": row.get("extension_vs_50d_pct"),
                "realized_volatility_30d_pct": row.get("realized_volatility_30d_pct"),
            }
        }

    # ── display ───────────────────────────────────────────────────────────────
    if "display" not in row:
        friendly = str(row.get("friendly_verdict") or row.get("action") or "")
        primary = str(row.get("primary_reason") or "")
        row["display"] = {
            "title": ticker,
            "subtitle": "Stock Momentum",
            "badge": friendly,
            "sort_key": score,
            "public_reason": primary,
            "detail_lines": list(row.get("reasons") or [])[:3],
        }

    # ── gate_groups ───────────────────────────────────────────────────────────
    if "gate_groups" not in row:
        row["gate_groups"] = _build_gate_groups(row, mm)

    # ── daily_opportunity dict ────────────────────────────────────────────────
    if "daily_opportunity" not in row:
        do_eligible = bool(row.get("daily_opportunity_eligible"))
        do_reason = str(row.get("daily_opportunity_reason") or "")
        row["daily_opportunity"] = {
            "eligible": do_eligible,
            "priority": round(score, 1) if do_eligible else None,
            "bucket": "stock_momentum",
            "reason": do_reason if do_eligible else "",
            "exclusion_reason": "" if do_eligible else do_reason,
        }

    return row


# ─── Gate groups ──────────────────────────────────────────────────────────────

def _build_gate_groups(row: dict[str, Any], mm: dict[str, Any]) -> dict[str, Any]:
    """Return nested gate-group dict for a stock_momentum row."""
    above50 = mm.get("above_sma_50")
    above200 = mm.get("above_sma_200")
    r3 = _num(mm.get("return_3m_pct"))
    r6 = _num(mm.get("return_6m_pct"))
    avg_vol = _num(mm.get("average_volume_30d"))
    rs6 = _num(mm.get("relative_strength_6m_pct") or mm.get("relative_strength_vs_qqq"))
    price = _num(mm.get("current_price"))
    vol30 = _num(mm.get("realized_volatility_30d") or mm.get("volatility_30d_pct"))
    ext50 = _num(row.get("extension_vs_50d_pct"))
    has_market = bool(row.get("has_market_data") or row.get("required_market_data_complete"))
    already_held = str(row.get("portfolio_status") or "").lower().startswith("already")
    allocation = _num(row.get("allocation_pct"))
    add_allowed = bool(row.get("add_allowed_boolean"))
    blockers = list(row.get("add_blockers") or [])
    action = str(row.get("action") or "").upper()

    # data group
    data_group = {
        "quote": _gate(
            label="Quote data",
            status="pass" if has_market else "unknown",
            reason="Market metrics complete." if has_market else "Market metrics incomplete or unavailable.",
            custom={"current_price": price},
        ),
        "candles": _gate(
            label="Candle data",
            status="pass" if (r3 is not None and r6 is not None) else "unknown",
            reason="Return metrics available." if (r3 is not None and r6 is not None) else "Return metrics unavailable.",
            custom={"return_3m_pct": r3, "return_6m_pct": r6},
        ),
        "benchmark": _gate(
            label="Benchmark comparison",
            status="pass" if rs6 is not None else "unknown",
            reason="Relative strength calculated." if rs6 is not None else "Relative strength unavailable.",
            custom={"relative_strength_6m_pct": rs6, "benchmark": "QQQ"},
        ),
    }

    # setup group
    trend_st = _ternary_status(above50 and above200, above50 or above200)
    mom_score = float(row.get("momentum_score") or row.get("score") or 0)
    setup_group = {
        "momentum": _gate(
            label="Momentum",
            status="pass" if mom_score >= 70 else ("watch" if mom_score >= 55 else "fail"),
            reason=f"Momentum score {mom_score:.0f}.",
            custom={"momentum_score": mom_score, "threshold": 70},
        ),
        "relative_strength": _gate(
            label="Relative strength",
            status="pass" if (rs6 is not None and rs6 > 0) else ("fail" if (rs6 is not None and rs6 < -5) else "unknown"),
            reason=f"RS vs QQQ: {rs6:.1f}%." if rs6 is not None else "Relative strength unavailable.",
            custom={"relative_strength_6m_pct": rs6},
        ),
        "trend": _gate(
            label="Trend filter",
            status=trend_st,
            reason=("Above both 50d and 200d MAs." if (above50 and above200)
                    else "Partial trend alignment." if (above50 or above200)
                    else "Trend broken."),
            custom={"above_sma_50": above50, "above_sma_200": above200},
        ),
        "volume": _gate(
            label="Volume",
            status="pass" if (avg_vol is not None and avg_vol >= 100_000) else ("watch" if avg_vol is not None else "unknown"),
            reason=f"30d avg volume: {int(avg_vol or 0):,}." if avg_vol is not None else "Volume data unavailable.",
            custom={"average_volume_30d": avg_vol},
        ),
        "price_action": _gate(
            label="Price action",
            status="pass" if ((r3 or 0) > 0 and (r6 or 0) > 0) else ("watch" if ((r3 or 0) > 0 or (r6 or 0) > 0) else "fail"),
            reason="Positive multi-period returns." if ((r3 or 0) > 0 and (r6 or 0) > 0) else "Mixed or negative returns.",
            custom={"return_3m_pct": r3, "return_6m_pct": r6},
        ),
    }

    # risk group
    risk_group = {
        "extension": _gate(
            label="Extension risk",
            status="pass" if (ext50 is not None and ext50 <= 20) else ("watch" if (ext50 is not None and ext50 <= 30) else ("fail" if ext50 is not None else "unknown")),
            reason=f"Extension vs 50d: {ext50:.1f}%." if ext50 is not None else "Extension data unavailable.",
            custom={"extension_vs_50d_pct": ext50},
        ),
        "drawdown": _gate(
            label="Volatility check",
            status="pass" if (vol30 is not None and vol30 < 50) else ("watch" if (vol30 is not None and vol30 < 80) else ("fail" if vol30 is not None else "unknown")),
            reason=f"30d realized vol: {vol30:.0f}%." if vol30 is not None else "Volatility data unavailable.",
            custom={"realized_volatility_30d_pct": vol30},
        ),
    }

    # portfolio group
    max_alloc = 15.0
    at_max = allocation is not None and allocation >= max_alloc
    portfolio_group = {
        "already_held": _gate(
            label="Held in portfolio",
            status="watch" if already_held else "pass",
            reason="Already held; review add-size logic." if already_held else "Not currently held; valid new entry.",
            custom={"already_held": already_held, "allocation_pct": allocation},
            blocking=False,
        ),
        "concentration": _gate(
            label="Position concentration",
            status="fail" if at_max else "pass",
            reason=f"Allocation {allocation:.1f}% at or above {max_alloc:.0f}% max." if at_max
                   else (f"Allocation {allocation:.1f}% within single-name limit." if allocation is not None
                         else "Allocation not available."),
            custom={"allocation_pct": allocation, "max_allocation_pct": max_alloc},
        ),
    }

    # daily_opportunity group
    do_eligible = bool(row.get("daily_opportunity_eligible"))
    do_action_ok = action in {"CONSIDER ADDING", "ADD ON PULLBACK"}
    daily_opp_group = {
        "eligible": _gate(
            label="Daily Opportunity eligible",
            status="pass" if do_eligible else "fail",
            reason=("Eligible for Daily Opportunity based on action and entry quality."
                    if do_eligible else "Not eligible for Daily Opportunity."),
            custom={"action": str(row.get("action") or ""), "add_allowed": add_allowed},
            blocking=False,
        ),
    }

    return {
        "data": data_group,
        "setup": setup_group,
        "risk": risk_group,
        "portfolio": portfolio_group,
        "daily_opportunity": daily_opp_group,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _infer_row_type(action_upper: str) -> str:
    if "CONSIDER" in action_upper or ("ADD" in action_upper and "AVOID" not in action_upper and "PULLBACK" not in action_upper):
        return "new_candidate"
    if "ADD ON PULLBACK" in action_upper:
        return "new_candidate"
    if "AVOID" in action_upper or "WEAK" in action_upper:
        return "rejected_candidate"
    return "observation"


def _gate(
    label: str,
    status: str,
    *,
    reason: str = "",
    custom: dict[str, Any] | None = None,
    blocking: bool | None = None,
) -> dict[str, Any]:
    """Build a single gate dict in the universal nested format."""
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


def _ternary_status(best: bool, middle: bool) -> str:
    if best:
        return "pass"
    if middle:
        return "watch"
    return "fail"


def _stable_row_id(strategy_id: str, ticker: str, run_id: str) -> str:
    """Deterministic row_id from strategy, ticker, and run context."""
    raw = f"{strategy_id}:{ticker}:{run_id}"
    return f"sm:{ticker}:{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
