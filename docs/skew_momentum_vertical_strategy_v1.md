# Strategy 2 - Skew Momentum Vertical Spread v1

## Thesis

Strategy 2 is a standalone read-only options scanner. It buys fair or cheap
near-the-money directional volatility and sells a farther out-of-the-money wing
only when that wing is relatively rich. A setup requires the combination of
directional momentum, favorable skew, liquid quotes, controlled debit, defined
risk, and asymmetric payoff.

It is independent from the Earnings Long Call Calendar strategy and is not an
earnings-specific or generic debit-spread screener.

## Structures

- Bullish: buy an ATM/slightly OTM call and sell a farther OTM call at the same expiration.
- Bearish: buy an ATM/slightly OTM put and sell a farther OTM put at the same expiration.

The scanner uses conservative debit (`long ask - short bid`) for risk gates and
mid debit for display/ranking context.

## Data And Universe

The capped universe combines current holdings, Robinhood watchlists, portfolio
gap candidates, and names with market metrics. Momentum uses 3M/6M/12M trend,
50D/200D status, and relative strength. Tradier supplies quotes, expirations,
option chains, IV, volume, open interest, and Greeks when available.

Dev mode uses `SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN`; production uses
`SKEW_VERTICAL_MAX_TICKERS_PER_RUN`.

## Ranking And Verdicts

The transparent 100-point rank is:

- Momentum: 25
- Skew richness: 25
- Payoff quality: 20
- Liquidity: 15
- Timing/DTE: 10
- Data quality: 5

Fatal liquidity, debit, reward/risk, or data-quality failures override score.
Only `PASS` rows enter Daily Opportunity. Watch and fail rows remain visible in
the Strategy 2 dashboard section with a blocker and next action.

High scores do not override fatal requirements. A high-score WATCH row can have
strong momentum and payoff quality while still being non-actionable because the
short wing is not rich enough, liquidity is weak, or direction is unconfirmed.

## Dashboard And Exports

The top `SKEW` KPI shows current-run PASS/WATCH/FAIL counts without implying
WATCH rows are actionable. The Strategy 2 section shows row, pass, watch, fail,
and scanned-ticker counts, plus the runtime cap and recent scanner-generated
cache history.

`Copy Skew Verticals Report` exports Strategy 2 decisions even when PASS is
zero. `Copy Options Strategies Report` combines active options lifecycle,
calendar strategy, Strategy 2, blocked/watch rows, and provider caveats. Daily
Brief always summarizes Strategy 2; Daily Opportunity still receives PASS rows
only.

Dev mode intentionally narrows scan breadth. The dashboard and exports disclose
the configured max, runtime cap, and tickers scanned so a narrow run is not
mistaken for full-universe coverage.

## Risk And Product Guardrails

- No order placement.
- No manual trade entry or tracking.
- No broken provider data can create a PASS row.
- Risk is defined by conservative debit.
- Nearby earnings are flagged as event risk and do not pass by default.
- Active-position lifecycle inference is intentionally deferred until it can be
  implemented from broker-detected legs without ambiguity.
- Automatically discovered Strategy 2 rows are retained in a scanner-generated
  SQLite audit cache. This is not manual trade tracking.

## Configuration

Configuration is exposed through `/config-check`. Important controls include
the `SKEW_VERTICAL_*` ticker caps, DTE range, momentum thresholds, skew/financing
thresholds, liquidity gates, debit limits, and reward/risk minimums.
