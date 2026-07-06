# Universal Strategy Observation Journal Design

Status: design stub only. Patch 29G adds no database migration.

The market-data cache stores facts such as quotes, candles, chains, and earnings events. A future universal strategy journal should separately store what each strategy inferred from those facts, which gates passed, the verdict produced, and later outcomes.

## Proposed observations

`strategy_observations` should include run and strategy identity, ticker, verdict/tier, scores, positive/actionable/live flags, blocker, gate and metric JSON, normalized earnings trust fields, structure JSON, market snapshot references, option-chain references, and the original compact row.

The normalized earnings fields introduced in Patch 29G are intended to survive unchanged: `earnings_trust_label`, `earnings_date`, `earnings_time`, `earnings_source_count`, `earnings_sources_seen`, and `earnings_source_conflict`.

## Proposed outcomes

`strategy_observation_outcomes` should reference an observation and record check time, then/current underlying and option values, estimated strategy return, favorable/adverse excursion, outcome label, and notes.

## Boundaries

- Do not duplicate raw market-data storage.
- Do not create manual trade tracking.
- Do not infer fills or outcomes without point-in-time evidence.
- Keep the existing Forward Factor journal unchanged until a deliberate migration is approved.
