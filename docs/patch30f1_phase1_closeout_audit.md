# Patch 30F.1 Phase 1 Closeout Audit

## Payload Source Audit

Observed live problem:

- `tradier_snapshot`: about 2.88 MB
- `report_snapshot_save`: about 1.32 MB
- `report_summary_json`: about 1.19 MB
- `forward_factor`: about 535 KB
- `stock_momentum`: about 197 KB

Root cause:

- `report_summary_json` in `PayloadProfile` was measuring the legacy pre-compaction report summary.
- That legacy object remained useful for archive/debug/fallback, but the metric name made it look like active dashboard/API summary bloat.
- `summary_payload_status` was derived from that legacy object, so the app emitted warning-level hot-path payload health even when compact summary was tiny.

Fix:

- `sections_bytes.report_summary_json` now means active hot summary bytes.
- `sections_bytes.legacy_report_summary_json` now means legacy/archive summary bytes.
- `summary_payload_status` uses active hot summary bytes.
- `legacy_report_summary_json_bytes` remains visible for archive/debug size tracking.
- `compact_payload_log()` now reports `hot_path`, `legacy_archive`, and `full_archive` separately.

Compatibility:

- Top-level `summary_json_bytes` is retained as a legacy compatibility alias for older tests/diagnostics.
- New `active_summary_json_bytes` identifies active hot summary bytes before compact manifest size is attached.

## Endpoint Source Audit

Hot-path compact source:

- `GET /api/dashboard/summary` reads `RunManifestRepository.latest()`.
- It does not load full report snapshots.

Row-store source:

- `GET /api/strategies/<strategy_id>/rows` reads `StrategyRowRepository` first.
- If rows exist, source is `strategy_row_store`.
- Tests spy on `ReportSnapshotRepository` and prove no full snapshot load occurs when row-store rows exist.

Remaining full-summary consumers:

- `GET /api/daily-opportunity`: still full snapshot backed; carry forward `TKT-DAILY-OPPORTUNITY-ROWSTORE`.
- `GET /api/open-positions`: still full snapshot backed; carry forward `TKT-OPEN-POSITIONS-ROWSTORE`.
- Developer/admin/detail endpoints can load full archive by explicit diagnostic/detail request.

## Calendar Explainability Audit

Problem:

- Earnings discovery quality could reject all July 16 candidates and leave scanner universe empty.
- The strategy still needed visible row-store explanations for late/invalid names.

Fix:

- Unified calendar rows now preserve entry-window fields from quality-filter rejected rows.
- Strategy row store derives `details.earnings_calendar` for rejected/diagnostic rows even when no spread exists.
- Row-store friendly verdicts now expose timing-specific labels.

Visible row-store statuses:

- `ENTRY WINDOW CLOSED / DO NOT ENTER`
- `SHORT LEG SPANS EARNINGS / DO NOT ENTER`
- `SHORT DTE TOO LOW / DO NOT ENTER`
- `FRONT LEG TOO DECAYED / DO NOT ENTER`
- `NO PRE-EARNINGS SHORT EXPIRY`
- `MONITOR / PRE-WINDOW`
- `MONITOR / DATA NEEDED`
- `DATE CONFLICT REVIEW`

Persisted details:

- `entry_window_status`
- `entry_window_reason`
- `short_leg_status`
- `short_leg_expires_before_earnings`
- `short_leg_dte_minimum`
- `short_leg_time_value_minimum`
- `short_leg_does_not_span_event`
- `available_pre_earnings_expirations`
- `rejected_expirations`
- `proposed_short_expiration`
- `proposed_long_expiration`

## Re-Audit Checklist

- Dashboard summary does not use full legacy summary.
- Strategy row endpoints read `StrategyRowRepository` first.
- No full snapshot load occurs for strategy row endpoints when row-store rows exist.
- Legacy fallback is source-labeled.
- Payload profiler separates compact, legacy, archive, and API response sizes.
- Calendar rejected candidates are preserved as rows.
- Calendar quality filter does not silently drop all timing-invalid candidates from the user-visible row layer.
- Post-earnings short legs are blocked.
- Too-low-DTE pre-earnings short legs produce explainable rows.
- Forward Factor remains dry-run.
- Broker raw dumps remain guarded by `BROKER_DEBUG_RAW_LOGS_ENABLED`.

## Carry Forward

- `TKT-DAILY-OPPORTUNITY-ROWSTORE`
- `TKT-OPEN-POSITIONS-ROWSTORE`
- `TKT-CALENDAR-PREWINDOW-BUDGET`
- `TKT-OPEN-OPTIONS-DEDUP`
- `TKT-DOUBLE-CALENDAR-PARENT`
