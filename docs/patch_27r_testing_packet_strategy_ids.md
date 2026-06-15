# Patch 27R - Testing Packet and Strategy ID Discovery

Patch 27R makes post-deploy QA compact and removes strategy-ID guessing.

## Provider-Free Diagnostics

Token-protected endpoints:

```text
/api/dev/strategy-ids
/api/dev/testing-packet
```

Strategy discovery exposes canonical IDs, display names, aliases, enabled and
dry-run state, counts, last run ID, and supported detail paths.

The testing packet uses stored report state only. It includes deploy/run
metadata, profiles, freshness, lifecycle and Daily Opportunity summaries,
bounded strategy pass/watch/fail samples, provider caveats, portfolio-gap
summary, endpoint-health checks, Forward Factor dry-run exclusion, and trade
execution disabled confirmation.

It excludes full holdings, raw provider payloads, chains, full logs, tokens,
and provider refreshes.

Wrong strategy IDs return a useful `valid_strategy_ids` list.

## Scope

- No provider fetch changes.
- No strategy, ranking, Daily Opportunity, lifecycle, or UI changes.
- No trade execution.
