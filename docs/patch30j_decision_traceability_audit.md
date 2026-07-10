# Patch 30J Decision Traceability Audit

## Scope

Patch 30J adds canonical decision semantics to universal strategy rows and makes Daily Opportunity consume those fields directly. It does not add broker writes, trade execution, provider fetch behavior, or Forward Factor promotion.

## Canonical Schema

- Current strategy row schema: `30J.v1`
- Minimum supported strategy row schema: `30A.v1`
- Semantic fields version: `30J.v1`

New canonical fields:

- `decision_class`
- `action_type`
- `actionability`
- `eligibility_status`
- `eligibility_reason`
- `exclusion_reason`
- `priority_tier`
- `review_status`
- `dry_run`
- `source_strategy_id`
- `source_row_id`
- `source_run_id`
- `semantic_source`
- `semantic_fields_version`

## Semantic Ownership

Primary semantic ownership now lives in strategy row normalization:

- `app/services/strategy_row_normalization_service.py`

The normalizer assigns row semantics before persistence. Strategy-specific mappings are centralized there for built-in rows:

- Earnings Calendar lifecycle rows become `decision_class=lifecycle`, `action_type=active_calendar`.
- Earnings Calendar timing failures become `decision_class=rejected`, `action_type=none`.
- Stock Momentum add/watch/tactical rows become stock-specific action types.
- Skew Momentum pass/watch/fail rows become entry/watch/rejected semantics.
- Forward Factor rows remain diagnostic, dry-run excluded, and non-actionable.

## Repository Persistence

`StrategyRowRepository` persists the semantic columns into `strategy_rows`. Existing databases are migrated in place with additive `ALTER TABLE ADD COLUMN` logic.

If an incoming row already contains semantic fields, the repository stores them with:

- `semantic_source=row`

If an older row lacks semantic fields, the repository applies bounded compatibility inference and marks it:

- `semantic_source=legacy_verdict_inference`

This makes fallback behavior visible instead of silently parsing verdicts downstream.

## Daily Opportunity Consumption

Daily Opportunity now reads row semantics instead of re-parsing verdict strings as the primary path.

Important response fields:

- `source=strategy_row_store`
- `fallback_used=false` when row store rows exist
- `eligible_before_limit`
- `returned_action_count`
- `action_limit`
- `truncated`
- `truncated_count`
- `exclusion_counts`
- `exclusion_samples`
- `semantic_source_counts`
- `inferred_semantics_count`

Each returned action includes provenance and trace fields:

- `source_strategy_id`
- `source_row_id`
- `source_run_id`
- `source_table`
- `strategy_row_url`
- `decision_class`
- `action_type`
- `actionability`
- `eligibility_status`
- `eligibility_reason`
- `priority_tier`
- `review_status`
- gate and blocker counts

## Exclusions

Daily Opportunity exclusions are now explicit and bounded by default:

- `exclusion_counts` gives aggregate reasons.
- `exclusion_samples` includes up to 10 compact examples.
- `include_exclusions=true` returns up to 100 compact exclusions.

Action-limit truncation is represented as an exclusion with `exclusion_code=action_limit`.

## API Support

`GET /api/strategies/<strategy_id>/rows?row_id=<row_id>` can return a specific persisted row. This supports `strategy_row_url` provenance links from Daily Opportunity without requiring full legacy snapshots.

`GET /api/strategies/schema` exposes the current, minimum-supported, and semantic field versions.

## Safety Checks

- Forward Factor remains dry-run.
- Forward Factor rows remain excluded from Daily Opportunity.
- No broker write path was added.
- No trade execution path was added.
- Legacy verdict inference remains labeled and counted.

## Carry-Forward

Patch 30J intentionally does not solve:

- SBUX missing put lifecycle row.
- Account alias deduplication.
- Double-calendar parent grouping.
- Broker raw log masking beyond prior patches.
