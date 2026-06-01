# UI Overhaul Readiness Audit

## Summary

The backend is usable for a small trusted-user deployment, but the UI overhaul should be protected by tests around decision contracts rather than visual snapshots alone. The biggest risks are stale docs, no existing test suite, large report-rendering functions, and several provider-heavy flows that are hard to smoke-test without live credentials.

## Comparable Project Lessons

- Local-first portfolio trackers such as Wealthfolio and OpenStocky emphasize privacy, simple setup, no required cloud account, and clear portfolio analytics. Algo Stock Advisor matches that spirit when it stays read-only and broker-aware.
- Larger platforms such as OpenAlgo and Senex Trader split market data, analytics, risk, execution, and UI into explicit layers. Algo Stock Advisor should not copy their infrastructure size, but it should copy the separation between decision objects and presentation.
- For a few users, the best fit is a modular monolith: keep Flask and service modules, add tests around service outputs, and avoid adding a database unless the product truly needs persistence.

## Documentation Findings

- `README.md` still referenced manual trade-memory Railway variables. That has been removed.
- `pipeline_finalization_trade_memory_v1.md` conflicted with the current no-manual-tracking product rule. It is now marked deprecated.
- `unified_calendar_trade_engine_v1.md` contained an obsolete note recommending trade memory as the next improvement. It now points to broker-detected lifecycle accuracy instead.
- `docs/README.md` now identifies current source-of-truth docs and superseded docs.

## Test Coverage Added

- `tests/test_calendar_verdict_service.py`
  - ASO-style untradeable candidate fails.
  - Ranking fail overrides raw scanner pass.
  - Pre-earnings financing defaults to research/watch.
  - Unknown timestamp produces unknown trade type.
- `tests/test_calendar_hold_through_service.py`
  - Positive P/L does not override large historical earnings moves.
  - Muted historical movement and low assignment risk support hold-through review.
- `tests/test_daily_opportunity_engine_service.py`
  - Failed calendar candidates do not appear as possible entries.
  - Active calendar lifecycle alerts outrank score thresholds.
- `tests/test_documentation_guardrails.py`
  - README does not recommend manual trade memory.
  - Deprecated manual trade-memory doc stays visibly deprecated.

## Remaining Gaps Before UI Rewrite

- Add route-level smoke tests once Flask dependencies are installed in the environment.
- Add fixture-based report rendering tests for the primary data objects the new UI will consume.
- Split `report_service.py` over time; it is over 2,000 lines and mixes extraction, formatting, HTML, and text rendering.
- Add contract examples for `tradier_snapshot` private keys such as `_daily_opportunity_engine`, `_unified_calendar_trade_engine`, `_calendar_ranking`, and `_calendar_lifecycle_checks`.
- Exclude `__pycache__` and compiled files from release archives.

## Suggested UI Contract Rule

The UI should render from final/normalized fields:

- `final_verdict`
- `trade_type_label`
- `main_blocker`
- `main_reason`
- `backtest_status`
- `account_risk_status`
- `hold_through_score`
- `hold_through_action`

It should not infer candidate eligibility from raw scanner labels.
