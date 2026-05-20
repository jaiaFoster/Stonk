# Future: Upcoming Earnings Trade Discovery

The true value target is an earnings-calendar scanner that starts from upcoming earnings events, not only from current portfolio/watchlist tickers.

## Intended flow

1. Pull upcoming earnings events for a date window.
2. Filter to optionable, liquid tickers.
3. Fetch Tradier expirations and chains.
4. Find calendars where the earnings event falls in the preferred relationship to the front/back expirations.
5. Score liquidity, bid/ask width, IV edge, debit, DTE, and earnings timing.
6. Rank candidates for same-day or next-day entry review.

## Future variables

```text
EARNINGS_DISCOVERY_ENABLED=true
EARNINGS_DISCOVERY_START_DAYS=0
EARNINGS_DISCOVERY_END_DAYS=10
EARNINGS_DISCOVERY_MAX_EVENTS=50
EARNINGS_DISCOVERY_MIN_AVG_VOLUME=1000000
EARNINGS_DISCOVERY_REQUIRE_OPTIONS=true
```

## Why not fully active yet

A full discovery scan can produce many API calls. It should be added after the watchlist candidate layer is stable and after we decide the best source for broad upcoming earnings events.
