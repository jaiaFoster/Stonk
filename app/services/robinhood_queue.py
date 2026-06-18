"""
app/services/robinhood_queue.py — Serialized per-user broker position fetch (28B/28C).

One broker auth at a time. Global threading lock prevents simultaneous
logins from triggering IP-level rate limiting.

All actual Robinhood calls go through BrokerCredentialProvider — this module
owns only the serialization layer.

SECURITY: decrypted password passed as arg, used only inside fetch_with_lock,
never logged or stored beyond the call.
"""

from __future__ import annotations

import threading
from typing import Any

from app import config

_rh_lock = threading.Lock()


class RobinhoodQueueTimeout(Exception):
    """Raised when the global lock cannot be acquired within the timeout."""


def fetch_with_lock(user_id: int, rh_username: str, rh_password: str) -> list[dict[str, Any]]:
    """
    Acquire the global serialization lock, fetch positions via BrokerCredentialProvider,
    release lock.

    Raises RobinhoodQueueTimeout if lock not available within RH_QUEUE_TIMEOUT_SECONDS.
    Raises RuntimeError on broker auth/fetch failure.

    NEVER logs rh_password.
    """
    timeout = int(getattr(config, "RH_QUEUE_TIMEOUT_SECONDS", 120))
    acquired = _rh_lock.acquire(timeout=timeout)
    if not acquired:
        raise RobinhoodQueueTimeout(
            "Robinhood fetch queue busy. Try again in 60 seconds."
        )
    try:
        from app.services.broker_provider import BrokerCredentialProvider
        provider = BrokerCredentialProvider.get_provider("robinhood")
        return provider.fetch_positions(rh_username, rh_password, user_id)
    finally:
        _rh_lock.release()
