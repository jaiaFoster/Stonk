"""
app/strategies/portfolio_snapshot.py — Future portfolio snapshot scoring.

This strategy shell is intentionally simple for now. It gives us a clean place
to add portfolio-level scoring after the folder refactor is deployed and stable.
"""

from app.strategies.base import StrategyResult


class PortfolioSnapshotStrategy:
    name = "Portfolio Snapshot"

    def evaluate_position(self, position: dict) -> StrategyResult:
        ticker = str(position.get("ticker", "UNKNOWN"))
        return StrategyResult(
            name=self.name,
            ticker=ticker,
            action="WATCH",
            score=None,
            confidence="Unscored",
            reasons=["Portfolio snapshot scoring has not been implemented yet."],
            risks=[],
            next_check=None,
        )
