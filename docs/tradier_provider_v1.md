# Tradier Provider v1

This patch adds Tradier as the dedicated options/market-data provider for the Algo Stock Advisor roadmap.

## Scope

Tradier Provider v1 is a connectivity and data-shape milestone. It does **not** place trades.

It fetches:

- stock quotes for selected equity tickers
- available option expirations
- one option-chain sample per selected ticker
- ATM call and ATM put summaries
- volume/open-interest totals for the sampled chain
- Greeks/IV when Tradier returns them

## Environment variables

Required:

```text
TRADIER_ACCESS_TOKEN=your_tradier_token
```

Optional:

```text
TRADIER_ENV=prod
TRADIER_MAX_TICKERS_PER_RUN=2
TRADIER_INCLUDE_GREEKS=true
TRADIER_MIN_DAYS_TO_EXPIRATION=7
TRADIER_CHAIN_EXPIRATIONS_PER_TICKER=1
```

Use `TRADIER_ENV=sandbox` only with a sandbox/paper token. Use `TRADIER_ENV=prod` with a live account token.

## Dev mode

The existing dev mode is respected:

```text
/run?token=YOUR_TOKEN&mode=dev
```

In dev mode:

- Robinhood still fetches the whole portfolio.
- NewsAPI is limited.
- Finnhub is limited.
- Tradier is limited to `DEV_MAX_TICKERS` from `DEV_TICKERS`.

Default dev tickers:

```text
DEV_TICKERS=NVDA,AMZN
DEV_MAX_TICKERS=2
```

## Expected logs

After deploy, a successful run should include:

```text
tradier imported OK
TRADIER_ACCESS_TOKEN set: True
TRADIER_ENV: prod
Fetching Tradier Provider v1...
Fetching Tradier Provider v1 for 2 equity ticker(s); env=prod; max_tickers=2
Tradier quotes fetched for 2/2 ticker(s)
Tradier NVDA: quote=yes, expirations=..., selected_expiration=..., contracts=...
Tradier Provider v1 fetched 2/2 ticker snapshot(s)
```

If token/env is wrong, the app should still complete and show a Tradier unavailable row.

## Why this comes before strategy logic

Calendar spreads require reliable option-chain structure before scoring can be meaningful. This patch proves the provider path and normalizes the basic fields needed later:

- underlying quote
- expiration dates
- strike ladder
- bid/ask/mid
- volume
- open interest
- IV/Greeks when available
- ATM call/put lookup

The next Tradier step should be a calendar-spread candidate finder using two expirations and near-ATM strikes.
