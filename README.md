# Algo Stock Advisor

## Strategy 2 - Skew Momentum Vertical Spread

Strategy 2 is a standalone, read-only options scanner for momentum-confirmed
same-expiration debit verticals where the short wing is relatively rich. It
requires momentum, skew, liquidity, controlled debit, and asymmetric payoff.
It is separate from the earnings-calendar strategy and adds no manual trade
entry, tracking, or execution. WATCH rows are informational; only PASS rows can
enter Daily Opportunity. The dashboard includes a Strategy 2 KPI, candidate
section, recent cache history, and dedicated exports. Dev mode may cap scan
breadth. See `docs/skew_momentum_vertical_strategy_v1.md`.

## Latest patch: Pre-Strategy Dashboard Hygiene + Strategy Interface Prep

Calendar opportunities now use normalized, auditable display states such as
`PASSED_ENTRY_REVIEW`, `BLOCKED_PRECHECK`, `BLOCKED_NO_STRUCTURE`,
`BLOCKED_RANKING`, and `PROVIDER_LIMITED`. The Calendar Reliability section
shows the complete discovery-to-verdict funnel, recent cached opportunities,
recoverability hints, and explicit provider-safety scan limits.

The normalized opportunity shape is strategy-agnostic so a future strategy can
emit the same display contract without mixing its scoring logic into the
earnings-calendar engine. Strategy 2 is not implemented by this patch.

## Previous patch: Calendar Reliability + Opportunity Cache v1

Calendar candle/history requests now use configurable per-ticker provider fallback:

```text
MARKET_DATA_PROVIDER_ORDER=finnhub,tradier,alphavantage
MARKET_DATA_CANDLE_REQUIRED_BARS=240
```

Each result includes candle-quality metadata and provider attempts. Calendar mini-backtests require high/medium candle quality, while a candle-provider failure alone does not turn a valid calendar structure into a failed trade verdict.

Automatically discovered calendar candidates are upserted into a scanner-generated SQLite audit cache:

```text
CALENDAR_OPPORTUNITY_CACHE_ENABLED=true
CALENDAR_OPPORTUNITY_DB_PATH=/app/data/calendar_opportunities.sqlite3
```

This cache is not manual trade memory and does not place or track trades. The default earnings discovery horizon is now `+4..+21` days.

## Previous patch: Options Lifecycle Accuracy v1

This app intentionally avoids manual trade input. Active option trades should come from broker detection.

The lifecycle engine now normalizes Robinhood option average prices, estimates calendar entry debit from detected broker legs, calculates current spread value/P&L, and shows assignment-risk context for short front legs.

Optional environment variables:

```text
ROBINHOOD_OPTION_AVG_PRICE_SCALE=auto
CALENDAR_LIFECYCLE_ASSIGNMENT_DTE=3
CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT=50
CALENDAR_LIFECYCLE_STOP_LOSS_PCT=-35
```

`ROBINHOOD_OPTION_AVG_PRICE_SCALE=auto` protects against Robinhood returning option average prices as cents instead of dollars. For example, a raw value of `172` is treated as `$1.72`, preventing a 100x lifecycle P/L error.


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

### Shared Data Integration Completion

Patch 25D routes shared candle/quote/chain facts through `MarketDataHub`, fulfills approved strategy requirements before evaluation, and reuses normalized run/SQLite cache keys. Data Coverage shows cache hits, provider fetches, stale fallbacks, cap skips, failures, and duplicate fetches prevented.

Reports load from the latest successful persistent snapshot without triggering providers. Hard-failed strategy rows preserve signal quality but expose zero actionability. Forward Factor remains deferred until production validation completes. See `docs/shared_data_integration_completion_v1.md`.

Patch 25E restores one canonical shared-metrics shape across Holdings, Macro, Stock Momentum, Portfolio Gap, Potential Adds, and Risk Review. Actionable adds require complete trend/liquidity/freshness facts; incomplete rows remain informational. Requirement planning now consolidates overlapping requests before provider fulfillment, and same-run broad option chains satisfy narrower requests. See `docs/shared_metrics_requirement_correctness_v1.md`.

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
MARKET_DATA_PROVIDER_ORDER=finnhub,tradier,alphavantage
MARKET_DATA_CANDLE_REQUIRED_BARS=240
EARNINGS_PROVIDER_ORDER=finnhub,alphavantage
EARNINGS_MERGE_PROVIDER_EVENTS=true
REPORT_SHOW_CALENDAR_DEBUG_SECTIONS=false
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
EARNINGS_DISCOVERY_END_DAYS=21
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
WATCHLIST_NAME_ALIASES=My First List:List 01
WATCHLIST_TICKERS=
WATCHLIST_MAX_TICKERS_PER_RUN=20
WATCHLIST_PRIORITIZE_FOR_SCANS=true
WATCHLIST_INCLUDE_ALREADY_HELD=true
```

Leave `WATCHLIST_NAMES` blank to discover and scan all Robinhood watchlists. Current production list name is `List 01`; `WATCHLIST_NAME_ALIASES` can map an older configured name to it. Use `WATCHLIST_TICKERS` only as an optional fallback scan list.

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

### Manual trade memory

Manual trade memory is disabled and should stay disabled. Railway Volumes are not required for the current read-only workflow. The app should create value by automatically detecting positions and opportunities every time you view it, not by relying on manual state entry.

### Daily opportunity controls

```text
DAILY_OPPORTUNITY_ENGINE_ENABLED=true
DAILY_OPPORTUNITY_MAX_ACTIONS=12
DAILY_OPPORTUNITY_MIN_SCORE=55
DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS=true
```

### Calendar verdict cleanup controls

```text
CALENDAR_TRUE_IV_FRONT_MAX_DAYS_AFTER_EVENT=7
CALENDAR_PRE_EARNINGS_FINANCING_CAN_PASS=false
CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS=false
CALENDAR_LIFECYCLE_FETCH_UNDERLYING_QUOTES=true
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
/research/calendar-backtest?token=YOUR_RUN_TOKEN&ticker=AVGO&mode=diagnostic
/refresh-active-trades?token=YOUR_RUN_TOKEN
/config-check?token=YOUR_RUN_TOKEN
/health
```

Open the base Railway URL on your phone and paste your token once.

## Lifecycle Backlog Cleanup v2

This patch deepens active-calendar lifecycle checks before the larger UI overhaul.
It keeps the app read-only and automatic: no manual trade entry, no manual trade memory, and no trade execution.

Highlights:

- Active broker-detected calendars now receive better underlying-price enrichment.
- Robinhood stock-position prices can feed option moneyness when dev-mode Tradier coverage is narrow.
- Calendar lifecycle checks now include short-leg moneyness, distance to strike, ITM/OTM status, assignment risk, short-leg extrinsic value, and rough net Greeks when available.
- Daily Opportunity now includes urgent active-calendar alerts so an open calendar can appear above ordinary stock-add candidates.
- `railway.toml` sets a Gunicorn start command for Railway deployments.

Relevant variables:

```text
CALENDAR_LIFECYCLE_ASSIGNMENT_DTE=3
CALENDAR_LIFECYCLE_NEAR_MONEY_PCT=2
CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT=50
CALENDAR_LIFECYCLE_STOP_LOSS_PCT=-35
```

## Calendar Ranking + Earnings Mini-Backtest v1

This patch expands the earnings-calendar strategy layer before the major UI overhaul.

### What changed

- Earnings trade discovery uses the intended `+4..+21` calendar-day window.
- Earnings calendar expiration selection is now event-aware:
  - Prefer a short/front leg that expires before the earnings event.
  - Prefer a long/back leg that remains open after the earnings event.
  - For after-market-close earnings, same-day expiration can be treated as before the event, but it is timing-sensitive.
- Calendar Ranking v2 adds a strict criteria gate for discovered candidates:
  - confirmed earnings timestamp
  - event-capturing expiration placement
  - acceptable bid/ask spread
  - acceptable open interest
  - acceptable volume
  - acceptable debit size
  - acceptable IV relationship
  - preferred entry timing window
- Earnings Mini-Backtest v1 runs only for candidates that pass the full Calendar Ranking v2 gate.
- The mini-backtest is candle-based: it reviews historical underlying moves around prior earnings, not historical option P/L.

### Important defaults

```text
EARNINGS_DISCOVERY_START_DAYS=4
EARNINGS_DISCOVERY_END_DAYS=21
EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE=6
EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE=12
EARNINGS_CALENDAR_LATE_ENTRY_DTE=4
CALENDAR_EARNINGS_EVENT_AWARE_EXPIRATIONS=true
CALENDAR_EARNINGS_FRONT_MIN_DTE=1
CALENDAR_EARNINGS_FRONT_MAX_DTE=14
CALENDAR_EARNINGS_BACK_MIN_DTE_AFTER_EVENT=14
CALENDAR_EARNINGS_BACK_MAX_DTE=75
CALENDAR_BACKTEST_ENABLED=true
CALENDAR_BACKTEST_MAX_CANDIDATES=3
CALENDAR_BACKTEST_MAX_EVENTS=10
CALENDAR_BACKTEST_LOOKBACK_DAYS=900
CALENDAR_BACKTEST_ENTRY_DAYS_BEFORE=7
CALENDAR_BACKTEST_EXIT_DAYS_AFTER=1
```

### Backtest rule

The app intentionally skips the mini-backtest unless the calendar candidate passes all core criteria. This prevents the expensive historical review from running on junk, illiquid, late, or structurally invalid calendars.

### Calendar Verdict + Hold-Through + Research Tools v1

- Final calendar verdicts now override raw scanner labels before a candidate can appear as a possible entry.
- Hard-fail checks block untradeable spreads, zero open interest, no live liquidity, inverted IV edge, unconfirmed earnings timestamps, and oversized debit risk.
- Calendar rows include explicit trade type, main blocker/reason, account risk status, raw scanner verdict, and backtest status.
- Active broker-detected calendar lifecycle rows include hold-through score/action fields. Positive P/L alone is not enough to support holding through earnings.
- Diagnostic mini-backtest status can explain failed candidates without making them eligible.
- Stateless research route: `/research/calendar-backtest?token=...&ticker=AVGO&mode=diagnostic`.
- Details: `docs/calendar_verdict_hold_through_research_v1.md`.

### Calendar Verdict Cleanup v2

- Calendar trade-type classification is now earnings-session aware.
- Failed ranking checks show correct threshold wording, such as `54.5% > 15% limit`.
- Active calendar lifecycle can enrich underlying quotes for option-only positions before moneyness/assignment-risk checks.
- Daily Opportunity prioritizes active calendars above stock-add ideas.
- Details: `docs/calendar_verdict_cleanup_v2.md`.

### UI Overhaul v1: Muted Black Terminal

- The production HTML report now uses the muted black terminal decision-dashboard hierarchy.
- Active broker-detected calendar lifecycle cards appear before holdings and potential stock adds.
- Holdings and all stock-add candidates are consolidated into compact mobile-friendly sections.
- Failed/watch-only calendar setups are moved lower and shown as blocked candidates.
- Provider/raw tables, full payload, and run log remain available in collapsed Monitor / Debug details.
- Details: `docs/ui_overhaul_muted_black_terminal_v1.md`.

### Open Items Cleanup Patch v1

- Added purpose-specific copy/download exports for daily brief, calendar report, holdings report, potential adds, and full debug payload.
- Potential Adds now separates actionable adds from Watch / Research and Risk Review rows.
- Zero-value positions are hidden from main decision sections and top counts while remaining available in debug output.
- Provider chips distinguish Finnhub key presence from blocked candle access and show Tradier fallback/dev-limited status when detected.
- Active calendar cards use lifecycle field aliases and show deep-ITM close/roll review warnings.
- Added token-protected `/refresh-active-trades` for lightweight broker-detected open-options repricing without broad scans.
- Details: `docs/open_items_cleanup_patch_v1.md`.

### Mega Patch Cleanup v1: Dashboard Hygiene Before Strategy 2

- Tightened Potential Adds membership so avoid/reduce/fail/risk-sourced rows cannot lead actionable add ideas.
- Added extra zero-value recommendation filtering for main dashboard sections.
- Added provider and macro scope caveats for dev-limited/fallback-limited market data.
- Improved Active Calendar zero-state copy and retained the Refresh Active Trades affordance.
- Added fallback ticker disclosure for expandable portfolio/macro buckets.
- Monitor payload copy now uses the same toast/fallback behavior as purpose-specific exports.
- Details: `docs/mega_patch_cleanup_v1_dashboard_hygiene.md`.

### Shared Market Data and Strategy Foundation

- `RunDataContext` reuses facts inside one run.
- `MarketDataHub` checks run cache, SQLite freshness, then providers under one request budget.
- SQLite stores reusable facts, provenance, TTLs, provider errors, coverage, completed report snapshots, and generic strategy opportunity history.
- Shared daily-candle metrics include momentum, SMA, volume, realized volatility, and relative strength.
- Missing provider data, stale cache, provider-budget skips, and dev-cap skips remain distinct from weak strategy signals.
- Calendar, Skew Momentum Vertical, and Stock Momentum publish requirements and normalized results through an explicit local strategy registry.
- Opening `/?token=...` loads the latest successful report snapshot without provider calls.
- Details: `docs/shared_market_data_foundation_v1.md` and `docs/strategy_registry_foundation_v1.md`.

### Shared Metrics Correctness v1

- Canonical shared metrics restore current price, momentum, SMA trend, liquidity, volatility, QQQ relative strength, provenance, freshness, and explicit data state to all decision surfaces.
- Missing or incomplete facts cannot become actionable stock-add rows.
- Strategy requirements are collected and merged before shared provider fulfillment.
- Normal dashboard GET loads latest successful snapshot with zero provider calls.
- Details: `docs/shared_metrics_requirement_correctness_v1.md`.

### Strategy 3: Forward Factor Calendar v1

- Dry-run-only scanner for source-defined implied forward volatility opportunities.
- Uses time-weighted forward variance, source-correct ex-earnings IV, approximate 60/90 DTE pairs, and matched-strike ±35-delta double calendars.
- Raw IV cannot produce PASS. Missing ex-earnings IV is shown as a hard failure, not silently substituted.
- FF rows write to the generic opportunity registry and appear in top summary, dashboard section, exports, and Monitor.
- Exact source entry/exit/backtest rules remain `SOURCE_UNSPECIFIED` until the complete transcript and screener package are supplied.
- Details: `docs/forward_factor_strategy_v1.md`.

Patch 26B fixes false FF `DATA STALE` rows by validating FF-specific shared facts instead of stock-momentum trend completeness. It stages cheap eligibility before bounded multi-expiration chains, exposes raw-IV diagnostics separately from source-qualified ex-earnings FF, and keeps all FF rows dry-run/non-actionable.

Patch 26C separates observed `average_volume_30d` from its configured threshold, prioritizes FF dev candidates with known price/volume prerequisites, and collapses dev-cap rows in the dashboard. Robinhood position refreshes now preserve per-account latest-known-good snapshots during 502/503/auth outages. Runs using stale broker fallback are saved as degraded diagnostics and do not replace the latest complete canonical report.

Important FF defaults:

```text
FORWARD_FACTOR_STRATEGY_ENABLED=true
FORWARD_FACTOR_DRY_RUN=true
FF_CHAIN_EXPIRATIONS_PER_TICKER=6
FF_DEV_MAX_TICKERS_PER_RUN=3
FF_DEV_MAX_CHAIN_TICKERS_PER_RUN=2
FF_MIN_FORWARD_FACTOR=0.20
FF_REQUIRE_NONZERO_SHORT_BID=true
FF_REQUIRE_VALID_LONG_ASK=true
```

### Railway start command

The app uses `start.sh` via `railway.toml` so Railway expands `$PORT` safely at runtime.
