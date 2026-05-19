"""
robinhood.py — Compatibility wrapper.

Existing code that imports `from robinhood import get_positions` will continue
to work. The real Robinhood provider now lives in
`app/providers/robinhood_provider.py`.
"""

from app.providers.robinhood_provider import (  # noqa: F401
    ACCOUNT_MAP,
    MAX_LOGIN_RETRIES,
    RETRY_INTERVAL_SECONDS,
    get_positions,
    login_with_retry,
)
