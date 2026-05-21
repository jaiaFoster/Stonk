"""
Compatibility wrapper for Portfolio Gap / Sector Suggestions v1.
"""

from app.services.portfolio_gap_service import build_portfolio_gap_analysis, classify_ticker

__all__ = ["build_portfolio_gap_analysis", "classify_ticker"]
