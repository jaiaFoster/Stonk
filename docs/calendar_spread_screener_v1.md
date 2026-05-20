# Calendar Spread Screener v1

Calendar Spread Screener v1 is a read-only Tradier-based scanner for possible new long call calendar spreads.

It does not place trades, detect existing open spreads, or recommend exits yet.

## What it scans

For each selected equity ticker, the scanner looks for:

- same underlying
- call options by default
- same strike
- front expiration to sell
- later expiration to buy
- near-ATM common strike
- positive estimated net debit
- acceptable bid/ask spread
- minimum volume and open interest
- front/back IV relationship when Tradier provides Greeks/IV

## Default structure

```text
Short front call
Long back call
Same strike
Near ATM
```

## Key environment variables

```text
CALENDAR_SCANNER_ENABLED=true
CALENDAR_MAX_TICKERS_PER_RUN=2
CALENDAR_OPTION_TYPE=call
CALENDAR_FRONT_MIN_DTE=7
CALENDAR_FRONT_MAX_DTE=21
CALENDAR_MIN_EXPIRATION_GAP_DAYS=14
CALENDAR_TARGET_EXPIRATION_GAP_DAYS=30
CALENDAR_BACK_MAX_DTE=70
CALENDAR_MAX_EXPIRATION_PAIRS_PER_TICKER=1
CALENDAR_MAX_CANDIDATES_PER_TICKER=1
CALENDAR_MIN_OPEN_INTEREST=50
CALENDAR_MIN_VOLUME=10
CALENDAR_MAX_LEG_SPREAD_PCT=15
CALENDAR_MAX_DEBIT_PCT_UNDERLYING=8
CALENDAR_MAX_ATM_DISTANCE_PCT=3
```

## Dev mode

When running:

```text
/run?token=YOUR_TOKEN&mode=dev
```

The scanner uses the same dev ticker subset as NewsAPI, Finnhub, and Tradier snapshots.

Default dev tickers:

```text
NVDA,AMZN
```

## Current limitations

This v1 scanner does not yet know whether the user already holds the spread.

Open calendar detection requires broker options-position parsing. A calendar can be detected when account positions contain:

```text
same underlying
same option type
same strike
different expirations
one long leg
one short leg
```

Example:

```text
+1 NVDA 2026-06-26 225C
-1 NVDA 2026-05-27 225C
```

For exit recommendations, the app will later need:

- original net debit
- current spread value
- percentage gain/loss
- short-leg moneyness
- days to front expiration
- assignment risk
- earnings timestamp
- target profit, such as 50%

## Next likely module

After this scanner, the next useful module is either:

1. open options position detector, or
2. earnings timestamp provider.

The open-position detector is needed for cutting/exit logic. The earnings provider is needed to turn this into a true earnings calendar spread scanner.
