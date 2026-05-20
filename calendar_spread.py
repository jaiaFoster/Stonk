"""
calendar_spread.py — Compatibility wrapper for Calendar Spread Screener v1.

The real implementation lives in app/services/calendar_spread_service.py.
"""

from app.services.calendar_spread_service import scan_calendar_spreads_for_positions

__all__ = ["scan_calendar_spreads_for_positions"]
