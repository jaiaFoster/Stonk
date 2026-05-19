"""
app/providers/robinhood_provider.py — Fetches current positions from Robinhood.

Uses the unofficial robin_stocks library and handles multiple account types:
Roth IRA, Rollover IRA, and Crypto.
"""

import builtins
import getpass
import time
import traceback

import requests

from app import config


def _patched_input(prompt=""):
    print(f"[PATCH] input() called with: {prompt}", flush=True)
    if "code" in prompt.lower() or "validation" in prompt.lower():
        print("Waiting 30s for you to approve on Robinhood app...", flush=True)
        time.sleep(30)
    return ""


# Patch before any imports that might trigger input().
builtins.input = _patched_input

# Patch getpass after config is loaded so we have the password.
getpass.getpass = lambda prompt="", stream=None: (
    print(f"[PATCH] getpass() called with: {prompt}", flush=True)
    or config.ROBINHOOD_PASSWORD
)

import robin_stocks.robinhood as r  # noqa: E402

DEBUG = True

ACCOUNT_MAP = {
    "973901945": "Roth IRA",
    "489284471": "Rollover IRA",
}

MAX_LOGIN_RETRIES = 3
RETRY_INTERVAL_SECONDS = 60


def dbg(msg, indent=0):
    if DEBUG:
        prefix = "   " * indent
        print(f"{prefix}[DBG] {msg}", flush=True)


def notify(message, title="Stonk Reporter Alert"):
    if not config.NTFY_TOPIC:
        print("NTFY_TOPIC not set; skipping ntfy alert.", flush=True)
        return

    try:
        print(f"Sending ntfy to topic: {config.NTFY_TOPIC}", flush=True)
        resp = requests.post(
            f"https://ntfy.sh/{config.NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": "high",
            },
            timeout=10,
        )
        print(f"ntfy response: {resp.status_code} {resp.text}", flush=True)
    except Exception as e:
        print(f"Failed to send ntfy alert: {e}", flush=True)


def login_with_retry():
    print("login_with_retry() called", flush=True)
    print(f"Username: {config.ROBINHOOD_USERNAME}", flush=True)
    print(f"Password set: {bool(config.ROBINHOOD_PASSWORD)}", flush=True)

    for attempt in range(1, MAX_LOGIN_RETRIES + 1):
        try:
            print(f"Login attempt {attempt}/{MAX_LOGIN_RETRIES}...", flush=True)
            r.login(
                username=config.ROBINHOOD_USERNAME,
                password=config.ROBINHOOD_PASSWORD,
                store_session=True,
                pickle_name="robinhood_session",
            )
            print("Login successful.", flush=True)
            return True

        except Exception as e:
            error_msg = str(e)
            print(f"Login failed (attempt {attempt}): {error_msg}", flush=True)
            traceback.print_exc()

            if attempt == 1:
                notify(
                    f"Robinhood login failed.\n"
                    f"Error: {error_msg}\n"
                    f"Please approve the login on your Robinhood app.\n"
                    f"Retrying every {RETRY_INTERVAL_SECONDS}s.",
                    title="Stonk Reporter - Login Failed",
                )
                print(
                    f"ntfy alert sent. Retrying every {RETRY_INTERVAL_SECONDS}s...",
                    flush=True,
                )
            else:
                print(f"Retrying in {RETRY_INTERVAL_SECONDS}s...", flush=True)

            if attempt < MAX_LOGIN_RETRIES:
                time.sleep(RETRY_INTERVAL_SECONDS)

    notify(
        f"Robinhood login failed after {MAX_LOGIN_RETRIES} attempts. Manual intervention needed.",
        title="Stonk Reporter - Login Gave Up",
    )
    print("Max retries reached. Giving up.", flush=True)
    return False


def get_positions():
    print("get_positions() called", flush=True)
    logged_in = False

    try:
        if not login_with_retry():
            return []

        logged_in = True
        all_positions = []

        # --- STOCK POSITIONS ---
        print("Fetching stock positions from IRA accounts...", flush=True)
        for acct_num, acct_label in ACCOUNT_MAP.items():
            print(f"Account: {acct_label} ({acct_num})", flush=True)
            try:
                raw = r.account.get_open_stock_positions(account_number=acct_num) or []
                print(f"Raw response: {len(raw)} record(s)", flush=True)

                if not raw:
                    print(f"No open positions for {acct_label}", flush=True)
                    continue

                for pos in raw:
                    try:
                        quantity = float(pos.get("quantity", 0))
                        if quantity <= 0:
                            continue

                        ticker = pos.get("symbol") or r.get_symbol_by_url(pos["instrument"])
                        print(f"Processing: {ticker}", flush=True)
                        position = _build_position_from_raw(
                            ticker,
                            pos,
                            account=acct_label,
                            quantity=quantity,
                        )
                        print(f"Built: {position}", flush=True)
                        all_positions.append(position)

                    except Exception as e:
                        print(f"Failed to build position: {e}", flush=True)
                        traceback.print_exc()

            except Exception as e:
                print(f"Failed to fetch {acct_label}: {e}", flush=True)
                traceback.print_exc()

        # --- CRYPTO ---
        print("Fetching crypto positions...", flush=True)
        try:
            crypto = r.crypto.get_crypto_positions()
            print(f"Found {len(crypto or [])} crypto position(s)", flush=True)

            for pos in (crypto or []):
                try:
                    ticker = pos["currency"]["code"]
                    quantity = float(pos["quantity"])
                    cost_bases = pos.get("cost_bases", [])
                    direct_cost = (
                        float(cost_bases[0]["direct_cost_basis"])
                        if cost_bases
                        else 0.0
                    )
                    avg_buy_price = direct_cost / quantity if quantity else 0.0
                    quote = r.crypto.get_crypto_quote(ticker)
                    current_price = float(quote["mark_price"]) if quote else None
                    gain_loss = (
                        (current_price - avg_buy_price) * quantity
                        if current_price
                        else None
                    )
                    gain_loss_pct = (
                        ((current_price - avg_buy_price) / avg_buy_price) * 100
                        if current_price and avg_buy_price
                        else None
                    )
                    position = {
                        "ticker": ticker,
                        "quantity": quantity,
                        "avg_buy_price": avg_buy_price,
                        "current_price": current_price,
                        "gain_loss": gain_loss,
                        "gain_loss_pct": gain_loss_pct,
                        "market_value": current_price * quantity if current_price else None,
                        "account": "Crypto",
                    }
                    print(f"Crypto {ticker}: Built: {position}", flush=True)
                    all_positions.append(position)
                except Exception as e:
                    print(f"Failed to build crypto position: {e}", flush=True)

        except Exception as e:
            print(f"Crypto fetch failed: {e}", flush=True)

        print(f"Total positions: {len(all_positions)}", flush=True)
        return all_positions

    except Exception as e:
        print(f"Robinhood error: {e}", flush=True)
        traceback.print_exc()
        return []

    finally:
        if logged_in:
            try:
                r.logout()
                print("Logged out.", flush=True)
            except Exception as e:
                print(f"Logout skipped or failed: {e}", flush=True)


def _build_position_from_raw(ticker, pos, account, quantity):
    avg_buy_price = float(pos["average_buy_price"])
    quote = r.get_latest_price(ticker)
    current_price = float(quote[0]) if quote else None
    gain_loss = (current_price - avg_buy_price) * quantity if current_price else None
    gain_loss_pct = (
        ((current_price - avg_buy_price) / avg_buy_price) * 100
        if current_price and avg_buy_price
        else None
    )
    return {
        "ticker": ticker,
        "quantity": quantity,
        "avg_buy_price": avg_buy_price,
        "current_price": current_price,
        "gain_loss": gain_loss,
        "gain_loss_pct": gain_loss_pct,
        "market_value": current_price * quantity if current_price else None,
        "account": account,
    }
