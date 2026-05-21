# Watchlist Robustness Fix v1

This patch improves Robinhood watchlist ingestion without changing the rest of the strategy pipeline.

## Changes

- `WATCHLIST_NAMES` now defaults to blank. Blank means: discover and scan all Robinhood watchlists.
- Supports Robinhood/watchlist payloads shaped as strings, dict rows, nested rows, direct instrument lists, and instrument URLs.
- Logs discovered Robinhood watchlist names.
- Logs ticker count for each fetched watchlist.
- Always merges manual `WATCHLIST_TICKERS` when present, even if `WATCHLIST_SOURCE` is set to only `robinhood`.
- Adds clearer no-data warnings.

## Recommended Railway variables

For automatic discovery:

```text
WATCHLIST_ENABLED=true
WATCHLIST_SOURCE=robinhood,manual
WATCHLIST_NAMES=
WATCHLIST_MAX_TICKERS_PER_RUN=20
```

For a reliable fallback while testing:

```text
WATCHLIST_TICKERS=NVDA,AMZN,META,GOOGL,TSM,PLTR,AVGO,AMD,MSFT,ORCL
```

For broader dev scans:

```text
DEV_TICKERS=NVDA,AMZN,META,TSM,PLTR
DEV_MAX_TICKERS=5
TRADIER_MAX_TICKERS_PER_RUN=5
CALENDAR_MAX_TICKERS_PER_RUN=5
EARNINGS_MAX_TICKERS_PER_RUN=20
```

## Expected log lines

```text
Watchlist Robinhood mode: discovering and scanning all watchlists.
Robinhood watchlist names found: ...
Robinhood watchlist '<name>': X ticker(s)
Watchlist Candidate Pipeline v1 produced X candidate(s), Y new, Z already held.
```
