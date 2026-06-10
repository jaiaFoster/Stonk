"""
app/services/daily_opportunity_engine_service.py — Daily Opportunity Engine v1.

One daily action list across the app:
- earnings-calendar entries from Unified Calendar Trade Engine
- stock adds / add-on-pullback from Stock Momentum Add Strategy
- portfolio gap watchlist suggestions
- portfolio risk/avoid names from existing portfolio scoring
"""

from __future__ import annotations

from typing import Any, Callable

from app import config

LogFn = Callable[[str], None]

ACTION_TYPE_PRIORITY = {
    "active_calendar": 0,
    "active_skew_vertical": 1,
    "calendar": 2,
    "skew_vertical": 3,
    "stock_add": 4,
    "stock": 4,
    "gap": 5,
    "holding": 6,
    "portfolio_risk": 7,
    "risk": 7,
    "monitor": 8,
}


def build_daily_opportunity_engine(
    unified_calendar_engine: dict[str, Any] | None,
    stock_momentum_strategy: dict[str, Any] | None,
    portfolio_gap_analysis: dict[str, Any] | None,
    recommendations: list[dict[str, Any]] | None,
    log_print: LogFn | None = None,
    skew_momentum_vertical_strategy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    result: dict[str, Any] = {
        "source": "daily_opportunity_engine_v1",
        "enabled": bool(getattr(config, "DAILY_OPPORTUNITY_ENGINE_ENABLED", True)),
        "has_data": False,
        "actions": [],
        "summary": {},
        "errors": [],
    }
    if not result["enabled"]:
        logger("Daily Opportunity Engine v1 disabled by DAILY_OPPORTUNITY_ENGINE_ENABLED=false.")
        return _finalize(result)

    actions: list[dict[str, Any]] = []
    actions.extend(_calendar_actions(unified_calendar_engine or {}))
    actions.extend(_skew_vertical_actions(skew_momentum_vertical_strategy or {}))
    # Stock add ideas are intentionally consolidated by ticker so the top-level
    # daily view does not show separate momentum/gap/watchlist rows for the same
    # candidate. Detailed strategy tables can stay lower in the report until the
    # full UI overhaul.
    actions.extend(_unified_stock_add_actions(stock_momentum_strategy or {}, portfolio_gap_analysis or {}))
    actions.extend(_portfolio_risk_actions(recommendations or []))

    # De-dupe by type+ticker+action so one idea does not spam the report.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for action in sorted(actions, key=_daily_sort_key):
        key = (str(action.get("type") or ""), str(action.get("ticker") or ""), str(action.get("action") or ""))
        if key in seen:
            continue
        seen.add(key)
        # Active option/calendar lifecycle alerts are allowed through even when
        # their raw trade score is low. A losing calendar can be the most
        # important thing to look at today.
        if float(action.get("priority_score") or 0) < float(getattr(config, "DAILY_OPPORTUNITY_MIN_SCORE", 55) or 55):
            if str(action.get("type") or "") not in {"calendar", "active_calendar", "active_skew_vertical"}:
                continue
        if _zero_value_row(action):
            continue
        deduped.append(action)
        if len(deduped) >= int(getattr(config, "DAILY_OPPORTUNITY_MAX_ACTIONS", 12) or 12):
            break

    result["actions"] = deduped
    result["has_data"] = bool(deduped)
    finalized = _finalize(result)
    summary = finalized.get("summary", {})
    logger(
        "Daily Opportunity Engine v1 produced "
        f"{summary.get('action_count', 0)} action(s): "
        f"{summary.get('calendar_count', 0)} calendar, "
        f"{summary.get('skew_vertical_count', 0)} skew_vertical, "
        f"{summary.get('stock_count', 0)} stock, "
        f"{summary.get('risk_count', 0)} risk."
    )
    skew_summary = (skew_momentum_vertical_strategy or {}).get("summary", {}) or {}
    logger(
        "Strategy 2 summary: "
        f"{skew_summary.get('pass_count', 0)} pass, "
        f"{skew_summary.get('watch_count', 0)} watch, "
        f"{skew_summary.get('blocked_count', 0)} fail; "
        f"{summary.get('skew_vertical_count', 0)} included in Daily Opportunity."
    )
    if bool(getattr(config, "DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS", True)):
        logger("Daily Opportunity Engine: active_calendar rows prioritized above stock_add rows.")
    return finalized


def _skew_vertical_actions(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in strategy.get("active_items", []) or []:
        out.append({
            "type": "active_skew_vertical",
            "ticker": row.get("ticker"),
            "priority_score": max(90.0, float(row.get("priority") or row.get("score") or 0)),
            "action": row.get("verdict") or "ACTIVE VERTICAL REVIEW",
            "why": row.get("primary_reason") or "Broker-detected active skew momentum vertical.",
            "next_step": row.get("next_action") or "Reprice the broker-detected position.",
            "source": "Skew Momentum Vertical Lifecycle",
        })
    for row in strategy.get("pass_items", []) or []:
        if not str(row.get("verdict") or "").startswith("PASS"):
            continue
        out.append({
            "type": "skew_vertical",
            "ticker": row.get("ticker"),
            "priority_score": float(row.get("priority") or row.get("score") or 0),
            "action": row.get("verdict"),
            "why": row.get("primary_reason") or row.get("momentum_reason"),
            "next_step": row.get("next_action") or "Recheck live bid/ask before entry.",
            "source": "Skew Momentum Vertical Strategy v1",
        })
    return out


def _daily_sort_key(item: dict[str, Any]) -> tuple[int, float]:
    if not bool(getattr(config, "DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS", True)):
        return (0, -float(item.get("priority_score") or 0))
    action_type = str(item.get("type") or "")
    return (ACTION_TYPE_PRIORITY.get(action_type, 99), -float(item.get("priority_score") or 0))


def _calendar_actions(engine: dict[str, Any]) -> list[dict[str, Any]]:
    out = []

    # Active trades first: this is the most important daily viewer value.
    # A detected open calendar may require action even when its P/L score is low.
    for row in engine.get("open_trade_rows") or []:
        verdict = str(row.get("verdict") or "").upper()
        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        score = float(raw.get("lifecycle_priority_score") or row.get("score") or 0)
        if "URGENT" in verdict or "EXIT" in verdict or "CUT" in verdict or "RECHECK" in verdict or "TAKE PROFIT" in verdict:
            score = max(score, 90.0 if ("URGENT" in verdict or "CUT" in verdict) else 78.0)
        out.append(
            {
                "type": "active_calendar",
                "ticker": row.get("ticker"),
                "priority_score": score,
                "action": row.get("verdict") or "Calendar review",
                "why": _active_calendar_why(row),
                "next_step": row.get("next_action") or "Review live spread quotes before acting.",
                "source": "Active Calendar Lifecycle v2",
            }
        )

    for row in engine.get("new_trade_rows") or []:
        verdict = str(row.get("verdict") or "").upper()
        if verdict.startswith("PASS") or "URGENT" in verdict or "TAKE PROFIT" in verdict or "EXIT" in verdict:
            out.append(
                {
                    "type": "calendar",
                    "ticker": row.get("ticker"),
                    "priority_score": float(row.get("score") or 0),
                    "action": row.get("verdict") or "Calendar review",
                    "why": _calendar_why(row),
                    "next_step": row.get("entry_plan") or row.get("next_action") or "Review live spread quotes before acting.",
                    "source": "Unified Calendar Trade Engine v1",
                }
            )
    return out



def _unified_stock_add_actions(strategy: dict[str, Any], gap: dict[str, Any]) -> list[dict[str, Any]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    risk_rows: list[dict[str, Any]] = []

    def ensure(ticker: Any, default_action: str = "WATCH / RESEARCH") -> dict[str, Any] | None:
        symbol = str(ticker or "").upper().strip()
        if not symbol:
            return None
        return by_ticker.setdefault(
            symbol,
            {
                "type": "stock_add",
                "ticker": symbol,
                "priority_score": 0.0,
                "action": default_action,
                "why_parts": [],
                "next_parts": [],
                "source_tags": [],
                "source": "Unified Stock Add Candidate v1",
            },
        )

    for item in strategy.get("items", []) or []:
        action = str(item.get("action") or "")
        group = _action_group(action)
        if group == "risk":
            risk_rows.append(_risk_action_from_item(item, "Stock Momentum Add Strategy v1"))
            continue
        if action not in {"CONSIDER ADDING", "ADD ON PULLBACK", "WATCH / CONFIRM TREND", "WATCH / RESEARCH"}:
            continue
        row = ensure(item.get("ticker"), action or "WATCH / RESEARCH")
        if not row:
            continue
        score = float(item.get("score") or 0)
        row["priority_score"] = max(float(row.get("priority_score") or 0), score)
        row["action"] = _merge_stock_action(str(row.get("action") or ""), action)
        row["source_tags"].append("momentum")
        for reason in (item.get("reasons") or [])[:3]:
            _append_unique(row["why_parts"], str(reason))
        if item.get("next_check"):
            _append_unique(row["next_parts"], str(item.get("next_check")))

    for item in gap.get("suggestions", []) or []:
        action = str(item.get("action") or item.get("category") or "WATCH / RESEARCH")
        group = _action_group(action)
        if group == "risk":
            risk_rows.append(_risk_action_from_item(item, "Portfolio Gap / Sector Suggestions v1", action=action))
            continue
        row = ensure(item.get("ticker"), action)
        if not row:
            continue
        score = float(item.get("score") or item.get("total_score") or 58)
        row["priority_score"] = max(float(row.get("priority_score") or 0), score)
        row["action"] = _merge_stock_action(str(row.get("action") or ""), action)
        row["source_tags"].append("sector_gap")
        _append_unique(row["why_parts"], item.get("reason") or item.get("rationale") or "Candidate helps fill an aggressive-growth portfolio gap.")
        for bucket in item.get("buckets", []) or []:
            _append_unique(row["why_parts"], f"Bucket: {bucket}")
        for risk in item.get("risks", []) or []:
            _append_unique(row["why_parts"], f"Risk: {risk}")
        if item.get("next_check"):
            _append_unique(row["next_parts"], str(item.get("next_check")))

    output = []
    for row in by_ticker.values():
        tags = sorted(set(row.pop("source_tags", []) or []))
        why_parts = row.pop("why_parts", [])
        next_parts = row.pop("next_parts", [])
        row["why"] = "; ".join(why_parts[:5]) or "Unified stock-add engine flagged this name."
        row["next_step"] = next_parts[0] if next_parts else "Confirm trend, sizing, sector fit, and thesis before adding."
        row["source"] = "Unified Stock Add Candidate v1 (" + ", ".join(tags or ["stock"]) + ")"
        output.append(row)
    return output + [row for row in risk_rows if not _zero_value_row(row)]


def _append_unique(items: list[str], value: Any) -> None:
    text = str(value or "").strip()
    if text and text not in items:
        items.append(text)


def _merge_stock_action(existing: str, new: str) -> str:
    combined = " ".join([existing.upper(), new.upper()])
    if _action_group(combined) == "risk":
        return new or existing or "RISK REVIEW"
    if "ADD ON PULLBACK" in combined:
        return "ADD ON PULLBACK"
    if "CONSIDER ADDING" in combined or "HIGH-PRIORITY" in combined:
        return "CONSIDER ADDING"
    if "WATCH" in combined:
        return "WATCH / CONFIRM TREND"
    return new or existing or "CONSIDER ADDING"


def _action_group(action: Any) -> str:
    text = str(action or "").upper()
    if any(token in text for token in ("AVOID", "REDUCE", "CUT", "TRIM", "DO NOT ADD", "FAIL")):
        return "risk"
    if any(token in text for token in ("CONSIDER ADDING", "ADD ON PULLBACK", "REVIEW ADD", "HIGH-PRIORITY CONSIDER ADDING")):
        return "actionable"
    return "watch"


def _risk_action_from_item(item: dict[str, Any], source: str, action: str | None = None) -> dict[str, Any]:
    return {
        "type": "risk",
        "ticker": item.get("ticker"),
        "priority_score": float(item.get("score") or item.get("total_score") or 58),
        "action": action or item.get("action") or item.get("category") or "RISK REVIEW",
        "why": _first_text(item.get("reason"), item.get("rationale"), item.get("risks"), item.get("reasons"), fallback="Risk/avoid row separated from add ideas."),
        "next_step": item.get("next_check") or "Review risk controls; do not treat as an add candidate.",
        "source": source,
        "quantity": item.get("quantity"),
        "market_value": item.get("market_value") if item.get("market_value") is not None else item.get("position_value"),
        "allocation_pct": item.get("allocation_pct"),
    }

def _stock_momentum_actions(strategy: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in strategy.get("items", []) or []:
        action = str(item.get("action") or "")
        if action in {"CONSIDER ADDING", "ADD ON PULLBACK", "WATCH / CONFIRM TREND"}:
            out.append(
                {
                    "type": "stock",
                    "ticker": item.get("ticker"),
                    "priority_score": float(item.get("score") or 0),
                    "action": action,
                    "why": "; ".join((item.get("reasons") or [])[:3]) or "Stock momentum strategy flagged this name.",
                    "next_step": item.get("next_check") or "Confirm trend and sizing before adding.",
                    "source": "Stock Momentum Add Strategy v1",
                }
            )
    return out


def _portfolio_gap_actions(gap: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for item in gap.get("suggestions", []) or []:
        out.append(
            {
                "type": "gap",
                "ticker": item.get("ticker"),
                "priority_score": float(item.get("score") or item.get("total_score") or 58),
                "action": item.get("action") or "CONSIDER ADDING",
                "why": item.get("reason") or item.get("rationale") or "Candidate helps fill an aggressive-growth portfolio gap.",
                "next_step": item.get("next_check") or "Use as a research/add candidate only after trend and valuation checks confirm.",
                "source": "Portfolio Gap / Sector Suggestions v1",
            }
        )
    return out


def _portfolio_risk_actions(recommendations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rec in recommendations:
        if _zero_value_row(rec):
            continue
        action = str(rec.get("action") or "").upper()
        if "AVOID" in action or "REDUCE" in action or float(rec.get("score") or 100) < 42:
            out.append(
                {
                    "type": "risk",
                    "ticker": rec.get("ticker"),
                    "priority_score": max(55.0, 100.0 - float(rec.get("score") or 50)),
                    "action": rec.get("action") or "RISK REVIEW",
                    "why": "; ".join((rec.get("risks") or [])[:3]) or "Portfolio score/risk profile is weak.",
                    "next_step": rec.get("next_check") or "Review thesis before adding more capital.",
                    "source": "Portfolio Scoring v2",
                }
            )
    return out


def _zero_value_row(row: dict[str, Any]) -> bool:
    quantity = _float_or_none(row.get("quantity"))
    value = _float_or_none(row.get("market_value") if row.get("market_value") is not None else row.get("position_value"))
    allocation = _float_or_none(row.get("allocation_pct"))
    if quantity is not None and value is not None:
        return abs(quantity) <= 1e-9 and abs(value) <= 0.01
    if value is not None and allocation is not None:
        return abs(value) <= 0.01 and abs(allocation) <= 1e-9
    return False


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_text(*values: Any, fallback: str = "—") -> str:
    for value in values:
        if isinstance(value, list) and value:
            return str(value[0])
        if value not in (None, "", []):
            return str(value)
    return fallback


def _active_calendar_why(row: dict[str, Any]) -> str:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    parts = []
    decision = raw.get("decision_summary")
    if decision:
        parts.append(str(decision))
    value = row.get("value")
    if value:
        parts.append(str(value))
    risks = [str(r) for r in (row.get("risks") or [])[:2]]
    parts.extend(risks)
    return "; ".join(parts) or "Open calendar needs lifecycle review."


def _calendar_why(row: dict[str, Any]) -> str:
    reqs = row.get("requirements") or []
    passed = [r.get("name") for r in reqs if str(r.get("status") or "").upper() == "PASS"]
    failed = [r.get("name") for r in reqs if str(r.get("status") or "").upper() == "FAIL"]
    parts = []
    if passed:
        parts.append("Pass: " + ", ".join(str(p) for p in passed[:3]))
    if failed:
        parts.append("Fail: " + ", ".join(str(f) for f in failed[:3]))
    return "; ".join(parts) or "Calendar engine flagged this row for review."


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    actions = result.get("actions", []) or []
    result["summary"] = {
        "action_count": len(actions),
        "calendar_count": sum(1 for a in actions if a.get("type") in {"calendar", "active_calendar"}),
        "skew_vertical_count": sum(1 for a in actions if a.get("type") in {"skew_vertical", "active_skew_vertical"}),
        "stock_count": sum(1 for a in actions if a.get("type") in {"stock", "stock_add"}),
        "gap_count": sum(1 for a in actions if a.get("type") == "gap"),
        "risk_count": sum(1 for a in actions if a.get("type") == "risk"),
    }
    result["has_data"] = bool(actions)
    return result
