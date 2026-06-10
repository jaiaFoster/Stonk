# Pre-Strategy Dashboard Hygiene + Strategy Interface Prep

This patch makes the current earnings-calendar pipeline explain its decisions
before another options strategy is added.

## Normalized Opportunity States

Calendar rows and scanner-generated cached rows expose:

- `display_state`
- `display_state_label`
- `display_tone`
- `primary_reason`
- `primary_blocker`
- `next_action`
- `recoverability_hint`

The allowed display states are:

`ACTIVE_OPEN`, `PASSED_ENTRY_REVIEW`, `WATCH_EARLY`, `WATCH_LATE`,
`BLOCKED_PRECHECK`, `BLOCKED_NO_STRUCTURE`, `BLOCKED_RANKING`,
`BLOCKED_FINAL_VERDICT`, `PROVIDER_LIMITED`, `CACHED_RECENT`, and
`UNKNOWN_REVIEW`.

Rows also expose a lightweight strategy-agnostic `opportunity` object. This is
interface preparation only; Strategy 2 is not implemented.

## Coverage Honesty

Calendar Reliability shows raw earnings events, optionability checks, precheck
passes, scanner candidates, ranking passes, final pass/watch/fail counts, cache
writes, and candle-rescue success.

The dashboard also discloses current provider-safety limits including ticker,
expiration, pair, and per-ticker candidate caps. A broad earnings discovery
window therefore cannot be mistaken for broad full-chain scanning.

## Product Guardrails

- Read-only behavior is preserved.
- The opportunity cache remains scanner-generated audit history.
- No manual trade entry, position tracking, journal, or order placement exists.
- Broker-detected positions remain the only source for active lifecycle rows.
