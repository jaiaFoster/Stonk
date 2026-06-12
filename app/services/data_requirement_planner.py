"""Merge strategy requirements and apply one honest run-level cap."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.models.market_data_models import SKIPPED_DEV_CAP, StrategyDataRequirement


class DataRequirementPlanner:
    def __init__(self, mode: str = "prod", dev_ticker_cap: int | None = None):
        self.mode = "dev" if str(mode).lower() == "dev" else "prod"
        self.dev_ticker_cap = dev_ticker_cap

    def merge(self, requirements: list[StrategyDataRequirement]) -> dict[str, Any]:
        merged: dict[str, dict[str, Any]] = {}
        ordered: list[str] = []
        for req in sorted(requirements, key=lambda item: item.priority, reverse=True):
            for ticker in req.tickers:
                symbol = str(ticker).upper().strip()
                if not symbol:
                    continue
                if symbol not in ordered:
                    ordered.append(symbol)
                row = merged.setdefault(symbol, {"ticker": symbol, "strategies": [], "data_types": set(), "derived_metrics": set(), "priority": req.priority})
                row["strategies"].append(req.strategy_id)
                row["priority"] = max(row["priority"], req.priority)
                if req.needs_quote:
                    row["data_types"].add("quote")
                if req.needs_daily_candles:
                    row["data_types"].add("candles")
                if req.needs_options_chain:
                    row["data_types"].add("options_chain")
                if req.needs_earnings_event:
                    row["data_types"].add("earnings_event")
                row["derived_metrics"].update(req.required_derived_metrics)
        allowed = set(ordered if self.mode != "dev" or not self.dev_ticker_cap else ordered[: self.dev_ticker_cap])
        for ticker, row in merged.items():
            row["data_types"] = sorted(row["data_types"])
            row["derived_metrics"] = sorted(row["derived_metrics"])
            row["state"] = "PLANNED" if ticker in allowed else SKIPPED_DEV_CAP
        return {
            "mode": self.mode,
            "requirements": [asdict(req) for req in requirements],
            "ticker_count": len(merged),
            "allowed_tickers": [ticker for ticker in ordered if ticker in allowed],
            "skipped_tickers": [ticker for ticker in ordered if ticker not in allowed],
            "by_ticker": merged,
        }

    def fulfill(self, hub: Any, requirements: list[StrategyDataRequirement]) -> dict[str, Any]:
        plan = self.merge(requirements)
        for req in requirements:
            allowed = [ticker for ticker in req.tickers if ticker.upper() in plan["allowed_tickers"]]
            hub.ensure_requirements(StrategyDataRequirement(**{**asdict(req), "tickers": allowed}))
            for ticker in req.tickers:
                if ticker.upper() not in plan["allowed_tickers"]:
                    hub.mark_skipped(ticker, req.strategy_id, SKIPPED_DEV_CAP)
        return plan
