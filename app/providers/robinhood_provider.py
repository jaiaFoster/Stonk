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

    Behavior:
    - If watchlist_names is empty, discover and scan every Robinhood watchlist.
    - If watchlist_names is provided, scan only matching names.
    - Handles several robin_stocks / Robinhood response shapes:
      dict rows, plain string watchlist names, nested payloads, direct instrument rows.
    - Returns normalized diagnostics instead of raising.
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
        "available_watchlist_names": [],
        "watchlists": [],
        "items": [],
        "tickers": [],
        "errors": [],
        "debug": [],
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

        # Try both the full payload and the info='name' helper. Some versions of
        # robin_stocks return a list of strings for info='name', while others
        # return dict payloads under results.
        all_watchlists_payload = None
        all_watchlist_names_payload = None
        try:
            all_watchlists_payload = r.account.get_all_watchlists() or {}
            result["debug"].append(f"get_all_watchlists type={type(all_watchlists_payload).__name__}")
        except Exception as e:
            safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.RUN_TOKEN])
            result["errors"].append(f"get_all_watchlists failed: {safe_error}")

        try:
            all_watchlist_names_payload = r.account.get_all_watchlists(info="name") or []
            result["debug"].append(f"get_all_watchlists(info='name') type={type(all_watchlist_names_payload).__name__}")
        except Exception as e:
            safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.RUN_TOKEN])
            result["debug"].append(f"get_all_watchlists(info='name') unavailable: {safe_error}")

        discovered_names = _discover_watchlist_names(all_watchlists_payload, all_watchlist_names_payload)
        result["available_watchlist_names"] = discovered_names

        if discovered_names:
            print("Robinhood watchlist names found: " + ", ".join(discovered_names), flush=True)
        else:
            print("Robinhood watchlist names found: none", flush=True)

        target_names = {name.lower() for name in requested_names}
        selected_names = [name for name in discovered_names if not target_names or name.lower() in target_names]

        if requested_names and not selected_names:
            result["errors"].append(
                "Requested Robinhood watchlist name(s) were not found. "
                f"Requested={requested_names}; Found={discovered_names or 'none'}"
            )

        # If no names were discoverable, try parsing direct instruments/tickers
        # from the get_all_watchlists payload as a last resort.
        if not selected_names:
            direct_items = _watchlist_results(all_watchlists_payload)
            direct_tickers = []
            for item in direct_items:
                ticker = _ticker_from_watchlist_item(item)
                if ticker and ticker not in direct_tickers:
                    direct_tickers.append(ticker)
                    _append_watchlist_item(result, ticker, "Robinhood Direct Payload", item)
                    if limit and len(result["tickers"]) >= limit:
                        break
            if direct_tickers:
                result["watchlists"].append(
                    {"name": "Robinhood Direct Payload", "tickers": direct_tickers, "errors": []}
                )
                result["has_data"] = bool(result["tickers"])
                result["summary"] = {
                    "watchlist_count": len(result["watchlists"]),
                    "ticker_count": len(result["tickers"]),
                }
                print(
                    f"Robinhood watchlists fetched via direct payload: "
                    f"{len(result['tickers'])} ticker(s)",
                    flush=True,
                )
                return result

            if not discovered_names and not requested_names:
                result["errors"].append("Robinhood returned no discoverable watchlist names or direct ticker rows.")
                return result

        seen = set()
        for list_name in selected_names:
            list_record = {
                "name": list_name,
                "tickers": [],
                "errors": [],
            }
            try:
                raw_items = r.account.get_watchlist_by_name(list_name) or {}
                rows = _watchlist_results(raw_items)
                result["debug"].append(
                    f"watchlist '{list_name}' payload type={type(raw_items).__name__}; rows={len(rows)}"
                )

                for item in rows:
                    ticker = _ticker_from_watchlist_item(item)
                    if not ticker:
                        continue
                    ticker = ticker.upper().strip()
                    if ticker not in list_record["tickers"]:
                        list_record["tickers"].append(ticker)
                    if ticker not in seen:
                        seen.add(ticker)
                        _append_watchlist_item(result, ticker, list_name, item)
                        if limit and len(result["tickers"]) >= limit:
                            break

                result["watchlists"].append(list_record)
                print(
                    f"Robinhood watchlist '{list_name}': "
                    f"{len(list_record['tickers'])} ticker(s)",
                    flush=True,
                )
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
            f"Robinhood watchlists fetched: {len(result['watchlists'])} list(s), "
            f"{len(result['tickers'])} ticker(s)",
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


def _append_watchlist_item(result, ticker, watchlist_name, raw_item):
    if ticker not in result["tickers"]:
        result["tickers"].append(ticker)
        result["items"].append(
            {
                "ticker": ticker,
                "watchlist_name": watchlist_name,
                "source": "robinhood",
                "raw": raw_item if isinstance(raw_item, dict) else {"raw": str(raw_item)},
            }
        )


def _discover_watchlist_names(full_payload, names_payload):
    """Return deduped watchlist names from full and info='name' payloads."""
    names = []

    def add(value):
        value = str(value or "").strip()
        if value and value.lower() not in {n.lower() for n in names}:
            names.append(value)

    # info='name' often returns ['Tech', 'AI', ...]
    if isinstance(names_payload, list):
        for row in names_payload:
            if isinstance(row, str):
                add(row)
            elif isinstance(row, dict):
                add(_watchlist_name_from_row(row))
    elif isinstance(names_payload, dict):
        for row in _watchlist_results(names_payload):
            if isinstance(row, str):
                add(row)
            elif isinstance(row, dict):
                add(_watchlist_name_from_row(row))

    for row in _watchlist_results(full_payload):
        if isinstance(row, str):
            add(row)
        elif isinstance(row, dict):
            add(_watchlist_name_from_row(row))

    return names


def _watchlist_name_from_row(row):
    if not isinstance(row, dict):
        return None
    for key in ["display_name", "name", "title", "label", "id"]:
        value = row.get(key)
        if value:
            return str(value).strip()
    return None


def _watchlist_results(payload):
    """Return result rows from several Robinhood/watchlist response shapes."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []

    for key in ["results", "items", "instruments", "watchlist", "watchlists", "data"]:
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _watchlist_results(value)
            if nested:
                return nested

    # Some calls return a single watchlist dict that itself contains instrument
    # fields rather than a list wrapper. Treat it as one row if it looks useful.
    if any(k in payload for k in ["symbol", "ticker", "instrument", "instrument_url", "url", "object"]):
        return [payload]

    return []


def _ticker_from_watchlist_item(item):
    """Extract a ticker symbol from a Robinhood watchlist row."""
    if isinstance(item, str):
        text = item.strip()
        if not text:
            return None
        # Plain symbol from info/list response.
        if text.isalnum() and 1 <= len(text) <= 8 and "/" not in text and "://" not in text:
            return text.upper()
        # Instrument URL string.
        if text.startswith("http"):
            try:
                symbol = r.get_symbol_by_url(text)
                if symbol:
                    return str(symbol).upper().strip()
            except Exception as e:
                print(f"Could not resolve watchlist instrument URL: {sanitize_for_log(e)}", flush=True)
        return None

    if not isinstance(item, dict):
        return None

    for key in ["symbol", "ticker", "stock_symbol"]:
        value = item.get(key)
        if value:
            return str(value).upper().strip()

    # Common nested shapes.
    for nested_key in ["object", "instrument", "instrument_data", "security", "equity", "item"]:
        nested = item.get(nested_key)
        if isinstance(nested, dict):
            for key in ["symbol", "ticker", "stock_symbol"]:
                value = nested.get(key)
                if value:
                    return str(value).upper().strip()
            for key in ["url", "instrument", "instrument_url"]:
                value = nested.get(key)
                if value:
                    resolved = _ticker_from_watchlist_item(str(value))
                    if resolved:
                        return resolved
        elif isinstance(nested, str):
            resolved = _ticker_from_watchlist_item(nested)
            if resolved:
                return resolved

    instrument = item.get("instrument_url") or item.get("instrument") or item.get("url")
    if isinstance(instrument, dict):
        return _ticker_from_watchlist_item(instrument)
    if instrument:
        return _ticker_from_watchlist_item(str(instrument))

    return None



def get_open_option_positions(account_numbers=None, max_positions=None):
    """
    Fetch open Robinhood option positions for configured accounts.

    This is read-only. It does not place, modify, or close trades. The goal is
    to support automatic calendar-spread detection for positions opened inside
    Robinhood, without requiring manual trade entry.
    """
    print("get_open_option_positions() called", flush=True)
    logged_in = False
    result = {
        "source": "robinhood",
        "configured": bool(config.ROBINHOOD_USERNAME and config.ROBINHOOD_PASSWORD),
        "accounts": [],
        "positions": [],
        "errors": [],
    }

    if not result["configured"]:
        result["errors"].append("Robinhood credentials are not configured.")
        return result

    accounts = []
    if account_numbers:
        for acct in account_numbers:
            acct = str(acct).strip()
            if acct:
                accounts.append((acct, ACCOUNT_MAP.get(acct, acct)))
    else:
        accounts = list(ACCOUNT_MAP.items())

    try:
        if not login_with_retry():
            result["errors"].append("Robinhood login failed while fetching option positions.")
            return result
        logged_in = True

        for account_number, account_label in accounts:
            account_record = {
                "account_number": account_number,
                "account_label": account_label,
                "raw_count": 0,
                "normalized_count": 0,
                "errors": [],
            }
            result["accounts"].append(account_record)
            try:
                raw_positions = r.options.get_open_option_positions(account_number=account_number) or []
                account_record["raw_count"] = len(raw_positions)
                print(f"Robinhood account {account_label}: fetched {len(raw_positions)} open option position(s).", flush=True)
            except Exception as e:
                safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.NTFY_TOPIC])
                account_record["errors"].append(safe_error)
                result["errors"].append(f"{account_label}: {safe_error}")
                print(f"Robinhood account {account_label}: option positions unavailable: {safe_error}", flush=True)
                continue

            for raw in raw_positions:
                try:
                    normalized = _normalize_option_position(raw, account_number, account_label)
                    if not normalized:
                        continue
                    result["positions"].append(normalized)
                    account_record["normalized_count"] += 1
                except Exception as e:
                    safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.NTFY_TOPIC])
                    account_record["errors"].append(safe_error)
                    print(f"Failed to normalize Robinhood option position: {safe_error}", flush=True)

            if max_positions and len(result["positions"]) >= int(max_positions):
                result["positions"] = result["positions"][: int(max_positions)]
                break

        print(
            f"Robinhood Open Options Detector: {len(result['positions'])} normalized option position(s) across {len(result['accounts'])} account(s).",
            flush=True,
        )
        return result

    except Exception as e:
        safe_error = sanitize_for_log(e, [config.ROBINHOOD_PASSWORD, config.NTFY_TOPIC])
        result["errors"].append(safe_error)
        print(f"Robinhood open option position fetch failed: {safe_error}", flush=True)
        traceback.print_exc()
        return result

    finally:
        if logged_in:
            try:
                r.logout()
                print("Logged out after Robinhood option position fetch.", flush=True)
            except Exception as e:
                print(f"Robinhood option logout skipped or failed: {sanitize_for_log(e)}", flush=True)


def _normalize_option_position(raw, account_number, account_label):
    if not isinstance(raw, dict):
        return None

    instrument_data = _option_instrument_from_position(raw)
    # Prefer option instrument metadata for contract facts because position rows
    # may use fields like "type" for position semantics rather than call/put.
    underlying = _first_present(instrument_data, ["chain_symbol", "symbol", "underlying_symbol"]) if isinstance(instrument_data, dict) else None
    if not underlying:
        underlying = _first_present(raw, ["chain_symbol", "symbol", "underlying_symbol"])
    underlying = str(underlying or "").upper().strip()

    expiration = _first_present(instrument_data, ["expiration_date", "expiration"]) if isinstance(instrument_data, dict) else None
    if not expiration:
        expiration = _first_present(raw, ["expiration_date", "expiration"])
    expiration = str(expiration or "").strip()[:10]

    option_type = _first_present(instrument_data, ["type", "option_type"]) if isinstance(instrument_data, dict) else None
    if not option_type:
        option_type = _first_present(raw, ["option_type", "type"])
    option_type = str(option_type or "").lower().strip()
    if option_type in {"c", "call"}:
        option_type = "call"
    elif option_type in {"p", "put"}:
        option_type = "put"

    strike = _float_or_none(_first_present(instrument_data, ["strike_price", "strike"])) if isinstance(instrument_data, dict) else None
    if strike is None:
        strike = _float_or_none(_first_present(raw, ["strike_price", "strike"]))

    quantity = _float_or_none(_first_present(raw, ["quantity", "net_quantity", "intraday_quantity"]))
    if quantity is None or quantity == 0:
        return None

    side = _infer_robinhood_option_side(raw, quantity)
    abs_quantity = abs(quantity)
    avg_price = _float_or_none(_first_present(raw, ["average_price", "average_open_price", "average_buy_price", "price"]))
    cost_basis = None
    if avg_price is not None:
        cost_basis = avg_price * abs_quantity * 100.0
        if side == "short":
            cost_basis *= -1.0

    option_id = _option_id_from_position(raw)
    option_symbol = _occ_symbol(underlying, expiration, option_type, strike)

    return {
        "source": "robinhood",
        "broker": "robinhood",
        "account_id": account_number,
        "account_label": account_label,
        "id": raw.get("id") or raw.get("url") or option_id,
        "option_id": option_id,
        "symbol": option_symbol,
        "underlying": underlying,
        "expiration": expiration,
        "expiration_date": expiration,
        "option_type": option_type,
        "strike": strike,
        "quantity": quantity,
        "abs_quantity": abs_quantity,
        "side": side,
        "side_is_explicit": side in {"long", "short"},
        "avg_cost_per_contract": avg_price,
        "cost_basis": cost_basis,
        "quote": {},
        "mid": None,
        "bid": None,
        "ask": None,
        "market_value_estimate": None,
        "raw": raw,
        "instrument": instrument_data or {},
    }


def _option_instrument_from_position(raw):
    option_id = _option_id_from_position(raw)
    if option_id:
        try:
            data = r.options.get_option_instrument_data_by_id(option_id)
            if isinstance(data, dict):
                return data
        except Exception as e:
            print(f"Could not fetch Robinhood option instrument {option_id}: {sanitize_for_log(e)}", flush=True)
    return {}


def _option_id_from_position(raw):
    for key in ["option_id", "option", "instrument", "url"]:
        value = raw.get(key)
        if not value:
            continue
        text = str(value).strip().rstrip("/")
        if not text:
            continue
        if "/" in text:
            return text.split("/")[-1]
        return text
    return None


def _infer_robinhood_option_side(raw, quantity):
    for key in ["side", "direction", "position_type", "quantity_direction", "opening_side", "strategy"]:
        text = str(raw.get(key) or "").lower()
        if "short" in text or "sell" in text or text in {"credit", "sold"}:
            return "short"
        if "long" in text or "buy" in text or text in {"debit", "bought"}:
            return "long"
    if quantity < 0:
        return "short"
    # Robinhood option position rows can omit long/short direction. Keep this
    # unknown so the calendar detector can infer front-short/back-long only
    # when the grouped legs make that structure plausible.
    return "unknown"


def _occ_symbol(underlying, expiration, option_type, strike):
    if not underlying or not expiration or option_type not in {"call", "put"} or strike is None:
        return ""
    try:
        yymmdd = expiration.replace("-", "")[2:]
        cp = "C" if option_type == "call" else "P"
        strike_int = int(round(float(strike) * 1000))
        return f"{underlying.upper()}{yymmdd}{cp}{strike_int:08d}"
    except Exception:
        return ""


def _first_present(row, keys):
    for key in keys:
        if isinstance(row, dict) and row.get(key) not in {None, ""}:
            return row.get(key)
    return None


def _float_or_none(value):
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
