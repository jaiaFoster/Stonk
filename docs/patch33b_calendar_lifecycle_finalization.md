# Patch 33B — Calendar Lifecycle Orchestration and Finalization Truth

## Purpose

Patch 33B closes the architecture gap between early earnings discovery,
calendar structure scans, durable strategy rows, and read-only API truth.

The patch does not add a strategy, broker write path, trade execution, or
Forward Factor promotion.

## Before Execution Graph

```text
raw earnings discovery
-> earnings discovery quality filter
-> load prior-run calendar candidates from module memory
-> launch new scanner in background
-> candle rescue on prior-run candidates
-> lifecycle check
-> calendar ranking on prior-run candidates
-> unified calendar engine on prior-run candidates
-> mini-backtest / audit
-> Daily Opportunity
-> strategy result normalization
-> format payload
-> payload profile
-> late join of scanner thread
-> report snapshot + canonical pointer
-> run manifest
-> StrategyRowRepository
-> provenance / data confidence / history / journal
```

Root cause: the only existing barrier was after calendar consumers had already
read stale or empty scan output.

## After Execution Graph

```text
raw earnings discovery
-> earnings discovery quality filter
-> create CalendarScanResult(run_id, scan_id)
-> current-run scanner execution
-> CALENDAR_SCAN_BARRIER
-> candle rescue on current-run candidates
-> lifecycle check
-> calendar ranking
-> unified calendar engine
-> lifecycle/opportunity projection
-> mini-backtest / audit
-> Daily Opportunity
-> strategy result normalization
-> StrategyRowRepository
-> opportunity history
-> observation journal
-> pipeline provenance
-> data-confidence validation
-> payload profile
-> report snapshot + canonical pointer
-> run manifest
-> endpoint verification
```

## Ownership Audit

1. Scanner ownership: `analysis_service.run_portfolio_pipeline` owns the
   current-run scan and `CalendarScanResult`.
2. Scanner storage: current-run result is attached to
   `tradier_snapshot["_calendar_scan_result"]`; old module-level state is no
   longer hot path.
3. Result ownership: run-scoped in memory and snapshot metadata, keyed by
   `run_id` and `scan_id`.
4. Early readers: ranking, unified engine, audit, and mini-backtest previously
   read before scan completion; now they run after the barrier.
5. Stale race: prior-run scanner candidates are no longer loaded into
   `calendar_candidates`.
6. `not_selected_for_scan`: still exists in legacy audit for raw items that
   never reach the quality/scanner stages, but current-run scanned candidates
   are tagged with current `scan_id`.
7. Budget deferrals: lifecycle projection preserves non-terminal states; row
   semantics continue to exclude budget/deferred rows from Daily Opportunity.
8. Front-leg DTE failure: quality rows remain visible and project to
   `STRUCTURE_UNAVAILABLE` / `BLOCKED` or `NOT_EVALUATED` lifecycle fields.
9. Calendar stage owner: `calendar_opportunity_projection_service.py`.
10. Daily Opportunity eligibility owner: strategy row semantics; calendar rows
    now carry `surface_eligible`, `entry_evaluation_eligible`, and
    `entry_allowed`.
11. Snapshot owner: `ReportSnapshotRepository`.
12. State at snapshot time: row store/history/journal/provenance/validation are
    written before snapshot save.
13. Row-count reconciliation: `CALENDAR_ROW_RECONCILIATION` is logged before and
    after persistence.
14. Daily Opportunity count drift: Daily Opportunity still consumes normalized
    strategy state; reconciliation exposes calendar daily-visible counts.
15. `row.action_present` / `row.score_present`: data-confidence profile now
    treats incomplete lifecycle rows as candidate/profile rows, not generic
    final-action rows.

## Files Changed

- `app/services/analysis_service.py`
- `app/services/calendar_scan_result_service.py`
- `app/services/calendar_opportunity_projection_service.py`
- `app/services/run_finalization_coordinator.py`
- `app/services/strategy_row_repository.py`
- `app/services/automated_data_validation_service.py`
- `app/services/endpoint_verification_service.py`
- `app/main.py`
- `AGENTS.md`
- `tests/test_patch33b_calendar_lifecycle_finalization.py`

## Compatibility Compromise

`ReportSnapshotRepository.save_success()` still combines snapshot write and
canonical complete snapshot publication. Patch 33B does not split that storage
contract. Instead, it moves required strategy artifact persistence before this
call so canonical publication cannot precede row-store/history/journal writes.

## Verification

Focused local validation:

```text
.venv/bin/python -m py_compile app/services/calendar_scan_result_service.py app/services/calendar_opportunity_projection_service.py app/services/run_finalization_coordinator.py app/services/strategy_row_repository.py app/services/automated_data_validation_service.py app/services/endpoint_verification_service.py app/services/strategy_execution_service.py app/providers/robinhood_provider.py app/services/analysis_service.py app/main.py
.venv/bin/python -m pytest -q tests/test_patch33b_calendar_lifecycle_finalization.py tests/test_patch33a1_strategy_lifecycle_kernel.py tests/test_patch33a1_calendar_lifecycle.py tests/test_patch33a1_calendar_lifecycle_integration.py tests/test_calendar_task_finalization_barrier.py tests/test_open_positions_calendar_cardinality.py
.venv/bin/python -m pytest -q
```

Latest focused result: `114 passed`.
Latest full-suite result: `3228 passed, 1 skipped, 2 subtests passed`.

## Return To Roadmap

Patch 33B completes orchestration/finalization repair for Earnings Calendar as
the first lifecycle-backed strategy. Follow-on work should use the same
opportunity lifecycle framework for additional strategies rather than adding
parallel row formats.
