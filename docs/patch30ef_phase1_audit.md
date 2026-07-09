# Patch 30E/F Phase 1 Audit

Patch: Legacy Summary Deprecation, Calendar Pre-Window Monitoring, Strategy Row Store Source of Truth.

## Normal Hot Path

Canonical normal run summary is the compact manifest stored in `report_snapshots.summary_json`.

Normal hot-path summary may include:

- run and quality metadata
- compact strategy counts
- compact Daily Opportunity summary
- broker/open-position/provider summaries
- payload profile sizes
- API links

Normal hot-path summary must not include:

- raw Tradier snapshots
- raw provider payloads
- raw option-chain JSON
- full Forward Factor diagnostics
- full Stock Momentum arrays
- full universal strategy row arrays
- full broker raw account payloads

## Endpoint Audit

Checked endpoints:

- `GET /api/dashboard/summary`
- `GET /api/daily-opportunity`
- `GET /api/open-positions`
- `GET /api/runs/latest`
- `GET /api/run/status/<job_id>`
- `POST /api/run/refresh`
- `GET /api/strategies`
- `GET /api/strategies/schema`
- `GET /api/strategies/<strategy_id>/rows`

Migrated in this patch:

- `GET /api/strategies/<strategy_id>/rows` reads `StrategyRowRepository` first.
- Strategy row fallback is now labeled with `source=legacy_snapshot_fallback`.
- Empty row store and no legacy snapshot returns `source=empty`.

Still using legacy/full snapshot fallback:

- Daily Opportunity remains full snapshot backed until `TKT-DAILY-OPPORTUNITY-ROWSTORE`.
- Open positions/lifecycle detail remains full snapshot backed until `TKT-OPEN-POSITIONS-ROWSTORE`.
- Admin/debug/developer-snapshot detail can still load full archive by explicit request.
- Dashboard detail/full compatibility remains available for rollback/debug.

## Payload Profile

Profiler now separates:

- `compact_summary_json_bytes`
- `legacy_report_summary_json_bytes`
- `full_archive_blob_bytes`
- `raw_provider_archive_blob_bytes`
- `dashboard_summary_response_bytes`
- `strategy_rows_response_bytes`
- `api_hot_path_bytes`

This avoids mixing compact UI payloads with archive/debug blobs.

Baseline from handoff:

- `compact_summary_json_bytes`: about 1,842 bytes
- `legacy_report_summary_json_bytes`: about 2.64 MB

Local tests assert:

- compact manifest excludes raw provider data
- compact manifest excludes full strategy row arrays
- compact manifest exposes API links and summary counts
- path-level payload audit reports nested contributors

## Top Legacy JSON Contributors By Object Path

Likely heavy paths from code audit and path-profiler fixtures:

- `legacy_report_summary.report_data.tradier_snapshot._strategy_results.forward_factor_calendar.rows[*].details`
- `legacy_report_summary.report_data.tradier_snapshot._strategy_results.stock_momentum.rows[*]`
- `legacy_report_summary.report_data.tradier_snapshot._stock_momentum_strategy.items[*]`
- `legacy_report_summary.report_data.tradier_snapshot._daily_opportunity_engine.actions[*]`
- `legacy_report_summary.report_data.tradier_snapshot.*.raw_option_chain`
- `legacy_report_summary.report_data.tradier_snapshot.*.chain_diagnostics`

Large contributors should remain archive/debug/detail only.

## Strategy Row Store

New source of truth:

- `app/services/strategy_row_repository.py`
- table: `strategy_rows`

Rows are written during `/run` for:

- `stock_momentum`
- `earnings_calendar`
- `skew_momentum_vertical`
- `forward_factor_calendar`

Strategy row endpoints return:

- `source=strategy_row_store` when rows exist
- `source=legacy_snapshot_fallback` when row store is empty but legacy snapshot exists
- `source=empty` when neither exists

Rows expose:

- `normalization_status`
- `normalization_errors`
- `missing_required_fields`

## Calendar Pre-Window / Entry Gate

Calendar quality rows now expose timing-specific fields:

- `entry_window_status`
- `entry_window_open`
- `entry_window_reason`
- `short_leg_expires_before_earnings`
- `short_leg_dte_minimum`
- `short_leg_time_value_minimum`
- `short_leg_does_not_span_event`
- `entry_window_front_expiration`
- `entry_window_front_dte`
- `expiry_gap_valid`

Statuses:

- `MONITOR_PRE_WINDOW` rendered as `MONITOR / PRE-WINDOW`
- `ENTRY_WINDOW_OPEN` rendered as `WATCH / ENTRY WINDOW OPEN`
- `ENTRY_WINDOW_CLOSING` rendered as `WATCH / ENTRY WINDOW CLOSING`
- `ENTRY_WINDOW_CLOSED`
- `NO_PRE_EARNINGS_SHORT_EXPIRY`
- `SHORT_LEG_SPANS_EARNINGS`
- `SHORT_DTE_TOO_LOW`
- `FRONT_LEG_TOO_DECAYED`
- `DATE_CONFLICT_REVIEW`
- `DATA_NEEDED` rendered as `MONITOR / DATA_NEEDED`

Hard blockers:

- post-earnings short leg
- only pre-earnings short leg below minimum DTE/time value
- no valid pre-earnings short expiry
- earnings date conflict

Pre-window rows are visible as monitor rows and are not clean actionable entries.

## Re-Audit Checklist

- Dashboard summary does not use legacy full summary as normal source.
- Strategy row endpoints read `StrategyRowRepository` first.
- Legacy snapshot fallback is source-labeled.
- Universal rows are persisted during `/run`.
- Universal rows are not only built at API request time.
- Compact manifest does not embed full strategy arrays.
- Compact manifest does not embed raw provider responses.
- Payload profiler reports compact vs legacy vs archive sizes separately.
- Calendar pre-window monitor exists.
- Calendar entry-window gate exists.
- Post-earnings short legs are blocked.
- Late pre-earnings short legs are blocked or downgraded.
- Date conflicts are monitored early.
- Forward Factor remains dry-run.
- Broker raw dumps are guarded by `BROKER_DEBUG_RAW_LOGS_ENABLED`.

## Carry-Forward

- `TKT-DAILY-OPPORTUNITY-ROWSTORE`
- `TKT-OPEN-POSITIONS-ROWSTORE`
- `TKT-OPEN-OPTIONS-DEDUP`
- `TKT-DOUBLE-CALENDAR-PARENT`
- `TKT-CALENDAR-PREWINDOW-BUDGET`
