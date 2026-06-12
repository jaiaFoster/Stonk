# Shared Metrics and Requirement Execution Correctness v1

Patch 25E restores shared market facts to the dashboard and tightens the boundary between missing data and weak signals.

## Canonical shared metrics

`shared_market_metrics_service` converts MarketDataHub records into one strategy-facing shape. Holdings, macro, Stock Momentum, Portfolio Gap, Potential Adds, and Risk Review consume the same:

- current price and freshness
- 1M, 3M, 6M, and 12M momentum
- 50D and 200D simple moving averages
- average volume and realized volatility
- relative strength versus QQQ
- provider provenance, confidence, and explicit missing-data state

Actionable add rows require complete trend, price, liquidity, and freshness facts. Incomplete data remains visible as Watch / Data Incomplete and cannot enter Daily Opportunity.

## Requirement execution

Enabled strategy plugins declare requirements before provider fulfillment. `DataRequirementPlanner` merges overlapping requirements and fulfills one consolidated request per approved ticker. A broader option-chain request can satisfy a narrower same-run request.

Coverage reporting separates run-context hits, SQLite hits, provider fetches, stale fallbacks, suppressed failures, cap skips, and duplicate fetches prevented.

## Opportunity identity and snapshots

Generic strategy opportunity identity includes strategy, ticker, direction, structure type, expirations, strikes, and event date. Repeated observations of the same structure deduplicate; materially changed structures remain distinct.

Normal dashboard GET requests load the latest successful persistent snapshot and make no provider calls. Snapshot headers disclose report generation, market-data refresh, active-trade refresh, and cached source timestamps.

## Deployment checks

Set Railway:

```text
EARNINGS_DISCOVERY_END_DAYS=21
WATCHLIST_NAMES=List 01
WATCHLIST_NAME_ALIASES=My First List:List 01
```

Leaving `WATCHLIST_NAMES` blank still discovers all watchlists. Aliases provide compatibility for renamed lists.

## Deferred

Forward Factor remains deferred. Full registry-only execution also remains a later migration; existing strategy math and calendar behavior are preserved while requirement collection and shared-data fulfillment become auditable.
