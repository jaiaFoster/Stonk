# Unified Calendar Engine Cleanup + Secondary Earnings Source v1

This patch makes two targeted changes:

1. **Report cleanup**
   - The main report now shows one calendar section: `Unified Calendar Trade Engine v1`.
   - The older lower-level calendar sections are no longer rendered as separate report sections by default:
     - Earnings Timestamp Provider v1
     - Earnings Trade Discovery v1
     - Tradier Quote / Options Snapshot
     - Calendar Spread Screener v1
     - Earnings Calendar Strategy v1
     - Open Options Position Detector v1
     - Calendar Lifecycle Check v1
   - Those modules still run underneath the hood and still feed the unified engine.
   - Run logs remain available for debugging.

2. **Secondary earnings provider**
   - Adds Alpha Vantage earnings calendar as an optional secondary source.
   - Finnhub remains the default primary source.
   - If `ALPHA_VANTAGE_API_KEY` is configured, the app can merge Finnhub and Alpha Vantage events.
   - Alpha Vantage dates are treated as unconfirmed session timestamps because its earnings-calendar CSV provides expected report dates but not before-open/after-close timing in this v1 integration.

## New optional Railway variables

```text
ALPHA_VANTAGE_API_KEY=your_alpha_vantage_key
EARNINGS_PROVIDER_ORDER=finnhub,alphavantage
EARNINGS_MERGE_PROVIDER_EVENTS=true
ALPHA_VANTAGE_EARNINGS_HORIZON=3month
REPORT_SHOW_CALENDAR_DEBUG_SECTIONS=false
```

`REPORT_SHOW_CALENDAR_DEBUG_SECTIONS` is included for future debugging, but the default user-facing report is now intentionally cleaner.

## Expected logs

```text
ALPHA_VANTAGE_API_KEY set: True
EARNINGS_PROVIDER_ORDER: ['finnhub', 'alphavantage']
EARNINGS_MERGE_PROVIDER_EVENTS: True
Fetching Earnings Trade Discovery v1 universe; providers=['finnhub', 'alphavantage']; ...
Unified Calendar Trade Engine v1 produced ...
```

## Notes

Alpha Vantage can expand the earnings universe, but it does not solve optionability/liquidity by itself. The unified calendar engine should still filter/reject names with no usable Tradier expirations/chains.
