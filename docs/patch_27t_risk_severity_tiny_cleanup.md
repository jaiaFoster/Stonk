# Patch 27T - Risk Severity and Tiny Position Cleanup

Patch 27T separates actionable risk from missing data, tiny leftovers, and
informational holds.

Risk rows use:

- `URGENT_RISK`
- `MATERIAL_REVIEW`
- `DATA_INCOMPLETE`
- `CLEANUP`
- `NO_ACTION_HOLD`

Configurable tiny-position defaults:

```text
TINY_POSITION_VALUE_THRESHOLD=50
TINY_POSITION_PORTFOLIO_PCT_THRESHOLD=0.5
```

Shell risk count and Daily Opportunity include only urgent/material risk. Tiny
tracking or leftover positions become cleanup rows. Missing-metric rows become
data incomplete. Full risk detail preserves all classified rows.

## Scope

- Risk classification and display/count semantics only.
- No strategy scoring changes.
- No options strategy changes.
- No trade execution.
