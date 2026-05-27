# Robinhood Calendar Auto-Detect v1

This patch pivots calendar tracking away from manual trade entry and toward automatic broker-position detection.

## Product rule

Algo Stock Advisor is a read-only viewing/discovery tool. Manual trade tracking, manual tracing, and manual entry workflows are intentionally out of scope.

## What changed

- Adds automatic Robinhood open-option position fetching through `robin_stocks.robinhood.options.get_open_option_positions`.
- Normalizes Robinhood option positions into the same option-leg shape used by the Tradier detector.
- Builds OCC symbols from Robinhood option metadata so Tradier quotes can reprice the legs when available.
- Detects long calendar spreads across both Tradier and Robinhood positions.
- If Robinhood omits explicit long/short side, the detector can infer front-short/back-long calendar structure from same ticker/type/strike legs with different expirations.
- Disables manual `/trades` entry/close/delete routes.
- Removes Trade Memory from the main report and root menu.

## New environment variables

```text
ROBINHOOD_OPTIONS_DETECTOR_ENABLED=true
ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS=
ROBINHOOD_OPTIONS_SCAN_DEFAULT_ACCOUNT=true
ROBINHOOD_OPTIONS_DEFAULT_ACCOUNT_LABEL=Investing
ROBINHOOD_OPTIONS_MAX_POSITIONS=50
ROBINHOOD_OPTIONS_INFER_CALENDARS=true
```

Leave `ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS` blank. Blank/default now scans Robinhood's default options account, normally shown in the app as `Investing`, plus the configured IRA accounts. You can set `ROBINHOOD_OPTIONS_ACCOUNT_NUMBERS` only if you intentionally want to restrict scanning.

## Expected logs

```text
Robinhood Open Options Detector: X normalized option position(s) across Y account(s).
Open Options Position Detector v2: X total position(s), Y option leg(s), Z calendar spread(s) detected.
Calendar Lifecycle Check v1 evaluating Z open calendar(s).
Unified Calendar Trade Engine v1 produced ... Z open-trade row(s).
```

## Important note

This is read-only. It does not place, modify, close, or manually store trades.


## v1.1 note: Investing/default account

The first auto-detect version scanned the hard-coded IRA account map. That missed calendars opened in the regular Robinhood `Investing` account. This patch calls `get_open_option_positions()` with no `account_number` first, which targets the default Robinhood options account, then also scans configured IRA accounts.
