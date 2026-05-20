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
TRADIER_ENV = os.environ.get("TRADIER_ENV", "prod").strip().lower()
TRADIER_MAX_TICKERS_PER_RUN = _int_env("TRADIER_MAX_TICKERS_PER_RUN", 2)
TRADIER_INCLUDE_GREEKS = _bool_env("TRADIER_INCLUDE_GREEKS", True)
TRADIER_MIN_DAYS_TO_EXPIRATION = _int_env("TRADIER_MIN_DAYS_TO_EXPIRATION", 7)
TRADIER_CHAIN_EXPIRATIONS_PER_TICKER = _int_env("TRADIER_CHAIN_EXPIRATIONS_PER_TICKER", 1)

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
