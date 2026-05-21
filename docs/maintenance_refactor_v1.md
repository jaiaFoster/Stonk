# Maintenance + Refactor v1

This patch performs a larger maintenance pass after the Daily Opportunity Engine work.

## Included

- Adds structured pipeline status tracking.
- Ensures report formatting happens after all strategy modules finish.
- Adds `/config-check?token=...` for redacted Railway/debug diagnostics.
- Converts root `config.py` into a shim to avoid drift from `app/config.py`.
- Moves shared report CSS/helpers into `app/services/report_assets.py`.
- Adds pipeline helper modules so `analysis_service.py` focuses on orchestration.
- Moves Daily Opportunity Engine to the top of the report.
- Adds a visible Pipeline Status report section.
- Collapses Full Advisor Payload and Run Log by default.
- Rewrites README.md for the current modular architecture.

## Why

The app is now functional but has many strategy modules. The next bottleneck is trustworthiness and maintainability: knowing which modules ran, reducing report clutter, and making Railway config easier to verify.

## Rollback

This patch is behavior-preserving from the outside: `/run`, `/run/status`, `/run/result`, and `/health` remain. The new `/config-check` endpoint is additive.
