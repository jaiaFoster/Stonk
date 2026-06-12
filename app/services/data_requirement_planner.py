"""Merge strategy requirements and apply one honest run-level cap."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.models.market_data_models import SKIPPED_DEV_CAP, SKIPPED_PROVIDER_BUDGET, StrategyDataRequirement


class DataRequirementPlanner:
    def __init__(self, mode: str = "prod", dev_ticker_cap: int | None = None):
        self.mode = "dev" if str(mode).lower() == "dev" else "prod"
        self.dev_ticker_cap = dev_ticker_cap

    def merge(self, requirements: list[StrategyDataRequirement], provider_budget: int | None = None) -> dict[str, Any]:
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
        dev_allowed = ordered if self.mode != "dev" or not self.dev_ticker_cap else ordered[: self.dev_ticker_cap]
        estimated_cost: dict[str, int] = {}
        approved: list[str] = []
        skipped_budget: list[str] = []
        remaining = provider_budget
        for ticker in dev_allowed:
            cost = len(merged[ticker]["data_types"])
            estimated_cost[ticker] = cost
            if remaining is not None and cost > remaining:
                skipped_budget.append(ticker)
            else:
                approved.append(ticker)
                if remaining is not None:
                    remaining -= cost
        allowed = set(approved)
        for ticker, row in merged.items():
            row["data_types"] = sorted(row["data_types"])
            row["derived_metrics"] = sorted(row["derived_metrics"])
            row["state"] = "APPROVED" if ticker in allowed else SKIPPED_PROVIDER_BUDGET if ticker in skipped_budget else SKIPPED_DEV_CAP
            row["estimated_provider_cost"] = estimated_cost.get(ticker, 0)
        return {
            "mode": self.mode,
            "requirements": [asdict(req) for req in requirements],
            "ticker_count": len(merged),
            "required": [asdict(req) for req in requirements],
            "optional": [],
            "approved": [ticker for ticker in ordered if ticker in allowed],
            "approved_requirements": [
                asdict(StrategyDataRequirement(**{**asdict(req), "tickers": [ticker for ticker in req.tickers if ticker.upper() in allowed]}))
                for req in requirements
            ],
            "allowed_tickers": [ticker for ticker in ordered if ticker in allowed],
            "skipped_tickers": [ticker for ticker in ordered if ticker not in allowed],
            "skipped_dev_cap": [ticker for ticker in ordered if ticker not in dev_allowed],
            "skipped_provider_budget": skipped_budget,
            "cache_satisfied": [],
            "provider_budget_remaining": remaining,
            "by_ticker": merged,
        }

    def fulfill(self, hub: Any, requirements: list[StrategyDataRequirement], *, force_refresh: bool = False) -> dict[str, Any]:
        plan = self.merge(requirements, provider_budget=hub.budget.remaining)
        for raw in plan["approved_requirements"]:
            req = StrategyDataRequirement(**raw)
            hub.ensure_requirements(req, force_refresh=force_refresh)
            for ticker in req.tickers:
                if ticker.upper() not in plan["approved"]:
                    hub.mark_skipped(ticker, req.strategy_id, SKIPPED_DEV_CAP)
        for req in requirements:
            for ticker in req.tickers:
                symbol = ticker.upper()
                if symbol in plan["skipped_dev_cap"]:
                    hub.mark_skipped(symbol, req.strategy_id, SKIPPED_DEV_CAP)
                elif symbol in plan["skipped_provider_budget"]:
                    hub.mark_skipped(symbol, req.strategy_id, SKIPPED_PROVIDER_BUDGET)
        return plan
