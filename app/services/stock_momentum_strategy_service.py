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
from app.services.data_state_message_service import data_state_message, required_market_metrics_complete
from app.services.strategy_row_normalization_service import normalize_strategy_row

LogFn = Callable[[str], None]

TECH_BUCKET_TICKERS = {
    "NVDA", "AMD", "AVGO", "MU", "TSM", "ASML", "CRDO", "ORCL", "MSFT", "AMZN", "GOOGL", "META", "PLTR"
}
SPECULATIVE_TICKERS = {"QBTS", "SMR", "SOFI", "HOOD", "BYND", "LPTH", "ONDS", "METU", "SNPW", "SNT", "BSTT", "CIBN"}
LEVERAGED_ETFS = {"SOXL", "TQQQ", "UPRO", "LABU", "TECL", "SPXL", "FAS", "NUGT", "ERX", "YINN"}


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
    has_market = required_market_metrics_complete(metrics)

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
        risks.append(data_state_message(metrics.get("data_state"), fetched_at=metrics.get("fetched_at"), reason=metrics.get("error")))

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
    entry = _entry_quality_gate(ticker, score, has_market, metrics, allocation, gap_suggestion)
    action = entry["action"]
    if action == "CONSIDER ADDING":
        next_check = "Consider adding only after live price confirms trend support and position sizing fits the target bucket."
    elif action == "ADD ON PULLBACK":
        next_check = "Do not chase; watch for a controlled pullback or support hold before adding."
    elif action == "WATCH / CONFIRM TREND":
        next_check = "Keep on watchlist until trend, relative strength, or catalyst quality improves."
    else:
        next_check = "Avoid adding until the trend/risk profile improves."
    if not entry["add_allowed_boolean"]:
        next_check = entry["suggested_entry_type"]

    above50 = (metrics or {}).get("above_sma_50")
    above200 = (metrics or {}).get("above_sma_200")
    r3 = _num((metrics or {}).get("return_3m_pct"))
    r6 = _num((metrics or {}).get("return_6m_pct"))
    avg_vol = _num((metrics or {}).get("average_volume_30d"))
    row = {
        "strategy_id": "stock_momentum",
        "ticker": ticker,
        "score": score,
        "momentum_score": score,
        "action": action,
        "portfolio_status": "Already held" if holding else "Not currently held",
        "allocation_pct": allocation,
        "has_market_data": has_market,
        "required_market_data_complete": has_market,
        "market_metrics": metrics,
        "gap_suggestion": gap_suggestion or {},
        **entry,
        "reasons": _dedupe(reasons)[:6],
        "risks": _dedupe(risks + entry["add_blockers"])[:8],
        "next_check": next_check,
        "relative_strength": _num((metrics or {}).get("relative_strength_6m_pct") or (metrics or {}).get("relative_strength_vs_qqq")),
        "trend_status": "clean" if (above50 and above200) else ("partial" if (above50 or above200) else "broken"),
        "volume_status": "adequate" if (avg_vol is not None and avg_vol >= 100_000) else ("low" if avg_vol is not None else "unavailable"),
        "price_action_status": "positive" if ((r3 or 0) > 0 and (r6 or 0) > 0) else ("mixed" if ((r3 or 0) > 0 or (r6 or 0) > 0) else "negative"),
        "risk_status": "elevated" if (risks or entry.get("add_blockers")) else "normal",
    }
    normalize_strategy_row(row, "stock_momentum")
    return row


def _entry_quality_gate(
    ticker: str,
    score: float,
    has_market: bool,
    metrics: dict[str, Any],
    allocation: float | None,
    gap_suggestion: dict[str, Any] | None,
) -> dict[str, Any]:
    """Separate strong momentum from a buyable entry."""
    extension_50 = _num(metrics.get("price_vs_sma_50_pct"))
    extension_200 = _num(metrics.get("price_vs_sma_200_pct"))
    volatility = _num(metrics.get("realized_volatility_30d") or metrics.get("volatility_30d_pct"))
    current_price = _num(metrics.get("current_price"))
    r3 = _num(metrics.get("return_3m_pct"))
    r6 = _num(metrics.get("return_6m_pct"))
    relative_strength = _num(metrics.get("relative_strength_6m_pct") or metrics.get("relative_strength_vs_qqq"))
    above50 = metrics.get("above_sma_50") is True
    above200 = metrics.get("above_sma_200") is True
    blockers: list[str] = []
    max_alloc = float(getattr(config, "STOCK_MOMENTUM_MAX_SINGLE_NAME_ALLOCATION_PCT", 15) or 15)
    max_extension = float(getattr(config, "STOCK_MOMENTUM_MAX_EXTENSION_VS_50D_PCT", 30) or 30)
    high_vol = float(getattr(config, "STOCK_MOMENTUM_HIGH_VOLATILITY_30D_PCT", 80) or 80)
    extreme_vol = float(getattr(config, "STOCK_MOMENTUM_EXTREME_VOLATILITY_30D_PCT", 100) or 100)
    leveraged = ticker in LEVERAGED_ETFS
    speculative = ticker in SPECULATIVE_TICKERS

    if not has_market:
        blockers.append("Complete market metrics required before an actionable add.")
    if not above200:
        blockers.append("Price must be above the 200-day trend.")
    if not above50:
        blockers.append("Price must be above or reclaiming the 50-day trend.")
    if r3 is None or r3 <= 0:
        blockers.append("Positive 3-month return required.")
    if r6 is None or r6 <= 0:
        blockers.append("Positive 6-month return required.")
    if relative_strength is None or relative_strength <= 0:
        blockers.append("Positive relative strength versus QQQ required.")
    if allocation is not None and allocation >= max_alloc:
        blockers.append("Single-name allocation is already at or above target.")
    if extension_50 is None:
        blockers.append("50-day extension unavailable.")
    elif extension_50 > max_extension:
        blockers.append(f"Price is {extension_50:.1f}% above the 50-day average; do not chase.")
    if volatility is None:
        blockers.append("30-day realized volatility unavailable.")
    if leveraged:
        blockers.append("Leveraged ETF is tactical only; never a normal long-term add.")

    initial_stop = round(current_price * 0.92, 2) if current_price is not None else None
    take_profit = round(current_price * 1.15, 2) if current_price is not None else None
    if initial_stop is None or take_profit is None:
        blockers.append("Stop and take-profit guidance unavailable.")

    clean_trend = above50 and above200 and (r3 or 0) > 0 and (r6 or 0) > 0 and (relative_strength or 0) > 0
    bucket_support = bool(gap_suggestion) or score >= 85
    if not bucket_support:
        blockers.append("Portfolio bucket support or clear leadership not confirmed.")

    entry_quality = "NO_BUY"
    suggested_entry_type = "Wait for trend repair and complete risk guidance."
    max_position_size_hint = "No new position."
    if leveraged:
        entry_quality = "TACTICAL_ONLY"
        suggested_entry_type = "Tactical only; wait for pullback/reclaim and use reduced size."
        max_position_size_hint = "Tactical starter only; below normal stock sizing."
    elif extension_50 is not None and extension_50 > max_extension:
        entry_quality = "EXTENDED_WAIT"
        suggested_entry_type = "Wait for pullback, consolidation, or 50-day reclaim."
        max_position_size_hint = "No add while extended."
    elif volatility is not None and volatility >= extreme_vol:
        entry_quality = "TACTICAL_ONLY"
        suggested_entry_type = "Tactical starter only after consolidation/reclaim."
        max_position_size_hint = "At most one-quarter normal position."
    elif volatility is not None and volatility >= high_vol:
        entry_quality = "HIGH_BETA_STARTER_ONLY"
        suggested_entry_type = "Starter only after support confirmation."
        max_position_size_hint = "At most one-half normal position."
    elif not clean_trend:
        entry_quality = "BROKEN_WAIT"
        suggested_entry_type = "Wait for trend repair or reclaim."
    elif blockers:
        entry_quality = "BUYABLE_PULLBACK" if all("unavailable" not in blocker.lower() for blocker in blockers) else "NO_BUY"
        suggested_entry_type = "Buyable only after blockers clear."
        max_position_size_hint = "Starter only after blockers clear."
    else:
        entry_quality = "BUYABLE_NOW"
        suggested_entry_type = "Staged entry near trend support; do not chase intraday strength."
        max_position_size_hint = "Starter position; scale only while bucket and single-name limits remain valid."

    add_allowed = entry_quality == "BUYABLE_NOW" and not blockers
    action = _action_for(score, has_market, metrics, allocation)
    if not add_allowed:
        if allocation is not None and allocation >= max_alloc:
            action = "HOLD / DO NOT ADD"
        elif entry_quality == "TACTICAL_ONLY":
            action = "TACTICAL ONLY / DO NOT CHASE"
        elif entry_quality == "HIGH_BETA_STARTER_ONLY":
            action = "STARTER ONLY / WAIT FOR PULLBACK"
        elif entry_quality in {"EXTENDED_WAIT", "BUYABLE_PULLBACK"}:
            action = "ADD ON PULLBACK"
        elif entry_quality == "BROKEN_WAIT":
            action = "WATCH / CONFIRM TREND"
        else:
            action = "HOLD / DO NOT ADD" if allocation is not None and allocation >= max_alloc else "WATCH / RESEARCH"

    return {
        "action": action,
        "entry_quality": entry_quality,
        "extension_vs_50d_pct": extension_50,
        "extension_vs_200d_pct": extension_200,
        "realized_volatility_30d_pct": volatility,
        "bucket_status": "SUPPORTED" if bucket_support else "NOT CONFIRMED",
        "suggested_entry_type": suggested_entry_type,
        "initial_stop": initial_stop,
        "take_profit_or_trailing_exit": (
            f"Initial take-profit review near {take_profit:.2f}; trail remainder if trend holds."
            if take_profit is not None else None
        ),
        "max_position_size_hint": max_position_size_hint,
        "add_allowed_boolean": add_allowed,
        "add_blockers": _dedupe(blockers),
    }


def _action_for(score: float, has_market: bool, metrics: dict[str, Any], allocation: float | None) -> str:
    high_dist = _num(metrics.get("distance_from_52w_high_pct")) if metrics else None
    max_alloc = float(getattr(config, "STOCK_MOMENTUM_MAX_SINGLE_NAME_ALLOCATION_PCT", 15) or 15)
    if allocation is not None and allocation >= max_alloc:
        return "HOLD / DO NOT ADD"
    if not has_market:
        return "WATCH / DATA INCOMPLETE"
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
