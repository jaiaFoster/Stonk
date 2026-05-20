"""
app/config.py — Credentials and app settings.

On Railway, set these as Environment Variables in the project dashboard.
Never commit real keys to GitHub. All values should come from environment
variables.
"""

import os


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


# --- Robinhood ---
ROBINHOOD_USERNAME = os.environ.get("ROBINHOOD_USERNAME")
ROBINHOOD_PASSWORD = os.environ.get("ROBINHOOD_PASSWORD")

# --- NewsAPI ---
# Free tier at newsapi.org — 100 requests/day.
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
NEWS_MAX_TICKERS_PER_RUN = _int_env("NEWS_MAX_TICKERS_PER_RUN", 8)
NEWS_PAGE_SIZE = _int_env("NEWS_PAGE_SIZE", 5)

# --- Finnhub market data ---
# Currently optional. Finnhub stock/candle may be plan-restricted depending on the key.
FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY")
MARKET_BENCHMARK_TICKER = os.environ.get("MARKET_BENCHMARK_TICKER", "QQQ")

# --- Tradier market/options data ---
# Required for Tradier Provider v1. Use your production token for live data,
# or a sandbox token with TRADIER_ENV=sandbox for delayed/paper-trading tests.
TRADIER_ACCESS_TOKEN = os.environ.get("TRADIER_ACCESS_TOKEN")
# Optional. If omitted, the app will try the Tradier user/profile endpoint to find accounts.
TRADIER_ACCOUNT_ID = os.environ.get("TRADIER_ACCOUNT_ID")
TRADIER_ENV = os.environ.get("TRADIER_ENV", "prod").strip().lower()
TRADIER_MAX_TICKERS_PER_RUN = _int_env("TRADIER_MAX_TICKERS_PER_RUN", 2)
TRADIER_INCLUDE_GREEKS = _bool_env("TRADIER_INCLUDE_GREEKS", True)
TRADIER_MIN_DAYS_TO_EXPIRATION = _int_env("TRADIER_MIN_DAYS_TO_EXPIRATION", 7)
TRADIER_CHAIN_EXPIRATIONS_PER_TICKER = _int_env("TRADIER_CHAIN_EXPIRATIONS_PER_TICKER", 1)


# --- Calendar spread screener ---
# Read-only scanner using Tradier option chains. This does not detect open
# broker positions and does not place orders.
CALENDAR_SCANNER_ENABLED = _bool_env("CALENDAR_SCANNER_ENABLED", True)
CALENDAR_MAX_TICKERS_PER_RUN = _int_env("CALENDAR_MAX_TICKERS_PER_RUN", 2)
CALENDAR_OPTION_TYPE = os.environ.get("CALENDAR_OPTION_TYPE", "call").strip().lower()
CALENDAR_FRONT_MIN_DTE = _int_env("CALENDAR_FRONT_MIN_DTE", 7)
CALENDAR_FRONT_MAX_DTE = _int_env("CALENDAR_FRONT_MAX_DTE", 21)
CALENDAR_MIN_EXPIRATION_GAP_DAYS = _int_env("CALENDAR_MIN_EXPIRATION_GAP_DAYS", 14)
CALENDAR_TARGET_EXPIRATION_GAP_DAYS = _int_env("CALENDAR_TARGET_EXPIRATION_GAP_DAYS", 30)
CALENDAR_BACK_MAX_DTE = _int_env("CALENDAR_BACK_MAX_DTE", 70)
CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER = _int_env("CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER", 1)
CALENDAR_MAX_CANDIDATES_PER_TICKER = _int_env("CALENDAR_MAX_CANDIDATES_PER_TICKER", 1)
CALENDAR_MIN_OPEN_INTEREST = _int_env("CALENDAR_MIN_OPEN_INTEREST", 50)
CALENDAR_MIN_VOLUME = _int_env("CALENDAR_MIN_VOLUME", 10)
CALENDAR_MAX_LEG_SPREAD_PCT = _int_env("CALENDAR_MAX_LEG_SPREAD_PCT", 15)
CALENDAR_MAX_DEBIT_PCT_UNDERLYING = _int_env("CALENDAR_MAX_DEBIT_PCT_UNDERLYING", 8)
CALENDAR_MAX_ATM_DISTANCE_PCT = _int_env("CALENDAR_MAX_ATM_DISTANCE_PCT", 3)

# --- Open options position detector ---
# Read-only account-position parsing. It detects option legs held at Tradier and
# groups simple calendar spreads. It does not place or close trades.
OPEN_OPTIONS_DETECTOR_ENABLED = _bool_env("OPEN_OPTIONS_DETECTOR_ENABLED", True)
OPEN_OPTIONS_QUOTE_LEGS = _bool_env("OPEN_OPTIONS_QUOTE_LEGS", True)
OPEN_OPTIONS_MAX_LEGS_TO_PRICE = _int_env("OPEN_OPTIONS_MAX_LEGS_TO_PRICE", 20)
OPEN_OPTIONS_MAX_ACCOUNTS = _int_env("OPEN_OPTIONS_MAX_ACCOUNTS", 3)


# --- Earnings timestamp provider ---
# Earnings Provider v1 is optional and read-only. It uses Finnhub earnings
# calendar by default because FINNHUB_API_KEY already exists in the app.
# If Finnhub denies or returns no data, the run still completes and earnings
# fields show as unavailable.
EARNINGS_PROVIDER_ENABLED = _bool_env("EARNINGS_PROVIDER_ENABLED", True)
EARNINGS_PROVIDER = os.environ.get("EARNINGS_PROVIDER", "finnhub").strip().lower()
EARNINGS_LOOKAHEAD_DAYS = _int_env("EARNINGS_LOOKAHEAD_DAYS", 45)
EARNINGS_LOOKBACK_DAYS = _int_env("EARNINGS_LOOKBACK_DAYS", 7)
EARNINGS_MAX_TICKERS_PER_RUN = _int_env("EARNINGS_MAX_TICKERS_PER_RUN", 8)

# --- Calendar lifecycle checker ---
# Uses detected open calendars from Tradier positions. It does not require
# persistence, but exit gain/loss is more useful when broker cost basis or a
# later trade-memory module provides entry debit.
CALENDAR_LIFECYCLE_ENABLED = _bool_env("CALENDAR_LIFECYCLE_ENABLED", True)
CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT = _int_env("CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT", 50)
CALENDAR_LIFECYCLE_MAX_LOSS_PCT = _int_env("CALENDAR_LIFECYCLE_MAX_LOSS_PCT", -35)
CALENDAR_LIFECYCLE_URGENT_DTE = _int_env("CALENDAR_LIFECYCLE_URGENT_DTE", 3)
CALENDAR_LIFECYCLE_REVIEW_DTE = _int_env("CALENDAR_LIFECYCLE_REVIEW_DTE", 7)
CALENDAR_LIFECYCLE_NEAR_MONEY_PCT = _int_env("CALENDAR_LIFECYCLE_NEAR_MONEY_PCT", 2)

# --- Endpoint security ---
# A secret token to protect the /run endpoint from being triggered by anyone.
RUN_TOKEN = os.environ.get("RUN_TOKEN")

# --- Optional notifications ---
# Used by the notification provider and Robinhood login failure alerts.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")


# --- Development/testing controls ---
# APP_MODE can be set to "dev" in Railway while actively testing, or you can
# pass ?mode=dev to /run for one-off dev runs. Dev mode still fetches the full
# Robinhood portfolio, but limits external provider calls such as NewsAPI,
# Finnhub, and Tradier.
APP_MODE = os.environ.get("APP_MODE", "prod").strip().lower()
DEV_MAX_TICKERS = _int_env("DEV_MAX_TICKERS", 2)
DEV_TICKERS = [
    ticker.strip().upper()
    for ticker in os.environ.get("DEV_TICKERS", "NVDA,AMZN").split(",")
    if ticker.strip()
]
