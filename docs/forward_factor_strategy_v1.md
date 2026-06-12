# Forward Factor Calendar Strategy v1

## Source-stated rules

- Sell rich nearer-term volatility and buy farther-term volatility through a matched-strike put calendar plus matched-strike call calendar.
- Use annualized IV decimals and calendar-day expiration times.
- Calculate time-weighted implied forward variance, require it to be positive, then calculate implied forward IV.
- Forward Factor is `front_ex_earnings_iv / forward_iv - 1`.
- Source-reported threshold is `0.20`.
- Source-reported favorable pairing is approximately 60/90 DTE.
- Source-reported structure is approximately ±35-delta double calendar.
- Source-reported historical claims are approximately 27% CAGR and 2.4 Sharpe. They are not expected returns.
- Ex-earnings IV is required. Raw IV cannot produce PASS.

## Engineering defaults

All `FF_*` DTE windows, delta tolerance, liquidity thresholds, package-slippage limit, ticker caps, and debit limits are engineering defaults. They are configurable and are not source performance claims.

## Assumptions

- Near-expiration delta selects strikes.
- Back expiration must contain exact matching strikes.
- MarketDataHub option-chain payload may carry `expiration_metrics[expiration].ex_earnings_iv`.
- Dry-run remains permanently forced on in v1.

## Unresolved source ambiguities

- Exact ex-earnings IV adjustment method.
- Exact scan frequency, entry timing, persistence requirement, holding period, exit, roll, event exclusions, position sizing, and transaction-cost model.
- Source screener examples and full transcript.

Because these are unresolved, lifecycle says `MANUAL REVIEW REQUIRED — SOURCE DOES NOT SPECIFY AUTOMATIC EXIT`, live actionability is zero, and backtest status is `BLOCKED / HISTORICAL OPTIONS DATA UNAVAILABLE`.

## Formula audit

Every evaluated row retains front/back ex-earnings IV, DTE, time in years, forward variance, forward IV, FF, selected strikes/deltas, debit, liquidity, formula version, and source-spec version.

## Shared-data behavior

FF declares requirements through Strategy Registry and uses MarketDataHub for quote, candles, earnings, derived liquidity facts, and a multi-expiration chain snapshot. It writes normalized rows to the generic strategy opportunity repository.
