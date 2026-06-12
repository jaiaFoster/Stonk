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

All `FF_*` DTE windows, delta tolerance, liquidity thresholds, required nonzero short bids, required valid long asks, package-slippage limit, ticker caps, and debit limits are engineering defaults. They are configurable and are not source performance claims.

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

## Patch 26B completion behavior

- FF-specific eligibility validates normalized quote, 240 daily candles, average volume, freshness, and exact missing fields. It no longer depends on Stock Momentum's 3M/200D confirmation gate.
- Cheap facts are evaluated before expensive multi-expiration chain requests.
- Dev mode evaluates at most three cheap candidates and requests chains for at most two survivors.
- `MarketDataHub.get_options_chain_set()` returns a shared normalized multi-expiration record and reuses broader cached chain coverage.
- Every eligible pair records source FF inputs when explicit ex-earnings IV exists. When it does not, raw-IV forward factor is retained under `diagnostic_raw_iv_forward_factor` only and verdict remains `FAIL / EX-EARNINGS IV UNAVAILABLE`.
- Pair audit, liquidity checks, leg identifiers, model-estimate scenario grid, stage counts, readiness, and provider/freshness facts remain auditable.

The current selected ex-earnings method is **explicit source/provider field only**. No event-variance removal formula can be source-aligned until the missing authoritative screener/transcript defines it.

## Formula audit

Every evaluated row retains front/back ex-earnings IV, DTE, time in years, forward variance, forward IV, FF, selected strikes/deltas, debit, liquidity, formula version, and source-spec version.

Scenario rows use a zero-rate Black-Scholes estimate for the remaining back-expiration options after front intrinsic value is removed. They are labeled `MODEL ESTIMATE — NOT GUARANTEED`; the app does not claim exact maximum profit.

## Shared-data behavior

FF declares requirements through Strategy Registry and uses MarketDataHub for quote, candles, earnings, derived liquidity facts, and a multi-expiration chain snapshot. It writes normalized rows to the generic strategy opportunity repository.

Patch 26C explicitly records observed price and `average_volume_30d`, their configured minimums, and pass/fail booleans. Threshold failures are distinct from missing data and unsupported securities. Dev selection prioritizes candidates whose cached/shared facts already clear known price and volume gates, while raw-IV diagnostic formula results remain separate from source-qualified FF.

## Patch 26D execution completion

- Cheap-stage approval reserves a bounded provider budget for the later FF chain-set request.
- `options_chain_set` is a distinct shared fact and cache identity. It preserves contracts by expiration; a short-dated single-expiration chain cannot satisfy FF.
- A broader fresh chain set may satisfy a narrower same-run or SQLite-backed request.
- Approved candidates request 50-105 DTE coverage, build valid 50-70 / 80-105 DTE pairs, and retain every formula input and intermediate result.
- Explicit source/provider ex-earnings IV remains required for a source-qualified result. Raw-IV Forward Factor is diagnostic only and produces no PASS.
- FF requests independent 120-day earnings context so a shorter general lookup cannot imply no event exists.
- Every raw-universe ticker receives exactly one PASS, WATCH, FAIL, or SKIPPED terminal row. Production caps use `SKIPPED / STRATEGY CAP`; crypto and unsupported assets are excluded before equity-options planning.
