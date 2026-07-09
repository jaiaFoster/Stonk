# Patch 30E/F Payload + Strategy Row Audit

## Current Summary JSON Source

`report_snapshots.summary_json` is the hot summary loaded by default with `ReportSnapshotRepository.latest_success(include_full=False)`.

Before this patch, runtime/profile labels were ambiguous:

- `payload_profile.sections_bytes.report_summary_json` measured the pre-save `report_summary` object, not the compact `report_snapshots.summary_json`.
- `ReportSnapshotRepository._save()` already wrote a compact manifest when `REPORT_FULL_DEBUG_PAYLOAD_ENABLED=false`.
- `RunManifest.summary_json_bytes` copied the legacy pre-save size, so diagnostics could report a large "summary JSON" even when the saved hot summary was compact.

Patch 30E/F separates:

- `compact_summary_json_bytes`
- `legacy_report_summary_json_bytes`
- `full_archive_blob_bytes`
- `raw_provider_archive_blob_bytes`
- `api_hot_path_bytes`

`summary_json_bytes` in run manifests now means compact hot summary bytes.

## Largest Contributor Audit

The new `app/services/payload_path_audit_service.py` reports nested JSON paths, not only top-level sections.

Known likely large paths from code audit:

1. `report_summary.strategy_results.*.rows`
2. `report_summary.strategy_results.*.items`
3. `tradier_snapshot._strategy_results.forward_factor_calendar.rows[*].details`
4. `tradier_snapshot._stock_momentum_strategy.items`
5. `tradier_snapshot._daily_opportunity_engine.actions` when unbounded

Normal hot summaries should now store links/counts, not row arrays.

## Routes That Still Read Full Snapshot

Intentionally still full/detail consumers:

- `/api/daily-opportunity`: still reads full snapshot until TKT-DAILY-OPPORTUNITY-ROWSTORE.
- `/api/open-positions`: still reads full snapshot for lifecycle/open option detail.
- advisor/knowledge endpoints: still read full snapshot for agent context.
- admin diagnostic endpoints: read full snapshot for diagnostics.

Changed in this patch:

- `/api/strategies/<strategy_id>/rows` reads `StrategyRowRepository` first.
- It falls back to legacy snapshots only when the row store is empty, and returns `source=legacy_snapshot_fallback`.

## Data Moved To StrategyRowRepository

Persisted during `/run`:

- `stock_momentum`
- `earnings_calendar`
- `skew_momentum_vertical`
- `forward_factor_calendar`

Rows are compact universal rows with detail/gate/metric/display JSON fields. Heavy provider payloads, option chains, full diagnostics, and raw blobs stay out of the hot row store.

## Archive / Debug Only

These remain full/archive data:

- full report summary blob
- full payload blob
- raw provider blob
- raw option chain/provider responses
- full debug payloads

## Calendar Entry Window Gate

Calendar precheck now annotates:

- `VALID_ENTRY_WINDOW`
- `ENTRY_WINDOW_CLOSING`
- `ENTRY_WINDOW_CLOSED`
- `NO_PRE_EARNINGS_SHORT_EXPIRY`
- `SHORT_LEG_SPANS_EARNINGS`
- `SHORT_DTE_TOO_LOW`
- `FRONT_LEG_TOO_DECAYED`

Closed/spanning/too-low cases cannot remain ordinary actionable near-misses.

## Before / After Size Evidence

Handoff baseline:

- live reported summary growth: about 2.6 MB around commit `640caa1c`
- 30D.2 reported `report_summary_json`: about 923 KB
- `report_snapshot_save`: about 1.05 MB

Local fixture after patch:

- compact manifest: below 750 KB target and below preferred 500 KB target
- focused tests assert compact manifest excludes strategy row arrays and raw provider payloads

Final live before/after numbers should be captured after deploy from:

- `/api/dashboard/summary`
- `payload_profile`
- `ReportSnapshotRepository.snapshot_profile()`

## Re-Audit Result

Patch code path after implementation:

- Strategy row endpoint reads `StrategyRowRepository` first.
- Legacy row fallback is explicit and labeled.
- Universal rows are persisted during `/run`.
- Compact manifest contains API links and counts, not full row arrays.
- Payload profiler distinguishes compact vs legacy/archive sizes.
- Broker raw account dumps are guarded by `BROKER_DEBUG_RAW_LOGS_ENABLED=false` by default.
