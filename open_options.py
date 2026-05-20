"""
open_options.py — Compatibility wrapper for Open Options Position Detector v1.

The real implementation lives in app/services/open_options_service.py.
"""

from app.services.open_options_service import detect_open_options_positions, parse_occ_option_symbol

__all__ = ["detect_open_options_positions", "parse_occ_option_symbol"]
