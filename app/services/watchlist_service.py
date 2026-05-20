"""
app/services/watchlist_service.py — Watchlist candidate ingestion.

This service creates a "watching" category from Robinhood watchlists and/or a
manual WATCHLIST_TICKERS fallback. Watchlist tickers are not treated as owned
positions; they are added to the external scan universe so earnings/options
modules can evaluate them as possible new trades or stock-add candidates.
"""

from __future__ import annotations

from typing import Any, Callable

from app import config
from app.providers.robinhood_provider import get_watchlist_tickers
from app.services.tradier_service import CRYPTO_TICKERS
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]


def get_watchlist_candidates(
    positions: list[dict[str, Any]],
    log_print: LogFn | None = None,
    run_mode: str = "prod",
) -> dict[str, Any]:
    """Return normalized watchlist candidates from Robinhood and manual config."""
    logger = log_print or (lambda msg: print(msg, flush=True))
    enabled = bool(getattr(config, "WATCHLIST_ENABLED", True))
    held_tickers = _held_tickers(positions)

    result: dict[str, Any] = {
        "source": "watchlist_pipeline_v1",
        "enabled": enabled,
        "has_data": False,
        "items": [],
        "tickers": [],
        "errors": [],
        "summary": {
            "candidate_count": 0,
            "new_candidate_count": 0,
            "already_held_count": 0,
            "scan_universe_count": 0,
        },
    }

    if not enabled:
        result["errors"].append("WATCHLIST_ENABLED=false")
        logger("Watchlist Candidate Pipeline v1 disabled by WATCHLIST_ENABLED=false.")
        return result

    logger("Fetching Watchlist Candidate Pipeline v1...")

    max_tickers = max(1, int(getattr(config, "WATCHLIST_MAX_TICKERS_PER_RUN", 20) or 20))
    source_flags = {part.strip().lower() for part in str(getattr(config, "WATCHLIST_SOURCE", "robinhood,manual")).split(",") if part.strip()}

    combined: dict[str, dict[str, Any]] = {}

    if "robinhood" in source_flags:
        try:
            rh_result = get_watchlist_tickers(
                watchlist_names=getattr(config, "WATCHLIST_NAMES", []) or [],
                max_tickers=max_tickers,
            )
            for error in rh_result.get("errors", []) or []:
                result["errors"].append(str(error))
            for item in rh_result.get("items", []) or []:
                ticker = _clean_ticker(item.get("ticker"))
                if not ticker or ticker in CRYPTO_TICKERS:
                    continue
                _merge_candidate(
                    combined,
                    ticker=ticker,
                    source="robinhood",
                    watchlist_name=item.get("watchlist_name") or "Robinhood",
                )
        except Exception as e:
            safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.RUN_TOKEN])
            result["errors"].append(f"Robinhood watchlist fetch failed: {safe_error}")

    if "manual" in source_flags or getattr(config, "WATCHLIST_TICKERS", []):
        for ticker in getattr(config, "WATCHLIST_TICKERS", []) or []:
            ticker = _clean_ticker(ticker)
            if not ticker or ticker in CRYPTO_TICKERS:
                continue
            _merge_candidate(combined, ticker=ticker, source="manual", watchlist_name="WATCHLIST_TICKERS")

    items: list[dict[str, Any]] = []
    for ticker, item in combined.items():
        already_held = ticker in held_tickers
        if already_held and not bool(getattr(config, "WATCHLIST_INCLUDE_ALREADY_HELD", True)):
            continue
        item["already_held"] = already_held
        item["portfolio_status"] = "Already held" if already_held else "Not currently held"
        item["scan_category"] = "existing holding watch" if already_held else "new candidate watch"
        items.append(item)

    items.sort(key=lambda x: (x.get("already_held", False), x.get("ticker", "")))
    items = items[:max_tickers]

    result["items"] = items
    result["tickers"] = [item["ticker"] for item in items]
    result["has_data"] = bool(items)
    result["summary"] = {
        "candidate_count": len(items),
        "new_candidate_count": sum(1 for item in items if not item.get("already_held")),
        "already_held_count": sum(1 for item in items if item.get("already_held")),
        "scan_universe_count": len(items),
    }

    logger(
        "Watchlist Candidate Pipeline v1 produced "
        f"{result['summary']['candidate_count']} candidate(s), "
        f"{result['summary']['new_candidate_count']} new, "
        f"{result['summary']['already_held_count']} already held."
    )
    if result["errors"]:
        logger("Watchlist Candidate Pipeline v1 warnings: " + "; ".join(result["errors"][:2]))

    return result


def synthetic_positions_from_watchlist(
    watchlist_result: dict[str, Any],
    positions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Create lightweight pseudo-positions so existing scan services can use watchlist tickers."""
    held = _held_tickers(positions)
    synthetic: list[dict[str, Any]] = []
    for item in (watchlist_result or {}).get("items", []) or []:
        ticker = _clean_ticker(item.get("ticker"))
        if not ticker or ticker in held or ticker in CRYPTO_TICKERS:
            continue
        synthetic.append(
            {
                "ticker": ticker,
                "quantity": 0.0,
                "avg_buy_price": None,
                "current_price": None,
                "gain_loss": None,
                "gain_loss_pct": None,
                "market_value": 0.0,
                "account": "Watchlist",
                "is_watchlist_candidate": True,
                "watchlists": item.get("watchlists", []),
            }
        )
    return synthetic


def merge_watchlist_universe_positions(
    positions: list[dict[str, Any]],
    watchlist_result: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return positions plus watchlist pseudo-positions, optionally prioritizing watchlist for scans."""
    synthetic = synthetic_positions_from_watchlist(watchlist_result, positions)
    if bool(getattr(config, "WATCHLIST_PRIORITIZE_FOR_SCANS", True)):
        return synthetic + positions
    return positions + synthetic


def _merge_candidate(bucket: dict[str, dict[str, Any]], ticker: str, source: str, watchlist_name: str) -> None:
    if ticker not in bucket:
        bucket[ticker] = {
            "ticker": ticker,
            "sources": [],
            "watchlists": [],
        }
    if source not in bucket[ticker]["sources"]:
        bucket[ticker]["sources"].append(source)
    if watchlist_name and watchlist_name not in bucket[ticker]["watchlists"]:
        bucket[ticker]["watchlists"].append(str(watchlist_name))


def _held_tickers(positions: list[dict[str, Any]]) -> set[str]:
    return {_clean_ticker(pos.get("ticker")) for pos in positions if _clean_ticker(pos.get("ticker"))}


def _clean_ticker(value: Any) -> str:
    return str(value or "").upper().strip()
