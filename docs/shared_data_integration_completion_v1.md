# Shared Data Integration Completion v1

Patch 25D makes the shared-data foundation the preferred read-through path before any Forward Factor work begins.

## Runtime flow

```text
Strategy registry requirements
  -> DataRequirementPlanner
  -> MarketDataHub
  -> RunDataContext
  -> fresh SQLite cache
  -> provider request when needed
  -> strategy evaluation and report snapshot
```

Market-data cache identity uses ticker, data type, and meaningful normalized parameters. Same-run requests reuse `RunDataContext`; fresh cross-run requests reuse SQLite. Force refresh bypasses both caches but remains subject to provider safety caps.

Calendar candle rescue now uses `MarketDataHub`. Coverage reports expose run-context hits, SQLite hits, provider fetches, stale fallbacks, provider failures, cap skips, and duplicate fetches prevented.

## Product semantics

- Missing or capped data is reported as missing/capped, not weak momentum.
- Strategy rows preserve diagnostic `signal_score` and expose separate `actionability_score`.
- Hard failures have zero actionability and cannot become Daily Opportunity entries.
- Generic strategy opportunities dual-write beside legacy strategy caches during migration.
- Generic option lifecycle envelopes distinguish calendars, call/put debit verticals, unknown multileg structures, and unpaired legs.

## Persistence and refresh

Successful reports are stored in a WAL-enabled SQLite snapshot repository on the configured persistent path. Failed runs do not replace the latest successful report. Dashboard page loads read snapshots and do not start provider work.

Full market refresh and active-trade refresh use separate locks. Active-trade refresh remains broker/lifecycle-only.

## Deployment

Set Railway `EARNINGS_DISCOVERY_END_DAYS=21`. Config check emits an actionable warning when Railway still requests 14.

## Deferred gate

Forward Factor remains intentionally unimplemented. Production validation must confirm no duplicate CCL/QQQ fetches, snapshot-only page reloads, cache-heavy second refreshes, and active-trade-only refresh scope.
