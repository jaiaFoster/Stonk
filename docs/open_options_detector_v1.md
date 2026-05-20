# Open Options Position Detector v1

Open Options Position Detector v1 is a read-only Tradier account-position parser.

It attempts to fetch open positions from Tradier and identify existing long calendar spreads from option legs.

## What it detects

A simple long calendar spread is detected when the account has option legs with:

```text
same underlying
same option type
same strike
different expirations
short front leg
long later-dated back leg
```

Example:

```text
-1 NVDA 2026-05-27 225C
+1 NVDA 2026-06-26 225C
```

Detected as:

```text
NVDA 225 call calendar
```

## Required setup

Set your Tradier token:

```text
TRADIER_ACCESS_TOKEN=...
```

Optionally set your account ID:

```text
TRADIER_ACCOUNT_ID=...
```

If `TRADIER_ACCOUNT_ID` is omitted, the detector tries to discover account IDs from Tradier's profile endpoint.

## Optional settings

```text
OPEN_OPTIONS_DETECTOR_ENABLED=true
OPEN_OPTIONS_QUOTE_LEGS=true
OPEN_OPTIONS_MAX_LEGS_TO_PRICE=20
OPEN_OPTIONS_MAX_ACCOUNTS=3
```

## Current limitations

- It only detects Tradier-held option positions.
- It cannot see Robinhood option positions yet.
- It estimates spread value from current option quotes when available.
- It does not yet know the original trade thesis or target profit unless cost/entry data is available.
- It does not place, close, or modify trades.

## Next step

The next module should add lifecycle scoring for detected calendars:

```text
current spread value vs entry debit
short-leg moneyness
front-leg DTE
profit target, e.g. 50%
assignment risk
exit / hold / roll recommendation
```
