# Unified Calendar Trade Engine v1

This patch adds a single user-facing calendar-trade workflow section while keeping the existing lower-level modules intact.

## Goal

The calendar workflow now reads as one decision pipeline:

1. Find upcoming earnings events from the configured earnings provider.
2. Run Tradier calendar-spread screening on the earnings-discovery universe.
3. Clearly mark pass/warn/fail requirements.
4. Show a possible spread only when one exists.
5. Score and rank possible entries.
6. Recommend an entry timing/next check.
7. Show already-entered Tradier calendars and lifecycle next actions.

## New report section

The report adds:

```text
Unified Calendar Trade Engine v1
```

This section combines:

- Earnings Trade Discovery v1
- Calendar Spread Screener v1
- Earnings Calendar Strategy v1
- Open Options Position Detector v1
- Calendar Lifecycle Check v1

The older detailed sections are still present below it as supporting/debug detail.

## New environment variable

Optional:

```text
UNIFIED_CALENDAR_ENGINE_ENABLED=true
```

Default is `true`.

## Notes

This is still read-only. It does not place trades and does not close trades.

Superseded note: lifecycle entry debit should come from broker-detected option-leg average prices or broker cost-basis data, not manual trade memory. See `calendar_verdict_hold_through_research_v1.md` and `options_lifecycle_accuracy_v1.md`.
