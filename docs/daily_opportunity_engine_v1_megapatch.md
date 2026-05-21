# Daily Opportunity Engine v1 Mega Patch

This patch combines three roadmap items into one deploy:

1. Earnings Discovery Quality Filter v1
2. Stock Momentum Add Strategy v1
3. Daily Opportunity Engine v1

It builds on the existing Unified Calendar Trade Engine and Portfolio Gap / Sector Suggestions modules.

## 1. Earnings Discovery Quality Filter v1

Old behavior in dev mode capped the raw earnings-discovery universe too early. This could produce only the first two low-quality tickers from the provider calendar, then waste the calendar scan on names with no useful option chains.

New behavior:

- Fetch a broader raw earnings list from Finnhub + Alpha Vantage.
- Deduplicate and normalize events.
- In dev mode, still fetch a broader raw list, but limit expensive Tradier optionability checks.
- Pre-check Tradier quote availability, underlying price, liquidity, and expiration-pair availability.
- Only send names that pass precheck into the full calendar-spread scanner.
- Preserve rejected names with reasons for the Unified Calendar Trade Engine.

Useful variables:

```text
EARNINGS_DISCOVERY_RAW_EVENT_LIMIT=100
EARNINGS_DISCOVERY_DEV_RAW_EVENT_LIMIT=50
EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK=12
EARNINGS_DISCOVERY_DEV_MAX_OPTIONABLE_TO_CHECK=6
EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES=6
EARNINGS_DISCOVERY_MIN_UNDERLYING_PRICE=5
EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME=500000
```

## 2. Stock Momentum Add Strategy v1

This is the second real strategy layer after earnings calendars. It is for normal stock adds, especially from current holdings and watchlist names.

It scores:

- 3M / 6M / 12M returns
- relative strength vs benchmark
- 50D / 200D trend state
- distance from 52-week high
- allocation sizing
- macro-priority buckets
- portfolio-gap support
- recent news visibility
- speculative/high-beta risk

Outputs include:

- CONSIDER ADDING
- ADD ON PULLBACK
- WATCH / CONFIRM TREND
- HOLD / DO NOT ADD
- AVOID ADDING

Useful variables:

```text
STOCK_MOMENTUM_STRATEGY_ENABLED=true
STOCK_MOMENTUM_MAX_CANDIDATES=12
STOCK_MOMENTUM_MIN_SCORE_TO_CONSIDER=62
STOCK_MOMENTUM_PULLBACK_FROM_HIGH_PCT=8
STOCK_MOMENTUM_OVEREXTENDED_FROM_HIGH_PCT=2
STOCK_MOMENTUM_MAX_SINGLE_NAME_ALLOCATION_PCT=15
STOCK_MOMENTUM_WATCHLIST_MARKET_DATA_MAX=6
```

## 3. Daily Opportunity Engine v1

This creates one ranked daily action list across strategies.

It combines:

- Unified Calendar Trade Engine entries / failures / open-trade lifecycle actions
- Stock Momentum Add Strategy candidates
- Portfolio Gap / Sector Suggestions
- Portfolio scoring risk/avoid names

Useful variables:

```text
DAILY_OPPORTUNITY_ENGINE_ENABLED=true
DAILY_OPPORTUNITY_MAX_ACTIONS=12
DAILY_OPPORTUNITY_MIN_SCORE=55
```

## Expected logs

```text
EARNINGS_DISCOVERY_RAW_EVENT_LIMIT: 100
EARNINGS_DISCOVERY_MAX_OPTIONABLE_TO_CHECK: 12
EARNINGS_DISCOVERY_MAX_FINAL_CANDIDATES: 6
Fetching Earnings Trade Discovery v2 raw universe...
Earnings Discovery Quality Filter v1 checking ...
Earnings Discovery Quality Filter v1 produced ...
Running Stock Momentum Add Strategy v1...
Stock Momentum Add Strategy v1 produced ...
Running Daily Opportunity Engine v1...
Daily Opportunity Engine v1 produced ...
```

## Report changes

New visible sections:

- Daily Opportunity Engine v1
- Stock Momentum Add Strategy v1

Existing visible sections kept:

- Portfolio Advisor Scores
- Market Momentum / Trend
- Positions
- Watchlist Stock Candidate Review
- Portfolio Gap / Sector Suggestions
- Unified Calendar Trade Engine
- Relevant News

The old lower-level calendar debug sections remain hidden by default through:

```text
REPORT_SHOW_CALENDAR_DEBUG_SECTIONS=false
```
