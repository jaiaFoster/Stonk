# Strategy Data Catalog

Patch 31A introduces a static, read-only catalog for fields that built-in and future custom strategies may reference.

The catalog is descriptive and validating only. It does not execute strategies, place trades, run provider fetches, inspect raw provider payloads, evaluate user code, or persist strategy drafts.

## Field IDs

Field IDs are stable product contracts, not provider paths. They are namespaced:

- `market.*`
- `technical.*`
- `momentum.*`
- `earnings.*`
- `options.*`
- `volatility.*`
- `portfolio.*`
- `position.*`
- `strategy.*`
- `data_quality.*`

Internal provider keys may change without changing a public field ID.

## Value Types

Supported v1 types:

- `number`
- `integer`
- `boolean`
- `string`
- `enum`
- `date`
- `datetime`
- `duration_days`
- `percentage`
- `currency`
- `list`

Complex dictionaries are not exposed for v1 gates.

## Operators

Operators are constrained by value type. Numeric fields allow comparisons such as `greater_than`, `between`, and `exists`. Boolean fields allow `is_true` and `is_false`. Date fields allow `before`, `after`, `between`, and day-window operators. List fields allow containment checks.

Regex, arbitrary functions, SQL, shell commands, Python expressions, `eval`, and `exec` are not supported.

## Availability Stages

- `static`
- `broker_snapshot`
- `run_context`
- `strategy_input`
- `strategy_output`
- `lifecycle`

Future strategy input gates may not use `strategy_output` or `lifecycle` fields because those values do not exist until after strategy execution.

## Allowed Uses

- `universe_filter`
- `data_requirement`
- `gate`
- `score`
- `verdict`
- `display`
- `post_process`

Fields are not marked usable everywhere by default.

## Missing Data

Fields declare conservative missing-data behavior:

- `fail_gate`
- `skip_rule`
- `treat_as_false`
- `use_default`
- `mark_data_needed`
- `diagnostic_only`

Financial decision fields generally fail or mark data needed when missing.

## Requirement Mapping

Fields map to future planner requirements such as:

- `quote`
- `candles`
- `benchmark_candles`
- `earnings_date`
- `options_chain`
- `options_chain_set`
- `broker_positions`

This mapping is metadata only in 31A. It does not trigger provider calls.

## Provider Cost Classes

- `none`
- `low`
- `medium`
- `high`
- `very_high`

The cost class is planning metadata, not billing.

## Security Exclusions

The catalog must not expose:

- broker credentials
- account numbers
- raw provider tokens
- account URLs
- user IDs
- raw provider payloads
- database paths
- stack traces
- secret configuration

Portfolio fields expose business facts only, such as `portfolio.has_position`.

## Versioning

- Catalog schema: `31A.v1`
- Strategy row schema: `30J.v1`
- Semantic fields: `30J.v1`

Adding a field is non-breaking. Removing or renaming a field requires deprecation first. Changing field meaning or units requires a new field ID or schema version.

## Future DSL

Future patches can use this catalog to validate custom strategy drafts. Patch 31A does not execute custom strategies or build the strategy editor UI.
