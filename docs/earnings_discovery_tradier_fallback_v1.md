# Earnings Discovery + Tradier Market Fallback v1

This patch separates the two scan universes:

1. Watchlist tickers are reviewed primarily as normal stock candidates.
2. Earnings-calendar trades start from an independent upcoming-earnings universe.

The earnings trade scanner now:

- Pulls an upcoming earnings calendar window from the configured earnings provider.
- Defaults to events 2–4 days from the run date.
- Converts those events into a dedicated earnings-discovery ticker universe.
- Runs Tradier option-chain/calendar screening only on that universe.
- Runs Earnings Calendar Strategy v1 only on those earnings-driven candidates.

This prevents watchlist names from being treated as earnings-calendar candidates unless they also appear in the upcoming earnings-discovery window.

The patch also adds a Tradier historical market-data fallback. If Finnhub stock candles return 403 or no useful data, Market Data v1 tries Tradier historical quotes and fills the same normalized momentum/trend metric shape.

## New Railway variables

```text
MARKET_DATA_USE_TRADIER_FALLBACK=true
TRADIER_HISTORICAL_LOOKBACK_DAYS=460
TRADIER_HISTORICAL_INTERVAL=daily

EARNINGS_DISCOVERY_ENABLED=true
EARNINGS_DISCOVERY_START_DAYS=2
EARNINGS_DISCOVERY_END_DAYS=4
EARNINGS_DISCOVERY_MAX_EVENTS=25
EARNINGS_DISCOVERY_MAX_TICKERS_PER_RUN=6
```

Dev mode still limits discovery to `DEV_MAX_TICKERS`.

## Expected logs

```text
Market Data v1: Finnhub unavailable; using Tradier historical quotes fallback.
Fetching Tradier historical fallback metrics ...
Fetching Earnings Trade Discovery v1 universe ...
Earnings Trade Discovery v1 found X event(s): [...]
Calendar scanner universe from earnings discovery: [...]
Calendar Spread Screener v1 produced X earnings-discovery candidate(s)
```
