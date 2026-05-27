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
# Optional secondary earnings-calendar provider. Alpha Vantage returns a CSV
# earnings calendar and is used as a fallback/merge source when configured.
ALPHA_VANTAGE_API_KEY = os.environ.get("ALPHA_VANTAGE_API_KEY")
MARKET_BENCHMARK_TICKER = os.environ.get("MARKET_BENCHMARK_TICKER", "QQQ")
MARKET_DATA_USE_TRADIER_FALLBACK = _bool_env("MARKET_DATA_USE_TRADIER_FALLBACK", True)
TRADIER_HISTORICAL_LOOKBACK_DAYS = _int_env("TRADIER_HISTORICAL_LOOKBACK_DAYS", 460)
TRADIER_HISTORICAL_INTERVAL = os.environ.get("TRADIER_HISTORICAL_INTERVAL", "daily").strip().lower()
MARKET_DATA_MAX_TICKERS_PER_RUN = _int_env("MARKET_DATA_MAX_TICKERS_PER_RUN", 20)

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

# Robinhood options are detected automatically for viewing/lifecycle checks.
# Manual trade entry is intentionally out of scope for this project.
ROBINHOOD_OPTIONS_DETECTOR_ENABLED = _bool_env("ROBINHOOD_OPTIONS_DETECTOR_ENABLED", True)
ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS = os.environ.get("ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS", "").strip()
ROBINHOOD_OPTIONS_MAX_POSITIONS = _int_env("ROBINHOOD_OPTIONS_MAX_POSITIONS", 50)
# The taxable/default Robinhood brokerage account is often displayed as "Investing" in the UI.
# Passing no account_number to robin_stocks options APIs targets this default account.
ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT = _bool_env("ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT", True)
ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL = os.environ.get("ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL", "Investing").strip() or "Investing"
ROBINHOOD_OPTIONS_INFER_CALENDARS = _bool_env("ROBINHOOD_OPTIONS_INFER_CALENDARS", True)
# Robinhood option average prices sometimes arrive as cents (172) instead of dollars (1.72).
# auto: treat unusually large option average prices as cents; dollars/cents force behavior.
ROBINHOOD_OPTION_AVG_PRICE_SCALE = os.environ.get("ROBINHOOD_OPTION_AVG_PRICE_SCALE", "auto").strip().lower() or "auto"


# --- Earnings timestamp provider ---
# Earnings Provider v1 is optional and read-only. It uses Finnhub earnings
# calendar by default because FINNHUB_API_KEY already exists in the app.
# If Finnhub denies or returns no data, the run still completes and earnings
# fields show as unavailable.
EARNINGS_PROVIDER_ENABLED = _bool_env("EARNINGS_PROVIDER_ENABLED", True)
# Primary provider name kept for backward compatibility.
EARNINGS_PROVIDER = os.environ.get("EARNINGS_PROVIDER", "finnhub").strip().lower()
# Ordered provider list. With ALPHA_VANTAGE_API_KEY set, default behavior is to
# use Finnhub + Alpha Vantage and merge/dedupe results.
EARNINGS_PROVIDER_ORDER = [
    provider.strip().lower()
    for provider in os.environ.get("EARNINGS_PROVIDER_ORDER", "finnhub,alphavantage").split(",")
    if provider.strip()
]
EARNINGS_MERGE_PROVIDER_EVENTS = _bool_env("EARNINGS_MERGE_PROVIDER_EVENTS", True)
ALPHA_VANTAGE_EARNINGS_HORIZON = os.environ.get("ALPHA_VANTAGE_EARNINGS_HORIZON", "3month").strip().lower()
EARNINGS_LOOKAHEAD_DAYS = _int_env("EARNINGS_LOOKAHEAD_DAYS", 45)
EARNINGS_LOOKBACK_DAYS = _int_env("EARNINGS_LOOKBACK_DAYS", 7)
EARNINGS_MAX_TICKERS_PER_RUN = _int_env("EARNINGS_MAX_TICKERS_PER_RUN", 8)

# --- Earnings trade discovery universe ---
# Separate from portfolio/watchlist. This starts from provider earnings-calendar
# events, then runs Tradier option-chain/calendar scoring only on those tickers.
EARNINGS_DISCOVERY_ENABLED = _bool_env("EARNINGS_DISCOVERY_ENABLED", True)
EARNINGS_DISCOVERY_START_DAYS = _int_env("EARNINGS_DISCOVERY_START_DAYS", 2)
EARNINGS_DISCOVERY_END_DAYS = _int_env("EARNINGS_DISCOVERY_END_DAYS", 4)
EARNINGS_DISCOVERY_MAX_EVENTS = _int_env("EARNINGS_DISCOVERY_MAX_EVENTS", 25)
# Raw discovery and optionability are intentionally separate. Dev mode should
# limit expensive Tradier checks, not randomly truncate the raw earnings list to
# the first two low-quality tickers.
EARNINGS_DISCOVERY_RAW_EVENT_LIMIT = _int_env("EARNINGS_DISCOVERY_RAW_EVENT_LIMIT", 100)
EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT = _int_env("EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT", 50)
EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK = _int_env("EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK", 12)
EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK = _int_env("EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK", 6)
EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES = _int_env("EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES", 6)
EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE = _int_env("EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE", 5)
EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME = _int_env("EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME", 500000)
EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN = _int_env("EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN", 6)

# --- Unified calendar trade engine ---
# User-facing orchestration layer that combines earnings discovery, candidate
# spread screening, earnings-calendar scoring, open-position detection, and
# lifecycle next actions into one calendar-trade workflow section.
UNIFIED_CALENDAR_ENGINE_ENABLED = _bool_env("UNIFIED_CALENDAR_ENGINE_ENABLED", True)
# Main report should show the unified calendar engine by default. Set this true
# only when debugging the lower-level calendar modules.
REPORT_SHOW_CALENDAR_DEBUG_SECTIONS = _bool_env("REPORT_SHOW_CALENDAR_DEBUG_SECTIONS", False)

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
CALENDAR_LIFECYCLE_ASSIGNMENT_DTE = _int_env("CALENDAR_LIFECYCLE_ASSIGNMENT_DTE", 3)
CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT = _int_env("CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT", CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT)
CALENDAR_LIFECYCLE_STOP_LOSS_PCT = _int_env("CALENDAR_LIFECYCLE_STOP_LOSS_PCT", CALENDAR_LIFECYCLE_MAX_LOSS_PCT)

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

# --- Earnings calendar strategy layer ---
# Evaluates whether calendar-spread candidates are actually positioned around
# earnings. This is read-only and uses already-fetched earnings/candidate data.
EARNINGS_CALENDAR_STRATEGY_ENABLED = _bool_env("EARNINGS_CALENDAR_STRATEGY_ENABLED", True)
EARNINGS_CALENDAR_URGENT_DTE = _int_env("EARNINGS_CALENDAR_URGENT_DTE", 1)
EARNINGS_CALENDAR_PREFERRED_BONUS = _int_env("EARNINGS_CALENDAR_PREFERRED_BONUS", 8)
EARNINGS_CALENDAR_UNKNOWN_TIMESTAMP_SCORE_CAP = _int_env("EARNINGS_CALENDAR_UNKNOWN_TIMESTAMP_SCORE_CAP", 60)
EARNINGS_CALENDAR_UNCONFIRMED_SCORE_CAP = _int_env("EARNINGS_CALENDAR_UNCONFIRMED_SCORE_CAP", 70)
EARNINGS_CALENDAR_SHORT_SPANS_EVENT_SCORE_CAP = _int_env("EARNINGS_CALENDAR_SHORT_SPANS_EVENT_SCORE_CAP", 55)

# --- Watchlist / portfolio gap candidate pipeline ---
# Pulls candidate tickers from Robinhood watchlists when available, and/or from
# WATCHLIST_TICKERS as a manual fallback. These tickers are treated as a
# "watching" category, not as owned positions.
# Leave WATCHLIST_NAMES blank to discover and scan all Robinhood watchlists.
WATCHLIST_ENABLED = _bool_env("WATCHLIST_ENABLED", True)
WATCHLIST_SOURCE = os.environ.get("WATCHLIST_SOURCE", "robinhood,manual").strip().lower()
WATCHLIST_NAMES = [
    name.strip()
    for name in os.environ.get("WATCHLIST_NAMES", "").split(",")
    if name.strip()
]
WATCHLIST_TICKERS = [
    ticker.strip().upper()
    for ticker in os.environ.get("WATCHLIST_TICKERS", "").split(",")
    if ticker.strip()
]
WATCHLIST_MAX_TICKERS_PER_RUN = _int_env("WATCHLIST_MAX_TICKERS_PER_RUN", 20)
WATCHLIST_PRIORITIZE_FOR_SCANS = _bool_env("WATCHLIST_PRIORITIZE_FOR_SCANS", True)
WATCHLIST_INCLUDE_ALREADY_HELD = _bool_env("WATCHLIST_INCLUDE_ALREADY_HELD", True)

# --- Portfolio gap / sector suggestions ---
# Rule-based v1. This is stock-focused and separate from options/calendar logic.
# Targets are intentionally aggressive-growth oriented rather than classic balanced allocation.
# Later versions can adjust these dynamically from macro/sector-strength data.
PORTFOLIO_GAP_ENABLED = _bool_env("PORTFOLIO_GAP_ENABLED", True)
PORTFOLIO_GAP_TARGET_PROFILE = os.environ.get("PORTFOLIO_GAP_TARGET_PROFILE", "aggressive_macro_growth").strip().lower()
PORTFOLIO_GAP_CORE_TARGETS = os.environ.get(
    "PORTFOLIO_GAP_CORE_TARGETS",
    "AI / Semiconductors:18,Mega-cap Tech / Cloud:18,Software / Fintech:12,Energy / Utilities / Infrastructure:12,Healthcare / Biotech:10,Industrials / Defense / Robotics:10,Financials:8,Consumer / Retail:7,International / ADR:5",
)
PORTFOLIO_GAP_MACRO_WINNING_BUCKETS = [
    bucket.strip()
    for bucket in os.environ.get(
        "PORTFOLIO_GAP_MACRO_WINNING_BUCKETS",
        "AI / Semiconductors,Mega-cap Tech / Cloud,Energy / Utilities / Infrastructure,Industrials / Defense / Robotics,Healthcare / Biotech",
    ).split(",")
    if bucket.strip()
]
PORTFOLIO_GAP_RISK_TARGETS = os.environ.get(
    "PORTFOLIO_GAP_RISK_TARGETS",
    "Crypto / Digital Assets:5,Speculative / High Beta:12,Leveraged ETFs:4,Single-Name Max:15",
)
PORTFOLIO_GAP_MAX_SUGGESTIONS = _int_env("PORTFOLIO_GAP_MAX_SUGGESTIONS", 10)
PORTFOLIO_GAP_MIN_SUGGESTION_SCORE = _int_env("PORTFOLIO_GAP_MIN_SUGGESTION_SCORE", 55)
PORTFOLIO_GAP_INCLUDE_ALREADY_HELD = _bool_env("PORTFOLIO_GAP_INCLUDE_ALREADY_HELD", True)

# --- Stock Momentum Add Strategy ---
# Read-only stock strategy for portfolio + watchlist names. It looks for
# aggressive-growth candidates that have momentum/trend support and produces
# add / add-on-pullback / watch / avoid labels.
STOCK_MOMENTUM_STRATEGY_ENABLED = _bool_env("STOCK_MOMENTUM_STRATEGY_ENABLED", True)
STOCK_MOMENTUM_MAX_CANDIDATES = _int_env("STOCK_MOMENTUM_MAX_CANDIDATES", 12)
STOCK_MOMENTUM_MIN_SCORE_TO_CONSIDER = _int_env("STOCK_MOMENTUM_MIN_SCORE_TO_CONSIDER", 62)
STOCK_MOMENTUM_PULLBACK_FROM_HIGH_PCT = _int_env("STOCK_MOMENTUM_PULLBACK_FROM_HIGH_PCT", 8)
STOCK_MOMENTUM_OVEREXTENDED_FROM_HIGH_PCT = _int_env("STOCK_MOMENTUM_OVEREXTENDED_FROM_HIGH_PCT", 2)
STOCK_MOMENTUM_MAX_SINGLE_NAME_ALLOCATION_PCT = _int_env("STOCK_MOMENTUM_MAX_SINGLE_NAME_ALLOCATION_PCT", 15)
STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX = _int_env("STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX", 6)

# --- Daily Opportunity Engine ---
# Unified daily action list that combines: calendar trade candidates, stock
# momentum add candidates, portfolio gap suggestions, and risk/avoid actions.
DAILY_OPPORTUNITY_ENGINE_ENABLED = _bool_env("DAILY_OPPORTUNITY_ENGINE_ENABLED", True)
DAILY_OPPORTUNITY_MAX_ACTIONS = _int_env("DAILY_OPPORTUNITY_MAX_ACTIONS", 12)
DAILY_OPPORTUNITY_MIN_SCORE = _int_env("DAILY_OPPORTUNITY_MIN_SCORE", 55)

# --- Trade memory / SQLite persistence ---
# Stores manually entered calendar trades so lifecycle checks can calculate
# entry-based targets across deploys/restarts. On Railway, attach a Volume and
# either mount it to /app/data or set TRADE_MEMORY_DB_PATH explicitly.
TRADE_MEMORY_ENABLED = _bool_env("TRADE_MEMORY_ENABLED", True)
DATA_DIR = os.environ.get(
    "DATA_DIR",
    os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "/app/data"),
).strip()
TRADE_MEMORY_DB_PATH = os.environ.get(
    "TRADE_MEMORY_DB_PATH",
    os.path.join(DATA_DIR, "trade_memory.sqlite3"),
).strip()
TRADE_MEMORY_DEFAULT_PROFIT_TARGET_PCT = _int_env("TRADE_MEMORY_DEFAULT_PROFIT_TARGET_PCT", 50)
TRADE_MEMORY_DEFAULT_MAX_LOSS_PCT = _int_env("TRADE_MEMORY_DEFAULT_MAX_LOSS_PCT", -35)
TRADE_MEMORY_DEFAULT_STATUS = os.environ.get("TRADE_MEMORY_DEFAULT_STATUS", "open").strip().lower()
