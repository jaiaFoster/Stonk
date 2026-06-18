"""
app/services/broker_provider.py — Broker credential abstraction layer (28C).

All broker credential operations go through this interface.
To swap to Plaid: add PlaidCredentialProvider and change get_provider() —
zero changes to personalization or queue code required.

SECURITY: passwords passed as plain strings only inside validate_credentials()
and fetch_positions(). Never stored in instance state. Never logged.
"""

from __future__ import annotations

import concurrent.futures
import os
import traceback
from typing import Any

from app import config


class BrokerCredentialProvider:
    """Abstract broker credential interface."""

    @staticmethod
    def get_provider(broker_type: str) -> "BrokerCredentialProvider":
        if broker_type == "robinhood":
            return RobinhoodCredentialProvider()
        raise ValueError(f"Unknown broker type: {broker_type!r}")

    def validate_credentials(self, username: str, password: str) -> tuple[bool, str]:
        """
        Attempt a real login to validate credentials.
        Returns (True, '') on success, (False, error_message) on failure.
        NEVER log password.
        """
        raise NotImplementedError

    def fetch_positions(self, username: str, password_decrypted: str, user_id: int) -> list[dict[str, Any]]:
        """
        Log in, fetch all positions, log out.
        Returns list of position dicts.
        Raises RuntimeError on auth failure.
        NEVER log password_decrypted.
        """
        raise NotImplementedError

    def broker_type(self) -> str:
        raise NotImplementedError


class RobinhoodCredentialProvider(BrokerCredentialProvider):
    """Robinhood implementation using robin_stocks."""

    def broker_type(self) -> str:
        return "robinhood"

    def validate_credentials(self, username: str, password: str) -> tuple[bool, str]:
        """
        Attempt Robinhood login in a worker thread with BROKER_VALIDATION_TIMEOUT_SECONDS.

        Returns (True, '') on success.
        Returns (False, error_key) on failure where error_key is one of:
          'validation_timeout', 'device_approval_required', 'rate_limited', 'login_failed'
        """
        if not getattr(config, "BROKER_CREDENTIAL_VALIDATION_ENABLED", True):
            # Validation disabled — accept creds without checking
            return True, ""

        timeout = int(getattr(config, "BROKER_VALIDATION_TIMEOUT_SECONDS", 30))

        def _attempt() -> tuple[bool, str]:
            import robin_stocks.robinhood as r
            try:
                # Use store_session=False — validation only, don't persist
                r.login(
                    username=username,
                    password=password,
                    store_session=False,
                )
                return True, ""
            except Exception as exc:
                err = str(exc)
                low = err.lower()
                rate_limited = (
                    "429" in low
                    or "too many requests" in low
                    or "get_prompts_status" in low
                )
                device_required = (
                    "verification" in low
                    or "challenge" in low
                    or "mfa" in low
                    or "approval" in low
                    or "approve" in low
                    or "prompt" in low
                    or "validation code" in low
                    or "max_verification_polls" in low
                )
                if rate_limited:
                    return False, "rate_limited"
                if device_required:
                    return False, "device_approval_required"
                return False, "login_failed"

        print(
            f"[broker_provider] Validating Robinhood credentials for user={username!r} "
            f"(timeout={timeout}s).",
            flush=True,
        )
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_attempt)
                try:
                    valid, err_key = future.result(timeout=timeout)
                    if valid:
                        print(
                            f"[broker_provider] Robinhood validation succeeded for user={username!r}.",
                            flush=True,
                        )
                    else:
                        print(
                            f"[broker_provider] Robinhood validation failed: {err_key} "
                            f"(user={username!r}).",
                            flush=True,
                        )
                    return valid, err_key
                except concurrent.futures.TimeoutError:
                    print(
                        f"[broker_provider] Robinhood validation timed out after {timeout}s "
                        f"(user={username!r}).",
                        flush=True,
                    )
                    return False, "validation_timeout"
        except Exception:
            traceback.print_exc()
            return False, "login_failed"

    def fetch_positions(self, username: str, password_decrypted: str, user_id: int) -> list[dict[str, Any]]:
        """
        Log in to Robinhood, fetch all stock positions, log out.
        Uses a per-user pickle for session persistence.
        NEVER logs password_decrypted.
        """
        import robin_stocks.robinhood as r

        data_dir = str(getattr(config, "DATA_DIR", "data"))
        pickle_name = os.path.join(data_dir, f"rh_user_{user_id}")

        try:
            r.login(
                username=username,
                password=password_decrypted,
                store_session=True,
                pickle_name=pickle_name,
            )
        except Exception as exc:
            err = str(exc).replace(password_decrypted, "[REDACTED]")
            raise RuntimeError(f"Robinhood login failed: {err}") from None

        try:
            positions: list[dict[str, Any]] = []
            raw = []
            try:
                raw = r.account.get_open_stock_positions() or []
            except Exception as exc:
                err = str(exc).replace(password_decrypted, "[REDACTED]")
                print(f"[broker_provider] get_open_stock_positions failed: {err}", flush=True)

            for pos in raw:
                try:
                    qty = float(pos.get("quantity") or 0)
                    if qty <= 0:
                        continue
                    ticker = pos.get("symbol") or ""
                    if not ticker:
                        url = pos.get("instrument") or ""
                        if url:
                            try:
                                ticker = r.get_symbol_by_url(url) or ""
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

                    positions.append({
                        "ticker": str(ticker).upper(),
                        "quantity": qty,
                        "avg_cost": avg_cost,
                        "current_price": current_price,
                        "market_value": market_value,
                        "unrealized_pnl_pct": pnl_pct,
                        "account_type": pos.get("account_number") or "default",
                    })
                except Exception:
                    traceback.print_exc()
                    continue

            print(
                f"[broker_provider] user_id={user_id} fetched {len(positions)} position(s).",
                flush=True,
            )
            return positions

        finally:
            try:
                r.logout()
            except Exception:
                pass
