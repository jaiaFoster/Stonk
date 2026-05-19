"""
Strategy modules for Algo Stock Advisor.
"""

from app.strategies.base import StrategyResult
from app.strategies.portfolio_snapshot import PortfolioSnapshotStrategy

__all__ = ["StrategyResult", "PortfolioSnapshotStrategy"]
