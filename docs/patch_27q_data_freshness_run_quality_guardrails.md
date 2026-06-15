# Patch 27Q - Data Freshness and Run Quality Guardrails

Patch 27Q makes cached report age and run quality explicit without calling
providers or changing any strategy decision.

## Stored Metadata Only

Freshness uses the persisted report snapshot, latest run manifest, stored
provider status, and broker fallback timestamps. It does not fetch data.

The compact freshness summary exposes:

- canonical report run ID and generated time
- latest attempted run ID, status, and quality
- report age and `FRESH`, `AGING`, `STALE`, or `UNKNOWN` state
- broker current, stale-fallback, or unavailable state
- market/options availability and conservative report-snapshot age proxy
- earnings timestamp as unknown when no reliable stored timestamp exists
- explicit warnings for degraded attempts, stale cache, and missing data

## User-Facing Honesty

The cached shell shows a compact data-status line. A stale cached complete
report is labeled `STALE_CACHED_REPORT`. If a newer degraded or failed run
exists, the shell labels the situation `LATEST_RUN_DEGRADED` while continuing
to show the latest usable complete report.

Defaults:

```text
REPORT_FRESHNESS_WARN_SECONDS=21600
REPORT_FRESHNESS_STALE_SECONDS=86400
```

## Scope

- No provider fetch changes.
- No strategy, ranking, Daily Opportunity, Forward Factor, or lifecycle changes.
- No execution or raw-archive changes.
- Diagnostics and snapshots remain read-only and provider-free.

