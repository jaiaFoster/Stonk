# Watchlist Stock Scoring v1

Small logic patch that makes watchlist items useful as normal stock candidates first.

## Changes

- Watchlist review is now **Watchlist Stock Candidate Review v2**.
- Watchlist tickers are scored primarily as stocks, even when there is no earnings event.
- Earnings/calendar logic is now an overlay, not the main category driver.
- Non-earnings watchlist items can now rank as:
  - `HIGH-PRIORITY STOCK WATCH`
  - `STOCK CANDIDATE / RESEARCH`
  - `STOCK WATCH / RESEARCH`
  - `ALREADY HELD / ADD-SIZE REVIEW`
- Calendar labels are reserved for actual earnings/calendar setups.
- If `WATCHLIST_NAMES` is stale or mistyped, the service falls back to scanning all discovered Robinhood watchlists instead of returning zero candidates.

## Recommended Railway settings

Leave `WATCHLIST_NAMES` blank or delete it. If it is stale, this patch now attempts an all-watchlists fallback.

For reliable fallback input:

```text
WATCHLIST_TICKERS=NVDA,AMZN,META,GOOGL,TSM,PLTR,AVGO,AMD,MSFT,ORCL
```

For trade-search testing:

```text
DEV_TICKERS=NVDA,AMZN,META,TSM,PLTR
DEV_MAX_TICKERS=5
TRADIER_MAX_TICKERS_PER_RUN=5
CALENDAR_MAX_TICKERS_PER_RUN=5
```

## Expected logs

```text
Running Watchlist Stock Candidate Review v2...
Watchlist Stock Candidate Review v2 produced X review(s), Y stock candidate(s), Z calendar/earnings setup(s), U urgent.
```
