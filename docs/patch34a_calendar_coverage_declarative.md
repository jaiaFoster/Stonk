# ASA Patch 34A — Calendar Coverage Expansion and Declarative Strategy Engine Foundations

## Scope

Patch 34A starts the broader strategy-engine architecture move without changing
broker behavior, trade execution, Forward Factor dry-run safety, or Daily
Opportunity ranking.

## Roadmap Audit

- Patch metadata moved from `33C.2.v1` to `34A.v1`.
- Legacy calendar cutover tickets are marked completed or superseded by 33C.x
  where the live canonical path already exists.
- Open-position parent projection remains tracked through GitHub issue #173 and
  related roadmap notes; 34A does not reopen the lifecycle redesign.
- Active 34A work is now explicit:
  - `TKT-CALENDAR-COVERAGE-EXPANSION`
  - `TKT-DECLARATIVE-STRATEGY-SCHEMA`
  - `TKT-EXPIRATION-PRIMITIVES`

## Implemented

- Added `app/models/strategy_definition.py` with versioned trusted schema
  contracts for strategy definitions, expiration requirements, pair rules, leg
  requirements, structures, and coverage accounting.
- Added `app/services/expiration_enumeration_service.py`:
  - normalizes expiration dates from provider-shaped records,
  - classifies weekly/monthly/quarterly/LEAPS expirations,
  - filters expirations against front/back requirements,
  - enumerates every pair and records rejection codes.
- Added `app/services/strategy_calculation_registry.py` with allowlisted
  calculation IDs only. JSON definitions cannot reference Python modules.
- Added `app/services/strategy_definition_loader_service.py`:
  - loads checked-in trusted JSON only from `config/strategies`,
  - validates schema version, field IDs, rule operators, calculations, and leg
    references,
  - rejects unsafe calculation references such as dotted module paths.
- Added checked-in `config/strategies/earnings_calendar.v1.json`.
- Added `app/services/calendar_coverage_telemetry_service.py` and run-time
  `CALENDAR_COVERAGE_FUNNEL` logging.
- Calendar canonical rows now carry:
  - `strategy_definition_id`
  - `strategy_definition_version`
  - `structure_template_id`
  - `enumeration_policy_version`
  - compact `coverage_accounting`

## Safety

- No provider calls occur during strategy-definition loading or validation.
- No arbitrary user code is executed.
- No `eval`, `exec`, shell execution, broker writes, or trade execution are
  introduced.
- Earnings Calendar strategy math remains code-owned; JSON is a trusted
  definition/provenance layer in this patch.
- Forward Factor remains dry-run.

## Validation Notes

Focused tests cover:

- weekly/monthly/quarterly/LEAPS expiration classification,
- malformed expiration handling,
- later valid pair selection when the nearest expiration fails,
- post-event short-leg blocking,
- built-in strategy-definition loading,
- unsafe calculation rejection,
- unknown field rejection,
- circular leg-reference rejection,
- calendar row declarative provenance,
- coverage funnel counts.

## Known Carry-Forward

- Full migration of live Earnings Calendar structure construction onto the JSON
  definition is intentionally partial in 34A.
- Daily Opportunity ranking is unchanged.
- Forward Factor promotion remains deferred.
- Custom Strategy DSL/UI work remains future roadmap work.
