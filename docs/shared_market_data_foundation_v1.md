# Shared Market Data Foundation v1

## Purpose

Strategies share normalized facts, not verdicts. Foundation stays on-demand and lightweight inside Flask, Railway, and SQLite.

## Architecture

```text
Provider adapters
  -> MarketDataHub
  -> RunDataContext + SQLite MarketDataRepository
  -> StrategyDataRequirement planner
  -> explicit local Strategy Registry
  -> independent strategy services through adapters
  -> generic opportunity history + report snapshot
  -> Daily Opportunity, dashboard, exports
```

## RunDataContext

One context exists per pipeline run. It stores quotes, candles, option chains, earnings events, broker positions, derived metrics, requirements, normalized strategy results, coverage, and fetch audit rows.

## MarketDataHub

Lookup order:

1. run context
2. fresh SQLite cache
3. provider call within shared budget
4. explicitly labeled stale-cache fallback
5. explicit missing/failed result

Provider failures are briefly suppressed to avoid repeatedly hitting a broken endpoint.

## Repository and TTL

`MarketDataRepository` enables SQLite WAL and a 5000ms busy timeout. It initializes market fact, fetch-log, provider-error, and coverage tables automatically.

Defaults:

- quotes: 900 seconds
- option chains: 1800 seconds
- candles: 43200 seconds
- earnings: 43200 seconds
- derived metrics: 43200 seconds
- provider errors: 900 seconds

## Shared Metrics

Daily candles produce momentum 1M/3M/6M/12M, SMA 50/200, price distance from both SMAs, average volume 30D, realized volatility 20D/30D, and relative strength versus QQQ. Missing bars or benchmark data produce reason strings.

## Coverage States

`COMPLETE`, `PARTIAL`, `MISSING_NOT_REQUESTED`, `MISSING_PROVIDER_FAILED`, `MISSING_UNSUPPORTED`, `SKIPPED_DEV_CAP`, `SKIPPED_PROVIDER_BUDGET`, `STALE_CACHE_USED`, `STALE_CACHE_REJECTED`, and `LOW_CONFIDENCE`.

Missing data is not weak momentum. Strategy 2 emits a data-unavailable blocker when momentum facts were never available.

## Persistence and Refresh

Completed reports persist in `report_snapshots`; failed runs never replace successful snapshots. Authenticated root dashboard loads latest completed snapshot without provider calls.

- Refresh Active Trades: broker positions and lifecycle only.
- Refresh Market Data: starts normal merged-strategy refresh.

## Non-goals

No trade execution, manual entry/tracking, Postgres, Redis, background ingestion daemon, dynamic plugin packages, data lake, Airflow/Dagster, or historical options warehouse.

## Deferred

Full removal of legacy direct provider calls is gradual. Calendar and stock services remain backward compatible while shared facts and adapters become primary plumbing. Forward Factor is not implemented until source formulas and examples are supplied.
