# Mega Patch: Calendar Cleanup + Secondary Earnings + Portfolio Gap v1

## What this patch does

This patch combines three changes:

1. Keeps Unified Calendar Trade Engine v1 as the main visible calendar-trading section.
2. Keeps the Alpha Vantage secondary earnings provider / merged earnings calendar logic.
3. Adds Portfolio Gap / Sector Suggestions v1.

## Portfolio Gap / Sector Suggestions v1

This is stock-focused and separate from the earnings-calendar engine.

It classifies current holdings and watchlist names into rough sector/theme buckets, then compares current exposure to configurable aggressive-growth target weights.

### User decisions implemented

- ETFs such as SOXL count as both sector exposure and leveraged/speculative risk exposure.
- Crypto is its own risk bucket, not an equity sector bucket.
- The app mostly reinforces winning macro sectors, not just what is winning inside the user's current portfolio.
- Underweight means below a configurable target weight.
- Suggestions can say `consider adding` when the score is high enough.

## Default target profile

The default target profile is `aggressive_macro_growth`.

Default core target weights:

- AI / Semiconductors: 18%
- Mega-cap Tech / Cloud: 18%
- Software / Fintech: 12%
- Energy / Utilities / Infrastructure: 12%
- Healthcare / Biotech: 10%
- Industrials / Defense / Robotics: 10%
- Financials: 8%
- Consumer / Retail: 7%
- International / ADR: 5%

Default risk targets / caps:

- Crypto / Digital Assets: 5%
- Speculative / High Beta: 12%
- Leveraged ETFs: 4%
- Single-Name Max: 15%

These are configurable through Railway environment variables.

## New optional Railway variables

```text
PORTFOLIO_GAP_ENABLED=true
PORTFOLIO_GAP_TARGET_PROFILE=aggressive_macro_growth
PORTFOLIO_GAP_CORE_TARGETS=AI / Semiconductors:18,Mega-cap Tech / Cloud:18,Software / Fintech:12,Energy / Utilities / Infrastructure:12,Healthcare / Biotech:10,Industrials / Defense / Robotics:10,Financials:8,Consumer / Retail:7,International / ADR:5
PORTFOLIO_GAP_MACRO_WINNING_BUCKETS=AI / Semiconductors,Mega-cap Tech / Cloud,Energy / Utilities / Infrastructure,Industrials / Defense / Robotics,Healthcare / Biotech
PORTFOLIO_GAP_RISK_TARGETS=Crypto / Digital Assets:5,Speculative / High Beta:12,Leveraged ETFs:4,Single-Name Max:15
PORTFOLIO_GAP_MAX_SUGGESTIONS=10
PORTFOLIO_GAP_MIN_SUGGESTION_SCORE=55
PORTFOLIO_GAP_INCLUDE_ALREADY_HELD=true
```

## Notes

This is intentionally rule-based v1. Later versions should replace static ticker maps with company-profile APIs, live sector relative-strength data, and a macro-regime module.
