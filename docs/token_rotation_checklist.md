# Token Rotation Readiness Checklist

## Environment Variables Involved

| Env Var | Config Reference | Current Default | Purpose |
|---------|-----------------|-----------------|---------|
| `RUN_TOKEN` | `config.RUN_TOKEN` | None (required) | Authenticates `/run`, `/refresh-market-data`, dashboard access, advisor legacy path |
| `DEV_API_TOKEN` | `config.DEV_API_TOKEN` | None (optional) | Separate token for `/api/dev/*` endpoints. Falls back to `RUN_TOKEN` when unset |
| `LEGACY_DEV_TOKEN_ENABLED` | `config.LEGACY_DEV_TOKEN_ENABLED` | `True` | Master switch: when `False`, legacy token bypass is fully disabled |

## Code Paths That Check `RUN_TOKEN`

### Direct auth (token must equal `RUN_TOKEN`)

| File | Line(s) | Function | Impact of Rotation |
|------|---------|----------|-------------------|
| `app/main.py` | 94-95 | `_valid_run_token()` | Gates `/run`, `/refresh-market-data`, dashboard (`/`). **Callers must update token.** |
| `app/main.py` | 103 | `_valid_dev_token()` | Fallback: `DEV_API_TOKEN or RUN_TOKEN`. Gates all `/api/dev/*` endpoints. |
| `app/api/advisor.py` | 32 | `_valid_token()` | Legacy path: `RUN_TOKEN` directly accepted for `/api/advisor/*`. iOS Shortcuts and Stonk Reporter use this. **Callers must update token.** |
| `app/db/telemetry.py` | 59 | `_token_identity()` | Identity labeling only — labels matching token as `"run_token"`. Cosmetic, no auth impact. |

### Legacy token bypass (`_is_legacy_token` → `DEV_API_TOKEN or RUN_TOKEN`)

| File | Line(s) | Function | Impact of Rotation |
|------|---------|----------|-------------------|
| `app/auth.py` | 35-46 | `_legacy_dev_token()`, `_is_legacy_token()` | Master gate. If `LEGACY_DEV_TOKEN_ENABLED=False`, this entire path is dead. |
| `app/auth.py` | 76 | `require_auth` decorator | Legacy token → synthetic admin user (`id=0`, `username=_legacy_dev`) |
| `app/auth.py` | 120 | `require_admin` decorator | Same legacy bypass path |
| `app/auth.py` | 97 | `require_dev` decorator | Same legacy bypass path |
| `app/main.py` | 109 | `_valid_dev_token()` | Calls `_is_legacy_token()` for dev endpoint access |
| `app/api/advisor.py` | 37 | `_valid_token()` | Calls `_is_legacy_token()` for advisor endpoint access |

### Redaction (token value used to scrub error messages)

| File | Function | Notes |
|------|----------|-------|
| `app/providers/robinhood_provider.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 3 call sites |
| `app/services/open_options_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 6 call sites |
| `app/services/calendar_spread_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 2 call sites |
| `app/services/tradier_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 2 call sites |
| `app/services/earnings_discovery_quality_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 2 call sites |
| `app/services/skew_momentum_vertical_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 1 call site |
| `app/services/market_data_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 1 call site |
| `app/services/watchlist_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 1 call site |
| `app/services/candle_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 1 call site |
| `app/services/earnings_mini_backtest_service.py` | `sanitize_for_log(e, [..., config.RUN_TOKEN])` | 1 call site |
| `app/services/redaction_service.py` | `"RUN_TOKEN"` in redaction list | Pattern-based redaction |
| `app/providers/earnings_provider.py` | `config.RUN_TOKEN` in sanitize list | 1 call site |
| `app/services/analysis_service.py` | `config.RUN_TOKEN` in sanitize list | 1 call site |

These all read `config.RUN_TOKEN` at call time — they'll automatically use the new value after rotation. **No code change needed.**

### Config check / diagnostics (presence-only checks)

| File | Line | Check | Notes |
|------|------|-------|-------|
| `app/services/config_check_service.py` | 45 | `bool(config.RUN_TOKEN)` | Reports whether token is configured, never its value |
| `app/services/pipeline_helpers.py` | 32 | `bool(config.RUN_TOKEN)` | Same — presence check only |
| `app/services/config_check_service.py` | 108 | `bool(config.DEV_API_TOKEN)` | Presence check for DEV_API_TOKEN |

### UI references

| File | Line(s) | Context |
|------|---------|---------|
| `app/main.py` | 1208-1253 | HTML login form: `<label>RUN_TOKEN</label>`, placeholder text, JS alert. **Cosmetic only** — the label tells the user what to paste, doesn't validate. |

## Can `LEGACY_DEV_TOKEN_ENABLED` Be Set to `False`?

### What breaks:
- Any caller using the raw `DEV_API_TOKEN` or `RUN_TOKEN` value as a bearer token to hit `@require_auth`, `@require_admin`, or `@require_dev` endpoints will get 401.
- This affects: `/api/admin/*`, `/api/user/*` (auth decorators), but NOT `/api/advisor/*` (has its own `_valid_token()` which checks `RUN_TOKEN` directly before the legacy path).
- This does NOT affect: `/api/dev/*` endpoints (they use `_valid_dev_token()` which checks `RUN_TOKEN` directly, separate from the legacy path).

### What doesn't break:
- Dashboard (`/`) — uses `_valid_run_token()`, not `require_auth`
- `/run`, `/refresh-market-data` — uses `_valid_run_token()`
- `/api/dev/*` — uses `_valid_dev_token()` which has its own direct `RUN_TOKEN` check
- `/api/advisor/*` — has direct `RUN_TOKEN` check in `_valid_token()` before legacy path
- All user auth via API keys and session tokens — unaffected
- All admin auth via session tokens — unaffected

### Verdict: **SAFE to set `LEGACY_DEV_TOKEN_ENABLED=False`**

The only callers that depend on the legacy token bypass through `require_auth`/`require_admin` are:
1. iOS Shortcuts / Stonk Reporter → these use `/api/advisor/*` which has its own direct `RUN_TOKEN` check that doesn't go through the legacy path
2. Manual curl/testing using the dev token → use a real user API key instead

**Precondition**: Verify no external automation hits `@require_auth`-protected endpoints (like `POST /api/user/run`) with the raw `RUN_TOKEN` value. If any do, they need to be migrated to a real user API key first.

## Rotation Procedure

1. **Generate new token**: `python -c "import secrets; print(secrets.token_hex(32))"`
2. **Update in Railway**: Set new `RUN_TOKEN` env var value
3. **If `DEV_API_TOKEN` is set separately**: Rotate it too (or remove it to use `RUN_TOKEN` fallback)
4. **Update external callers**: iOS Shortcuts, Stonk Reporter, any bookmarked URLs with `?token=...`
5. **Verify**: Hit `/health` (no auth), then `/api/dev/status?token=<new>` (dev auth), then `/api/advisor/snapshot?token=<new>` (advisor auth)
6. **Optional**: Set `LEGACY_DEV_TOKEN_ENABLED=False` after confirming all callers use real user auth or direct `RUN_TOKEN` path
