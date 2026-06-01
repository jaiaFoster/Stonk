# Documentation Index

This folder is a patch history plus a few forward-looking design notes. Treat newer docs and explicit product guardrails as the source of truth when older patch notes disagree.

## Current Product Rules

- The app is read-only decision support. It must not place, modify, or close trades.
- Active options/calendar trades should be broker/provider detected.
- Manual trade tracking, manual lifecycle tracking, and user-entered cost basis are out of scope.
- Research routes are allowed only when stateless and non-persistent.
- Calendar candidates should use final verdicts before any user-facing `PASS` label.

## Current High-Value Docs

- `calendar_verdict_hold_through_research_v1.md` - final verdicts, hard fails, trade type, hold-through scoring, diagnostic research.
- `options_lifecycle_accuracy_v1.md` - broker-detected lifecycle accuracy and Robinhood average-price normalization.
- `robinhood_calendar_auto_detect_v1.md` - automatic open calendar detection and disabled manual routes.
- `calendar_ranking_backtest_v1.md` - ranking gate and eligibility mini-backtest rule.
- `daily_opportunity_engine_v1_megapatch.md` - top-level action ordering and daily report intent.
- `mobile_friendly_ui_v1.md` - prior UI constraints before a larger redesign.

## Historical / Superseded Docs

- `pipeline_finalization_trade_memory_v1.md` is deprecated. Keep the finalization-order lesson; do not revive manual trade memory.
- `unified_calendar_trade_engine_v1.md` has a superseded trade-memory note. Current lifecycle accuracy should come from broker-detected data.

## UI Overhaul Readiness Checklist

- Keep report data objects stable: `daily_opportunity`, `unified_calendar_trade_engine`, `calendar_ranking`, `earnings_mini_backtest`, and `calendar_lifecycle`.
- Preserve priority order: active calendar lifecycle, current holdings, unified stock-add list, portfolio gaps, debug/provider sections.
- Add or update tests when changing verdict, ranking, lifecycle, or Daily Opportunity behavior.
- Do not couple the new UI directly to raw scanner verdicts.
