# UI Overhaul v1: Muted Black Terminal

This patch changes the production HTML report presentation only. The scoring engines, calendar verdict logic, broker-detected active calendar lifecycle data, and stock-add strategy behavior remain unchanged.

## Production Report Hierarchy

The report now renders as a compact mobile-friendly decision dashboard:

1. Compact persistent top summary
2. Macro context strip
3. Active Calendar Lifecycle
4. Holdings / Portfolio Advisor
5. Unified Potential Adds
6. Calendar Candidates / Blocked Setups
7. Portfolio + Macro Infographic
8. Monitor / Debug

Active calendars are displayed before holdings and new ideas because they are time-sensitive. Holdings appear before potential stock adds. Failed or watch-only calendar candidates are lower on the page and visually treated as informational, not actionable.

## Visual Direction

The selected direction is the muted black terminal preview:

- true black background
- slate panels
- blue-gray accents
- green only for positive financial/pass states
- amber for watch/review states
- red for fail/risk states
- compact terminal-inspired type without neon styling

## Data Contract

The UI uses normalized/final fields from existing services. Active calendar rows come from broker-detected open calendars in the unified calendar engine, with lifecycle-check fallback. The patch does not add manual trade entry, manual trade tracking, manual position entry, order placement, trade modification, or trade closing.

Collapsed active-calendar cards show ticker, action/verdict, P/L, short DTE, moneyness, assignment risk, and next check. Expanded details show structure, current debit, entry debit, target, stop, underlying, hold-through details, reasons, and warnings.

## Debug Availability

Raw provider tables, full advisor payload, and run log remain available in the Monitor / Debug section, collapsed by default.
