# Lifecycle Backlog Cleanup v2

This patch grabs several high-value roadmap items before the larger UI overhaul.

## What changed

### Active calendar lifecycle detail

- Attaches the best known underlying stock price to detected broker calendars.
- Uses Robinhood stock-position prices when Tradier quote coverage is limited in dev mode.
- Calculates short-leg moneyness, distance to strike, ITM/OTM status, assignment risk, short-leg intrinsic/extrinsic value, and rough net Greeks when quote Greeks are available.
- Adds a concise decision summary and lifecycle priority score.

### Daily opportunity priority

- Active calendar lifecycle alerts now appear in the Daily Opportunity Engine.
- Urgent active calendars rank above ordinary stock-add candidates.
- Daily Opportunity calendar counts now include active-calendar alerts.

### Unified calendar engine

- Active calendar score now means daily-review priority, not attractiveness.
- Urgent/cut/exit active trades rank high because they need attention.
- Open-calendar value summaries now include underlying price, short moneyness, and assignment risk.

### Config check

- `/config-check` now exposes calendar lifecycle assignment-DTE and near-money thresholds.

### Railway hardening

- Adds `railway.toml` with a Gunicorn start command to help Railway avoid launching Flask's dev server.

## New or relevant variables

```text
CALENDAR_LIFECYCLE_ASSIGNMENT_DTE=3
CALENDAR_LIFECYCLE_NEAR_MONEY_PCT=2
CALENDAR_LIFECYCLE_TAKE_PROFIT_PCT=50
CALENDAR_LIFECYCLE_STOP_LOSS_PCT=-35
```

## Expected PDD improvement

The PDD active calendar should show:

- underlying price source, ideally `robinhood_position`
- short moneyness against the 98 call strike
- distance to strike in dollars and percent
- short ITM/OTM status
- assignment risk level
- short-leg extrinsic value
- Daily Opportunity row for the active calendar if it needs urgent review
