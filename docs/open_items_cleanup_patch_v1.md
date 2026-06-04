# Open Items Cleanup Patch v1

This patch tightens the report decision surface without changing strategy scoring or adding trade execution behavior.

## What Changed

- Added purpose-specific exports:
  - Copy Daily Brief
  - Copy Calendar Report
  - Copy Holdings Report
  - Copy Potential Adds
  - Download Full Debug Payload
- Added robust copy behavior with clipboard API, visible toast, fallback textarea, and text download support.
- Split Potential Adds into Actionable Adds and Watch / Research.
- Added a separate Risk Review section for avoid, reduce, cut, and existing-position risk controls.
- Added zero-value asset filtering for main dashboard holdings, potential adds, risk review, and top counts. Raw data remains available in Monitor / Debug and debug exports.
- Improved provider chips so Finnhub key presence is not shown as simple candle health when candle access is blocked.
- Improved market-data unavailable wording for dev-limited/fallback-limited runs.
- Fixed active-calendar lifecycle fallback binding from lifecycle `checks`.
- Added visible active-calendar aliases for current debit, entry debit, P/L, target/stop, underlying, short DTE, moneyness, assignment risk, hold-through details, and pricing warnings.
- Added deep-ITM short-leg warning labels for urgent active calendars.
- Improved blocked calendar candidate cards so final verdict, trade type, main blocker, guardrail, and backtest status lead.
- Added a token-protected `/refresh-active-trades` endpoint and report button.
- Made top summary counters clickable anchors.
- Made portfolio/macro exposure buckets expandable when bucket details are attached.

## Refresh Active Trades

`/refresh-active-trades?token=RUN_TOKEN` is read-only and limited to active trade repricing inputs:

- broker-detected open option positions
- active calendar grouping
- lifecycle repricing/action checks

It intentionally skips broad earnings discovery, news, watchlist scans, sector suggestions, stock momentum scans, and full portfolio scoring.

## Preserved Rules

- No order placement.
- No trade closing or rolling.
- No manual trade entry.
- No manual trade tracking.
- No manual position input.
- Active option/calendar trades still come from broker detection only.
