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


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default


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
MARKET_DATA_PROVIDER_ORDER = [
    provider.strip().lower()
    for provider in os.environ.get("MARKET_DATA_PROVIDER_ORDER", "finnhub,tradier,alphavantage").split(",")
    if provider.strip()
]
MARKET_DATA_CANDLE_REQUIRED_BARS = _int_env("MARKET_DATA_CANDLE_REQUIRED_BARS", 240)
MARKET_DATA_CANDLE_RECENT_DAYS = _int_env("MARKET_DATA_CANDLE_RECENT_DAYS", 7)
TRADIER_HISTORICAL_LOOKBACK_DAYS = _int_env("TRADIER_HISTORICAL_LOOKBACK_DAYS", 460)
TRADIER_HISTORICAL_INTERVAL = os.environ.get("TRADIER_HISTORICAL_INTERVAL", "daily").strip().lower()
MARKET_DATA_MAX_TICKERS_PER_RUN = _int_env("MARKET_DATA_MAX_TICKERS_PER_RUN", 20)
MARKET_DATA_HUB_ENABLED = _bool_env("MARKET_DATA_HUB_ENABLED", True)
MARKET_DATA_ENABLE_SQLITE_CACHE = _bool_env("MARKET_DATA_ENABLE_SQLITE_CACHE", True)
MARKET_DATA_ENABLE_WAL = _bool_env("MARKET_DATA_ENABLE_WAL", True)
MARKET_DATA_DB_PATH = os.environ.get(
    "MARKET_DATA_DB_PATH",
    "/app/data/market_data.sqlite3" if os.path.isdir("/app/data") else "data/market_data.sqlite3",
)
MARKET_DATA_QUOTE_TTL_SECONDS = _int_env("MARKET_DATA_QUOTE_TTL_SECONDS", 900)
MARKET_DATA_OPTIONS_CHAIN_TTL_SECONDS = _int_env("MARKET_DATA_OPTIONS_CHAIN_TTL_SECONDS", 1800)
MARKET_DATA_CANDLES_TTL_SECONDS = _int_env("MARKET_DATA_CANDLES_TTL_SECONDS", 43200)
MARKET_DATA_EARNINGS_TTL_SECONDS = _int_env("MARKET_DATA_EARNINGS_TTL_SECONDS", 43200)
MARKET_DATA_DERIVED_METRICS_TTL_SECONDS = _int_env("MARKET_DATA_DERIVED_METRICS_TTL_SECONDS", 43200)
MARKET_DATA_PROVIDER_ERROR_TTL_SECONDS = _int_env("MARKET_DATA_PROVIDER_ERROR_TTL_SECONDS", 900)
MARKET_DATA_MAX_PROVIDER_FETCHES_PER_RUN = _int_env("MARKET_DATA_MAX_PROVIDER_FETCHES_PER_RUN", 25)
MARKET_DATA_SHOW_COVERAGE_PANEL = _bool_env("MARKET_DATA_SHOW_COVERAGE_PANEL", True)
REPORT_SNAPSHOT_DB_PATH = os.environ.get(
    "REPORT_SNAPSHOT_DB_PATH",
    "/app/data/report_snapshots.sqlite3" if os.path.isdir("/app/data") else "data/report_snapshots.sqlite3",
)
BROKER_POSITION_SNAPSHOT_DB_PATH = os.environ.get("BROKER_POSITION_SNAPSHOT_DB_PATH", REPORT_SNAPSHOT_DB_PATH)
STRATEGY_OPPORTUNITY_DB_PATH = os.environ.get(
    "STRATEGY_OPPORTUNITY_DB_PATH",
    "/app/data/strategy_opportunities.sqlite3" if os.path.isdir("/app/data") else "data/strategy_opportunities.sqlite3",
)
RUN_MANIFEST_DB_PATH = os.environ.get("RUN_MANIFEST_DB_PATH", REPORT_SNAPSHOT_DB_PATH)
USAGE_TELEMETRY_ENABLED = _bool_env("USAGE_TELEMETRY_ENABLED", True)
USAGE_TELEMETRY_DB_PATH = os.environ.get("USAGE_TELEMETRY_DB_PATH", REPORT_SNAPSHOT_DB_PATH)
USAGE_TELEMETRY_RETENTION_LIMIT = _int_env("USAGE_TELEMETRY_RETENTION_LIMIT", 5000)
USAGE_TELEMETRY_SIZE_PROFILE_RETENTION_LIMIT = _int_env("USAGE_TELEMETRY_SIZE_PROFILE_RETENTION_LIMIT", 500)
USAGE_TELEMETRY_METADATA_MAX_CHARS = _int_env("USAGE_TELEMETRY_METADATA_MAX_CHARS", 2000)
USAGE_TELEMETRY_SIZE_WARNING_BYTES = _int_env("USAGE_TELEMETRY_SIZE_WARNING_BYTES", 250_000)
USAGE_TELEMETRY_SIZE_LARGE_BYTES = _int_env("USAGE_TELEMETRY_SIZE_LARGE_BYTES", 500_000)
USAGE_TELEMETRY_SIZE_CRITICAL_BYTES = _int_env("USAGE_TELEMETRY_SIZE_CRITICAL_BYTES", 1_000_000)
ENABLE_RUNTIME_PROFILE = _bool_env("ENABLE_RUNTIME_PROFILE", True)
ENABLE_VERBOSE_RUNTIME_PROFILE = _bool_env("ENABLE_VERBOSE_RUNTIME_PROFILE", False)
ENABLE_PAYLOAD_SIZE_PROFILE = _bool_env("ENABLE_PAYLOAD_SIZE_PROFILE", True)
PROVIDER_PAYLOAD_BUDGET_BYTES = _int_env("PROVIDER_PAYLOAD_BUDGET_BYTES", 1_000_000)
ENABLE_STORAGE_PROFILE = _bool_env("ENABLE_STORAGE_PROFILE", True)
ENABLE_DEV_SNAPSHOT_ENDPOINT = _bool_env("ENABLE_DEV_SNAPSHOT_ENDPOINT", os.environ.get("APP_MODE", "prod").strip().lower() == "dev")
ENABLE_DEV_DIAGNOSTICS_ENDPOINTS = _bool_env("ENABLE_DEV_DIAGNOSTICS_ENDPOINTS", ENABLE_DEV_SNAPSHOT_ENDPOINT)
DEV_SNAPSHOT_REQUIRE_TOKEN = _bool_env("DEV_SNAPSHOT_REQUIRE_TOKEN", True)
DEV_SNAPSHOT_DEFAULT_MODE = os.environ.get("DEV_SNAPSHOT_DEFAULT_MODE", "latest").strip().lower()
DEV_SNAPSHOT_ALLOW_FRESH = _bool_env("DEV_SNAPSHOT_ALLOW_FRESH", False)
DEV_SNAPSHOT_INCLUDE_RAW_PROVIDER_PAYLOADS = _bool_env("DEV_SNAPSHOT_INCLUDE_RAW_PROVIDER_PAYLOADS", False)
DEV_SNAPSHOT_INCLUDE_FULL_LOG = _bool_env("DEV_SNAPSHOT_INCLUDE_FULL_LOG", False)
DEV_SNAPSHOT_INCLUDE_FULL_STRATEGY_ROWS = _bool_env("DEV_SNAPSHOT_INCLUDE_FULL_STRATEGY_ROWS", True)
REPORT_INCLUDE_RAW_PROVIDER_PAYLOADS = _bool_env("REPORT_INCLUDE_RAW_PROVIDER_PAYLOADS", False)
REPORT_INCLUDE_HEAVY_DEBUG = _bool_env("REPORT_INCLUDE_HEAVY_DEBUG", False)
REPORT_SNAPSHOT_RETENTION_LIMIT = _int_env("REPORT_SNAPSHOT_RETENTION_LIMIT", 20)
REPORT_SNAPSHOT_MAX_LOG_LINES = _int_env("REPORT_SNAPSHOT_MAX_LOG_LINES", 250)
REPORT_SNAPSHOT_STORE_COMPRESSED_FULL = _bool_env("REPORT_SNAPSHOT_STORE_COMPRESSED_FULL", True)
REPORT_SNAPSHOT_HOT_LOG_LINES = _int_env("REPORT_SNAPSHOT_HOT_LOG_LINES", 10)
REPORT_SNAPSHOT_HOT_STRATEGY_ROWS = _int_env("REPORT_SNAPSHOT_HOT_STRATEGY_ROWS", 5)
REPORT_FRESHNESS_WARN_SECONDS = _int_env("REPORT_FRESHNESS_WARN_SECONDS", 21600)
REPORT_FRESHNESS_STALE_SECONDS = _int_env("REPORT_FRESHNESS_STALE_SECONDS", 86400)
RUN_MANIFEST_RETENTION_LIMIT = _int_env("RUN_MANIFEST_RETENTION_LIMIT", 200)
MARKET_DATA_FETCH_LOG_RETENTION_DAYS = _int_env("MARKET_DATA_FETCH_LOG_RETENTION_DAYS", 14)
MARKET_DATA_COVERAGE_RETENTION_DAYS = _int_env("MARKET_DATA_COVERAGE_RETENTION_DAYS", 30)
OPTION_CHAIN_SNAPSHOT_RETENTION_DAYS = _int_env("OPTION_CHAIN_SNAPSHOT_RETENTION_DAYS", 7)
ACTIVE_TRADES_DEFAULT_DETAIL = os.environ.get("ACTIVE_TRADES_DEFAULT_DETAIL", "summary").strip().lower()

# --- Dashboard presentation ---
# The default route renders a compact operational shell. The existing complete
# report remains available on demand with ?view=full or ?detail=full.
DASHBOARD_DEFAULT_VIEW = os.environ.get("DASHBOARD_DEFAULT_VIEW", "shell").strip().lower()
REPORT_DEFAULT_MAX_ROWS_PER_SECTION = _int_env("REPORT_DEFAULT_MAX_ROWS_PER_SECTION", 3)
REPORT_SHOW_DETAIL_LOAD_BUTTONS = _bool_env("REPORT_SHOW_DETAIL_LOAD_BUTTONS", True)
REPORT_SHOW_FULL_DEBUG_BY_DEFAULT = _bool_env("REPORT_SHOW_FULL_DEBUG_BY_DEFAULT", False)

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
# Require ≥2 sources to confirm earnings timestamp (TKT-025/026).
EARNINGS_CONFIRM_REQUIRE_MULTI_SOURCE = _bool_env("EARNINGS_CONFIRM_REQUIRE_MULTI_SOURCE", True)
# Dates from different sources within this many days flag earnings_source_conflict (TKT-025).
EARNINGS_DATE_CONFLICT_THRESHOLD_DAYS = _int_env("EARNINGS_DATE_CONFLICT_THRESHOLD_DAYS", 2)
EARNINGS_MIN_SOURCES_FOR_HIGH_CONFIDENCE = _int_env("EARNINGS_MIN_SOURCES_FOR_HIGH_CONFIDENCE", 2)
ALPHA_VANTAGE_EARNINGS_HORIZON = os.environ.get("ALPHA_VANTAGE_EARNINGS_HORIZON", "3month").strip().lower()
EARNINGS_LOOKAHEAD_DAYS = _int_env("EARNINGS_LOOKAHEAD_DAYS", 45)
EARNINGS_LOOKBACK_DAYS = _int_env("EARNINGS_LOOKBACK_DAYS", 7)
EARNINGS_MAX_TICKERS_PER_RUN = _int_env("EARNINGS_MAX_TICKERS_PER_RUN", 8)

# --- Earnings trade discovery universe ---
# Separate from portfolio/watchlist. This starts from provider earnings-calendar
# events, then runs Tradier option-chain/calendar scoring only on those tickers.
EARNINGS_DISCOVERY_ENABLED = _bool_env("EARNINGS_DISCOVERY_ENABLED", True)
EARNINGS_DISCOVERY_START_DAYS = _int_env("EARNINGS_DISCOVERY_START_DAYS", 4)
# The product contract is a +4..+21 discovery horizon. Preserve the requested
# Railway value for diagnostics, but do not allow a stale override to silently
# change the runtime calendar universe.
EARNINGS_DISCOVERY_END_DAYS_REQUESTED = _int_env("EARNINGS_DISCOVERY_END_DAYS", 21)
EARNINGS_DISCOVERY_END_DAYS = 21
EARNINGS_DISCOVERY_MAX_EVENTS = _int_env("EARNINGS_DISCOVERY_MAX_EVENTS", 25)
# Raw discovery and optionability are intentionally separate. Dev mode should
# limit expensive Tradier checks, not randomly truncate the raw earnings list to
# the first two low-quality tickers.
_is_prod_mode = os.environ.get("APP_MODE", "prod").strip().lower() != "dev"
EARNINGS_DISCOVERY_RAW_EVENT_LIMIT = _int_env("EARNINGS_DISCOVERY_RAW_EVENT_LIMIT", 200 if _is_prod_mode else 100)
EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT = _int_env("EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT", 50)
EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK = _int_env("EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK", 40 if _is_prod_mode else 12)
EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK = _int_env("EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK", 6)
EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES = _int_env("EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES", 20 if _is_prod_mode else 6)
EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE = _int_env("EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE", 5)
EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME = _int_env("EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME", 500000)
EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN = _int_env("EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN", 6)

# --- Universe discovery (expanded ticker universe) ---
UNIVERSE_DISCOVERY_ENABLED = _bool_env("UNIVERSE_DISCOVERY_ENABLED", True)
UNIVERSE_DISCOVERY_CONSTITUENT_REFRESH_DAYS = _int_env("UNIVERSE_DISCOVERY_CONSTITUENT_REFRESH_DAYS", 7)
UNIVERSE_DISCOVERY_CACHE_TTL_HOURS = _int_env("UNIVERSE_DISCOVERY_CACHE_TTL_HOURS", 20)
EARNINGS_DISCOVERY_UNIVERSE_MAX_CANDIDATES = _int_env("EARNINGS_DISCOVERY_UNIVERSE_MAX_CANDIDATES", 50)
UNIVERSE_MIN_PRICE = _float_env("UNIVERSE_MIN_PRICE", 10.0)
UNIVERSE_MAX_PRICE = _float_env("UNIVERSE_MAX_PRICE", 1000.0)
UNIVERSE_MIN_AVG_VOLUME = _int_env("UNIVERSE_MIN_AVG_VOLUME", 500000)
UNIVERSE_DISCOVERY_DB_PATH = os.environ.get(
    "UNIVERSE_DISCOVERY_DB_PATH",
    "/app/data/universe_discovery.sqlite3" if os.path.isdir("/app/data") else "data/universe_discovery.sqlite3",
)

# --- Earnings calendar timing / ranking defaults ---
# Calendar discovery now scans farther ahead than the original +2..+4 day
# window. The strategy should find candidates early enough to review, then
# rank whether today is a good entry window. Backtests only run on candidates
# that pass the full criteria gate.
EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE = _int_env("EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE", 6)
EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE = _int_env("EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE", 12)
EARNINGS_CALENDAR_LATE_ENTRY_DTE = _int_env("EARNINGS_CALENDAR_LATE_ENTRY_DTE", 4)
CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS = _bool_env("CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS", True)
CALENDAR_EARNINGS_FRONT_MIN_DTE = _int_env("CALENDAR_EARNINGS_FRONT_MIN_DTE", 1)
CALENDAR_EARNINGS_FRONT_MAX_DTE = _int_env("CALENDAR_EARNINGS_FRONT_MAX_DTE", 14)
CALENDAR_EARNINGS_BACK_MIN_DTE_AFTER_EVENT = _int_env("CALENDAR_EARNINGS_BACK_MIN_DTE_AFTER_EVENT", 14)
CALENDAR_EARNINGS_BACK_MAX_DTE = _int_env("CALENDAR_EARNINGS_BACK_MAX_DTE", 75)
CALENDAR_RANKING_MIN_SCORE_TO_BACKTEST = _int_env("CALENDAR_RANKING_MIN_SCORE_TO_BACKTEST", 70)
CALENDAR_RANKING_MIN_PASSED_REQUIREMENTS = _int_env("CALENDAR_RANKING_MIN_PASSED_REQUIREMENTS", 7)
CALENDAR_BACKTEST_ENABLED = _bool_env("CALENDAR_BACKTEST_ENABLED", True)
CALENDAR_BACKTEST_MAX_CANDIDATES = _int_env("CALENDAR_BACKTEST_MAX_CANDIDATES", 3)
CALENDAR_BACKTEST_MAX_EVENTS = _int_env("CALENDAR_BACKTEST_MAX_EVENTS", 10)
CALENDAR_BACKTEST_LOOKBACK_DAYS = _int_env("CALENDAR_BACKTEST_LOOKBACK_DAYS", 900)
CALENDAR_BACKTEST_ENTRY_DAYS_BEFORE = _int_env("CALENDAR_BACKTEST_ENTRY_DAYS_BEFORE", 7)
CALENDAR_BACKTEST_EXIT_DAYS_AFTER = _int_env("CALENDAR_BACKTEST_EXIT_DAYS_AFTER", 1)

# --- Automatically generated calendar opportunity cache ---
# This stores scanner snapshots only. It is not manual trade memory/tracking.
CALENDAR_OPPORTUNITY_CACHE_ENABLED = _bool_env("CALENDAR_OPPORTUNITY_CACHE_ENABLED", True)
CALENDAR_OPPORTUNITY_DB_PATH = os.environ.get(
    "CALENDAR_OPPORTUNITY_DB_PATH",
    "/app/data/calendar_opportunities.sqlite3" if os.path.isdir("/app/data") else "data/calendar_opportunities.sqlite3",
)
CALENDAR_OPPORTUNITY_CACHE_RECENT_LIMIT = _int_env("CALENDAR_OPPORTUNITY_CACHE_RECENT_LIMIT", 20)

# --- Strategy 2: Skew Momentum Vertical Spread ---
# Read-only scanner. It combines directional momentum with favorable short-wing
# skew and strict liquidity/payoff gates. It never places or tracks trades.
SKEW_VERTICAL_STRATEGY_ENABLED = _bool_env("SKEW_VERTICAL_STRATEGY_ENABLED", True)
SKEW_VERTICAL_MAX_TICKERS_PER_RUN = _int_env("SKEW_VERTICAL_MAX_TICKERS_PER_RUN", 8)
SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN = _int_env("SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN", 3)
SKEW_VERTICAL_INCLUDE_HOLDINGS = _bool_env("SKEW_VERTICAL_INCLUDE_HOLDINGS", True)
SKEW_VERTICAL_INCLUDE_WATCHLIST = _bool_env("SKEW_VERTICAL_INCLUDE_WATCHLIST", True)
SKEW_VERTICAL_INCLUDE_PORTFOLIO_GAP = _bool_env("SKEW_VERTICAL_INCLUDE_PORTFOLIO_GAP", True)
SKEW_VERTICAL_MIN_UNDERLYING_PRICE = _float_env("SKEW_VERTICAL_MIN_UNDERLYING_PRICE", 10)
SKEW_VERTICAL_MIN_AVERAGE_VOLUME = _int_env("SKEW_VERTICAL_MIN_AVERAGE_VOLUME", 1000000)
SKEW_VERTICAL_MIN_MOMENTUM_SCORE = _float_env("SKEW_VERTICAL_MIN_MOMENTUM_SCORE", 65)
SKEW_VERTICAL_MIN_BEARISH_MOMENTUM_SCORE = _float_env("SKEW_VERTICAL_MIN_BEARISH_MOMENTUM_SCORE", 65)
SKEW_VERTICAL_ALLOW_BEARISH = _bool_env("SKEW_VERTICAL_ALLOW_BEARISH", True)
SKEW_VERTICAL_ALLOW_BULLISH = _bool_env("SKEW_VERTICAL_ALLOW_BULLISH", True)
SKEW_VERTICAL_MIN_DTE = _int_env("SKEW_VERTICAL_MIN_DTE", 7)
SKEW_VERTICAL_TARGET_DTE = _int_env("SKEW_VERTICAL_TARGET_DTE", 21)
SKEW_VERTICAL_MAX_DTE = _int_env("SKEW_VERTICAL_MAX_DTE", 45)
SKEW_VERTICAL_EXPIRATIONS_PER_TICKER = _int_env("SKEW_VERTICAL_EXPIRATIONS_PER_TICKER", 3)
SKEW_VERTICAL_AVOID_EARNINGS_WITHIN_DAYS = _int_env("SKEW_VERTICAL_AVOID_EARNINGS_WITHIN_DAYS", 7)
SKEW_VERTICAL_ALLOW_EARNINGS_EVENT_RISK = _bool_env("SKEW_VERTICAL_ALLOW_EARNINGS_EVENT_RISK", False)
SKEW_VERTICAL_LONG_DELTA_MIN = _float_env("SKEW_VERTICAL_LONG_DELTA_MIN", 0.35)
SKEW_VERTICAL_LONG_DELTA_MAX = _float_env("SKEW_VERTICAL_LONG_DELTA_MAX", 0.60)
SKEW_VERTICAL_SHORT_DELTA_MIN = _float_env("SKEW_VERTICAL_SHORT_DELTA_MIN", 0.10)
SKEW_VERTICAL_SHORT_DELTA_MAX = _float_env("SKEW_VERTICAL_SHORT_DELTA_MAX", 0.35)
SKEW_VERTICAL_MAX_ATM_DISTANCE_PCT = _float_env("SKEW_VERTICAL_MAX_ATM_DISTANCE_PCT", 5)
SKEW_VERTICAL_MIN_WIDTH_DOLLARS = _float_env("SKEW_VERTICAL_MIN_WIDTH_DOLLARS", 2.5)
SKEW_VERTICAL_MAX_WIDTH_DOLLARS = _float_env("SKEW_VERTICAL_MAX_WIDTH_DOLLARS", 25)
SKEW_VERTICAL_MAX_CANDIDATES_PER_TICKER = _int_env("SKEW_VERTICAL_MAX_CANDIDATES_PER_TICKER", 3)
SKEW_VERTICAL_MIN_SHORT_IV_EDGE = _float_env("SKEW_VERTICAL_MIN_SHORT_IV_EDGE", 0.02)
SKEW_VERTICAL_MIN_SHORT_PREMIUM_FINANCING_PCT = _float_env("SKEW_VERTICAL_MIN_SHORT_PREMIUM_FINANCING_PCT", 20)
SKEW_LOTTERY_CALL_FILTER_ENABLED = _bool_env("SKEW_LOTTERY_CALL_FILTER_ENABLED", True)
SKEW_LOTTERY_CALL_DELTA_THRESHOLD = _float_env("SKEW_LOTTERY_CALL_DELTA_THRESHOLD", 0.15)
SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD = _float_env("SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD", 0.10)
SKEW_RICHNESS_THRESHOLD = _float_env("SKEW_RICHNESS_THRESHOLD", 12.5)
SKEW_DIAGNOSTIC_MODE = _bool_env("SKEW_DIAGNOSTIC_MODE", False)
SKEW_VERTICAL_MAX_DEBIT_PCT_OF_WIDTH = _float_env("SKEW_VERTICAL_MAX_DEBIT_PCT_OF_WIDTH", 45)
SKEW_VERTICAL_TARGET_DEBIT_PCT_OF_WIDTH = _float_env("SKEW_VERTICAL_TARGET_DEBIT_PCT_OF_WIDTH", 30)
SKEW_VERTICAL_MIN_OPEN_INTEREST = _int_env("SKEW_VERTICAL_MIN_OPEN_INTEREST", 50)
SKEW_VERTICAL_MIN_VOLUME = _int_env("SKEW_VERTICAL_MIN_VOLUME", 10)
SKEW_VERTICAL_MAX_LEG_SPREAD_PCT = _float_env("SKEW_VERTICAL_MAX_LEG_SPREAD_PCT", 15)
SKEW_VERTICAL_MAX_SPREAD_MARKET_WIDTH_PCT = _float_env("SKEW_VERTICAL_MAX_SPREAD_MARKET_WIDTH_PCT", 20)
SKEW_VERTICAL_MAX_DEBIT_DOLLARS = _float_env("SKEW_VERTICAL_MAX_DEBIT_DOLLARS", 300)
SKEW_VERTICAL_WARN_DEBIT_DOLLARS = _float_env("SKEW_VERTICAL_WARN_DEBIT_DOLLARS", 150)
SKEW_VERTICAL_MIN_REWARD_RISK = _float_env("SKEW_VERTICAL_MIN_REWARD_RISK", 1.5)
SKEW_VERTICAL_PREFERRED_REWARD_RISK = _float_env("SKEW_VERTICAL_PREFERRED_REWARD_RISK", 2.0)
SKEW_VERTICAL_MAX_ACCOUNT_RISK_PCT = _float_env("SKEW_VERTICAL_MAX_ACCOUNT_RISK_PCT", 2.0)
SKEW_VERTICAL_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT = _float_env("SKEW_VERTICAL_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT", 1.0)
SKEW_STALE_STRUCTURE_THRESHOLD_PCT = _float_env("SKEW_STALE_STRUCTURE_THRESHOLD_PCT", 0.03)
SKEW_VERTICAL_LIFECYCLE_ENABLED = _bool_env("SKEW_VERTICAL_LIFECYCLE_ENABLED", False)
SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED = _bool_env("SKEW_VERTICAL_OPPORTUNITY_CACHE_ENABLED", True)
SKEW_VERTICAL_OPPORTUNITY_DB_PATH = os.environ.get(
    "SKEW_VERTICAL_OPPORTUNITY_DB_PATH",
    "/app/data/skew_vertical_opportunities.sqlite3" if os.path.isdir("/app/data") else "data/skew_vertical_opportunities.sqlite3",
)
SKEW_VERTICAL_OPPORTUNITY_CACHE_RECENT_LIMIT = _int_env("SKEW_VERTICAL_OPPORTUNITY_CACHE_RECENT_LIMIT", 20)

# --- Strategy 3: Forward Factor Calendar (dry-run only) ---
FORWARD_FACTOR_STRATEGY_ENABLED = _bool_env("FORWARD_FACTOR_STRATEGY_ENABLED", True)
FORWARD_FACTOR_DRY_RUN = True
FF_CHAIN_BUDGET_RESERVED = _bool_env("FF_CHAIN_BUDGET_RESERVED", True)
FF_MIN_CHAIN_SET_BUDGET = _int_env("FF_MIN_CHAIN_SET_BUDGET", 4)
FF_SKIP_IF_ALREADY_FAILED_RECENTLY = _bool_env("FF_SKIP_IF_ALREADY_FAILED_RECENTLY", True)
FF_RECENT_FAIL_SKIP_THRESHOLD = _int_env("FF_RECENT_FAIL_SKIP_THRESHOLD", 30)
FF_JOURNAL_ENABLED = _bool_env("FF_JOURNAL_ENABLED", True)
FF_JOURNAL_DB_PATH = os.environ.get(
    "FF_JOURNAL_DB_PATH",
    "/app/data/ff_observations.db" if os.path.isdir("/app/data") else "data/ff_observations.db",
)
TELEMETRY_ENABLED = _bool_env("TELEMETRY_ENABLED", True)
LOCAL_VAULT_OUTPUT_PATH: str | None = os.environ.get("LOCAL_VAULT_OUTPUT_PATH") or None
VAULT_ENABLED = _bool_env("VAULT_ENABLED", False)
VAULT_DB_PATH = os.environ.get(
    "VAULT_DB_PATH",
    "/app/data/vault.db" if os.path.isdir("/app/data") else "data/vault.db",
)
VAULT_MAX_ENTRIES: int = int(os.environ.get("VAULT_MAX_ENTRIES") or 30)
VAULT_SCHEMA_VERSION: int = int(os.environ.get("VAULT_SCHEMA_VERSION") or 1)
TELEMETRY_DB_PATH = os.environ.get(
    "TELEMETRY_DB_PATH",
    "/app/data/telemetry.db" if os.path.isdir("/app/data") else "data/telemetry.db",
)
FF_FORMULA_VERSION = os.environ.get("FF_FORMULA_VERSION", "volvibes_v1")
FF_SOURCE_SPEC_VERSION = _int_env("FF_SOURCE_SPEC_VERSION", 1)
FF_MIN_FORWARD_FACTOR = _float_env("FF_MIN_FORWARD_FACTOR", 0.20)
FF_FRONT_TARGET_DTE = _int_env("FF_FRONT_TARGET_DTE", 60)
FF_FRONT_DTE_MIN = _int_env("FF_FRONT_DTE_MIN", 50)
FF_FRONT_DTE_MAX = _int_env("FF_FRONT_DTE_MAX", 70)
FF_BACK_TARGET_DTE = _int_env("FF_BACK_TARGET_DTE", 90)
FF_BACK_DTE_MIN = _int_env("FF_BACK_DTE_MIN", 80)
FF_BACK_DTE_MAX = _int_env("FF_BACK_DTE_MAX", 105)
FF_MIN_EXPIRATION_GAP_DAYS = _int_env("FF_MIN_EXPIRATION_GAP_DAYS", 20)
FF_MAX_EXPIRATION_GAP_DAYS = _int_env("FF_MAX_EXPIRATION_GAP_DAYS", 50)
FF_EXPIRATION_PAIRS_PER_TICKER = _int_env("FF_EXPIRATION_PAIRS_PER_TICKER", 3)
FF_CHAIN_EXPIRATIONS_PER_TICKER = _int_env("FF_CHAIN_EXPIRATIONS_PER_TICKER", 6)
FF_MAX_CHAIN_TICKERS_PER_RUN = _int_env("FF_MAX_CHAIN_TICKERS_PER_RUN", 4)
FF_EARNINGS_LOOKAHEAD_DAYS = _int_env("FF_EARNINGS_LOOKAHEAD_DAYS", 120)
FF_TARGET_CALL_DELTA = _float_env("FF_TARGET_CALL_DELTA", 0.35)
FF_TARGET_PUT_DELTA = _float_env("FF_TARGET_PUT_DELTA", -0.35)
FF_DELTA_TOLERANCE = _float_env("FF_DELTA_TOLERANCE", 0.05)
FF_MIN_UNDERLYING_PRICE = _float_env("FF_MIN_UNDERLYING_PRICE", 10)
FF_MIN_AVERAGE_VOLUME = _int_env("FF_MIN_AVERAGE_VOLUME", 1000000)
FF_MIN_LEG_OPEN_INTEREST = _int_env("FF_MIN_LEG_OPEN_INTEREST", 50)
FF_MIN_LEG_VOLUME = _int_env("FF_MIN_LEG_VOLUME", 5)
FF_MAX_LEG_BID_ASK_PCT = _float_env("FF_MAX_LEG_BID_ASK_PCT", 20)
FF_MAX_PACKAGE_SLIPPAGE_PCT = _float_env("FF_MAX_PACKAGE_SLIPPAGE_PCT", 15)
FF_WARN_PACKAGE_SLIPPAGE_PCT = _float_env("FF_WARN_PACKAGE_SLIPPAGE_PCT", 10)
FF_REQUIRE_NONZERO_SHORT_BID = _bool_env("FF_REQUIRE_NONZERO_SHORT_BID", True)
FF_REQUIRE_VALID_LONG_ASK = _bool_env("FF_REQUIRE_VALID_LONG_ASK", True)
FF_ALLOW_DIAGNOSTIC_STRUCTURE_WITHOUT_SOURCE_IV = _bool_env("FF_ALLOW_DIAGNOSTIC_STRUCTURE_WITHOUT_SOURCE_IV", True)
FF_MAX_DEBIT_DOLLARS = _float_env("FF_MAX_DEBIT_DOLLARS", 500)
FF_WARN_DEBIT_DOLLARS = _float_env("FF_WARN_DEBIT_DOLLARS", 250)
FF_MAX_ACCOUNT_RISK_PCT = _float_env("FF_MAX_ACCOUNT_RISK_PCT", 2.0)
FF_DRY_RUN_MAX_ACCOUNT_RISK_PCT = _float_env("FF_DRY_RUN_MAX_ACCOUNT_RISK_PCT", 1.0)
FF_MAX_TICKERS_PER_RUN = _int_env("FF_MAX_TICKERS_PER_RUN", 10)
FF_DEV_MAX_TICKERS_PER_RUN = _int_env("FF_DEV_MAX_TICKERS_PER_RUN", 3)
FF_DEV_MAX_CHAIN_TICKERS_PER_RUN = _int_env("FF_DEV_MAX_CHAIN_TICKERS_PER_RUN", 2)
FF_MAX_CANDIDATES_PER_TICKER = _int_env("FF_MAX_CANDIDATES_PER_TICKER", 3)
FF_CANDIDATE_DISCOVERY_POOL_SIZE = _int_env("FF_CANDIDATE_DISCOVERY_POOL_SIZE", 12)
FF_CANDIDATE_HISTORY_LOOKBACK_RUNS = _int_env("FF_CANDIDATE_HISTORY_LOOKBACK_RUNS", 10)
FF_SCAN_MODE = os.environ.get("FF_SCAN_MODE", "balanced").strip().lower()

# --- Calendar final verdict / hard-fail layer ---
CALENDAR_HARD_FAIL_MAX_LEG_SPREAD_PCT = _int_env("CALENDAR_HARD_FAIL_MAX_LEG_SPREAD_PCT", 25)
CALENDAR_HARD_FAIL_MIN_OPEN_INTEREST = _int_env("CALENDAR_HARD_FAIL_MIN_OPEN_INTEREST", 1)
CALENDAR_HARD_FAIL_MIN_VOLUME_IF_LOW_OI = _int_env("CALENDAR_HARD_FAIL_MIN_VOLUME_IF_LOW_OI", 1)
CALENDAR_HARD_FAIL_LOW_OI_THRESHOLD = _int_env("CALENDAR_HARD_FAIL_LOW_OI_THRESHOLD", 10)
CALENDAR_HARD_FAIL_BACK_IV_OVER_FRONT_IV_PCT = _int_env("CALENDAR_HARD_FAIL_BACK_IV_OVER_FRONT_IV_PCT", 5)
CALENDAR_REQUIRE_CONFIRMED_EARNINGS_TIMESTAMP_FOR_ENTRY = _bool_env("CALENDAR_REQUIRE_CONFIRMED_EARNINGS_TIMESTAMP_FOR_ENTRY", True)
CALENDAR_FINAL_VERDICT_USE_RANKING = _bool_env("CALENDAR_FINAL_VERDICT_USE_RANKING", True)
CALENDAR_ALLOWED_TRADE_TYPES = os.environ.get(
    "CALENDAR_ALLOWED_TRADE_TYPES",
    "true_earnings_iv_crush_calendar,pre_earnings_financing_or_directional_long_vol",
)
CALENDAR_TRUE_IV_FRONT_MAX_DAYS_AFTER_EVENT = _int_env("CALENDAR_TRUE_IV_FRONT_MAX_DAYS_AFTER_EVENT", 7)
CALENDAR_TRUE_IV_CRUSH_CAN_PASS = _bool_env("CALENDAR_TRUE_IV_CRUSH_CAN_PASS", True)
CALENDAR_PRE_EARNINGS_FINANCING_CAN_PASS = _bool_env("CALENDAR_PRE_EARNINGS_FINANCING_CAN_PASS", False)
CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS = _bool_env("CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS", False)
CALENDAR_DIAGNOSTIC_BACKTEST_ENABLED = _bool_env("CALENDAR_DIAGNOSTIC_BACKTEST_ENABLED", True)
CALENDAR_DIAGNOSTIC_BACKTEST_ALLOW_FAILED_CANDIDATES = _bool_env("CALENDAR_DIAGNOSTIC_BACKTEST_ALLOW_FAILED_CANDIDATES", True)
CALENDAR_DIAGNOSTIC_BACKTEST_SKIP_IF_UNTRADEABLE = _bool_env("CALENDAR_DIAGNOSTIC_BACKTEST_SKIP_IF_UNTRADEABLE", True)
CALENDAR_ACCOUNT_GUARDRAILS_ENABLED = _bool_env("CALENDAR_ACCOUNT_GUARDRAILS_ENABLED", True)
CALENDAR_MAX_DEBIT_DOLLARS = _int_env("CALENDAR_MAX_DEBIT_DOLLARS", 500)
CALENDAR_MAX_ACCOUNT_RISK_PCT = float(os.environ.get("CALENDAR_MAX_ACCOUNT_RISK_PCT", "3.0") or 3.0)
CALENDAR_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT = float(os.environ.get("CALENDAR_EXPERIMENTAL_MAX_ACCOUNT_RISK_PCT", "1.5") or 1.5)
CALENDAR_WARN_DEBIT_DOLLARS = _int_env("CALENDAR_WARN_DEBIT_DOLLARS", 250)
CALENDAR_ASSUME_MAX_LOSS_IS_DEBIT = _bool_env("CALENDAR_ASSUME_MAX_LOSS_IS_DEBIT", True)
# TKT-012: Tiered debit cap — higher underlying price allows larger debit %.
CALENDAR_DEBIT_CAP_TIER_1_MAX_PRICE = _float_env("CALENDAR_DEBIT_CAP_TIER_1_MAX_PRICE", 100.0)
CALENDAR_DEBIT_CAP_TIER_1_PCT = _float_env("CALENDAR_DEBIT_CAP_TIER_1_PCT", 0.08)
CALENDAR_DEBIT_CAP_TIER_2_MAX_PRICE = _float_env("CALENDAR_DEBIT_CAP_TIER_2_MAX_PRICE", 500.0)
CALENDAR_DEBIT_CAP_TIER_2_PCT = _float_env("CALENDAR_DEBIT_CAP_TIER_2_PCT", 0.10)
CALENDAR_DEBIT_CAP_TIER_3_PCT = _float_env("CALENDAR_DEBIT_CAP_TIER_3_PCT", 0.12)
# TKT-024: Override for account value when positions data unavailable.
_CALENDAR_AV_OVERRIDE = os.environ.get("CALENDAR_ACCOUNT_VALUE_OVERRIDE")
CALENDAR_ACCOUNT_VALUE_OVERRIDE: float | None = float(_CALENDAR_AV_OVERRIDE) if _CALENDAR_AV_OVERRIDE else None
CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT = _float_env("CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT", 0.02)
# TKT-027: Flag candidates where underlying drifted more than this since scan.
CALENDAR_PRICE_FRESHNESS_THRESHOLD = _float_env("CALENDAR_PRICE_FRESHNESS_THRESHOLD", 0.015)
DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS = _bool_env("DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS", True)
CALENDAR_LIFECYCLE_FETCH_UNDERLYING_QUOTES = _bool_env("CALENDAR_LIFECYCLE_FETCH_UNDERLYING_QUOTES", True)

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
# Optional separate token for read-only developer diagnostics. Falls back to
# RUN_TOKEN when unset.
DEV_API_TOKEN = os.environ.get("DEV_API_TOKEN")
ROBINHOOD_LOGIN_TIMEOUT_SECONDS = _int_env("ROBINHOOD_LOGIN_TIMEOUT_SECONDS", 150)
RUN_STALE_TIMEOUT_SECONDS = _int_env("RUN_STALE_TIMEOUT_SECONDS", 900)

# --- Optional notifications ---
# Used by the notification provider and Robinhood login failure alerts.
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")


# --- Development/testing controls ---
# APP_MODE can be set to "dev" in Railway while actively testing, or you can
# pass ?mode=dev to /run for one-off dev runs. Dev mode still fetches the full
# Robinhood portfolio, but limits external provider calls such as NewsAPI,
# Finnhub, and Tradier.
APP_MODE = os.environ.get("APP_MODE", "prod").strip().lower()
DEV_MAX_TICKERS = _int_env("DEV_MAX_TICKERS", 6)
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
WATCHLIST_NAME_ALIASES = {
    source.strip(): target.strip()
    for pair in os.environ.get("WATCHLIST_NAME_ALIASES", "My First List:List 01").split(",")
    if ":" in pair
    for source, target in [pair.split(":", 1)]
    if source.strip() and target.strip()
}
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
TINY_POSITION_VALUE_THRESHOLD = _float_env("TINY_POSITION_VALUE_THRESHOLD", 50)
TINY_POSITION_PORTFOLIO_PCT_THRESHOLD = _float_env("TINY_POSITION_PORTFOLIO_PCT_THRESHOLD", 0.5)

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
STOCK_MOMENTUM_MAX_EXTENSION_VS_50D_PCT = _float_env("STOCK_MOMENTUM_MAX_EXTENSION_VS_50D_PCT", 30)
STOCK_MOMENTUM_HIGH_VOLATILITY_30D_PCT = _float_env("STOCK_MOMENTUM_HIGH_VOLATILITY_30D_PCT", 80)
STOCK_MOMENTUM_EXTREME_VOLATILITY_30D_PCT = _float_env("STOCK_MOMENTUM_EXTREME_VOLATILITY_30D_PCT", 100)

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

# --- Moomoo broker integration ---
MOOMOO_OPEND_HOST = os.environ.get("MOOMOO_OPEND_HOST")
MOOMOO_OPEND_PORT = _int_env("MOOMOO_OPEND_PORT", 11111)

# --- Plaid broker integration ---
PLAID_CLIENT_ID = os.environ.get("PLAID_CLIENT_ID")
PLAID_SECRET = os.environ.get("PLAID_SECRET")
PLAID_ENV = os.environ.get("PLAID_ENV", "production")
PLAID_REFRESH_ON_EVERY_RUN = _bool_env("PLAID_REFRESH_ON_EVERY_RUN", True)

# --- 28A: User auth / multi-user foundation ---
ASA_ADMIN_USERNAME = os.environ.get("ASA_ADMIN_USERNAME", "jaia").strip()
ASA_ADMIN_PASSWORD = os.environ.get("ASA_ADMIN_PASSWORD", "").strip()
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "")
SESSION_EXPIRY_HOURS = _int_env("SESSION_EXPIRY_HOURS", 168)  # 7 days
ROBINHOOD_ENCRYPTION_KEY = os.environ.get("ROBINHOOD_ENCRYPTION_KEY", "")
USERS_DB_PATH = os.environ.get(
    "USERS_DB_PATH",
    os.path.join(DATA_DIR, "users.db"),
).strip()
# Legacy dev token bypass — set False to fully enforce user auth
LEGACY_DEV_TOKEN_ENABLED = _bool_env("LEGACY_DEV_TOKEN_ENABLED", True)

# --- 28B: Per-user personalization ---
# How long to wait for the Robinhood fetch lock before timing out (seconds).
RH_QUEUE_TIMEOUT_SECONDS = _int_env("RH_QUEUE_TIMEOUT_SECONDS", 120)
# Core run age threshold: warn user their personalization is built on stale signals.
CORE_RUN_STALE_THRESHOLD_HOURS = _float_env("CORE_RUN_STALE_THRESHOLD_HOURS", 4.0)

# --- 28C: Credential hardening ---
# Validate Robinhood credentials via live login attempt before storing.
BROKER_CREDENTIAL_VALIDATION_ENABLED = _bool_env("BROKER_CREDENTIAL_VALIDATION_ENABLED", True)
# Timeout for credential validation login attempt (seconds).
BROKER_VALIDATION_TIMEOUT_SECONDS = _int_env("BROKER_VALIDATION_TIMEOUT_SECONDS", 30)

# --- 28D: Run hardening ---
# Max user run records returned by /api/user/runs and admin history endpoints.
USER_RUN_HISTORY_LIMIT = _int_env("USER_RUN_HISTORY_LIMIT", 10)
# A running run older than this many seconds is treated as stale and ignored for dedup.
USER_RUN_STALE_RUNNING_SECONDS = _int_env("USER_RUN_STALE_RUNNING_SECONDS", 180)

# --- 28E: Admin complete + rate limiting ---
# Max personalization runs allowed per user per 60-minute rolling window.
USER_RUN_RATE_LIMIT_PER_HOUR = _int_env("USER_RUN_RATE_LIMIT_PER_HOUR", 3)
# Comma-separated username prefixes treated as test users in admin list view.
ADMIN_TEST_USER_PATTERNS = os.environ.get("ADMIN_TEST_USER_PATTERNS", "testuser,smoke,rh_test,rh28b")

# --- 29A: Sysadmin seed (TKT-036) ---
# Separate sysadmin account from member dev account (jaia).
ASA_SYSADMIN_USERNAME = os.environ.get("ASA_SYSADMIN_USERNAME", "asa_admin").strip()
ASA_SYSADMIN_PASSWORD = os.environ.get("ASA_SYSADMIN_PASSWORD", "").strip()

# --- 29A: Skew vertical exit signal thresholds (TKT-035) ---
SKEW_PROFIT_TARGET_PCT = _float_env("SKEW_PROFIT_TARGET_PCT", 50.0)   # % of max profit → EXIT_TARGET
SKEW_STOP_LOSS_PCT = _float_env("SKEW_STOP_LOSS_PCT", 50.0)           # % loss of debit → EXIT_STOP
SKEW_EXIT_DTE_THRESHOLD = _int_env("SKEW_EXIT_DTE_THRESHOLD", 5)      # DTE <= this → EXIT_EXPIRY
