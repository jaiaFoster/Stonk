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


def build_daily_opportunity_engine(
    unified_calendar_engine: dict[str, Any] | None,
    stock_momentum_strategy: dict[str, Any] | None,
    portfolio_gap_analysis: dict[str, Any] | None,
    recommendations: list[dict[str, Any]] | None,
    log_print: LogFn | None = None,
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
    actions.extend(_stock_momentum_actions(stock_momentum_strategy or {}))
    actions.extend(_portfolio_gap_actions(portfolio_gap_analysis or {}))
    actions.extend(_portfolio_risk_actions(recommendations or []))

    # De-dupe by type+ticker+action so one idea does not spam the report.
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for action in sorted(actions, key=lambda item: float(item.get("priority_score") or 0), reverse=True):
        key = (str(action.get("type") or ""), str(action.get("ticker") or ""), str(action.get("action") or ""))
        if key in seen:
            continue
        seen.add(key)
        if float(action.get("priority_score") or 0) < float(getattr(config, "DAILY_OPPORTUNITY_MIN_SCORE", 55) or 55):
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
        f"{summary.get('stock_count', 0)} stock, "
        f"{summary.get('risk_count', 0)} risk."
    )
    return finalized


def _calendar_actions(engine: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for row in (engine.get("new_trade_rows") or []) + (engine.get("open_trade_rows") or []):
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
        "calendar_count": sum(1 for a in actions if a.get("type") == "calendar"),
        "stock_count": sum(1 for a in actions if a.get("type") == "stock"),
        "gap_count": sum(1 for a in actions if a.get("type") == "gap"),
        "risk_count": sum(1 for a in actions if a.get("type") == "risk"),
    }
    result["has_data"] = bool(actions)
    return result
