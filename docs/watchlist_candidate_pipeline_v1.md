# Watchlist Candidate Pipeline v1 + Display Polish

This patch combines the small pipeline/display polish cleanup with a new watchlist candidate layer.

## Goals

- Pull candidate tickers from Robinhood watchlists when possible.
- Fall back to manual `WATCHLIST_TICKERS` if Robinhood watchlist access fails or is unavailable.
- Add watchlist tickers to the external scan universe as a `Watchlist` category.
- Review watchlist tickers using existing data: earnings timestamps, Tradier options/calendar candidates, earnings-calendar strategy, news, and portfolio ownership.
- Keep everything read-only.

## New report section

`Watchlist Candidate Review v1`

This section classifies candidates as:

- `POTENTIAL EARNINGS CALENDAR`
- `URGENT EARNINGS REVIEW`
- `OPTIONS WATCH`
- `EARNINGS WATCH`
- `STOCK WATCH / RESEARCH`
- `ALREADY HELD / MONITOR`
- `WATCH ONLY / AVOID TRADE`

## Railway variables

Optional:

```text
WATCHLIST_ENABLED=true
WATCHLIST_SOURCE=robinhood,manual
WATCHLIST_NAMES=My First List
WATCHLIST_TICKERS=NVDA,AMZN,META
WATCHLIST_MAX_TICKERS_PER_RUN=20
WATCHLIST_PRIORITIZE_FOR_SCANS=true
WATCHLIST_INCLUDE_ALREADY_HELD=true
```

For testing a specific watchlist ticker in dev mode, include it in:

```text
DEV_TICKERS=NVDA,AMZN
DEV_MAX_TICKERS=2
```

For a real scan tomorrow, consider increasing:

```text
CALENDAR_MAX_TICKERS_PER_RUN=5
EARNINGS_MAX_TICKERS_PER_RUN=20
TRADIER_MAX_TICKERS_PER_RUN=5
NEWS_MAX_TICKERS_PER_RUN=8
```

## Notes

Robinhood watchlist access uses the unofficial `robin_stocks` account watchlist helpers. If those endpoints change, the app falls back to `WATCHLIST_TICKERS` and the run still completes.

This patch does not place trades and does not add persistence.
