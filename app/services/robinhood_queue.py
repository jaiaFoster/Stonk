"""
app/services/robinhood_queue.py — Serialized per-user Robinhood fetch (28B).

One Robinhood auth at a time. Global threading lock prevents simultaneous
logins from triggering IP-level rate limiting.

SECURITY: decrypted password passed as arg, used only inside fetch_robinhood_positions,
never logged or stored beyond the call.
"""

from __future__ import annotations

import os
import threading
import traceback
from typing import Any

from app import config

_rh_lock = threading.Lock()


class RobinhoodQueueTimeout(Exception):
    """Raised when the global lock cannot be acquired within the timeout."""


def fetch_robinhood_positions(rh_username: str, rh_password: str, user_id: int) -> list[dict[str, Any]]:
    """
    Log in to Robinhood as the given user, fetch all stock positions, log out.

    Uses a per-user pickle file under DATA_DIR so sessions persist across
    requests (first login requires app approval; subsequent ones reuse the token).

    NEVER logs rh_password. Caller must del rh_password immediately after return.
    """
    import robin_stocks.robinhood as r  # noqa: E402

    # Per-user pickle stored on the Railway volume alongside other DB files.
    pickle_name = os.path.join(
        str(getattr(config, "DATA_DIR", "data")),
        f"rh_user_{user_id}",
    )

    try:
        r.login(
            username=rh_username,
            password=rh_password,
            store_session=True,
            pickle_name=pickle_name,
        )
    except Exception as exc:
        # Sanitize: never include password in logged or raised text.
        err = str(exc).replace(rh_password, "[REDACTED]")
        raise RuntimeError(f"Robinhood login failed: {err}") from None

    try:
        positions: list[dict[str, Any]] = []

        # Fetch from the default brokerage account (no account_number → gets "Investing")
        raw = []
        try:
            raw = r.account.get_open_stock_positions() or []
        except Exception as exc:
            err = str(exc).replace(rh_password, "[REDACTED]")
            print(f"[robinhood_queue] get_open_stock_positions failed: {err}", flush=True)

        for pos in raw:
            try:
                qty = float(pos.get("quantity") or 0)
                if qty <= 0:
                    continue

                ticker = pos.get("symbol") or ""
                if not ticker:
                    instrument_url = pos.get("instrument") or ""
                    if instrument_url:
                        try:
                            ticker = r.get_symbol_by_url(instrument_url) or ""
                        except Exception:
                            pass
                if not ticker:
                    continue

                avg_cost = float(pos.get("average_buy_price") or 0)

                current_price: float | None = None
                try:
                    quotes = r.stocks.get_latest_price(ticker)
                    if quotes and quotes[0] is not None:
                        current_price = float(quotes[0])
                except Exception:
                    pass

                market_value = current_price * qty if current_price is not None else None
                pnl_pct: float | None = None
                if current_price is not None and avg_cost and avg_cost > 0:
                    pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 3)

                account_type = pos.get("account_number") or "default"

                positions.append({
                    "ticker": str(ticker).upper(),
                    "quantity": qty,
                    "avg_cost": avg_cost,
                    "current_price": current_price,
                    "market_value": market_value,
                    "unrealized_pnl_pct": pnl_pct,
                    "account_type": account_type,
                })

            except Exception:
                traceback.print_exc()
                continue

        print(
            f"[robinhood_queue] user_id={user_id} fetched {len(positions)} position(s).",
            flush=True,
        )
        return positions

    finally:
        try:
            r.logout()
        except Exception:
            pass


def fetch_with_lock(user_id: int, rh_username: str, rh_password: str) -> list[dict[str, Any]]:
    """
    Acquire the global lock, fetch positions, release lock.

    Raises RobinhoodQueueTimeout if lock is not available within
    RH_QUEUE_TIMEOUT_SECONDS (default 120s).

    Raises RuntimeError on Robinhood auth/fetch failure.

    NEVER logs rh_password.
    """
    timeout = int(getattr(config, "RH_QUEUE_TIMEOUT_SECONDS", 120))
    acquired = _rh_lock.acquire(timeout=timeout)
    if not acquired:
        raise RobinhoodQueueTimeout(
            "Robinhood fetch queue busy. Try again in 60 seconds."
        )
    try:
        return fetch_robinhood_positions(rh_username, rh_password, user_id)
    finally:
        _rh_lock.release()
