# ASA Patch 34A.1 - Declarative Enumeration and Reliability Reconciliation

Patch 34A.1 completes the immediate reliability backlog discovered after the
34A declarative strategy-definition foundation.

## Scope

- Keep Earnings Calendar candidate eligibility separate from structure rejection.
- Ensure every checked calendar ticker reaches the declarative expiration
  enumerator or emits an explicit `CALENDAR_EXPIRATION_AUDIT ... result=NOT_RUN`.
- Split projection reconciliation from endpoint reconciliation.
- Reconcile Daily Opportunity by action identity, not only by action count.
- Derive data-confidence failed counts and failure codes from the same hard
  failure collection.
- Emit double-calendar parent rows before strategy-row persistence when child
  lifecycle rows naturally group.

## Calendar Enumeration

The discovery quality filter now treats expiration enumeration as diagnostic
coverage, not as a stable-eligibility rejection gate. Stable eligibility remains
limited to ticker support, quote/price, earnings trust, broad liquidity, and
provider availability. Structural outcomes such as no valid front/back pair are
stored as `expiration_pair_diagnostics`, `rejected_expirations`, and
`expiration_enumeration_result`.

Canonical audit logs:

- `STRATEGY_DEFINITION_REGISTERED`
- `STRATEGY_DEFINITION_PROVENANCE`
- `CALENDAR_EXPIRATION_AUDIT`

Canonical rejection examples:

- `FRONT_BELOW_MIN_DTE`
- `FRONT_ABOVE_MAX_DTE`
- `FRONT_AFTER_EVENT`
- `BACK_BEFORE_EVENT`
- `PAIR_GAP_TOO_SMALL`
- `PAIR_GAP_TOO_LARGE`

## Data Confidence

`run_validation_suite()` now returns `hard_failure_records` and
`failure_codes` from one collection. The logging helper no longer scans only the
first 50 reports to infer codes, so `failed=N` with `failure_codes=[]` should not
recur unless a future caller bypasses the canonical result shape.

## Daily Opportunity Parity

The pipeline compares in-run Daily Opportunity actions with repository-backed
endpoint actions by identity set:

- in-run action IDs
- endpoint action IDs
- `only_in_run`
- `only_in_endpoint`

A mismatch sets `FAILED_VALIDATION`; this is intentional because Daily
Opportunity is now a row-store contract, not a best-effort legacy count.

## Open Position Parent Projection

Open calendar child lifecycle rows are passed through
`open_options_position_reconciliation_service` before persistence. When a call
calendar and put calendar share ticker/front/back expirations, the projection
adds an `OPEN_POSITION_PARENT` double-calendar row ahead of child rows.

Account alias dedup remains separate. This patch does not merge broker accounts
or remove child rows.

## Reconciliation Logs

The former overloaded `CALENDAR_ROW_RECONCILIATION` log is split:

- `CALENDAR_PROJECTION_RECONCILIATION`: generated canonical projection counts.
- `CALENDAR_ENDPOINT_RECONCILIATION`: persisted/API/open-position row counts.

## Ticket Status

- `TKT-DATA-CONFIDENCE-RECONCILIATION`: completed locally.
- `TKT-DAILY-OPPORTUNITY-PARITY`: completed locally.
- `TKT-ENDPOINT-ROW-RECONCILIATION`: completed locally.
- `TKT-OPEN-POSITION-PARENT-PROJECTION`: partial; parent projection exists,
  but account alias dedup remains a separate reliability ticket.

## Rollback Notes

This patch does not change broker behavior, execute trades, promote Forward
Factor, or change strategy thresholds. Rollback risk is concentrated in
calendar discovery quality eligibility, row-store parent projection, and Daily
Opportunity validation strictness.

## Return To Roadmap

After this patch is validated, the sprint should return to the broader strategy
engine roadmap rather than continuing ad hoc cleanup unless live verification
finds a blocking contradiction.
