"""
app/services/robinhood_queue.py — Serialized per-user broker position fetch (28B/28C/28D).

One broker auth at a time. Global threading lock prevents simultaneous
logins from triggering IP-level rate limiting.

All actual Robinhood calls go through BrokerCredentialProvider — this module
owns only the serialization layer.

SECURITY: decrypted password passed as arg, used only inside fetch_with_lock,
never logged or stored beyond the call.
"""

from __future__ import annotations

import os
import threading
from typing import Any

from app import config

_rh_lock = threading.Lock()


class RobinhoodQueueTimeout(Exception):
    """Raised when the global lock cannot be acquired within the timeout."""


class RobinhoodDeviceApprovalRequired(Exception):
    """Raised when Robinhood requires device approval (no cached session)."""


def session_cache_available(user_id: int) -> bool:
    """Return True if a per-user Robinhood session pickle exists on disk."""
    data_dir = str(getattr(config, "DATA_DIR", "data"))
    pickle_path = os.path.join(data_dir, f"robinhood_user_{user_id}.pickle")
    return os.path.exists(pickle_path)


def fetch_with_lock(user_id: int, rh_username: str, rh_password: str) -> list[dict[str, Any]]:
    """
    Acquire the global serialization lock, fetch positions via BrokerCredentialProvider,
    release lock.

    Raises RobinhoodQueueTimeout if lock not available within RH_QUEUE_TIMEOUT_SECONDS.
    Raises RobinhoodDeviceApprovalRequired if Robinhood demands device approval.
    Raises RuntimeError on other broker auth/fetch failure.

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
        try:
            return provider.fetch_positions(rh_username, rh_password, user_id)
        except RuntimeError as exc:
            # Re-classify device approval errors so callers can surface them specifically
            low = str(exc).lower()
            if (
                "device_approval" in low
                or "verification" in low
                or "challenge" in low
                or "approval" in low
                or "approve" in low
                or "device approval" in low
            ):
                raise RobinhoodDeviceApprovalRequired(str(exc)) from exc
            raise
    finally:
        _rh_lock.release()


def fetch_all_with_lock(
    user_id: int, rh_username: str, rh_password: str
) -> tuple[list[dict[Any, Any]], list[dict[Any, Any]], list[dict[Any, Any]]]:
    """
    Like fetch_with_lock but also fetches raw option positions in the same session.
    Returns (stock_positions, raw_option_positions, discovered_accounts).
    One login, one logout — no second Robinhood session.
    Raises RobinhoodQueueTimeout, RobinhoodDeviceApprovalRequired, or RuntimeError.
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
        try:
            return provider.fetch_positions_with_options(rh_username, rh_password, user_id)
        except RuntimeError as exc:
            low = str(exc).lower()
            if (
                "device_approval" in low
                or "verification" in low
                or "challenge" in low
                or "approval" in low
                or "approve" in low
                or "device approval" in low
            ):
                raise RobinhoodDeviceApprovalRequired(str(exc)) from exc
            raise
    finally:
        _rh_lock.release()
