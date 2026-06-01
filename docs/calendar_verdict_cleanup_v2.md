# Calendar Verdict Cleanup v2

Focused cleanup before the UI overhaul.

## Fixes

- Trade-type classification is now earnings-session aware.
  - BMO: same-day short expiration includes event risk.
  - AMC: same-day short expiration generally expires before the release and does not include event risk.
  - Unknown/unconfirmed session stays watch-only by default.
- Pre-earnings financing / long-vol structures no longer display as true earnings IV-crush calendars.
- Front/event-leg calendars that include earnings are no longer generically labeled `NOT AN EARNINGS CALENDAR`; if the front leg is too far after earnings, they are `INVALID FOR STRATEGY`.
- Ranking criterion text now shows the correct failed operator, such as `54.5% > 15% limit` and `27 < 50 minimum`.
- Broker-detected active calendars can fetch underlying equity quotes so moneyness and assignment-risk reasoning can populate even when the underlying stock is not held.
- Daily Opportunity now sorts active calendar lifecycle rows above stock-add ideas.
- No-proposed-spread rows now expose a human-readable main blocker.

## New / Relevant Config

```text
CALENDAR_TRUE_IV_FRONT_MAX_DAYS_AFTER_EVENT=7
CALENDAR_PRE_EARNINGS_FINANCING_CAN_PASS=false
CALENDAR_UNKNOWN_TIMESTAMP_CAN_PASS=false
DAILY_OPPORTUNITY_PRIORITIZE_ACTIVE_CALENDARS=true
CALENDAR_LIFECYCLE_FETCH_UNDERLYING_QUOTES=true
```

## Product Guardrails

- No manual trade entry.
- No manual position entry.
- No manual lifecycle tracking.
- No persistent manual trade memory.
- Research route remains stateless and read-only.
