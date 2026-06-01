# Calendar Verdict + Hold-Through + Research Tools v1

This patch keeps Algo Stock Advisor read-only and broker-aware while improving calendar decision quality.

## What Changed

- Added a final calendar verdict layer with hard-fail overrides before any candidate can display as `PASS`.
- Added explicit `trade_type` classification for earnings IV-crush calendars, pre-earnings financing/long-vol structures, invalid structures, and unknown timestamp cases.
- Promoted Calendar Ranking v2 and hard-fail verdicts above raw scanner labels in unified rows, ranking rows, and Daily Opportunity.
- Added account/debit guardrails using detected portfolio market value when available.
- Added hold-through scoring fields for broker-detected active calendar spreads.
- Added diagnostic mini-backtest modes for failed candidates without making them eligible.
- Added stateless research route: `/research/calendar-backtest?token=...&ticker=AVGO`.

## Safety Rules

- No manual trade forms were reintroduced.
- No manual trade memory is used as source of truth.
- The research route does not persist, create, alter, close, or track trades.
- Eligibility backtests still run only for fully-qualified candidates.
- Diagnostic output can explain failed candidates, but it cannot turn a failed candidate into an entry.

## Key Fields

Calendar candidates and report rows can now expose:

- `final_verdict`
- `trade_type`
- `trade_type_label`
- `main_blocker`
- `main_reason`
- `backtest_status`
- `account_risk_status`
- `account_risk_warning`
- `raw_scanner_verdict`

Active calendar lifecycle rows can now expose:

- `hold_through_score`
- `hold_through_action`
- `hold_through_reasons`
- `hold_through_blockers`
- `historical_move_warning`

## Expected Behavior

ASO-style liquidity failure:

- Final verdict is fail/do-not-enter, commonly `FAIL / UNTRADEABLE SPREAD`, `FAIL / NO OPEN INTEREST`, or `FAIL / NO LIVE LIQUIDITY`.
- Eligibility backtest is skipped.
- Diagnostic mode explains that execution quality is the main blocker.
- Daily Opportunity does not show it as a possible entry.

PDD-style active trade:

- Positive current P/L can help the score but does not dominate.
- Large historical earnings movement, when attached to the row, lowers hold-through support and can produce `CONSIDER CLOSING BEFORE EARNINGS`.

AVGO-style candidate:

- Candidate can pass only after final verdict, trade type, ranking, liquidity, IV, timestamp, and account-risk gates allow it.
- Active broker-detected lifecycle behavior remains intact.
