"""
config.py — Compatibility wrapper.

Existing files or tools that still import `config` will continue to work.
The real config now lives in `app/config.py`.
"""

from app.config import *  # noqa: F401,F403
