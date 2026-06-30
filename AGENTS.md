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
