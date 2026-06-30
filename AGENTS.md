## Serialization Policy

Every field added to a strategy row, position dict, or knowledge context MUST appear
in the API surface without a separate fix. The rule:

- `_strategy_summary` in `app/services/developer_snapshot_service.py` uses an EXCLUDE
  list, not a whitelist. Adding a field to a strategy row automatically surfaces it in
  all API endpoints. To suppress a field, add it to `_STRATEGY_SUMMARY_EXCLUDE` with a
  comment explaining why.

- Any new `row["field"] = value` in a strategy service must be verified with:
  `curl $BASE/api/dev/snapshot/detail/strategies?token=$DEV`
  and confirmed present before the PR is closed.

This policy eliminates the "field exists internally but disappears" class of bugs.

## Open Tickets (Calendar Pipeline Audit)

- **TKT-CAL-001** — CAG/LEVI/EPAC are not bugs; they simply fall outside the current
  earnings discovery window. Re-verify they reappear when their earnings date enters
  the window.
- **TKT-CAL-002** — After `EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME` was lowered to
  250,000, re-check that mid-caps which now pass the volume gate reach the IV
  relationship check and fail there with a clear label if the IV setup is adverse.
- **TKT-CAL-003** — Wire `date_confidence` (now promoted to the top-level calendar
  row, see Item 2 fix in `unified_calendar_trade_engine_service.py`) into the Saku
  morning-brief context so a `single_source` earnings date shows as a caution on
  WATCH/PASS calendar signals.
