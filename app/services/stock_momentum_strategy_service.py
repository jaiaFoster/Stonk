"""
app/services/stock_momentum_strategy_service.py — Stock Momentum Add Strategy v1.

Read-only stock-entry strategy for current holdings and watchlist ideas. It is
meant to complement the earnings-calendar strategy: calendars are for event
trades; this module finds normal equity add/watch opportunities using momentum,
trend, portfolio sizing, macro-priority buckets, and watchlist context.
"""

from __future__ import annotations

from typing import Any, Callable

from app import config

LogFn = Callable[[str], None]

TECH_BUCKET_TICKERS = {
    "NVDA", "AMD", "AVGO", "MU", "TSM", "ASML", "CRDO", "ORCL", "MSFT", "AMZN", "GOOGL", "META", "PLTR"
}
SPECULATIVE_TICKERS = {"QBTS", "SMR", "SOFI", "HOOD", "BYND", "LPTH", "ONDS", "METU", "SNPW", "SNT", "BSTT", "CIBN"}


def build_stock_momentum_strategy(
    positions: list[dict[str, Any]],
    watchlist_candidates: dict[str, Any] | None,
    recommendations: list[dict[str, Any]] | None,
    market_metrics: dict[str, dict[str, Any]] | None,
    portfolio_gap_analysis: dict[str, Any] | None,
    news_map: dict[str, list[dict[str, Any]]] | None,
    log_print: LogFn | None = None,
) -> dict[str, Any]:
    """Score portfolio + watchlist stocks for add / pullback / watch decisions."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    result: dict[str, Any] = {
        "source": "stock_momentum_add_strategy_v1",
        "enabled": bool(getattr(config, "STOCK_MOMENTUM_STRATEGY_ENABLED", True)),
        "has_data": False,
        "items": [],
        "summary": {},
        "errors": [],
    }
    if not result["enabled"]:
        logger("Stock Momentum Add Strategy v1 disabled by STOCK_MOMENTUM_STRATEGY_ENABLED=false.")
        return _finalize(result)

    positions = positions or []
    recs = recommendations or []
    metrics = market_metrics or {}
    news_map = news_map or {}
    held_by_ticker = _held_by_ticker(positions)
    rec_by_ticker = {str(r.get("ticker") or "").upper().strip(): r for r in recs if str(r.get("ticker") or "").strip()}
    gap_suggestions = _gap_suggestions_by_ticker(portfolio_gap_analysis or {})

    tickers: list[str] = []
    for ticker in held_by_ticker:
        if ticker not in tickers:
            tickers.append(ticker)
    for item in (watchlist_candidates or {}).get("items", []) or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker and ticker not in tickers:
            tickers.append(ticker)

    items: list[dict[str, Any]] = []
    for ticker in tickers:
        row = _score_ticker(
            ticker=ticker,
            holding=held_by_ticker.get(ticker),
            recommendation=rec_by_ticker.get(ticker),
            metrics=metrics.get(ticker) or (rec_by_ticker.get(ticker) or {}).get("market_metrics") or {},
            gap_suggestion=gap_suggestions.get(ticker),
            news_items=news_map.get(ticker, []) or [],
        )
        if row:
            items.append(row)

    items.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    max_items = max(1, int(getattr(config, "STOCK_MOMENTUM_MAX_CANDIDATES", 12) or 12))
    result["items"] = items[:max_items]
    result["has_data"] = bool(result["items"])
    finalized = _finalize(result)
    summary = finalized.get("summary", {})
    logger(
        "Stock Momentum Add Strategy v1 produced "
        f"{summary.get('candidate_count', 0)} candidate(s), "
        f"{summary.get('consider_add_count', 0)} consider-add, "
        f"{summary.get('pullback_count', 0)} add-on-pullback."
    )
    return finalized


def select_stock_momentum_market_data_tickers(
    positions: list[dict[str, Any]],
    watchlist_candidates: dict[str, Any] | None,
    run_mode: str = "prod",
) -> list[str]:
    """Pick extra tickers worth market-data calls for stock momentum analysis."""
    clean_mode = str(run_mode or "prod").strip().lower()
    max_count = int(getattr(config, "STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX", 6) or 6)
    if clean_mode == "dev":
        max_count = min(max_count, max(1, int(getattr(config, "DEV_MAX_TICKERS", 2) or 2) + 2))
    held = {str(p.get("ticker") or "").upper().strip() for p in positions or [] if str(p.get("ticker") or "").strip()}
    candidates = []
    for item in (watchlist_candidates or {}).get("items", []) or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if not ticker or ticker in held:
            continue
        score = _theme_priority_score(ticker)
        candidates.append((score, ticker))
    candidates.sort(key=lambda pair: (-pair[0], pair[1]))
    return [ticker for _, ticker in candidates[:max_count]]


def _score_ticker(
    ticker: str,
    holding: dict[str, Any] | None,
    recommendation: dict[str, Any] | None,
    metrics: dict[str, Any] | None,
    gap_suggestion: dict[str, Any] | None,
    news_items: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not ticker or ticker in {"BTC", "SOL", "ETH", "DOGE", "USDC", "USDT"}:
        return None

    score = 45.0
    reasons: list[str] = []
    risks: list[str] = []
    next_check = "Re-run with fresh trend and quote data before adding."
    metrics = metrics or {}
    has_market = bool(metrics.get("has_data"))

    if has_market:
        r3 = _num(metrics.get("return_3m_pct"))
        r6 = _num(metrics.get("return_6m_pct"))
        r12 = _num(metrics.get("return_12m_pct"))
        rs6 = _num(metrics.get("relative_strength_6m_pct"))
        high_dist = _num(metrics.get("distance_from_52w_high_pct"))
        above50 = metrics.get("above_sma_50") is True
        above200 = metrics.get("above_sma_200") is True
        if r3 is not None and r3 > 5:
            score += 9; reasons.append("Positive 3-month momentum.")
        elif r3 is not None and r3 < -5:
            score -= 8; risks.append("Negative 3-month momentum.")
        if r6 is not None and r6 > 8:
            score += 12; reasons.append("Strong 6-month momentum.")
        elif r6 is not None and r6 < 0:
            score -= 10; risks.append("6-month momentum is negative.")
        if r12 is not None and r12 > 15:
            score += 10; reasons.append("12-month leadership trend is positive.")
        if rs6 is not None and rs6 > 0:
            score += 8; reasons.append("Relative strength is beating the benchmark.")
        elif rs6 is not None and rs6 < -5:
            score -= 6; risks.append("Relative strength is lagging the benchmark.")
        if above200:
            score += 9; reasons.append("Price is above the 200-day trend filter.")
        else:
            score -= 12; risks.append("Price is below the 200-day trend filter.")
        if above50:
            score += 5; reasons.append("Price is above the 50-day trend filter.")
        if high_dist is not None:
            pullback = abs(high_dist)
            if -float(getattr(config, "STOCK_MOMENTUM_PULLBACK_FROM_HIGH_PCT", 8) or 8) <= high_dist <= -2:
                score += 4; reasons.append("Within a reasonable pullback from 52-week highs.")
            elif high_dist > -float(getattr(config, "STOCK_MOMENTUM_OVEREXTENDED_FROM_HIGH_PCT", 2) or 2):
                risks.append("Very close to 52-week highs; avoid chasing without a setup.")
    else:
        risks.append("No market trend data available; lower confidence stock setup.")

    allocation = _num((holding or {}).get("allocation_pct"))
    if allocation is None and recommendation:
        allocation = _num(recommendation.get("allocation_pct"))
    max_alloc = float(getattr(config, "STOCK_MOMENTUM_MAX_SINGLE_NAME_ALLOCATION_PCT", 15) or 15)
    if holding:
        if allocation is not None and allocation >= max_alloc:
            score -= 10; risks.append("Already near/above max single-name allocation target.")
        else:
            score += 2; reasons.append("Already held; evaluate as add-size candidate rather than brand-new entry.")
    else:
        score += 5; reasons.append("Not currently held; valid new stock candidate.")

    if _theme_priority_score(ticker) >= 4:
        score += 6; reasons.append("Ticker fits a macro-priority growth bucket.")
    if gap_suggestion:
        score += 5; reasons.append("Portfolio gap engine also flagged this as useful exposure.")
    if news_items:
        score += 3; reasons.append("Recent relevant news/catalyst visibility exists.")
    if ticker in SPECULATIVE_TICKERS:
        score -= 4; risks.append("Speculative/high-beta name; size smaller and require stronger confirmation.")

    score = round(max(0.0, min(100.0, score)), 1)
    action = _action_for(score, has_market, metrics, allocation)
    if action == "CONSIDER ADDING":
        next_check = "Consider adding only after live price confirms trend support and position sizing fits the target bucket."
    elif action == "ADD ON PULLBACK":
        next_check = "Do not chase; watch for a controlled pullback or support hold before adding."
    elif action == "WATCH / CONFIRM TREND":
        next_check = "Keep on watchlist until trend, relative strength, or catalyst quality improves."
    else:
        next_check = "Avoid adding until the trend/risk profile improves."

    return {
        "ticker": ticker,
        "score": score,
        "action": action,
        "portfolio_status": "Already held" if holding else "Not currently held",
        "allocation_pct": allocation,
        "has_market_data": has_market,
        "market_metrics": metrics,
        "gap_suggestion": gap_suggestion or {},
        "reasons": _dedupe(reasons)[:6],
        "risks": _dedupe(risks)[:6],
        "next_check": next_check,
    }


def _action_for(score: float, has_market: bool, metrics: dict[str, Any], allocation: float | None) -> str:
    high_dist = _num(metrics.get("distance_from_52w_high_pct")) if metrics else None
    max_alloc = float(getattr(config, "STOCK_MOMENTUM_MAX_SINGLE_NAME_ALLOCATION_PCT", 15) or 15)
    if allocation is not None and allocation >= max_alloc:
        return "HOLD / DO NOT ADD"
    if score >= 78 and has_market:
        if high_dist is not None and high_dist > -float(getattr(config, "STOCK_MOMENTUM_OVEREXTENDED_FROM_HIGH_PCT", 2) or 2):
            return "ADD ON PULLBACK"
        return "CONSIDER ADDING"
    if score >= float(getattr(config, "STOCK_MOMENTUM_MIN_SCORE_TO_CONSIDER", 62) or 62):
        return "WATCH / CONFIRM TREND"
    return "AVOID ADDING"


def _finalize(result: dict[str, Any]) -> dict[str, Any]:
    items = result.get("items", []) or []
    result["summary"] = {
        "candidate_count": len(items),
        "consider_add_count": sum(1 for item in items if item.get("action") == "CONSIDER ADDING"),
        "pullback_count": sum(1 for item in items if item.get("action") == "ADD ON PULLBACK"),
        "watch_count": sum(1 for item in items if "WATCH" in str(item.get("action") or "")),
        "avoid_count": sum(1 for item in items if "AVOID" in str(item.get("action") or "")),
    }
    result["has_data"] = bool(items)
    return result


def _held_by_ticker(positions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    total = sum(float(p.get("market_value") or 0) for p in positions or []) or 1.0
    result: dict[str, dict[str, Any]] = {}
    for p in positions or []:
        ticker = str(p.get("ticker") or "").upper().strip()
        if not ticker:
            continue
        existing = result.setdefault(ticker, {"ticker": ticker, "market_value": 0.0, "accounts": []})
        existing["market_value"] += float(p.get("market_value") or 0)
        existing.setdefault("accounts", []).append(p.get("account"))
    for row in result.values():
        row["allocation_pct"] = row["market_value"] / total * 100.0
    return result


def _gap_suggestions_by_ticker(portfolio_gap_analysis: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out = {}
    for item in (portfolio_gap_analysis or {}).get("suggestions", []) or []:
        ticker = str(item.get("ticker") or "").upper().strip()
        if ticker:
            out[ticker] = item
    return out


def _theme_priority_score(ticker: str) -> int:
    ticker = str(ticker or "").upper().strip()
    if ticker in TECH_BUCKET_TICKERS:
        return 6
    if ticker in {"NVO", "LLY", "ISRG", "TMO", "ALGN"}:
        return 5
    if ticker in {"FSLR", "VST", "CEG", "ETN", "GEV"}:
        return 5
    if ticker in {"LMT", "RTX", "NOC", "KTOS"}:
        return 4
    if ticker in {"LULU", "SBUX", "ELF", "W"}:
        return 3
    return 1


def _num(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text); out.append(text)
    return out
