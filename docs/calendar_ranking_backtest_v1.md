# Calendar Ranking + Earnings Mini-Backtest v1

Goal: improve the earnings-calendar discovery/ranking system before the UI overhaul.

## Strategy change

The previous discovery window was too tight. It found names such as CRDO and HPE at roughly 4 DTE, but by then the scanner often selected post-earnings short expirations or treated the setup as late. This patch expands discovery to find setups earlier while still showing late candidates for review.

## New behavior

- Raw earnings discovery defaults to +4..+21 days.
- Quality filtering prioritizes the ideal entry window before spending Tradier optionability checks.
- Calendar spread scanning uses the attached earnings event to select expirations:
  - short/front expiration before the earnings event
  - long/back expiration after the earnings event
- Calendar Ranking v2 gives each candidate an explicit pass/fail gate.
- Earnings Mini-Backtest v1 runs only on fully-qualified candidates.

## Backtest scope

The mini-backtest is intentionally candle-based for now. It checks historical earnings behavior of the underlying stock:

- pre-event run-up from the configured entry window
- earnings gap
- event close move
- post-event exit move
- average/max absolute move

It does not yet simulate historical option chains, IV crush, or actual calendar-spread P/L.

## Key config

```text
EARNINGS_DISCOVERY_START_DAYS=4
EARNINGS_DISCOVERY_END_DAYS=21
EARNINGS_CALENDAR_IDEAL_ENTRY_MIN_DTE=6
EARNINGS_CALENDAR_IDEAL_ENTRY_MAX_DTE=12
CALENDAR_BACKTEST_ENABLED=true
```
