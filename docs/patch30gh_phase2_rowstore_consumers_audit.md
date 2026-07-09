# Patch 30G/H Phase 2 Row-Store Consumer Audit

Patch: ASA Patch 30G/H - Phase 2: Daily Opportunity Row Store + Open Positions Lifecycle Cleanup

## Summary

Phase 1 made StrategyRowRepository a write path. Phase 2 makes core read paths prefer it.

This patch keeps the legacy full report snapshot as a labeled fallback only. Normal Daily Opportunity and Open Positions reads now try normalized strategy rows first.

No broker writes, trade execution, Forward Factor promotion, or strategy threshold changes were added.

## Source Behavior

### Dashboard Summary

- Source remains compact manifest / hot summary.
- No new full legacy snapshot dependency was added.

### Strategy Row APIs

- Existing source behavior retained:
  - `source=strategy_row_store` when rows exist.
  - `source=legacy_snapshot_fallback` only when the row store is empty or unavailable.
  - `source=empty` when neither source has rows.

### Daily Opportunity

Before:
- `/api/daily-opportunity` loaded the full report snapshot and read `_daily_opportunity_engine`.

After:
- `/api/daily-opportunity` reads StrategyRowRepository first.
- Response source metadata includes:
  - `source=strategy_row_store`
  - `fallback_used=false`
  - `latest_run_id`
  - `row_count_considered`
  - `eligible_count`
  - `excluded_count`
  - `dry_run_exclusions`
  - `strategy_counts`
- Legacy fallback remains, labeled as `source=legacy_snapshot_fallback`.

Forward Factor:
- FF rows are read for traceability.
- FF rows remain excluded while dry-run.
- Dry-run exclusions report rows seen, eligible count, and `excluded_reason=dry_run`.

Ranking:
- Active calendar lifecycle rows are sorted ahead of new trade/stock rows.
- Stock momentum add rows remain eligible.
- Skew PASS rows remain eligible.
- Fail, diagnostic, and rejected candidate rows do not become trade actions.

### Open Positions / Lifecycle

Before:
- `/api/open-positions` loaded full snapshot open-position sections.
- Lifecycle checks could exist while endpoint returned `active_calendar_count=0`.

After:
- `/api/open-positions` reads earnings calendar lifecycle rows from StrategyRowRepository first.
- Response includes:
  - `source=strategy_row_store`
  - `fallback_used=false`
  - `active_calendar_count`
  - `calendar_structures`
  - `lifecycle_rows`
  - `open_option_leg_count`
  - `dedup_summary`
  - `warnings`
- Legacy fallback remains, labeled as `source=legacy_snapshot_fallback`.

SBUX behavior:
- Minimum behavior is endpoint/lifecycle agreement. If lifecycle has two child calendar rows, open positions returns two calendar structures.
- Duplicate lifecycle structures are preserved and surfaced with `dedup_summary.duplicate_warning=true`.
- Double-calendar parent grouping remains a carry-forward unless already provided by upstream lifecycle rows.

## Earnings Calendar Row Reason Enrichment

Calendar timing explainability fields now survive scanner to row store:

- `entry_window_status`
- `entry_window_open`
- `entry_window_reason`
- `current_dte_to_earnings`
- `ideal_entry_window`
- `estimated_entry_date`
- `days_until_entry_window`
- `available_expirations`
- `available_pre_earnings_expirations`
- `rejected_expirations`
- `proposed_short_expiration`
- `proposed_long_expiration`
- `blocker_code`
- `blocker_detail`

Late candidates can now show rows such as:

- `ENTRY WINDOW CLOSED / DO NOT ENTER`
- `SHORT LEG SPANS EARNINGS / DO NOT ENTER`
- `SHORT DTE TOO LOW / DO NOT ENTER`
- `MONITOR / PRE-WINDOW`
- `MONITOR / DATA NEEDED`
- `DATE CONFLICT REVIEW`

Example July 16-style row:

- July 10 short leg rejected because `SHORT_DTE_TOO_LOW`.
- July 17 short leg rejected because `SHORT_LEG_SPANS_EARNINGS`.
- Row remains visible as `rejected_candidate` instead of disappearing.

## Payload Re-Audit

- Hot dashboard summary remains compact-path based.
- Daily Opportunity no longer requires the full legacy snapshot when row-store rows exist.
- Open Positions no longer requires the full legacy snapshot when lifecycle rows exist.
- Legacy fallback is still present for compatibility and labeled.
- Payload profiler split from 30F.1 remains the source of compact vs legacy archive metrics.

## Remaining Legacy Fallback Users

Still allowed as compatibility/archive/debug paths:

- `/api/daily-opportunity` fallback when StrategyRowRepository has no rows.
- `/api/open-positions` fallback when lifecycle rows are unavailable.
- Existing developer/admin/debug endpoints that intentionally inspect archive snapshots.

## Safety Checklist

- No broker writes added.
- No trade execution added.
- Forward Factor remains dry-run.
- Forward Factor remains excluded from Daily Opportunity.
- Diagnostic/rejected rows remain non-actionable.
- No new provider calls from read-only endpoints.
- No full snapshot dependency added to dashboard summary.

## Carry-Forward Tickets

- `TKT-DOUBLE-CALENDAR-PARENT`: SBUX double calendar should roll up into one parent `double_calendar` with call/put child calendars.
- `TKT-OPEN-OPTIONS-DEDUP`: account-alias duplicate legs need stronger identity mapping before safe merge.
- `TKT-BROKER-RAW-LOGS`: continue watching for raw broker identifiers in normal logs.
- `TKT-DAILY-OPPORTUNITY-UI-TRACE`: expose source row IDs and eligibility/exclusion details more cleanly in UI.
- `TKT-CALENDAR-PREWINDOW-BUDGET`: add queueing/prioritization if provider budget blocks all 21-day chain previews.
