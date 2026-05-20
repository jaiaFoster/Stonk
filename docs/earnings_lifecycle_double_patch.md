# Earnings Timestamp Provider v1 + Calendar Lifecycle Check v1

This double patch combines the next two roadmap steps to reduce deploy cycles.

## Earnings Timestamp Provider v1

Adds read-only upcoming/recent earnings context for portfolio tickers.

Current provider:

- `EARNINGS_PROVIDER=finnhub`
- Uses existing `FINNHUB_API_KEY`
- Gracefully returns unavailable data if access is denied or no event is found

Optional Railway variables:

```text
EARNINGS_PROVIDER_ENABLED=true
EARNINGS_PROVIDER=finnhub
EARNINGS_LOOKAHEAD_DAYS=45
EARNINGS_LOOKBACK_DAYS=7
EARNINGS_MAX_TICKERS_PER_RUN=8
```

In dev mode, earnings fetches are limited by `DEV_TICKERS` / `DEV_MAX_TICKERS`.

## Calendar Lifecycle Check v1

Adds read-only lifecycle review for detected open calendars from Tradier-held option legs.

It evaluates:

- short-leg DTE
- long-leg DTE
- current estimated spread value
- entry debit estimate when broker cost basis is usable
- estimated P/L percentage when possible
- short-leg moneyness
- assignment / pin risk
- earnings date/session context when available

Optional Railway variables:

```text
CALENDAR_LIFECYCLE_ENABLED=true
CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT=50
CALENDAR_LIFECYCLE_MAX_LOSS_PCT=-35
CALENDAR_LIFECYCLE_URGENT_DTE=3
CALENDAR_LIFECYCLE_REVIEW_DTE=7
CALENDAR_LIFECYCLE_NEAR_MONEY_PCT=2
```

## Important limitations

This is still read-only. It does not place or close trades.

Exact lifecycle P/L is limited without persistence. If Tradier cost basis is unavailable or has inconsistent sign conventions, the app will show current spread value but not exact gain/loss. A later trade-memory module should store exact entry debit, target profit, and stop/review thresholds.

## Expected log lines

```text
Fetching Earnings Timestamp Provider v1...
Earnings Timestamp Provider v1 fetched X/Y event(s)
Running Calendar Lifecycle Check v1...
Calendar Lifecycle Check v1 produced N check(s), U urgent, E exit-review.
```
