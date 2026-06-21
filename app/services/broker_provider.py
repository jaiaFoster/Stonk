"""
app/services/broker_provider.py — Broker credential abstraction layer (28C + Plaid).

Two providers: RobinhoodCredentialProvider (direct login via robin_stocks)
and PlaidCredentialProvider (Plaid Link → access_token → /investments/holdings/get).

SECURITY: passwords/access_tokens passed as plain strings only inside
validate_credentials() and fetch_positions(). Never stored in instance state.
Never logged.
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
        if broker_type == "plaid":
            return PlaidCredentialProvider()
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
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Log in once, fetch stock positions AND raw option positions, log out.
        Returns (stock_positions, raw_option_positions, discovered_accounts).
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
        os.makedirs(data_dir, exist_ok=True)

        try:
            r.login(
                username=username,
                password=password_decrypted,
                store_session=True,
                pickle_path=data_dir,
                pickle_name=f"_user_{user_id}",
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
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """
        Log in once, fetch stock positions (all dynamically discovered accounts)
        AND raw Robinhood option positions, log out.
        Returns (stock_positions, raw_option_positions, discovered_accounts).
        r.logout() is guaranteed in finally — no second session opened.
        NEVER logs password_decrypted.
        """
        import robin_stocks.robinhood as r
        from app.providers.robinhood_provider import discover_accounts

        data_dir = str(getattr(config, "DATA_DIR", "data"))
        os.makedirs(data_dir, exist_ok=True)

        try:
            r.login(
                username=username,
                password=password_decrypted,
                store_session=True,
                pickle_path=data_dir,
                pickle_name=f"_user_{user_id}",
            )
        except Exception as exc:
            err = str(exc).replace(password_decrypted, "[REDACTED]")
            low = err.lower()
            from app.db.users import log_user_error
            log_user_error(user_id, "broker_provider.login", type(exc).__name__, err)
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

            discovered = discover_accounts()
            discovered_map = {a["account_number"]: a["account_type"] for a in discovered}

            # --- Discovered accounts (replaces hardcoded ACCOUNT_MAP) ---
            if discovered:
                for acct_num, acct_label in discovered_map.items():
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
                                "account_number": acct_num,
                            })
                        except Exception:
                            traceback.print_exc()
                            continue
            else:
                # Fallback: no accounts discovered — fetch from default account
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
                            "account_type": "Default",
                            "account_number": None,
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
                            "account_number": None,
                        })
                    except Exception:
                        traceback.print_exc()
                        continue
            except Exception as exc:
                err = str(exc).replace(password_decrypted, "[REDACTED]")
                print(f"[broker_provider] crypto positions failed (non-fatal): {err}", flush=True)
                from app.db.users import log_user_error
                log_user_error(user_id, "broker_provider.crypto", type(exc).__name__, err)

            print(
                f"[broker_provider] user_id={user_id} total stock+crypto positions: {len(stock_positions)}.",
                flush=True,
            )

            # --- Raw option positions (same session, no second login) ---
            # Loop discovered accounts for options just like the stock fetch above.
            raw_option_positions: list[dict[str, Any]] = []
            try:
                seen_option_ids: set[str] = set()
                option_account_nums = list(discovered_map.keys()) + [None]
                for acct_num in option_account_nums:
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
                        opt_pos["_source_account_number"] = acct_num
                        opt_pos["_source_account_type"] = discovered_map.get(acct_num, "Default") if acct_num else "Default"
                        raw_option_positions.append(opt_pos)
                print(
                    f"[broker_provider] user_id={user_id} fetched {len(raw_option_positions)} "
                    f"raw option position(s) total across {len(discovered_map)} discovered account(s) + default.",
                    flush=True,
                )
                if raw_option_positions:
                    print(f"[broker_provider] sample raw option position keys: {sorted(raw_option_positions[0].keys())}", flush=True)
            except Exception as exc:
                err = str(exc).replace(password_decrypted, "[REDACTED]")
                print(f"[broker_provider] get_open_option_positions failed (non-fatal): {err}", flush=True)
                from app.db.users import log_user_error
                log_user_error(user_id, "broker_provider.options", type(exc).__name__, err)

            return stock_positions, raw_option_positions, discovered

        finally:
            try:
                r.logout()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Plaid broker provider
# ---------------------------------------------------------------------------

def _plaid_client():
    """Create a Plaid API client from config. Lazy import to avoid import-time failures."""
    import plaid
    from plaid.api import plaid_api

    env_map = {
        "sandbox": plaid.Environment.Sandbox,
        "production": plaid.Environment.Production,
    }
    plaid_env = env_map.get(config.PLAID_ENV or "production", plaid.Environment.Production)

    configuration = plaid.Configuration(
        host=plaid_env,
        api_key={
            "clientId": config.PLAID_CLIENT_ID,
            "secret": config.PLAID_SECRET,
        },
    )
    api_client = plaid.ApiClient(configuration)
    return plaid_api.PlaidApi(api_client)


_PLAID_ACCOUNT_TYPE_MAP = {
    "roth": "Roth IRA",
    "ira": "Traditional IRA",
    "roth 401k": "Roth 401k",
    "401k": "401k",
    "brokerage": "Individual",
    "non-taxable brokerage account": "Individual",
}


def _classify_plaid_account_type(account: dict[str, Any]) -> str:
    subtype = str(account.get("subtype") or "").lower()
    return _PLAID_ACCOUNT_TYPE_MAP.get(subtype, subtype.title() if subtype else "Unknown")


def _normalize_plaid_holding(
    holding: dict[str, Any], security: dict[str, Any], account: dict[str, Any]
) -> dict[str, Any]:
    qty = float(holding.get("quantity") or 0)
    cost_basis = float(holding.get("cost_basis") or 0)
    avg_cost = cost_basis / qty if qty else 0.0
    close_price = security.get("close_price")
    current_price = float(close_price) if close_price is not None else None
    market_value_raw = holding.get("institution_value")
    market_value = float(market_value_raw) if market_value_raw is not None else (
        current_price * qty if current_price is not None else None
    )
    pnl_pct: float | None = None
    if current_price is not None and avg_cost and avg_cost > 0:
        pnl_pct = round((current_price - avg_cost) / avg_cost * 100, 3)
    return {
        "ticker": str(security.get("ticker_symbol") or "").upper(),
        "quantity": qty,
        "avg_cost": avg_cost,
        "current_price": current_price,
        "market_value": market_value,
        "unrealized_pnl_pct": pnl_pct,
        "account_type": _classify_plaid_account_type(account),
        "account_number": account.get("account_id"),
        "_broker": "plaid",
    }


def _normalize_plaid_option(
    holding: dict[str, Any], security: dict[str, Any], account: dict[str, Any]
) -> dict[str, Any]:
    """Normalize a Plaid option holding into ASA's raw option position shape.

    Plaid option quantity = contracts × 100 (share-equivalent).
    Divide by 100 to match Robinhood's contract-count convention.
    """
    contract = security.get("option_contract") or {}
    raw_qty = float(holding.get("quantity") or 0)
    qty = raw_qty / 100.0

    return {
        "chain_symbol": str(contract.get("underlying_security_ticker") or "").upper(),
        "type": str(contract.get("contract_type") or "").lower(),
        "strike_price": str(contract.get("strike_price") or "0"),
        "expiration_date": contract.get("expiration_date"),
        "quantity": str(qty),
        "average_price": str(holding.get("cost_basis") or "0"),
        "id": holding.get("holding_id") or holding.get("security_id") or id(holding),
        "_source_account_number": account.get("account_id"),
        "_source_account_type": _classify_plaid_account_type(account),
        "_broker": "plaid",
        "_plaid_raw_quantity": raw_qty,
    }


class PlaidCredentialProvider(BrokerCredentialProvider):
    """Plaid investment broker — no username/password, uses access_token from Link flow."""

    def broker_type(self) -> str:
        return "plaid"

    def validate_credentials(self, public_token: str, *args) -> tuple[bool, str]:
        """Exchange public_token for access_token. Returns (success, error_key)."""
        try:
            from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
            client = _plaid_client()
            req = ItemPublicTokenExchangeRequest(public_token=public_token)
            response = client.item_public_token_exchange(req)
            return True, ""
        except Exception as exc:
            err = str(exc)
            print(f"[plaid_provider] token exchange failed: {type(exc).__name__}", flush=True)
            return False, "exchange_failed"

    def exchange_public_token(self, public_token: str) -> tuple[str, str]:
        """Exchange public_token → (access_token, item_id). Raises on failure."""
        from plaid.model.item_public_token_exchange_request import ItemPublicTokenExchangeRequest
        client = _plaid_client()
        req = ItemPublicTokenExchangeRequest(public_token=public_token)
        response = client.item_public_token_exchange(req)
        return response.access_token, response.item_id

    def fetch_positions(self, access_token: str, _password_unused: str = "", user_id: int = 0) -> list[dict[str, Any]]:
        """Fetch all investment holdings via Plaid. Returns normalized position list."""
        positions, _options, _accounts = self.fetch_positions_with_options(access_token, "", user_id)
        return positions

    def fetch_positions_with_options(
        self, access_token: str, _password_unused: str = "", user_id: int = 0
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Fetch holdings + options from Plaid. Returns (stock_positions, raw_option_positions, discovered_accounts)."""
        from plaid.model.investments_holdings_get_request import InvestmentsHoldingsGetRequest

        client = _plaid_client()

        if config.PLAID_REFRESH_ON_EVERY_RUN:
            try:
                from plaid.model.investments_refresh_request import InvestmentsRefreshRequest
                client.investments_refresh(InvestmentsRefreshRequest(access_token=access_token))
                print(f"[plaid_provider] user_id={user_id}: investments refresh triggered.", flush=True)
            except Exception as exc:
                print(f"[plaid_provider] investments refresh failed (non-fatal): {type(exc).__name__}", flush=True)

        try:
            response = client.investments_holdings_get(
                InvestmentsHoldingsGetRequest(access_token=access_token)
            )
        except Exception as exc:
            from app.db.users import log_user_error
            log_user_error(user_id, "plaid_provider.fetch", type(exc).__name__, str(exc))
            raise RuntimeError(f"Plaid holdings fetch failed: {type(exc).__name__}") from None

        holdings = response.holdings or []
        securities = {s.security_id: s.to_dict() for s in (response.securities or [])}
        accounts = {a.account_id: a.to_dict() for a in (response.accounts or [])}

        stock_positions: list[dict[str, Any]] = []
        raw_option_positions: list[dict[str, Any]] = []
        discovered_accounts: list[dict[str, Any]] = []

        seen_accounts: set[str] = set()
        for acct_id, acct in accounts.items():
            if acct_id not in seen_accounts:
                seen_accounts.add(acct_id)
                discovered_accounts.append({
                    "account_number": acct_id,
                    "account_type": _classify_plaid_account_type(acct),
                    "broker_type": "plaid",
                })

        for h in holdings:
            h_dict = h.to_dict() if hasattr(h, "to_dict") else h
            sec_id = h_dict.get("security_id")
            sec = securities.get(sec_id, {})
            acct_id = h_dict.get("account_id")
            acct = accounts.get(acct_id, {})

            sec_type = str(sec.get("type") or "").lower()

            if sec_type == "derivative" or sec.get("option_contract"):
                raw_option_positions.append(_normalize_plaid_option(h_dict, sec, acct))
            elif sec_type in ("equity", "etf", "mutual fund", ""):
                ticker = str(sec.get("ticker_symbol") or "").upper()
                if not ticker:
                    continue
                qty = float(h_dict.get("quantity") or 0)
                if qty <= 0:
                    continue
                stock_positions.append(_normalize_plaid_holding(h_dict, sec, acct))

        print(
            f"[plaid_provider] user_id={user_id}: {len(stock_positions)} stock position(s), "
            f"{len(raw_option_positions)} option position(s), "
            f"{len(discovered_accounts)} account(s).",
            flush=True,
        )

        return stock_positions, raw_option_positions, discovered_accounts
