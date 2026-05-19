# Future Feature Backlog

These ideas are intentionally parked here so the current build can focus on API-safe development and the upcoming Tradier integration.

## Portfolio Gap + New Stock Suggestions

Goal: suggest new stocks to research based on portfolio gaps, sector/theme coverage, quality, momentum, and risk fit.

Possible future inputs:

- Company sector and industry
- Market cap
- Country/exchange
- Peer companies
- Revenue growth
- Margins and profitability
- Debt and dilution risk
- Analyst trends
- Earnings surprise/guidance
- Price momentum and trend data
- Relative strength vs QQQ/SPY

Possible future outputs:

- Overweight sectors/themes
- Underweight sectors/themes
- Missing exposure buckets
- Stronger peers to research
- Watchlist candidates
- Avoid/low-quality names

Example future recommendation:

```text
Research Bucket: Cybersecurity / infrastructure software
Reason:
- Current portfolio is heavy in AI/semis, fintech, quantum, and energy infrastructure.
- Limited direct cybersecurity or enterprise software exposure.
- Screen for profitable growth names with positive 6-12 month relative strength.
```

## Sector / Theme Buckets

Potential buckets:

- AI / semiconductors
- Mega-cap cloud platforms
- Robotics / automation
- Fintech
- Quantum / speculative compute
- Energy infrastructure
- Nuclear / clean energy
- Consumer brands
- Banking / financials
- Cybersecurity
- Healthcare innovation
- Defense tech

## Important Constraint

Do not implement this until the app has reliable market/profile data and Tradier integration is underway. Keep the immediate roadmap focused on:

1. Dev mode and provider budget controls
2. Tradier provider
3. Options-chain data model
4. Earnings calendar spread scanner
