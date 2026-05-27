# Algo Stock Advisor

A modular personal portfolio and trade-opportunity assistant.

Algo Stock Advisor gathers brokerage positions, watchlists, news, earnings calendars, historical market data, options chains, and strategy outputs so a daily report can recommend practical next actions. The current product is focused on aggressive growth stock review, earnings-calendar spread discovery, watchlist triage, portfolio gap analysis, and open calendar-spread lifecycle checks.

> This is personal decision-support software. It does not place trades. Treat every output as research, not financial advice.

---

## What the app does today

The app currently supports:

- Robinhood position ingestion through `robin_stocks`
- Roth IRA and rollover IRA stock positions
- Robinhood crypto positions
- Robinhood watchlist discovery with manual fallback tickers
- NewsAPI relevance-scored ticker headlines
- Finnhub earnings timestamps and earnings discovery
- Alpha Vantage earnings-calendar fallback / secondary source
- Tradier quotes, historical fallback market data, expirations, option chains, Greeks, IV, volume, and open interest
- Tradier account-position parsing for open option legs and simple calendar detection
- Portfolio scoring for aggressive quality/momentum positioning
- Portfolio gap / sector-theme suggestions
- Stock Momentum Add Strategy v1
- Earnings-calendar spread discovery and screening
- Unified Calendar Trade Engine v1
- Calendar lifecycle checks for detected open Tradier calendars
- Automatic Robinhood + Tradier open-options detection for active calendar lifecycle checks
- Daily Opportunity Engine v1
- Async `/run` endpoint with loading screen and phone-approval messaging
- Redacted `/config-check` endpoint for deployment debugging

---

## Main workflow

1. Visit:

```text
https://your-railway-app/run?token=YOUR_RUN_TOKEN
```

2. The app starts a background run and shows a loading screen.
3. Approve Robinhood login on your phone if prompted.
4. The app fetches portfolio, watchlist, news, market, earnings, Tradier options, and account-position data.
5. The report renders with the highest-level decision sections first.

For API-safe testing:

```text
https://your-railway-app/run?token=YOUR_RUN_TOKEN&mode=dev
```

Dev mode still fetches the full Robinhood portfolio, but limits external provider calls.

---

## Key report sections

### Daily Opportunity Engine v1

The top-level action list. It combines:

- earnings calendar spread candidates
- stock momentum add ideas
- portfolio gap suggestions
- risk-review names

### Automatic Active Calendar Detection

Manual trade tracking is intentionally out of scope. The app is a read-only viewing/discovery tool: open calendars should be detected from broker option positions. Robinhood options and Tradier options are normalized into common option legs, grouped into calendar spreads, repriced when possible, and evaluated by the lifecycle checker. The Robinhood detector now scans the default taxable brokerage account shown in Robinhood as “Investing” in addition to configured IRA accounts, because options calendars are commonly held there.

### Pipeline Status

A structured integrity check showing which modules completed before the report was formatted.

### Portfolio Advisor Scores

Aggressive quality/momentum stock review for current holdings.

### Stock Momentum Add Strategy v1

Normal-stock strategy for portfolio and watchlist names. It uses available market trend/momentum data to classify names as consider adding, add on pullback, watch, or avoid.

### Watchlist Stock Candidate Review v2

Robinhood watchlist tickers scored as normal stock ideas, not as calendar trades. Optional `WATCHLIST_TICKERS` remains only as a fallback scan list, not a trade-entry workflow.

### Portfolio Gap / Sector Suggestions v1

Rule-based sector/theme exposure. ETFs such as SOXL count as both sector exposure and leveraged/speculative risk. Crypto is tracked as its own risk bucket.

### Unified Calendar Trade Engine v1

One calendar-trading workflow:

```text
find upcoming earnings
→ filter for optionable/tradable names
→ scan possible calendars
→ state pass/fail requirements
→ show proposed spread when valid
→ score/rank candidates
→ show open calendars
→ recommend lifecycle next actions
```

### Debug / Copyable Output

The full advisor payload and run log are collapsed by default to reduce clutter.

---

## Health and diagnostics

Health check:

```text
/health
```

Redacted configuration check:

```text
/config-check?token=YOUR_RUN_TOKEN
```

This returns JSON showing whether required keys are present, which modules are enabled, and whether obvious defaults/stale settings may be limiting results.

---

## Important Railway variables

### Required

```text
RUN_TOKEN=your_private_run_token
ROBINHOOD_USERNAME=...
ROBINHOOD_PASSWORD=...
TRADIER_ACCESS_TOKEN=...
```

### Strongly recommended

```text
NEWS_API_KEY=...
FINNHUB_API_KEY=...
ALPHA_VANTAGE_API_KEY=...
TRADIER_ENV=prod
MARKET_DATA_USE_TRADIER_FALLBACK=true
EARNINGS_PROVIDER_ORDER=finnhub,alphavantage
EARNINGS_MERGE_PROVIDER_EVENTS=true
REPORT_SHOW_CALENDAR_DEBUG_SECTIONS=false
TRADE_MEMORY_ENABLED=true
DATA_DIR=/app/data
TRADE_MEMORY_DB_PATH=/app/data/trade_memory.sqlite3
```

### Dev mode controls

```text
APP_MODE=prod
DEV_TICKERS=NVDA,AMZN
DEV_MAX_TICKERS=2
```

You can also run one request in dev mode with `?mode=dev`.

### Earnings discovery controls

```text
EARNINGS_DISCOVERY_START_DAYS=2
EARNINGS_DISCOVERY_END_DAYS=4
EARNINGS_DISCOVERY_RAW_EVENT_LIMIT=100
EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT=50
EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK=12
EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK=6
EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES=6
EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE=5
EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME=500000
```

### Tradier / options controls

```text
TRADIER_MAX_TICKERS_PER_RUN=2
TRADIER_INCLUDE_GREEKS=true
TRADIER_MIN_DAYS_TO_EXPIRATION=7
TRADIER_CHAIN_EXPIRATIONS_PER_TICKER=1
CALENDAR_MAX_TICKERS_PER_RUN=2
CALENDAR_OPTION_TYPE=call
CALENDAR_FRONT_MIN_DTE=7
CALENDAR_FRONT_MAX_DTE=21
CALENDAR_MIN_EXPIRATION_GAP_DAYS=14
CALENDAR_TARGET_EXPIRATION_GAP_DAYS=30
CALENDAR_BACK_MAX_DTE=70
CALENDAR_MIN_OPEN_INTEREST=50
CALENDAR_MIN_VOLUME=10
CALENDAR_MAX_LEG_SPREAD_PCT=15
CALENDAR_MAX_DEBIT_PCT_UNDERLYING=8
CALENDAR_MAX_ATM_DISTANCE_PCT=3
```

### Automatic Robinhood options detection

```text
OPEN_OPTIONS_DETECTOR_ENABLED=true
ROBINHOOD_OPTIONS_DETECTOR_ENABLED=true
ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT=true
ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL=Investing
ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS=
ROBINHOOD_OPTIONS_MAX_POSITIONS=50
ROBINHOOD_OPTIONS_INFER_CALENDARS=true
```

Leave `ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS` blank unless you intentionally want to restrict scanning. Blank/default now scans the default Robinhood options account, usually shown as `Investing`, plus the known IRA accounts.

### Watchlist controls

```text
WATCHLIST_ENABLED=true
WATCHLIST_SOURCE=robinhood,manual
WATCHLIST_NAMES=
WATCHLIST_TICKERS=
WATCHLIST_MAX_TICKERS_PER_RUN=20
WATCHLIST_PRIORITIZE_FOR_SCANS=true
WATCHLIST_INCLUDE_ALREADY_HELD=true
```

Leave `WATCHLIST_NAMES` blank to discover and scan all Robinhood watchlists. Use `WATCHLIST_TICKERS` only as an optional fallback scan list.

### Portfolio gap controls

```text
PORTFOLIO_GAP_ENABLED=true
PORTFOLIO_GAP_TARGET_PROFILE=aggressive_macro_growth
PORTFOLIO_GAP_MAX_SUGGESTIONS=10
PORTFOLIO_GAP_MIN_SUGGESTION_SCORE=55
PORTFOLIO_GAP_INCLUDE_ALREADY_HELD=true
```

Optional target overrides:

```text
PORTFOLIO_GAP_CORE_TARGETS=AI / Semiconductors:18,Mega-cap Tech / Cloud:18,Software / Fintech:12,Energy / Utilities / Infrastructure:12,Healthcare / Biotech:10,Industrials / Defense / Robotics:10,Financials:8,Consumer / Retail:7,International / ADR:5
PORTFOLIO_GAP_MACRO_WINNING_BUCKETS=AI / Semiconductors,Mega-cap Tech / Cloud,Energy / Utilities / Infrastructure,Industrials / Defense / Robotics,Healthcare / Biotech
PORTFOLIO_GAP_RISK_TARGETS=Crypto / Digital Assets:5,Speculative / High Beta:12,Leveraged ETFs:4,Single-Name Max:15
```

### Stock momentum controls

```text
STOCK_MOMENTUM_STRATEGY_ENABLED=true
STOCK_MOMENTUM_MAX_CANDIDATES=12
STOCK_MOMENTUM_MIN_SCORE_TO_CONSIDER=62
STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX=6
```

### Trade memory controls

```text
TRADE_MEMORY_ENABLED=true
DATA_DIR=/app/data
TRADE_MEMORY_DB_PATH=/app/data/trade_memory.sqlite3
TRADE_MEMORY_DEFAULT_PROFIT_TARGET_PCT=50
TRADE_MEMORY_DEFAULT_MAX_LOSS_PCT=-35
```

Railway Volumes are not required for the current read-only workflow. The app should create value by automatically detecting positions and opportunities every time you view it, not by relying on manual state entry.

### Daily opportunity controls

```text
DAILY_OPPORTUNITY_ENGINE_ENABLED=true
DAILY_OPPORTUNITY_MAX_ACTIONS=12
DAILY_OPPORTUNITY_MIN_SCORE=55
```

---

## Project structure

```text
stock-advisor/
├── main.py                         # Compatibility entrypoint for Railway/Gunicorn
├── config.py                       # Compatibility shim to app.config
├── app/
│   ├── main.py                     # Flask routes, async run lifecycle, config-check
│   ├── config.py                   # Environment configuration
│   ├── models/                     # Shared dataclasses/models
│   ├── providers/                  # External API adapters
│   ├── services/                   # Pipeline, reports, strategy services
│   ├── strategies/                 # Portfolio scoring strategies
│   └── utils/                      # Log redaction and small utilities
├── docs/                           # Patch and feature docs
├── requirements.txt
├── Procfile
└── Dockerfile
```

---

## Local development

Install dependencies:

```bash
pip install -r requirements.txt
```

Run locally:

```bash
python main.py
```

Open:

```text
http://localhost:5000/health
http://localhost:5000/config-check?token=YOUR_RUN_TOKEN
http://localhost:5000/run?token=YOUR_RUN_TOKEN&mode=dev
```

---

## Deployment note

The intended Railway/Gunicorn command is:

```bash
gunicorn main:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 300
```

If Railway logs show Flask’s development server warning, check whether Railway is overriding the start command.

---

## Roadmap

Near-term high-value items:

1. Add historical earnings mini-backtest: last 10 earnings moves, gap/fade behavior, post-earnings drift.
2. Improve automatic Robinhood options/calendar detection, including better side inference and exact P/L from broker cost basis when available.
3. Improve calendar ranking with historical move, IV crush, liquidity, and debit/risk scoring.
4. Add company profile/fundamental data for better watchlist and sector-gap scoring.
5. Expand UI polish: tabs, cards, badges, saved settings, and stronger auth.

## Mobile Friendly UI v1

This patch makes the app easier to use from the Railway production URL and on mobile screens.

### Added

- `/` root endpoint now renders a small endpoint menu.
- The menu stores `RUN_TOKEN` locally in the browser so you do not need to type endpoint URLs manually.
- Report pages now include a mobile viewport tag.
- The main report has quick navigation chips near the top.
- Tables become horizontally scrollable on narrow screens instead of breaking the page.
- The Pipeline Status table has moved down into the Debug / Copyable Output area.
- Loading pages are easier to read on mobile.

### Main URLs

```text
/
/run?token=YOUR_RUN_TOKEN&mode=dev
/trades?token=YOUR_RUN_TOKEN  # disabled legacy manual-entry route
/config-check?token=YOUR_RUN_TOKEN
/health
```

Open the base Railway URL on your phone and paste your token once.
