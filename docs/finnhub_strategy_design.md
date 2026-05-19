# Finnhub Strategy Design Notes

## Goal

Use Finnhub data to move the app from “rate current holdings” to “diagnose portfolio gaps and suggest new stocks to research.”

This should not become automatic buying. It should produce a ranked research list with clear reasons, risks, and missing data.

## Best Finnhub Data Uses

### 1. Market metrics

Use price history to calculate:

- 1M / 3M / 6M / 12M returns
- relative strength vs QQQ or SPY
- 50-day and 200-day trend state
- distance from 52-week high/low
- volatility proxy
- volume/liquidity

This powers momentum/trend scoring.

### 2. Company profile

Use profile data to classify:

- sector
- industry
- country
- market cap
- exchange
- IPO date if useful

This powers sector coverage, concentration, and portfolio gap detection.

### 3. Basic financials

Use fundamental metrics to score:

- revenue growth
- gross margin
- operating margin
- net margin
- ROE / ROA / ROIC if available
- debt/equity
- current ratio
- valuation multiples

This powers quality/growth scoring.

### 4. Earnings and analyst data

Use estimates / earnings / recommendations later to score:

- earnings surprise
- upcoming earnings risk
- analyst trend changes
- EPS/revenue estimate revisions
- price target dispersion

This powers catalyst scoring.

### 5. Peer data

Use peers to find new-stock candidates near a holding.

Example:

- User owns NVDA.
- Pull NVDA peers.
- Score peers by momentum, trend, quality, and valuation sanity.
- Suggest the strongest peer candidates if the portfolio wants more semiconductor/AI exposure.

## Portfolio Gap Engine

The app should calculate current exposure by:

- sector
- industry
- account
- asset type
- mega-cap / mid-cap / speculative bucket
- tech theme bucket, where possible

Then it should flag:

- overexposed areas
- missing sectors
- duplicate exposure
- too much single-name risk
- not enough high-quality compounders
- too much speculative drawdown exposure

## New Stock Suggestion Engine

Candidate sources:

1. Peers of current winners
2. User-defined watchlist
3. Sector/theme lists
4. High relative-strength universe
5. Earnings/analyst upgrade screens

Initial candidate scoring:

| Factor | Weight |
|---|---:|
| Momentum / relative strength | 30 |
| Trend health | 20 |
| Quality / fundamentals | 20 |
| Portfolio fit / diversification | 15 |
| Catalyst / earnings / analyst trend | 10 |
| Liquidity sanity | 5 |

Output example:

```text
Candidate: AMD
Action: RESEARCH / WATCH
Score: 78
Portfolio fit: Adds semiconductor exposure, but overlaps NVDA.
Reasons:
- Strong 6M relative strength vs QQQ
- Above 200-day trend
- Peer of current AI/semi winner
Risks:
- High beta
- Existing tech concentration is already high
Next check:
- Compare valuation and earnings revisions before adding
```

## Important Guardrails

- Suggestions are research candidates, not trade instructions.
- Do not suggest adding to a sector that is already overconcentrated unless candidate quality is exceptional.
- Do not suggest low-liquidity names.
- Do not suggest stocks with broken trend unless explicitly flagged as speculative turnaround.
- Prefer adding to winners and high-quality leaders, not averaging down into weak names.

## Implementation Order

1. Stabilize provider errors and secret-safe logging.
2. Confirm which Finnhub endpoints the current plan can access.
3. Add company profile provider for sector/industry/market cap.
4. Add sector exposure table to the report.
5. Add watchlist file/config.
6. Add peer lookup for current top-scoring holdings.
7. Add candidate scoring model.
8. Add “New Stocks to Research” report section.
