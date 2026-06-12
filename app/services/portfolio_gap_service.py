"""
app/services/portfolio_gap_service.py — Portfolio Gap / Sector Suggestions v1.

Rule-based, defensive analysis for aggressive-growth portfolio construction.
This module is intentionally stock-focused. It does not feed the earnings-calendar
trade engine and does not place trades.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from app import config
from app.services.data_state_message_service import data_state_message, required_market_metrics_complete


CORE_BUCKETS = [
    "AI / Semiconductors",
    "Mega-cap Tech / Cloud",
    "Software / Fintech",
    "Energy / Utilities / Infrastructure",
    "Healthcare / Biotech",
    "Industrials / Defense / Robotics",
    "Financials",
    "Consumer / Retail",
    "International / ADR",
]

RISK_BUCKETS = [
    "Crypto / Digital Assets",
    "Speculative / High Beta",
    "Leveraged ETFs",
]

# Tickers can intentionally map to multiple buckets. For example SOXL counts as
# both AI/Semis exposure and Leveraged ETF risk, per the user's preference.
TICKER_BUCKET_MAP: dict[str, dict[str, list[str]]] = {
    # Current holdings / common large-cap tech
    "NVDA": {"core": ["AI / Semiconductors", "Mega-cap Tech / Cloud"]},
    "AMZN": {"core": ["Mega-cap Tech / Cloud", "Consumer / Retail"]},
    "GOOGL": {"core": ["Mega-cap Tech / Cloud"]},
    "META": {"core": ["Mega-cap Tech / Cloud"]},
    "MSFT": {"core": ["Mega-cap Tech / Cloud", "Software / Fintech"]},
    "ORCL": {"core": ["Mega-cap Tech / Cloud", "Software / Fintech"]},
    "IBM": {"core": ["Mega-cap Tech / Cloud", "Software / Fintech"]},
    "CRM": {"core": ["Software / Fintech"]},
    "SNOW": {"core": ["Software / Fintech"]},
    "DDOG": {"core": ["Software / Fintech"]},
    "NET": {"core": ["Software / Fintech"]},
    "CRWD": {"core": ["Software / Fintech"]},
    "ZS": {"core": ["Software / Fintech"]},
    "PANW": {"core": ["Software / Fintech"]},

    # Semis / AI infrastructure
    "AMD": {"core": ["AI / Semiconductors"]},
    "AVGO": {"core": ["AI / Semiconductors"]},
    "TSM": {"core": ["AI / Semiconductors", "International / ADR"]},
    "ASML": {"core": ["AI / Semiconductors", "International / ADR"]},
    "MU": {"core": ["AI / Semiconductors"]},
    "CRDO": {"core": ["AI / Semiconductors"], "risk": ["Speculative / High Beta"]},
    "SOXL": {"core": ["AI / Semiconductors"], "risk": ["Leveraged ETFs", "Speculative / High Beta"]},
    "SOXX": {"core": ["AI / Semiconductors"]},
    "SMH": {"core": ["AI / Semiconductors"]},

    # Fintech / financials
    "SOFI": {"core": ["Software / Fintech"], "risk": ["Speculative / High Beta"]},
    "PYPL": {"core": ["Software / Fintech"]},
    "HOOD": {"core": ["Software / Fintech"], "risk": ["Speculative / High Beta"]},
    "JPM": {"core": ["Financials"]},
    "V": {"core": ["Financials", "Software / Fintech"]},
    "MA": {"core": ["Financials", "Software / Fintech"]},

    # Energy / infrastructure / power demand
    "VST": {"core": ["Energy / Utilities / Infrastructure"]},
    "CEG": {"core": ["Energy / Utilities / Infrastructure"]},
    "NEE": {"core": ["Energy / Utilities / Infrastructure"]},
    "FSLR": {"core": ["Energy / Utilities / Infrastructure"]},
    "SMR": {"core": ["Energy / Utilities / Infrastructure"], "risk": ["Speculative / High Beta"]},

    # Healthcare / biotech
    "NVO": {"core": ["Healthcare / Biotech", "International / ADR"]},
    "LLY": {"core": ["Healthcare / Biotech"]},
    "ISRG": {"core": ["Healthcare / Biotech", "Industrials / Defense / Robotics"]},
    "ALGN": {"core": ["Healthcare / Biotech"]},
    "MRNA": {"core": ["Healthcare / Biotech"], "risk": ["Speculative / High Beta"]},

    # Industrials / defense / robotics
    "PLTR": {"core": ["Software / Fintech", "Industrials / Defense / Robotics"], "risk": ["Speculative / High Beta"]},
    "ACHR": {"core": ["Industrials / Defense / Robotics"], "risk": ["Speculative / High Beta"]},
    "RKLB": {"core": ["Industrials / Defense / Robotics"], "risk": ["Speculative / High Beta"]},
    "ONDS": {"core": ["Industrials / Defense / Robotics"], "risk": ["Speculative / High Beta"]},
    "TER": {"core": ["Industrials / Defense / Robotics", "AI / Semiconductors"]},

    # Consumer / retail / brands
    "NKE": {"core": ["Consumer / Retail"]},
    "LULU": {"core": ["Consumer / Retail"]},
    "SBUX": {"core": ["Consumer / Retail"]},
    "ELF": {"core": ["Consumer / Retail"], "risk": ["Speculative / High Beta"]},
    "BYND": {"core": ["Consumer / Retail"], "risk": ["Speculative / High Beta"]},
    "W": {"core": ["Consumer / Retail"], "risk": ["Speculative / High Beta"]},

    # International / ADR / misc user watchlist
    "FANUY": {"core": ["International / ADR", "Industrials / Defense / Robotics"]},
    "STLA": {"core": ["Consumer / Retail", "International / ADR"]},
    "UFG": {"core": ["Financials"]},

    # Crypto
    "BTC": {"risk": ["Crypto / Digital Assets"]},
    "ETH": {"risk": ["Crypto / Digital Assets"]},
    "SOL": {"risk": ["Crypto / Digital Assets", "Speculative / High Beta"]},
}

TECH_KEYWORDS = ("AI", "CLOUD", "SOFTWARE", "DATA", "CYBER", "SEMICONDUCTOR", "CHIP")


def build_portfolio_gap_analysis(
    positions: list[dict[str, Any]],
    watchlist_candidates: dict[str, Any] | None,
    watchlist_review: dict[str, Any] | None,
    recommendations: list[dict[str, Any]] | None,
    market_metrics: dict[str, dict[str, Any]] | None,
    news_map: dict[str, list[dict[str, Any]]] | None,
    log_print=None,
) -> dict[str, Any]:
    if not getattr(config, "PORTFOLIO_GAP_ENABLED", True):
        return {
            "source": "portfolio_gap_sector_suggestions_v1",
            "enabled": False,
            "has_data": False,
            "summary": {},
            "exposure_rows": [],
            "risk_rows": [],
            "suggestions": [],
            "errors": [],
        }

    try:
        core_targets = _parse_target_map(getattr(config, "PORTFOLIO_GAP_CORE_TARGETS", ""), _default_core_targets())
        risk_targets = _parse_target_map(getattr(config, "PORTFOLIO_GAP_RISK_TARGETS", ""), _default_risk_targets())
        macro_winners = set(getattr(config, "PORTFOLIO_GAP_MACRO_WINNING_BUCKETS", []) or [])

        owned_tickers = {str(p.get("ticker", "")).upper().strip() for p in positions if p.get("ticker")}
        total_value = sum(_to_float(p.get("market_value")) for p in positions if _to_float(p.get("market_value")) > 0)
        total_value = total_value if total_value > 0 else 1.0

        core_values: dict[str, float] = defaultdict(float)
        risk_values: dict[str, float] = defaultdict(float)
        unknown_value = 0.0
        single_name_values: dict[str, float] = defaultdict(float)

        for position in positions:
            ticker = str(position.get("ticker", "")).upper().strip()
            value = max(_to_float(position.get("market_value")), 0.0)
            if not ticker or value <= 0:
                continue
            single_name_values[ticker] += value
            classification = classify_ticker(ticker)
            core_buckets = classification.get("core", [])
            risk_buckets = classification.get("risk", [])
            if core_buckets:
                split_value = value / len(core_buckets)
                for bucket in core_buckets:
                    core_values[bucket] += split_value
            elif not risk_buckets:
                unknown_value += value
            for bucket in risk_buckets:
                risk_values[bucket] += value

        exposure_rows = _build_exposure_rows(core_values, core_targets, total_value, macro_winners, unknown_value)
        risk_rows = _build_risk_rows(risk_values, risk_targets, total_value, single_name_values)

        watchlist_items = (watchlist_candidates or {}).get("items", []) or []
        review_by_ticker = {
            str(item.get("ticker", "")).upper().strip(): item
            for item in (watchlist_review or {}).get("items", []) or []
            if item.get("ticker")
        }
        recommendation_by_ticker = {
            str(item.get("ticker", "")).upper().strip(): item
            for item in (recommendations or [])
            if item.get("ticker")
        }

        suggestions = []
        for item in watchlist_items:
            ticker = str(item.get("ticker", "")).upper().strip()
            if not ticker:
                continue
            if ticker in owned_tickers and not getattr(config, "PORTFOLIO_GAP_INCLUDE_ALREADY_HELD", True):
                continue
            classification = classify_ticker(ticker)
            review_item = review_by_ticker.get(ticker, {})
            rec_item = recommendation_by_ticker.get(ticker, {})
            metrics = (market_metrics or {}).get(ticker, {}) or rec_item.get("market_metrics", {}) or {}
            news_items = (news_map or {}).get(ticker, []) or []
            suggestion = _score_watchlist_suggestion(
                ticker=ticker,
                classification=classification,
                review_item=review_item,
                metrics=metrics,
                news_items=news_items,
                exposure_rows=exposure_rows,
                risk_rows=risk_rows,
                macro_winners=macro_winners,
                already_held=ticker in owned_tickers,
                watchlists=item.get("watchlists", []),
                sources=item.get("sources", []),
            )
            suggestions.append(suggestion)

        min_score = float(getattr(config, "PORTFOLIO_GAP_MIN_SUGGESTION_SCORE", 55))
        max_suggestions = int(getattr(config, "PORTFOLIO_GAP_MAX_SUGGESTIONS", 10))
        suggestions = [s for s in suggestions if _to_float(s.get("score")) >= min_score]
        suggestions.sort(key=lambda s: (_to_float(s.get("score")), str(s.get("ticker", ""))), reverse=True)
        suggestions = suggestions[:max_suggestions]

        overweight = [row for row in exposure_rows if row.get("status") in {"OVERWEIGHT", "HIGH / MONITOR"}]
        underweight = [row for row in exposure_rows if row.get("status") in {"UNDERWEIGHT", "MISSING"}]
        macro_reinforce = [row for row in exposure_rows if row.get("macro_bias") == "Macro winner / reinforce"]

        result = {
            "source": "portfolio_gap_sector_suggestions_v1",
            "enabled": True,
            "has_data": True,
            "target_profile": getattr(config, "PORTFOLIO_GAP_TARGET_PROFILE", "aggressive_macro_growth"),
            "summary": {
                "total_value": total_value,
                "core_bucket_count": len(exposure_rows),
                "risk_bucket_count": len(risk_rows),
                "overweight_count": len(overweight),
                "underweight_count": len(underweight),
                "macro_reinforce_count": len(macro_reinforce),
                "suggestion_count": len(suggestions),
            },
            "exposure_rows": exposure_rows,
            "risk_rows": risk_rows,
            "suggestions": suggestions,
            "notes": [
                "Targets are aggressive-growth macro targets, not a balanced-index allocation.",
                "ETF tickers can count toward both sector exposure and leveraged/speculative risk.",
                "Crypto is shown as its own risk bucket rather than as a sector gap.",
                "Macro-winning buckets are configurable until a live macro-regime module is added.",
            ],
            "errors": [],
        }
        if log_print:
            log_print(
                "Portfolio Gap / Sector Suggestions v1 produced "
                f"{len(exposure_rows)} exposure row(s), {len(risk_rows)} risk row(s), "
                f"{len(suggestions)} suggestion(s)."
            )
        return result
    except Exception as exc:  # defensive: never block /run
        if log_print:
            log_print(f"Portfolio Gap / Sector Suggestions v1 failed: {exc}")
        return {
            "source": "portfolio_gap_sector_suggestions_v1",
            "enabled": True,
            "has_data": False,
            "summary": {},
            "exposure_rows": [],
            "risk_rows": [],
            "suggestions": [],
            "errors": [str(exc)],
        }


def classify_ticker(ticker: str) -> dict[str, list[str]]:
    clean = str(ticker or "").upper().strip()
    if clean in TICKER_BUCKET_MAP:
        mapped = TICKER_BUCKET_MAP[clean]
        return {
            "core": list(dict.fromkeys(mapped.get("core", []) or [])),
            "risk": list(dict.fromkeys(mapped.get("risk", []) or [])),
        }

    risk: list[str] = []
    core: list[str] = []
    if clean.endswith("X") or clean in {"TQQQ", "UPRO", "SQQQ", "LABU", "TECL"}:
        risk.extend(["Leveraged ETFs", "Speculative / High Beta"])
    if clean.endswith("Y") or clean in {"BABA", "JD", "PDD", "TM", "SONY"}:
        core.append("International / ADR")
    if clean in {"QQQ", "SPY", "VTI", "VOO", "IWM"}:
        core.append("ETFs / Broad Market")
    return {"core": list(dict.fromkeys(core)), "risk": list(dict.fromkeys(risk))}


def _build_exposure_rows(
    core_values: dict[str, float],
    core_targets: dict[str, float],
    total_value: float,
    macro_winners: set[str],
    unknown_value: float,
) -> list[dict[str, Any]]:
    rows = []
    all_buckets = list(dict.fromkeys(list(core_targets.keys()) + CORE_BUCKETS + list(core_values.keys())))
    for bucket in all_buckets:
        if bucket == "ETFs / Broad Market":
            continue
        value = core_values.get(bucket, 0.0)
        current_pct = value / total_value * 100.0
        target_pct = core_targets.get(bucket, 0.0)
        gap_pct = target_pct - current_pct
        status = _exposure_status(current_pct, target_pct)
        macro_bias = "Macro winner / reinforce" if bucket in macro_winners else "Neutral"
        if status in {"OVERWEIGHT", "HIGH / MONITOR"} and macro_bias.startswith("Macro"):
            guidance = "Winning macro bucket, but size should still be monitored. Add only to leaders."
        elif status in {"UNDERWEIGHT", "MISSING"} and macro_bias.startswith("Macro"):
            guidance = "Below target in a macro-priority bucket; consider adding the best watchlist candidates."
        elif status in {"UNDERWEIGHT", "MISSING"}:
            guidance = "Below target; fill only with high-quality/momentum names, not just for diversification."
        else:
            guidance = "Near target; reinforce winners selectively."
        rows.append({
            "bucket": bucket,
            "current_pct": current_pct,
            "target_pct": target_pct,
            "gap_pct": gap_pct,
            "status": status,
            "macro_bias": macro_bias,
            "guidance": guidance,
        })

    if unknown_value > 0:
        rows.append({
            "bucket": "Unknown / Needs Classification",
            "current_pct": unknown_value / total_value * 100.0,
            "target_pct": 0.0,
            "gap_pct": -(unknown_value / total_value * 100.0),
            "status": "REVIEW",
            "macro_bias": "Unknown",
            "guidance": "Add ticker mapping or company profile data for cleaner gap analysis.",
        })
    rows.sort(key=lambda r: (r.get("status") not in {"UNDERWEIGHT", "MISSING"}, -abs(_to_float(r.get("gap_pct")))))
    return rows


def _build_risk_rows(
    risk_values: dict[str, float],
    risk_targets: dict[str, float],
    total_value: float,
    single_name_values: dict[str, float],
) -> list[dict[str, Any]]:
    rows = []
    for bucket in RISK_BUCKETS:
        current_pct = risk_values.get(bucket, 0.0) / total_value * 100.0
        max_pct = risk_targets.get(bucket, 0.0)
        status = "OK"
        if max_pct and current_pct > max_pct * 1.25:
            status = "ABOVE RISK TARGET"
        elif max_pct and current_pct > max_pct:
            status = "NEAR / SLIGHTLY ABOVE TARGET"
        rows.append({
            "bucket": bucket,
            "current_pct": current_pct,
            "target_pct": max_pct,
            "gap_pct": max_pct - current_pct,
            "status": status,
            "guidance": _risk_guidance(bucket, status),
        })

    single_name_max = risk_targets.get("Single-Name Max", 15.0)
    largest = sorted(single_name_values.items(), key=lambda kv: kv[1], reverse=True)[:5]
    for ticker, value in largest:
        pct_value = value / total_value * 100.0
        if pct_value >= single_name_max * 0.8:
            rows.append({
                "bucket": f"Single-name concentration: {ticker}",
                "current_pct": pct_value,
                "target_pct": single_name_max,
                "gap_pct": single_name_max - pct_value,
                "status": "MONITOR" if pct_value <= single_name_max else "ABOVE RISK TARGET",
                "guidance": "Large positions can stay large if they are macro winners, but additions should be deliberate.",
            })
    return rows


def _score_watchlist_suggestion(
    ticker: str,
    classification: dict[str, list[str]],
    review_item: dict[str, Any],
    metrics: dict[str, Any],
    news_items: list[dict[str, Any]],
    exposure_rows: list[dict[str, Any]],
    risk_rows: list[dict[str, Any]],
    macro_winners: set[str],
    already_held: bool,
    watchlists: list[str] | None,
    sources: list[str] | None,
) -> dict[str, Any]:
    score = 50.0
    reasons: list[str] = []
    risks: list[str] = []
    next_check = "Consider adding only after confirming trend, liquidity, and thesis quality."
    core = classification.get("core", []) or ["Unknown / Needs Classification"]
    risk = classification.get("risk", []) or []
    exposure_by_bucket = {row.get("bucket"): row for row in exposure_rows}
    risk_by_bucket = {row.get("bucket"): row for row in risk_rows}

    if not already_held:
        score += 8
        reasons.append("Not currently held; can fill a portfolio gap without adding to existing concentration.")
    else:
        score -= 3
        reasons.append("Already held; use as add-size or trim/rebalance review rather than a new position.")

    for bucket in core:
        row = exposure_by_bucket.get(bucket, {})
        status = row.get("status")
        if bucket in macro_winners:
            score += 8
            reasons.append(f"{bucket} is a macro-priority bucket; reinforce only leaders.")
        if status in {"UNDERWEIGHT", "MISSING"}:
            score += 8
            reasons.append(f"Helps fill under-target exposure in {bucket}.")
        elif status in {"OVERWEIGHT", "HIGH / MONITOR"}:
            score -= 5
            risks.append(f"{bucket} exposure is already high; this should be a quality/momentum reinforcement, not blind diversification.")

    for bucket in risk:
        row = risk_by_bucket.get(bucket, {})
        status = row.get("status")
        if status == "ABOVE RISK TARGET":
            score -= 10
            risks.append(f"Adds to {bucket}, which is already above risk target.")
        elif status == "NEAR / SLIGHTLY ABOVE TARGET":
            score -= 5
            risks.append(f"Adds to {bucket}; size carefully.")
        else:
            risks.append(f"Carries {bucket} exposure; keep position sizing intentional.")

    review_score = _to_float(review_item.get("stock_score") or review_item.get("score"))
    if review_score >= 65:
        score += 6
        reasons.append("Watchlist stock review score is relatively strong.")
    elif review_score and review_score < 55:
        score -= 4
        risks.append("Watchlist stock review score is weak or uncertain.")

    market_complete = required_market_metrics_complete(metrics)
    if market_complete:
        if _to_float(metrics.get("return_6m_pct")) > 10 and metrics.get("above_sma_200") is True:
            score += 8
            reasons.append("Trend confirmation: positive 6-month return and above 200-day trend.")
        elif metrics.get("above_sma_200") is False:
            score -= 8
            risks.append("Below 200-day trend; wait for repair before adding.")
        if _to_float(metrics.get("relative_strength_6m_pct")) > 0:
            score += 4
            reasons.append("Relative strength versus benchmark is positive.")
    else:
        risks.append(data_state_message(metrics.get("data_state"), fetched_at=metrics.get("fetched_at"), reason=metrics.get("error")))

    if news_items:
        score += min(4, len(news_items) * 2)
        reasons.append("Recent relevant news/catalyst visibility exists.")

    if ticker in {"SOXL", "TQQQ", "UPRO", "LABU", "TECL"}:
        risks.append("Leveraged ETF: counts as both sector exposure and risk exposure.")
        next_check = "Consider only as tactical exposure, not long-term core sizing."

    category = "CONSIDER ADDING / RESEARCH"
    if score >= 75:
        category = "HIGH-PRIORITY CONSIDER ADDING"
        next_check = "Confirm trend/liquidity, then consider staged entry sizing."
    elif score >= 65:
        category = "CONSIDER ADDING / RESEARCH"
    elif score >= 55:
        category = "WATCH / RESEARCH"
    else:
        category = "LOW PRIORITY / WAIT"

    if not market_complete and "AVOID" not in category:
        category = "WATCH / DATA INCOMPLETE"
        next_check = "Wait for complete shared trend, liquidity, and freshness data before treating this as actionable."

    return {
        "ticker": ticker,
        "score": max(0.0, min(100.0, score)),
        "category": category,
        "already_held": already_held,
        "core_buckets": core,
        "risk_buckets": risk,
        "watchlists": watchlists or [],
        "sources": sources or [],
        "reasons": reasons[:5],
        "risks": risks[:5],
        "next_check": next_check,
        "market_metrics": metrics,
        "required_market_data_complete": market_complete,
    }


def _exposure_status(current_pct: float, target_pct: float) -> str:
    if target_pct <= 0:
        return "REVIEW" if current_pct > 0 else "NO TARGET"
    if current_pct < max(1.0, target_pct * 0.25):
        return "MISSING"
    if current_pct < target_pct * 0.75:
        return "UNDERWEIGHT"
    if current_pct > target_pct * 1.35:
        return "OVERWEIGHT"
    if current_pct > target_pct * 1.1:
        return "HIGH / MONITOR"
    return "NEAR TARGET"


def _risk_guidance(bucket: str, status: str) -> str:
    if bucket == "Crypto / Digital Assets":
        return "Separate high-volatility bucket; do not use it to fill equity sector gaps."
    if bucket == "Leveraged ETFs":
        return "Tactical only. Counts toward sector exposure and risk exposure."
    if status == "ABOVE RISK TARGET":
        return "Avoid adding unless there is a strong, time-bound tactical reason."
    return "Keep sizing deliberate and below target unless macro setup is exceptional."


def _parse_target_map(raw: str, fallback: dict[str, float]) -> dict[str, float]:
    result = dict(fallback)
    if not raw:
        return result
    for part in str(raw).split(","):
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        key = key.strip()
        try:
            result[key] = float(value.strip())
        except ValueError:
            continue
    return result


def _default_core_targets() -> dict[str, float]:
    return {
        "AI / Semiconductors": 18.0,
        "Mega-cap Tech / Cloud": 18.0,
        "Software / Fintech": 12.0,
        "Energy / Utilities / Infrastructure": 12.0,
        "Healthcare / Biotech": 10.0,
        "Industrials / Defense / Robotics": 10.0,
        "Financials": 8.0,
        "Consumer / Retail": 7.0,
        "International / ADR": 5.0,
    }


def _default_risk_targets() -> dict[str, float]:
    return {
        "Crypto / Digital Assets": 5.0,
        "Speculative / High Beta": 12.0,
        "Leveraged ETFs": 4.0,
        "Single-Name Max": 15.0,
    }


def _to_float(value: Any) -> float:
    try:
        if value is None:
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
