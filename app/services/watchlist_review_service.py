"""
app/services/watchlist_review_service.py — Watchlist stock candidate review.

Watchlist items are primarily reviewed as normal stock candidates. Earnings and
calendar-spread strategy data are treated as an overlay only when an actual
nearby earnings setup exists. This keeps the watchlist useful even when there
are no earnings events.
"""

from __future__ import annotations

from typing import Any, Callable

LogFn = Callable[[str], None]


LARGE_CAP_TECH_OR_GROWTH = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSM", "AVGO",
    "AMD", "PLTR", "ORCL", "CRM", "ADBE", "NOW", "SNOW", "SHOP", "ASML",
    "QCOM", "ARM", "INTC", "MU", "NFLX", "UBER", "HOOD", "SOFI",
}

SPECULATIVE_TICKERS = {
    "QBTS", "SMR", "IONQ", "RGTI", "SOUN", "BBAI", "ACHR", "JOBY", "RKLB",
    "HOOD", "SOFI", "PLTR",
}


def review_watchlist_candidates(
    watchlist_result: dict[str, Any],
    tradier_snapshot: dict[str, dict[str, Any]],
    earnings_events: dict[str, dict[str, Any]],
    news_map: dict[str, list[dict[str, Any]]],
    positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: print(msg, flush=True))
    logger("Running Watchlist Stock Candidate Review v2...")

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
        tradier = (tradier_snapshot or {}).get(ticker, {}) or {}
        quote = tradier.get("quote", {}) or {}
        article_count = len(news_map.get(ticker, []) or [])

        stock_score, stock_reasons, stock_risks = _score_stock_candidate(
            ticker=ticker,
            already_held=already_held,
            tradier=tradier,
            quote=quote,
            article_count=article_count,
        )

        overlay_delta, overlay_category, overlay_reasons, overlay_risks = _score_earnings_calendar_overlay(
            earnings=earnings,
            calendar=calendar,
            strategy=strategy,
        )

        final_score = max(0.0, min(100.0, stock_score + overlay_delta))
        category = _category_for(
            score=final_score,
            stock_score=stock_score,
            already_held=already_held,
            overlay_category=overlay_category,
            strategy=strategy,
            earnings=earnings,
        )
        next_check = _next_check(category, earnings, calendar, strategy)

        reviews.append(
            {
                "ticker": ticker,
                "score": round(final_score, 1),
                "stock_score": round(stock_score, 1),
                "overlay_score_delta": round(overlay_delta, 1),
                "category": category,
                "primary_review_type": "stock_candidate",
                "earnings_calendar_overlay": overlay_category,
                "already_held": already_held,
                "portfolio_status": "Already held" if already_held else "Not currently held",
                "watchlists": item.get("watchlists", []),
                "sources": item.get("sources", []),
                "earnings": earnings,
                "calendar_candidate": calendar,
                "earnings_calendar_strategy": strategy,
                "tradier_snapshot": _compact_tradier_snapshot(tradier),
                "news_article_count": article_count,
                "reasons": (stock_reasons + overlay_reasons)[:7],
                "risks": (stock_risks + overlay_risks)[:7],
                "stock_reasons": stock_reasons[:5],
                "stock_risks": stock_risks[:5],
                "overlay_reasons": overlay_reasons[:4],
                "overlay_risks": overlay_risks[:4],
                "next_check": next_check,
            }
        )

    reviews.sort(key=lambda r: float(r.get("score") or 0), reverse=True)
    summary = {
        "candidate_count": len(reviews),
        "new_candidate_count": sum(1 for r in reviews if not r.get("already_held")),
        "already_held_count": sum(1 for r in reviews if r.get("already_held")),
        "stock_candidate_count": sum(1 for r in reviews if "STOCK" in str(r.get("category", "")).upper()),
        "potential_trade_count": sum(1 for r in reviews if "CALENDAR" in str(r.get("category", "")).upper()),
        "urgent_count": sum(1 for r in reviews if "URGENT" in str(r.get("category", "")).upper()),
    }
    logger(
        "Watchlist Stock Candidate Review v2 produced "
        f"{summary['candidate_count']} review(s), "
        f"{summary['stock_candidate_count']} stock candidate(s), "
        f"{summary['potential_trade_count']} calendar/earnings setup(s), "
        f"{summary['urgent_count']} urgent."
    )
    return {
        "source": "watchlist_stock_candidate_review_v2",
        "enabled": bool((watchlist_result or {}).get("enabled", True)),
        "has_data": bool(reviews),
        "items": reviews,
        "summary": summary,
        "errors": (watchlist_result or {}).get("errors", []) or [],
    }


def _score_stock_candidate(
    ticker: str,
    already_held: bool,
    tradier: dict[str, Any],
    quote: dict[str, Any],
    article_count: int,
) -> tuple[float, list[str], list[str]]:
    """Score a watchlist ticker primarily as a stock candidate."""
    score = 50.0
    reasons: list[str] = []
    risks: list[str] = []

    if already_held:
        score += 2
        reasons.append("Already held; review as add-size/trim/watch candidate rather than a new position.")
    else:
        score += 8
        reasons.append("Not currently held; valid new stock-candidate watchlist item.")

    if ticker in LARGE_CAP_TECH_OR_GROWTH:
        score += 8
        reasons.append("Fits the aggressive growth/technology watchlist mandate.")
    elif ticker in SPECULATIVE_TICKERS:
        score += 3
        risks.append("Speculative growth ticker; keep sizing conservative until stronger data confirms it.")

    if quote:
        score += 6
        reasons.append("Tradier quote data is available for live review.")
        spread_pct = _quote_spread_pct(quote)
        if spread_pct is not None:
            if spread_pct <= 0.15:
                score += 5
                reasons.append("Underlying quote spread is tight enough for normal stock monitoring.")
            elif spread_pct <= 0.75:
                score += 2
                reasons.append("Underlying quote spread is acceptable but not ideal.")
            else:
                score -= 6
                risks.append("Underlying quote spread is wide; avoid acting without checking live liquidity.")
        volume = _float(quote.get("volume"))
        if volume is not None:
            if volume >= 5_000_000:
                score += 6
                reasons.append("High underlying volume supports liquidity.")
            elif volume >= 1_000_000:
                score += 3
                reasons.append("Underlying volume appears acceptable.")
            elif volume > 0:
                score -= 4
                risks.append("Underlying volume appears light; verify liquidity before adding.")
    else:
        risks.append("No Tradier quote data available in this run; stock review is lower confidence.")

    if article_count:
        score += min(5, 2 + article_count)
        reasons.append(f"{article_count} relevant recent news article(s) found.")
    else:
        risks.append("No relevant recent news found; catalyst score is neutral.")

    return max(0.0, min(92.0, score)), reasons, risks


def _score_earnings_calendar_overlay(
    earnings: dict[str, Any],
    calendar: dict[str, Any],
    strategy: dict[str, Any],
) -> tuple[float, str, list[str], list[str]]:
    """Add/deduct only for actual earnings/calendar setups.

    A watchlist ticker without earnings remains a stock candidate. Calendar logic
    should not dominate non-earnings names.
    """
    reasons: list[str] = []
    risks: list[str] = []

    has_earnings = bool((earnings or {}).get("has_data"))
    has_calendar = bool(calendar)
    action = str((strategy or {}).get("action") or "").upper()

    if not has_earnings and not has_calendar:
        return 0.0, "NO EARNINGS/CALENDAR OVERLAY", reasons, ["No earnings-calendar setup found; review primarily as a stock candidate."]

    if has_earnings and not has_calendar:
        dte = earnings.get("days_until_earnings")
        if dte is not None and dte <= 1:
            return 2.0, "URGENT EARNINGS WATCH", reasons, ["Earnings are today/tomorrow, but no calendar candidate was generated."]
        return 4.0, "EARNINGS WATCH", ["Earnings timestamp is available for this watchlist ticker."], []

    if has_calendar and not has_earnings:
        return 4.0, "OPTIONS LIQUIDITY WATCH", ["Tradier generated an options/calendar structure, but no earnings timestamp is available."], ["Treat as an options-liquidity signal, not an earnings calendar trade."]

    # Both earnings and calendar exist; now earnings-calendar strategy matters.
    if (strategy or {}).get("is_preferred_setup"):
        return 18.0, "POTENTIAL EARNINGS CALENDAR", ["Earnings-calendar strategy marked this as a preferred setup."], []
    if "URGENT" in action:
        return 4.0, "URGENT EARNINGS REVIEW", ["Calendar candidate exists with nearby earnings."], ["Earnings-calendar strategy marked this as urgent review, not automatic entry."]
    if "AVOID" in action:
        return -18.0, "EARNINGS CALENDAR AVOID", [], ["Earnings-calendar strategy marked this calendar candidate as avoid/risky."]
    if "MANUAL" in action:
        return -2.0, "MANUAL EARNINGS REVIEW", [], ["Earnings-calendar strategy requires manual review."]

    return 6.0, "EARNINGS + OPTIONS WATCH", ["Both earnings timestamp and options structure are available."], []


def _category_for(
    score: float,
    stock_score: float,
    already_held: bool,
    overlay_category: str,
    strategy: dict[str, Any],
    earnings: dict[str, Any],
) -> str:
    overlay_upper = str(overlay_category or "").upper()
    action = str((strategy or {}).get("action") or "").upper()

    if "AVOID" in overlay_upper or "AVOID" in action:
        return "WATCH ONLY / AVOID TRADE"
    if "URGENT" in overlay_upper:
        return "URGENT EARNINGS REVIEW"
    if "POTENTIAL EARNINGS CALENDAR" in overlay_upper:
        return "POTENTIAL EARNINGS CALENDAR"
    if already_held:
        return "ALREADY HELD / ADD-SIZE REVIEW" if score >= 62 else "ALREADY HELD / MONITOR"
    if score >= 72:
        return "HIGH-PRIORITY STOCK WATCH"
    if score >= 62:
        return "STOCK CANDIDATE / RESEARCH"
    if "OPTIONS" in overlay_upper:
        return "OPTIONS LIQUIDITY WATCH"
    if earnings.get("has_data"):
        return "EARNINGS WATCH"
    return "STOCK WATCH / RESEARCH"


def _next_check(category: str, earnings: dict[str, Any], calendar: dict[str, Any], strategy: dict[str, Any]) -> str:
    category_upper = str(category or "").upper()
    if "URGENT" in category_upper:
        return "Review before entry; verify earnings timestamp, live bid/ask, IV, and short-leg event risk."
    if "EARNINGS CALENDAR" in category_upper:
        return "Check full chain, event timing, debit, and exit plan immediately before considering entry."
    if "HIGH-PRIORITY STOCK" in category_upper:
        return "Review as a normal stock add candidate; confirm thesis, allocation room, and live price action."
    if "STOCK CANDIDATE" in category_upper:
        return "Keep in stock research queue; look for trend, valuation, and catalyst confirmation before adding."
    if "ALREADY HELD" in category_upper:
        return "Use as an add-size or risk-review prompt for the existing position."
    if "OPTIONS" in category_upper:
        return "Keep in options scan universe, but do not treat as earnings-calendar trade without earnings fit."
    if "AVOID" in category_upper:
        return "Do not enter based on current strategy data; keep only for research."
    return "Keep in watchlist and re-run scanner when trend, earnings, or options data improves."


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


def _compact_tradier_snapshot(tradier: dict[str, Any]) -> dict[str, Any]:
    quote = tradier.get("quote", {}) or {}
    return {
        "has_data": bool(tradier.get("has_data")) or bool(quote),
        "last": quote.get("last") or quote.get("last_price"),
        "bid": quote.get("bid"),
        "ask": quote.get("ask"),
        "volume": quote.get("volume"),
        "quote_spread_pct": _quote_spread_pct(quote),
        "expiration_count": len(tradier.get("expirations", []) or []),
    }


def _quote_spread_pct(quote: dict[str, Any]) -> float | None:
    bid = _float(quote.get("bid"))
    ask = _float(quote.get("ask"))
    last = _float(quote.get("last") or quote.get("last_price"))
    if bid is None or ask is None or ask <= 0 or bid <= 0 or ask < bid:
        return None
    mid = (bid + ask) / 2.0
    denom = mid if mid > 0 else last
    if not denom or denom <= 0:
        return None
    return (ask - bid) / denom * 100.0


def _float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
