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

    def fetch_positions_with_options(
        self, username: str, password_decrypted: str, user_id: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Log in once, fetch stock positions AND raw option positions, log out.
        Returns (stock_positions, raw_option_positions).
        Options fetch uses the same authenticated session — no second login.
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

    def fetch_positions_with_options(
        self, username: str, password_decrypted: str, user_id: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Log in once, fetch stock positions (all IRA/brokerage accounts via ACCOUNT_MAP)
        AND raw Robinhood option positions, log out.
        r.logout() is guaranteed in finally — no second session opened.
        NEVER logs password_decrypted.
        """
        import robin_stocks.robinhood as r
        from app.providers.robinhood_provider import ACCOUNT_MAP

        data_dir = str(getattr(config, "DATA_DIR", "data"))
        # Ensure the pickle directory exists before login so store_session can write the file.
        os.makedirs(data_dir, exist_ok=True)
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
            low = err.lower()
            if (
                "device_approval" in low
                or "verification" in low
                or "challenge" in low
                or "approval" in low
                or "approve" in low
                or "mfa" in low
            ):
                raise RuntimeError(f"device_approval_required: {err}") from None
            raise RuntimeError(f"Robinhood login failed: {err}") from None

        try:
            stock_positions: list[dict[str, Any]] = []

            # --- IRA / brokerage accounts from ACCOUNT_MAP ---
            # Fetch each named account explicitly so we don't miss Roth IRA / Rollover IRA.
            fetched_account_nums: set[str] = set()
            for acct_num, acct_label in ACCOUNT_MAP.items():
                fetched_account_nums.add(acct_num)
                try:
                    raw = r.account.get_open_stock_positions(account_number=acct_num) or []
                    print(
                        f"[broker_provider] user_id={user_id} account={acct_label} "
                        f"({acct_num}): {len(raw)} raw position(s).",
                        flush=True,
                    )
                except Exception as exc:
                    err = str(exc).replace(password_decrypted, "[REDACTED]")
                    print(
                        f"[broker_provider] get_open_stock_positions({acct_num}) failed: {err}",
                        flush=True,
                    )
                    raw = []

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
                        stock_positions.append({
                            "ticker": str(ticker).upper(),
                            "quantity": qty,
                            "avg_cost": avg_cost,
                            "current_price": current_price,
                            "market_value": market_value,
                            "unrealized_pnl_pct": pnl_pct,
                            "account_type": acct_label,
                        })
                    except Exception:
                        traceback.print_exc()
                        continue

            # --- Default brokerage account (if not already covered by ACCOUNT_MAP) ---
            if not ACCOUNT_MAP:
                try:
                    raw_default = r.account.get_open_stock_positions() or []
                    print(
                        f"[broker_provider] user_id={user_id} default account: {len(raw_default)} raw position(s).",
                        flush=True,
                    )
                except Exception as exc:
                    err = str(exc).replace(password_decrypted, "[REDACTED]")
                    print(f"[broker_provider] get_open_stock_positions (default) failed: {err}", flush=True)
                    raw_default = []

                for pos in raw_default:
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
                        current_price = None
                        try:
                            quotes = r.stocks.get_latest_price(ticker)
                            if quotes and quotes[0] is not None:
                                current_price = float(quotes[0])
                        except Exception:
                            pass
                        market_value = current_price * qty if current_price is not None else None
                        pnl_pct = None
                        if current_price is not None and avg_cost and avg_cost > 0:
                            pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 3)
                        stock_positions.append({
                            "ticker": str(ticker).upper(),
                            "quantity": qty,
                            "avg_cost": avg_cost,
                            "current_price": current_price,
                            "market_value": market_value,
                            "unrealized_pnl_pct": pnl_pct,
                            "account_type": "default",
                        })
                    except Exception:
                        traceback.print_exc()
                        continue

            # --- Crypto positions ---
            try:
                crypto_raw = r.crypto.get_crypto_positions() or []
                print(
                    f"[broker_provider] user_id={user_id} crypto: {len(crypto_raw)} position(s).",
                    flush=True,
                )
                for pos in crypto_raw:
                    try:
                        ticker = str(pos.get("currency", {}).get("code") or "").upper()
                        if not ticker:
                            continue
                        qty = float(pos.get("quantity") or 0)
                        if qty <= 0:
                            continue
                        cost_bases = pos.get("cost_bases") or []
                        direct_cost = float(cost_bases[0].get("direct_cost_basis") or 0) if cost_bases else 0.0
                        avg_cost = direct_cost / qty if qty else 0.0
                        quote = r.crypto.get_crypto_quote(ticker) or {}
                        current_price = float(quote.get("mark_price") or 0) or None
                        market_value = current_price * qty if current_price else None
                        pnl_pct = None
                        if current_price and avg_cost and avg_cost > 0:
                            pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 3)
                        stock_positions.append({
                            "ticker": ticker,
                            "quantity": qty,
                            "avg_cost": avg_cost,
                            "current_price": current_price,
                            "market_value": market_value,
                            "unrealized_pnl_pct": pnl_pct,
                            "account_type": "Crypto",
                        })
                    except Exception:
                        traceback.print_exc()
                        continue
            except Exception as exc:
                err = str(exc).replace(password_decrypted, "[REDACTED]")
                print(f"[broker_provider] crypto positions failed (non-fatal): {err}", flush=True)

            print(
                f"[broker_provider] user_id={user_id} total stock+crypto positions: {len(stock_positions)}.",
                flush=True,
            )

            # --- Raw option positions (same session, no second login) ---
            # Loop ACCOUNT_MAP for options just like the stock fetch above.
            raw_option_positions: list[dict[str, Any]] = []
            try:
                seen_option_ids: set[str] = set()
                for acct_num in list(ACCOUNT_MAP.keys()) + [None]:
                    try:
                        batch = r.options.get_open_option_positions(account_number=acct_num) or []
                    except Exception as exc:
                        err = str(exc).replace(password_decrypted, "[REDACTED]")
                        print(f"[broker_provider] acct={acct_num} get_open_option_positions failed: {err}", flush=True)
                        continue
                    print(f"[broker_provider] acct={acct_num} returned {len(batch)} option position(s)", flush=True)
                    for opt_pos in batch:
                        opt_id = opt_pos.get("id") or opt_pos.get("option_id") or id(opt_pos)
                        if opt_id in seen_option_ids:
                            continue
                        seen_option_ids.add(opt_id)
                        raw_option_positions.append(opt_pos)
                print(
                    f"[broker_provider] user_id={user_id} fetched {len(raw_option_positions)} "
                    f"raw option position(s) total across {len(ACCOUNT_MAP)} mapped account(s) + default.",
                    flush=True,
                )
                if raw_option_positions:
                    print(f"[broker_provider] sample raw option position keys: {sorted(raw_option_positions[0].keys())}", flush=True)
            except Exception as exc:
                err = str(exc).replace(password_decrypted, "[REDACTED]")
                print(f"[broker_provider] get_open_option_positions failed (non-fatal): {err}", flush=True)

            return stock_positions, raw_option_positions

        finally:
            try:
                r.logout()
            except Exception:
                pass
