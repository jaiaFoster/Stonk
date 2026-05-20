# Earnings Calendar Strategy v1

This patch adds an earnings-aware strategy layer on top of the existing Calendar Spread Screener v1.

## Purpose

The previous scanner could find structurally valid long call calendars, but it did not know whether the candidate actually fit an earnings-calendar setup.

This patch keeps the scanner read-only and adds a separate evaluator that checks each candidate against the Earnings Timestamp Provider v1 output.

## What it classifies

For each calendar candidate, the strategy checks:

- Does the ticker have an earnings event?
- Is the earnings timestamp confirmed?
- Is earnings before the front expiration?
- Is earnings between the front and back expirations?
- Does the short leg span the earnings event?
- Does the back leg capture the event?
- Is earnings today or very soon?
- Are liquidity and debit still acceptable?

## Preferred earnings-calendar structure

The preferred simple structure for v1 is:

- short front leg expires before earnings
- long back leg expires after earnings
- same strike and option type
- acceptable bid/ask width
- acceptable volume and open interest
- manageable debit

## Important actions

- `EARNINGS CALENDAR CANDIDATE`: candidate has an earnings-aware structure worth watching.
- `URGENT REVIEW / EARNINGS SOON`: event is too close for blind entry.
- `AVOID / SHORT LEG EVENT RISK`: short front leg spans the earnings event.
- `REGULAR CALENDAR ONLY`: candidate may be a valid calendar, but not an earnings calendar.
- `MANUAL REVIEW / TIMESTAMP NEEDED`: no usable earnings timestamp.

## New environment variables

All are optional:

```text
EARNINGS_CALENDAR_STRATEGY_ENABLED=true
EARNINGS_CALENDAR_URGENT_DTE=1
EARNINGS_CALENDAR_PREFERRED_BONUS=8
EARNINGS_CALENDAR_UNKNOWN_TIMESTAMP_SCORE_CAP=60
EARNINGS_CALENDAR_UNCONFIRMED_SCORE_CAP=70
EARNINGS_CALENDAR_SHORT_SPANS_EVENT_SCORE_CAP=55
```

## Current limitations

This is still read-only. It does not place orders, persist trades, or replace live order-ticket review. It does not yet do multi-strike/multi-expiration optimization beyond the candidate list produced by Calendar Spread Screener v1.

## Expected log lines

```text
Running Earnings Calendar Strategy v1...
Earnings Calendar Strategy v1 evaluated X candidate(s); Y preferred setup(s), Z urgent review.
Earnings Calendar Strategy v1 produced X evaluation(s), Y preferred, Z urgent-review.
```
