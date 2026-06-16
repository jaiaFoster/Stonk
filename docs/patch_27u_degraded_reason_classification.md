# Patch 27U - Degraded Reason Classification

Patch 27U classifies degraded run reasons from metadata already stored by the
pipeline. It does not call providers and does not infer beyond available
evidence.

Structured fields:

- `degraded_reason_code`
- `degraded_reason_label`
- `degraded_stage`
- `degraded_provider`
- `degraded_evidence`
- `reason_confidence`

Initial reason codes:

- `ROBINHOOD_APPROVAL_TIMEOUT`
- `ROBINHOOD_AUTH_UNAVAILABLE`
- `BROKER_DATA_UNAVAILABLE`
- `PROVIDER_PARTIAL_FAILURE`
- `MARKET_OR_OPTIONS_DATA_UNAVAILABLE`
- `RUN_TIMEOUT_OR_STALE_LOCK`
- `UNKNOWN`

If stored metadata is insufficient, the reason remains `UNKNOWN` and the shell
continues to display `Reason: unknown`.

This patch is diagnostics/display-only. It does not change providers,
strategies, Daily Opportunity, Forward Factor, lifecycle, raw archives, or
execution behavior.
