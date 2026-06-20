# TKT-040: Broker Data Flow Audit

## Data Flow Overview

### Core Run Path (robinhood.py -> robinhood_provider.py)

```
robinhood.py
  -> login_with_retry()          # Shared config credentials
  -> discover_accounts()         # Dynamic account enumeration (TKT-043)
  -> get_positions()             # Per-account stock + crypto fetch
     -> get_open_stock_positions(account_number=X)  per discovered account
     -> get_crypto_positions()
  -> r.logout()                  # Always in finally block
```

- Uses `config.ROBINHOOD_USERNAME` / `config.ROBINHOOD_PASSWORD`
- Pickle: `robinhood_session.pickle` in default location
- Results stored in report snapshot (not user_positions)

### Per-User Run Path (personalization.py -> robinhood_queue.py -> broker_provider.py)

```
personalization.py::run_personalization()
  -> decrypt_robinhood_password()
  -> fetch_all_with_lock()                    # robinhood_queue.py — global lock
     -> BrokerCredentialProvider.fetch_positions_with_options()
        -> r.login(pickle_path=DATA_DIR, pickle_name=f"_user_{user_id}")
        -> discover_accounts()                # TKT-043: dynamic account enumeration
        -> get_open_stock_positions(account_number=X)  per discovered account
        -> get_crypto_positions()
        -> get_open_option_positions(account_number=X)  per discovered account + default
        -> r.logout()                         # Always in finally block
  -> save_user_broker_accounts()              # TKT-043: persist discovered accounts
  -> detect_from_robinhood_raw_positions()    # open_options_service
  -> save_user_positions()                    # Stock positions to DB
  -> save_user_option_positions()             # Calendar spreads to DB
  -> save_user_option_positions_to_positions()# Options to user_positions table
  -> build_user_daily_opportunity()           # Personalized signals
```

- Uses per-user encrypted credentials from `users.robinhood_password_encrypted`
- Pickle: `robinhood_user_{user_id}.pickle` in `DATA_DIR`
- Global `_rh_lock` prevents concurrent broker sessions

### Standalone Options Fetch (robinhood_provider.py::get_open_option_positions)

```
get_open_option_positions()
  -> login_with_retry()
  -> discover_accounts()
  -> _option_accounts_to_scan(discovered_accounts=...)
  -> get_open_option_positions(account_number=X)  per account + default
  -> r.logout()
```

- Used by direct API calls; separate from personalization flow
- Same shared credentials as core run

## Serialization

- `robinhood_queue.py` owns a global `threading.Lock` (`_rh_lock`)
- All per-user broker calls go through `fetch_with_lock()` or `fetch_all_with_lock()`
- Timeout: `RH_QUEUE_TIMEOUT_SECONDS` (default 120s)
- Core run does NOT go through the queue — it uses its own login via `login_with_retry()`

## Account Discovery (TKT-043)

Prior to TKT-043, a hardcoded `ACCOUNT_MAP` dict mapped account numbers to labels.
This was factually wrong (labels were incorrect) and missed any new accounts.

Dynamic discovery uses `r.profiles.load_account_profile(dataType="results")` which hits
`https://api.robinhood.com/accounts/?default_to_all_accounts=true`. Each account dict
contains `account_number`, `type`, and other metadata.

Fallback: if discovery returns empty, fetch from default account (no account_number param).

## Credential Security

- Robinhood passwords: encrypted at rest with Fernet (`ROBINHOOD_ENCRYPTION_KEY`)
- Decrypted only inside `run_personalization()`, deleted immediately after fetch
- Never logged: all error messages redact `password_decrypted` before print
- Pickle session files: per-user, stored in `DATA_DIR`

## Audit Findings

1. **No parallel code paths conflict**: Core run and per-user run use separate login sessions.
   Core run uses config credentials; per-user uses encrypted per-user credentials.
   The `_rh_lock` serializes per-user fetches but does NOT protect the core run path.
   This is acceptable because the core run operates on a fixed schedule, not on-demand.

2. **Pickle path correctness**: Verified in TKT-035 Round 4. robin_stocks constructs filename as
   `"robinhood" + pickle_name + ".pickle"`. Per-user: `pickle_path=DATA_DIR, pickle_name=f"_user_{user_id}"`.
   Core run: `pickle_name="robinhood_session"` in default location.

3. **Account coverage**: With dynamic discovery, ALL accounts are covered — no hardcoded
   assumptions about which accounts exist. Options fetch includes discovered accounts + default
   (None) as a dedup-safe fallback.

4. **r.logout() always in finally**: Verified in all three paths (core run, per-user, standalone options).

5. **Error classification**: Device approval, rate limiting, and timeout errors are properly
   classified and surfaced to callers. No silent swallowing of broker auth errors.

6. **Race condition on core run vs per-user**: Theoretically possible for both paths to be
   logged into the same Robinhood account simultaneously (core run uses config creds, per-user
   could be the same user). robin_stocks uses session-based auth, so concurrent sessions should
   work. The serialization lock only protects per-user paths from each other.
