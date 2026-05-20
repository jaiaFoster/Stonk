"""
earnings.py — Compatibility wrapper for Earnings Timestamp Provider v1.
"""

from app.providers.earnings_provider import FinnhubEarningsProvider, get_provider
from app.services.earnings_service import get_earnings_for_positions

__all__ = ["FinnhubEarningsProvider", "get_provider", "get_earnings_for_positions"]
