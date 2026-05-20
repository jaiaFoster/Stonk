"""
app/services/watchlist_review_service.py — Watchlist candidate review.

Combines watchlist candidates with earnings timestamps, calendar-candidate data,
earnings-calendar strategy evaluations, and news presence. This is intentionally
read-only: it identifies things to research/scan, not orders to place.
"""

from __future__ import annotations

from typing import Any, Callable

LogFn = Callable[[str], None]


def review_watchlist_candidates(
    watchlist_result: dict[str, Any],
    tradier_snapshot: dict[str, dict[str, Any]],
    earnings_events: dict[str, dict[str, Any]],
    news_map: dict[str, list[dict[str, Any]]],
    positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    logger("Running Watchlist Candidate Review v1...")

    items = (watchlist_result or {}).get("items", []) or []
    held_tickers = {str(pos.get("ticker") or "").upper().strip() for pos in positions if pos.get("ticker")}
    candidate_by_ticker = _calendar_candidates_by_ticker(tradier_snapshot)
    strategy_by_ticker = _earnings_strategy_by_ticker(tradier_snapshot)

    reviews: list[dict[str, Any]] = []
    for item in items:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        already_held = ticker in held_tickers or bool(item.get("already_held"))
        earnings = earnings_events.get(ticker, {}) or {}
        calendar = candidate_by_ticker.get(ticker, {})
        strategy = strategy_by_ticker.get(ticker, {})
        article_count = len(news_map.get(ticker, []) or [])

        score = 45.0
        reasons: list[str] = []
        risks: list[str] = []

        if already_held:
            score -= 2
            reasons.append("Ticker is already held; treat as monitoring/add-size review, not a new idea.")
        else:
            score += 8
            reasons.append("Ticker is not currently held; valid new-candidate watchlist item.")

        if earnings.get("has_data"):
            dte = earnings.get("days_until_earnings")
            score += 8
            reasons.append("Upcoming/nearby earnings timestamp is available.")
            if dte is not None and dte <= 1:
                score += 5
                risks.append("Earnings are today/tomorrow; requires urgent manual review before any entry.")
        else:
            risks.append("No earnings timestamp available in this run.")

        if calendar:
            cal_score = _float(calendar.get("score"))
            score += 10 if cal_score >= 78 else 4
            reasons.append("Tradier calendar candidate was generated for this ticker.")
        else:
            risks.append("No Tradier calendar candidate generated under current scan limits/settings.")

        if strategy:
            action = str(strategy.get("action") or "").upper()
            if strategy.get("is_preferred_setup"):
                score += 18
                reasons.append("Earnings-calendar strategy marked this as a preferred setup.")
            elif "URGENT" in action:
                score += 4
                risks.append("Earnings-calendar strategy marked this as urgent review, not automatic entry.")
            elif "AVOID" in action:
                score -= 18
                risks.append("Earnings-calendar strategy marked this candidate as avoid/risky.")
            elif "MANUAL" in action:
                risks.append("Earnings-calendar strategy requires manual review.")
        else:
            risks.append("No earnings-calendar strategy evaluation for this ticker.")

        if article_count:
            score += 3
            reasons.append(f"{article_count} relevant news article(s) found.")

        score = max(0.0, min(100.0, score))
        category = _category_for(score, already_held, strategy, calendar, earnings)
        next_check = _next_check(category)

        reviews.append(
            {
                "ticker": ticker,
                "score": round(score, 1),
                "category": category,
                "already_held": already_held,
                "portfolio_status": "Already held" if already_held else "Not currently held",
                "watchlists": item.get("watchlists", []),
                "sources": item.get("sources", []),
                "earnings": earnings,
                "calendar_candidate": calendar,
                "earnings_calendar_strategy": strategy,
                "news_article_count": article_count,
                "reasons": reasons[:5],
                "risks": risks[:5],
                "next_check": next_check,
            }
        )

    reviews.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    summary = {
        "candidate_count": len(reviews),
        "new_candidate_count": sum(1 for r in reviews if not r.get("already_held")),
        "already_held_count": sum(1 for r in reviews if r.get("already_held")),
        "potential_trade_count": sum(1 for r in reviews if "CALENDAR" in str(r.get("category", "")).upper()),
        "urgent_count": sum(1 for r in reviews if "URGENT" in str(r.get("category", "")).upper()),
    }
    logger(
        "Watchlist Candidate Review v1 produced "
        f"{summary['candidate_count']} review(s), "
        f"{summary['potential_trade_count']} potential calendar/earnings setup(s), "
        f"{summary['urgent_count']} urgent."
    )
    return {
        "source": "watchlist_candidate_review_v1",
        "enabled": bool((watchlist_result or {}).get("enabled", True)),
        "has_data": bool(reviews),
        "items": reviews,
        "summary": summary,
        "errors": (watchlist_result or {}).get("errors", []) or [],
    }


def _calendar_candidates_by_ticker(tradier_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = (tradier_snapshot or {}).get("_calendar_spread_candidates", {}) or {}
    items = raw.get("items", []) if isinstance(raw, dict) else []
    result: dict[str, dict[str, Any]] = {}
    for item in items or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker and ticker not in result:
            result[ticker] = item
    return result


def _earnings_strategy_by_ticker(tradier_snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = (tradier_snapshot or {}).get("_earnings_calendar_strategy", {}) or {}
    items = raw.get("items", []) if isinstance(raw, dict) else []
    result: dict[str, dict[str, Any]] = {}
    for item in items or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker and ticker not in result:
            result[ticker] = item
    return result


def _category_for(
    score: float,
    already_held: bool,
    strategy: dict[str, Any],
    calendar: dict[str, Any],
    earnings: dict[str, Any],
) -> str:
    action = str((strategy or {}).get("action") or "").upper()
    if "AVOID" in action:
        return "WATCH ONLY / AVOID TRADE"
    if "URGENT" in action:
        return "URGENT EARNINGS REVIEW"
    if (strategy or {}).get("is_preferred_setup"):
        return "POTENTIAL EARNINGS CALENDAR"
    if calendar and score >= 65:
        return "OPTIONS WATCH"
    if already_held:
        return "ALREADY HELD / MONITOR"
    if earnings.get("has_data"):
        return "EARNINGS WATCH"
    return "STOCK WATCH / RESEARCH"


def _next_check(category: str) -> str:
    category = category.upper()
    if "URGENT" in category:
        return "Review before entry; verify earnings timestamp, live bid/ask, IV, and short-leg event risk."
    if "EARNINGS CALENDAR" in category:
        return "Check full chain, event timing, and debit immediately before considering entry."
    if "OPTIONS WATCH" in category:
        return "Keep in the options scan universe; needs earnings-aware confirmation before trade."
    if "AVOID" in category:
        return "Do not enter based on current strategy data; keep only for research."
    return "Keep in watchlist and re-run scanner when earnings/trend/options data improves."


def _float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
