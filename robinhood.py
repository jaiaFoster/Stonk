"""
robinhood.py — Fetches current stock positions from Robinhood.
Uses the unofficial robin_stocks library.
Handles multiple account types: Roth IRA, Investing, Rollover IRA, Crypto.
If login fails due to MFA/session expiry, sends an ntfy alert and waits for retry.
"""

import robin_stocks.robinhood as r
import config
import requests
import time
import pyotp

# Set to True to print verbose debug info
DEBUG = True

# Account number -> friendly label mapping
ACCOUNT_MAP = {
    "973901945": "Roth IRA",
    "489284471": "Rollover IRA",
}

MAX_LOGIN_RETRIES = 10
RETRY_INTERVAL_SECONDS = 30


def dbg(msg, indent=0):
    if DEBUG:
        prefix = "   " * indent
        print(f"{prefix}[DBG] {msg}")


def notify(message, title="⚠️ Stonk Reporter"):
    """Send an ntfy notification."""
    try:
        requests.post(
            f"https://ntfy.sh/{config.NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": title, "Priority": "high"},
            timeout=10,
        )
    except Exception as e:
        print(f"Failed to send ntfy alert: {e}")


def login_with_retry():
    """
    Attempt Robinhood login with TOTP if available.
    If login fails, send an ntfy alert and retry on a loop
    until it succeeds or max retries are hit.
    """
    totp_secret = getattr(config, "ROBINHOOD_TOTP_SECRET", None)

    for attempt in range(1, MAX_LOGIN_RETRIES + 1):
        try:
            print(f"🔐 Login attempt {attempt}/{MAX_LOGIN_RETRIES}...")

            mfa_code = pyotp.TOTP(totp_secret).now() if totp_secret else None
            if mfa_code:
                dbg(f"Generated TOTP code: {mfa_code}")

            r.login(
                username=config.ROBINHOOD_USERNAME,
                password=config.ROBINHOOD_PASSWORD,
                mfa_code=mfa_code,
                store_session=True,
                pickle_name="/app/session/robinhood_session",
            )
            print("✅ Login successful.")
            return True

        except Exception as e:
            error_msg = str(e)
            print(f"❌ Login failed (attempt {attempt}): {error_msg}")

            if "EOF" in error_msg or "MFA" in error_msg.upper() or "challenge" in error_msg.lower():
                if attempt == 1:
                    # First failure — send alert
                    notify(
                        f"Robinhood login failed — MFA or session issue detected.\n"
                        f"Error: {error_msg}\n"
                        f"Will retry every {RETRY_INTERVAL_SECONDS}s. "
                        f"If using device approval, approve it on your phone.",
                        title="⚠️ Stonk Reporter — Login Failed"
                    )
                    print(f"📲 Alert sent to ntfy. Retrying every {RETRY_INTERVAL_SECONDS}s...")
                else:
                    print(f"⏳ Retrying in {RETRY_INTERVAL_SECONDS}s...")
            else:
                # Non-MFA error — alert and stop retrying
                notify(
                    f"Robinhood login error (non-MFA):\n{error_msg}",
                    title="⚠️ Stonk Reporter — Login Error"
                )
                return False

            if attempt < MAX_LOGIN_RETRIES:
                time.sleep(RETRY_INTERVAL_SECONDS)

    notify(
        f"Robinhood login failed after {MAX_LOGIN_RETRIES} attempts. Manual intervention needed.",
        title="🚨 Stonk Reporter — Login Gave Up"
    )
    print(f"🚨 Max retries reached. Giving up.")
    return False


def get_positions():
    try:
        if not login_with_retry():
            return []

        all_positions = []

        # --- STOCK POSITIONS (IRA accounts by account number) ---
        print("\n📦 Fetching stock positions from IRA accounts...")
        for acct_num, acct_label in ACCOUNT_MAP.items():
            print(f"\n   📂 {acct_label} ({acct_num})")
            try:
                raw = r.account.get_open_stock_positions(account_number=acct_num) or []
                dbg(f"Raw response: {len(raw)} record(s)", indent=1)

                if not raw:
                    print(f"      ⚠️  No open positions returned for {acct_label}")
                    continue

                for pos in raw:
                    try:
                        quantity = float(pos.get("quantity", 0))
                        if quantity <= 0:
                            dbg(f"Skipping — quantity is 0", indent=2)
                            continue

                        ticker = pos.get("symbol") or r.get_symbol_by_url(pos["instrument"])
                        print(f"\n      ▶ Processing: {ticker}")
                        dbg(f"quantity={quantity}, avg_buy_price={pos.get('average_buy_price')}", indent=3)

                        position = _build_position_from_raw(ticker, pos, account=acct_label, quantity=quantity)
                        print(f"        ✅ Built: {position}")
                        all_positions.append(position)

                    except Exception as e:
                        print(f"        ❌ Failed to build position: {e}")
                        dbg(f"Raw pos: {pos}", indent=3)

            except Exception as e:
                print(f"      ❌ Failed to fetch {acct_label}: {e}")
                import traceback
                traceback.print_exc()

        # --- CRYPTO ---
        print("\n📦 Fetching crypto positions...")
        try:
            crypto = r.crypto.get_crypto_positions()
            print(f"   Found {len(crypto or [])} crypto position(s)")

            for pos in (crypto or []):
                try:
                    ticker = pos["currency"]["code"]
                    quantity = float(pos["quantity"])
                    cost_bases = pos.get("cost_bases", [])
                    direct_cost = float(cost_bases[0]["direct_cost_basis"]) if cost_bases else 0.0
                    avg_buy_price = direct_cost / quantity if quantity else 0.0
                    quote = r.crypto.get_crypto_quote(ticker)
                    current_price = float(quote["mark_price"]) if quote else None
                    gain_loss = (current_price - avg_buy_price) * quantity if current_price else None
                    gain_loss_pct = ((current_price - avg_buy_price) / avg_buy_price) * 100 if current_price and avg_buy_price else None
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
                    print(f"   ▶ Crypto {ticker}: ✅ Built: {position}")
                    all_positions.append(position)
                except Exception as e:
                    print(f"   ❌ Failed to build crypto position: {e}")

        except Exception as e:
            print(f"   ❌ Crypto fetch failed: {e}")

        print(f"\n✅ Total positions across all accounts: {len(all_positions)}")
        r.logout()
        print("🔓 Logged out.")
        return all_positions

    except Exception as e:
        print(f"❌ Robinhood error: {e}")
        import traceback
        traceback.print_exc()
        return []


def _build_position_from_raw(ticker, pos, account, quantity):
    """Build a position dict from raw position data."""
    avg_buy_price = float(pos["average_buy_price"])
    quote = r.get_latest_price(ticker)
    current_price = float(quote[0]) if quote else None
    gain_loss = (current_price - avg_buy_price) * quantity if current_price else None
    gain_loss_pct = ((current_price - avg_buy_price) / avg_buy_price) * 100 if current_price and avg_buy_price else None
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
