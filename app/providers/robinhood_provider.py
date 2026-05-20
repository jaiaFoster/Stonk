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
from app.utils.log_safety import sanitize_for_log


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
        print("Sending ntfy alert.", flush=True)
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
        print(f"Failed to send ntfy alert: {sanitize_for_log(e, [config.NTFY_TOPIC])}", flush=True)


def login_with_retry():
    print("login_with_retry() called", flush=True)
    print(f"Username set: {bool(config.ROBINHOOD_USERNAME)}", flush=True)
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
            error_msg = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.NTFY_TOPIC])
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
                        print(f"Failed to build position: {sanitize_for_log(e)}", flush=True)
                        traceback.print_exc()

            except Exception as e:
                print(f"Failed to fetch {acct_label}: {sanitize_for_log(e)}", flush=True)
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
                    print(f"Failed to build crypto position: {sanitize_for_log(e)}", flush=True)

        except Exception as e:
            print(f"Crypto fetch failed: {sanitize_for_log(e)}", flush=True)

        print(f"Total positions: {len(all_positions)}", flush=True)
        return all_positions

    except Exception as e:
        print(f"Robinhood error: {sanitize_for_log(e)}", flush=True)
        traceback.print_exc()
        return []

    finally:
        if logged_in:
            try:
                r.logout()
                print("Logged out.", flush=True)
            except Exception as e:
                print(f"Logout skipped or failed: {sanitize_for_log(e)}", flush=True)


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


def get_watchlist_tickers(watchlist_names=None, max_tickers=None):
    """
    Fetch Robinhood watchlist tickers defensively.

    Returns a normalized result dict rather than raising. This uses the
    robin_stocks account watchlist helpers when available:
    - get_all_watchlists()
    - get_watchlist_by_name(name)
    """
    print("get_watchlist_tickers() called", flush=True)
    logged_in = False
    requested_names = [str(n).strip() for n in (watchlist_names or []) if str(n).strip()]
    limit = int(max_tickers or 0) if max_tickers is not None else None

    result = {
        "source": "robinhood",
        "has_data": False,
        "configured": bool(config.ROBINHOOD_USERNAME and config.ROBINHOOD_PASSWORD),
        "requested_names": requested_names,
        "watchlists": [],
        "items": [],
        "tickers": [],
        "errors": [],
        "summary": {
            "watchlist_count": 0,
            "ticker_count": 0,
        },
    }

    try:
        if not login_with_retry():
            result["errors"].append("Robinhood login failed while fetching watchlists.")
            return result

        logged_in = True
        all_watchlists = r.account.get_all_watchlists() or {}
        raw_lists = _watchlist_results(all_watchlists)

        if not raw_lists:
            result["errors"].append("Robinhood returned no watchlists.")
            return result

        target_names = {name.lower() for name in requested_names}
        selected_lists = []
        for raw_watchlist in raw_lists:
            if not isinstance(raw_watchlist, dict):
                continue
            display_name = str(
                raw_watchlist.get("display_name")
                or raw_watchlist.get("name")
                or raw_watchlist.get("id")
                or "Unknown"
            ).strip()
            if target_names and display_name.lower() not in target_names:
                continue
            selected_lists.append(display_name)

        if not selected_lists and requested_names:
            result["errors"].append(
                "Requested Robinhood watchlist name(s) were not found: " + ", ".join(requested_names)
            )
            return result

        seen = set()
        for list_name in selected_lists:
            list_record = {
                "name": list_name,
                "tickers": [],
                "errors": [],
            }
            try:
                raw_items = r.account.get_watchlist_by_name(list_name) or {}
                rows = _watchlist_results(raw_items)
                for item in rows:
                    ticker = _ticker_from_watchlist_item(item)
                    if not ticker:
                        continue
                    ticker = ticker.upper().strip()
                    if ticker not in list_record["tickers"]:
                        list_record["tickers"].append(ticker)
                    if ticker not in seen:
                        seen.add(ticker)
                        result["items"].append(
                            {
                                "ticker": ticker,
                                "watchlist_name": list_name,
                                "source": "robinhood",
                                "raw": item if isinstance(item, dict) else {},
                            }
                        )
                        result["tickers"].append(ticker)
                        if limit and len(result["tickers"]) >= limit:
                            break
                result["watchlists"].append(list_record)
            except Exception as e:
                safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.RUN_TOKEN])
                list_record["errors"].append(str(safe_error))
                result["watchlists"].append(list_record)
                result["errors"].append(f"Failed to fetch watchlist {list_name}: {safe_error}")

            if limit and len(result["tickers"]) >= limit:
                break

        result["has_data"] = bool(result["tickers"])
        result["summary"] = {
            "watchlist_count": len(result["watchlists"]),
            "ticker_count": len(result["tickers"]),
        }
        print(
            f"Robinhood watchlists fetched: {len(result['watchlists'])} list(s), {len(result['tickers'])} ticker(s)",
            flush=True,
        )
        return result

    except Exception as e:
        safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.RUN_TOKEN])
        result["errors"].append(str(safe_error))
        print(f"Robinhood watchlist fetch failed: {safe_error}", flush=True)
        return result

    finally:
        if logged_in:
            try:
                r.logout()
                print("Logged out after watchlist fetch.", flush=True)
            except Exception as e:
                print(f"Watchlist logout skipped or failed: {sanitize_for_log(e)}", flush=True)


def _watchlist_results(payload):
    """Return result rows from several Robinhood/watchlist response shapes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ["results", "items", "instruments", "watchlist"]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
    # Some robin_stocks watchlist calls return {"results": [...]}, while some
    # lower-level responses may wrap the list one level deeper.
    nested = payload.get("data")
    if isinstance(nested, dict):
        return _watchlist_results(nested)
    return []


def _ticker_from_watchlist_item(item):
    """Extract a ticker symbol from a Robinhood watchlist row."""
    if not isinstance(item, dict):
        return None

    for key in ["symbol", "ticker"]:
        value = item.get(key)
        if value:
            return str(value).upper().strip()

    instrument = item.get("instrument") or item.get("instrument_url")
    if not instrument and isinstance(item.get("object"), dict):
        obj = item.get("object") or {}
        instrument = obj.get("url") or obj.get("instrument")
        for key in ["symbol", "ticker"]:
            if obj.get(key):
                return str(obj.get(key)).upper().strip()

    if instrument:
        try:
            symbol = r.get_symbol_by_url(instrument)
            if symbol:
                return str(symbol).upper().strip()
        except Exception as e:
            print(f"Could not resolve watchlist instrument URL: {sanitize_for_log(e)}", flush=True)

    return None
