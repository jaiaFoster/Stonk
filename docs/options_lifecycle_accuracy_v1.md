# Options Lifecycle Accuracy v1

This patch improves the read-only lifecycle engine for automatically detected calendar spreads.

## Why

Robinhood open-option detection now finds calendars automatically, but the first PDD lifecycle row exposed a 100x cost-basis display issue: a $1.72 entry debit could appear as $172.00. The app should never require manual trade input, so lifecycle accuracy has to come from broker-detected option legs and normalized broker cost basis.

## Adds

- Robinhood option average-price normalization with `ROBINHOOD_OPTION_AVG_PRICE_SCALE`.
- Entry debit calculation from broker leg average prices.
- Fallback entry debit calculation from broker total cost basis.
- Current spread debit, value, per-spread P/L, total P/L, and P/L percent.
- Target debit and stop debit based on lifecycle thresholds.
- Short-leg moneyness, distance to strike, and assignment-risk level.
- Short/long leg quote detail for debugging and lifecycle confidence.
- Pricing-quality warnings for missing/wide quote data or inferred sides.
- Daily Opportunity stock-add de-duplication by ticker, so momentum and sector-gap reasons begin merging into one candidate row.

## New optional environment variables

```text
ROBINHOOD_OPTION_AVG_PRICE_SCALE=auto
CALENDAR_LIFECYCLE_ASSIGNMENT_DTE=3
CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT=50
CALENDAR_LIFECYCLE_STOP_LOSS_PCT=-35
```

`ROBINHOOD_OPTION_AVG_PRICE_SCALE` accepts:

```text
auto
cents
dollars
```

Default `auto` treats unusually large option average prices, such as `172`, as cents and converts them to `1.72`.

## Expected result for PDD

The PDD calendar should remain automatically detected from Robinhood Investing. Its entry debit should show near the real per-spread debit, not a 100x inflated value. The lifecycle section should show richer P/L, target/stop, and assignment-risk context.
