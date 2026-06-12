"""Backward-compatible adapters around existing strategy services."""

from __future__ import annotations

from typing import Any

from app import config
from app.models.market_data_models import StrategyDisplayMetadata, StrategyResult
from app.services.data_requirement_service import earnings_calendar_requirement, skew_vertical_requirement, stock_momentum_requirement


def _tickers(context: Any) -> list[str]:
    return list(dict.fromkeys(str(ticker).upper() for ticker in getattr(context, "analysis_tickers", []) if ticker))


class EarningsCalendarStrategy:
    strategy_id = "earnings_calendar"
    strategy_label = "Earnings Calendar"
    version = "v1"
    display_metadata = StrategyDisplayMetadata("CAL", "Earnings Calendar Setups", "Calendars", 10)

    def is_enabled(self) -> bool:
        return bool(config.CALENDAR_SCANNER_ENABLED)

    def build_universe(self, context: Any) -> list[str]:
        return _tickers(context)

    def data_requirements(self, context: Any, universe: list[str]):
        return earnings_calendar_requirement(universe)

    def normalize_result(self, raw: dict[str, Any], context: Any) -> StrategyResult:
        rows = raw.get("new_trade_rows", []) or raw.get("items", []) or []
        return _normalize(self, raw, rows)


class SkewMomentumVerticalStrategy:
    strategy_id = "skew_momentum_vertical"
    strategy_label = "Skew Momentum Vertical"
    version = "v1"
    display_metadata = StrategyDisplayMetadata("SKEW", "Skew Momentum Verticals", "Skew Verticals", 20)

    def is_enabled(self) -> bool:
        return bool(config.SKEW_VERTICAL_STRATEGY_ENABLED)

    def build_universe(self, context: Any) -> list[str]:
        return _tickers(context)

    def data_requirements(self, context: Any, universe: list[str]):
        return skew_vertical_requirement(universe)

    def normalize_result(self, raw: dict[str, Any], context: Any) -> StrategyResult:
        return _normalize(self, raw, raw.get("items", []) or [])


class StockMomentumStrategy:
    strategy_id = "stock_momentum"
    strategy_label = "Stock Momentum Add"
    version = "v1"
    display_metadata = StrategyDisplayMetadata("ADDS", "Stock Momentum Adds", "Potential Adds", 30, False, True)

    def is_enabled(self) -> bool:
        return bool(config.STOCK_MOMENTUM_STRATEGY_ENABLED)

    def build_universe(self, context: Any) -> list[str]:
        return _tickers(context)

    def data_requirements(self, context: Any, universe: list[str]):
        return stock_momentum_requirement(universe)

    def normalize_result(self, raw: dict[str, Any], context: Any) -> StrategyResult:
        return _normalize(self, raw, raw.get("items", []) or [])


def _normalize(plugin: Any, raw: dict[str, Any], rows: list[dict[str, Any]]) -> StrategyResult:
    def verdict(row: dict[str, Any]) -> str:
        return str(row.get("final_verdict") or row.get("verdict") or row.get("action") or "").upper()
    pass_count = sum(1 for row in rows if verdict(row).startswith(("PASS", "CONSIDER ADDING", "ADD ON")))
    watch_count = sum(1 for row in rows if "WATCH" in verdict(row) or "RESEARCH" in verdict(row))
    skipped_count = sum(1 for row in rows if "SKIPPED" in verdict(row) or "DATA CAP" in verdict(row))
    fail_count = max(0, len(rows) - pass_count - watch_count - skipped_count)
    return StrategyResult(
        strategy_id=plugin.strategy_id, strategy_label=plugin.strategy_label, version=plugin.version,
        enabled=plugin.is_enabled(), ran=bool(raw), rows=rows, active_rows=raw.get("active_items", []) or raw.get("open_trade_rows", []) or [],
        pass_count=pass_count, watch_count=watch_count, fail_count=fail_count, skipped_count=skipped_count,
        scanned_tickers=list((raw.get("summary", {}) or {}).get("scanned_tickers") or raw.get("scanned_tickers") or []),
        data_coverage=(raw.get("data_coverage") or {}), provider_notes=raw.get("provider_notes", []) or [],
        errors=raw.get("errors", []) or [], summary=raw.get("summary", {}) or {},
    )
