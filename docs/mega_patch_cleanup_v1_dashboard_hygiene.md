# Mega Patch Cleanup v1: Dashboard Hygiene Before Strategy 2

This patch is a view-model/UI cleanup pass for the Muted Black Terminal dashboard. It does not implement Strategy 2 and does not change trade execution behavior.

## Cleanup Items

- Potential Adds keeps actionable add candidates visually separate from Watch / Research and Risk Review rows.
- Avoid/reduce/fail/risk-sourced rows are treated as risk controls, not add ideas.
- Zero-value positions are filtered from main dashboard sections and counts, while raw debug output remains available.
- Provider chips distinguish Finnhub credentials from candle usability and show dev-limited data scope.
- Macro context includes a scope caveat so limited market-data subsets are not presented as full macro authority.
- Active Calendar Lifecycle has a clear zero-active empty state and a visible Refresh Active Trades button.
- Top counters use cleaned section membership and remain anchor-linked.
- Portfolio/macro buckets are expandable and show attached or fallback ticker mappings.
- Monitor payload copy uses the robust copy/toast/fallback path.

## Preserved

- No trade placement, order sending, manual trade entry, manual trade tracking, or manual position entry.
- Active options/calendar trades remain broker-detected only.
- Strategy scoring logic is unchanged.
- Strategy 2 is not implemented in this patch.
