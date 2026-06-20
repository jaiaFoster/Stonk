# TKT-040: Broker Data Flow — Ground Truth

## 1. Entry Points

### POST /api/user/run (per-user personalization)
```
app/api/user.py → POST /api/user/run
  → app/services/personalization.py::run_personalization()
    → decrypt_robinhood_password()
    → fetch_all_with_lock(user_id, rh_username, rh_password)
        [robinhood_queue.py — acquires global _rh_lock]
      → broker_provider.py::RobinhoodCredentialProvider.fetch_positions_with_options()
        → r.login(store_session=True, pickle_path=DATA_DIR, pickle_name=f"_user_{user_id}")
        → discover_accounts()  → r.profiles.load_account_profile(dataType="results")
        → per account: r.account.get_open_stock_positions(account_number=X)
        → r.crypto.get_crypto_positions()
        → per account + None: r.options.get_open_option_positions(account_number=X)
        → r.logout()  [finally block]
      ← returns (stock_positions, raw_option_positions, discovered_accounts)
    → save_user_broker_accounts(user_id, discovered_accounts)
    → detect_from_robinhood_raw_positions(raw_option_positions)
    → save_user_positions(user_id, run_id, positions)           # stocks to user_positions
    → save_user_option_positions(user_id, run_id, calendars)    # calendars to user_option_positions
    → save_user_option_positions_to_positions(user_id, run_id, ...) # options to user_positions
    → build_user_daily_opportunity(...)
    → save_user_daily_opportunity(user_id, run_id, daily_opp)
```

### Core pipeline (server-side shared credentials)
```
app/main.py → POST /api/run
  → app/services/analysis_service.py::run_portfolio_pipeline()
    → portfolio_service.py::get_portfolio_positions_with_status()
      → robinhood_provider.py::get_positions_with_status() → get_positions()
        → login_with_retry()  [uses config.ROBINHOOD_USERNAME/PASSWORD]
        → discover_accounts()
        → per account: r.account.get_open_stock_positions(account_number=X)
        → r.crypto.get_crypto_positions()
        → r.logout()  [finally block]
      ← positions stored in report snapshot, NOT in user_positions table
```

### Standalone options fetch (robinhood_provider.py)
```
get_open_option_positions()
  → login_with_retry()
  → discover_accounts()
  → _option_accounts_to_scan(discovered_accounts=...)
  → per account + None: r.options.get_open_option_positions(account_number=X)
  → r.logout()  [finally block]
```

## 2. Storage Destinations

| Data | Table | Written by |
|------|-------|-----------|
| Stock positions | `user_positions` | `save_user_positions()` |
| Options (normalized) | `user_positions` (position_type='options') | `save_user_option_positions_to_positions()` |
| Calendar spreads | `user_option_positions` | `save_user_option_positions()` |
| Discovered accounts | `user_broker_accounts` | `save_user_broker_accounts()` |
| Daily opportunity | `user_daily_opportunity` | `save_user_daily_opportunity()` |
| Core report snapshot | JSON on disk | `report_snapshot_service` |

## 3. Account Discovery

`discover_accounts()` → `r.profiles.load_account_profile(dataType="results")`
→ hits `https://api.robinhood.com/accounts/?default_to_all_accounts=true`

Returns list of `{"account_number": "...", "account_type": "..."}` dicts.
Classification uses `_classify_account_type()` which checks `brokerage_account_type`,
`account_type`, `type`, and `is_pinnacle_account` fields.

Fallback: if discovery returns empty/fails, fetch from default account (no account_number param).

## 4. Serialization & Locking

- `robinhood_queue.py` owns `_rh_lock` (global `threading.Lock`)
- All per-user broker calls go through `fetch_all_with_lock()` (timeout: `RH_QUEUE_TIMEOUT_SECONDS`, default 120s)
- Core pipeline does NOT go through the queue — separate session via `login_with_retry()`
- Per-user pickle: `DATA_DIR/robinhood_user_{user_id}.pickle`
- Core pickle: default robin_stocks location

## 5. Legacy / Dead Code Paths

### `broker_provider.py::RobinhoodCredentialProvider.fetch_positions()` (line 151)
**Status: DEAD CODE — no callers.**
- Defined but never called. Was the original pre-options stock-only fetch.
- Does NOT use `discover_accounts()` — fetches default account only.
- No crypto, no options.
- `account_type` set to `pos.get("account_number") or "default"` — wrong field semantics.
- **Risk: LOW** — unreachable. Could be removed for cleanliness.

### `robinhood_queue.py::fetch_with_lock()` (line 40)
**Status: DEAD CODE — no callers.**
- Defined but never called. Was the serialization wrapper for `fetch_positions()`.
- Replaced by `fetch_all_with_lock()` which calls `fetch_positions_with_options()`.
- **Risk: LOW** — unreachable. Could be removed.

### `robinhood.py` (root-level wrapper)
**Status: CLEAN.**
- Re-exports: `get_positions`, `login_with_retry`, `discover_accounts`, `MAX_LOGIN_RETRIES`, `RETRY_INTERVAL_SECONDS`.
- No hardcoded account map. No legacy patterns.

## 6. Credential Security

- Per-user Robinhood passwords: Fernet-encrypted at rest (`ROBINHOOD_ENCRYPTION_KEY`)
- Decrypted only inside `run_personalization()`, `del`-ed in finally block
- All error messages redact `password_decrypted` before print
- Core pipeline: credentials from env vars (`config.ROBINHOOD_USERNAME/PASSWORD`)
- `r.logout()` always in finally block — verified in all three paths

## 7. Risk Assessment

| Risk | Severity | Notes |
|------|----------|-------|
| Core run + per-user concurrent session | Low | Different pickle files, session-based auth works concurrently |
| `_rh_lock` doesn't protect core run | Low | Core run is scheduled, not on-demand |
| Dead `fetch_positions()` / `fetch_with_lock()` | Low | Unreachable — cleanup only |
| Option dedup relies on `id` field | Low | Fallback to `option_id` then `id(obj)` |
