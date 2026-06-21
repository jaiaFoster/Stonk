# TKT-040: Telemetry Depth Audit

## 1. Current State

### 1a. Advisor Event Telemetry (`app/db/telemetry.py`)

Database: `TELEMETRY_DB_PATH` (SQLite).

**`advisor_events` table:**
| Column | Type | Content |
|--------|------|---------|
| event_type | TEXT | Always `"endpoint_hit"` |
| endpoint | TEXT | Route path (`/api/advisor/positions`, etc.) |
| token_identity | TEXT | `"run_token"` or `sha256:<12chars>` — never raw token |
| run_id_served | TEXT | Run ID from the served snapshot |
| timestamp | TEXT | Auto-generated |

Written by: `log_event()` called from `app/api/advisor.py::_log_event()`. Fire-and-forget, errors swallowed.

Gated by: `config.TELEMETRY_ENABLED`.

**`advisor_feedback` table:**
| Column | Type | Content |
|--------|------|---------|
| ticker | TEXT | Feedback subject |
| run_id | TEXT | Which run the feedback is about |
| action_taken | TEXT | `bought/watched/ignored/rejected` |
| outcome | TEXT | `positive/negative/neutral/pending/null` |
| notes | TEXT | Free text |
| submitted_at | TEXT | Auto-generated |

Written by: `record_feedback()`. Currently unused — no UI or endpoint writes to it.

### 1b. Usage Telemetry (`app/services/usage_telemetry_service.py`)

Database: `USAGE_TELEMETRY_DB_PATH` (SQLite).

**`usage_events` table:**
| Column | Type | Content |
|--------|------|---------|
| event_type | TEXT | Allowlisted: `copy_export`, `dashboard_load`, `detail_request`, `download_export`, `feedback`, `section_close`, `section_open`, `snapshot_request` |
| section | TEXT | Detail section name |
| source | TEXT | Origin label (`cached_dashboard`, `developer_snapshot`, etc.) |
| run_id | TEXT | Run ID context |
| metadata_json | TEXT | Allowlisted key-value pairs |
| created_at | TEXT | ISO timestamp |

Written by: `record_usage_event()` called from `app/main.py::_record_usage()` on dashboard load, snapshot request, detail request, and usage event POST.

Retention: `USAGE_TELEMETRY_RETENTION_LIMIT` (default 1000 rows, FIFO).

**`telemetry_size_profiles` table:**
| Column | Type | Content |
|--------|------|---------|
| run_id | TEXT | Primary key |
| mode | TEXT | `prod`/`dev` |
| status | TEXT | `complete`/`degraded` |
| snapshot_sizes_json | TEXT | Integer sizes of each stored blob/field |
| section_sizes_json | TEXT | Per-section byte sizes from payload profile |
| created_at | TEXT | ISO timestamp |

Written by: `record_snapshot_size_profile()` called from `report_snapshot_service.py::_save()` on every snapshot save.

Retention: `USAGE_TELEMETRY_SIZE_PROFILE_RETENTION_LIMIT` (default 50 rows).

### 1c. Error Logging

All error logging is `print(..., flush=True)` to stdout. ~45 error/traceback print statements across 16 files. No structured error table, no per-user error attribution, no error aggregation.

### 1d. Diagnostic Endpoints

| Endpoint | Auth | Content |
|----------|------|---------|
| `/api/dev/usage-telemetry` | dev token | Usage event counts, detail section breakdown, size budget report |
| `/api/dev/feature-health` | dev token | Telemetry summary (endpoint hits, feedback count) |
| `/api/dev/status` | dev token | App boot time, active job status, run lock |

## 2. What's NOT Currently Captured

| Gap | Impact |
|-----|--------|
| **Per-request error logs** | Errors go to stdout only. No queryable history — once Railway log buffer rotates, they're gone. |
| **User-attributed errors** | Errors print user_id in some places but there's no structured `(user_id, run_id, error_type, traceback, timestamp)` table. Cannot answer "what errors did user X hit last week?" |
| **Request/response audit trail** | No record of what was requested or returned. If a user reports a bad response, there's no way to reproduce what they saw. |
| **Provider call failures** | Robinhood/Tradier/news provider errors print to stdout but aren't aggregated. Cannot answer "how often does Robinhood login fail?" without grepping Railway logs. |
| **Per-user run diagnostics** | `user_runs` table has `error_message` (single string) but no structured failure trace. Multiple sequential errors during a run collapse into one message. |
| **Personalization pipeline timing** | No timing breakdown for discover_accounts vs stock fetch vs options fetch vs analysis. Cannot identify which pipeline stage is slow for a given user. |
| **Feedback table unused** | `advisor_feedback` table exists but nothing writes to it — no UI, no endpoint. Dead schema. |
| **Rate limit observability** | Rate-limited runs are logged via `record_rate_limited_run()` in user_runs, but no aggregate view or alerting. |

## 3. Proposed Design (Not Implemented)

### 3a. Structured Error Journal

```sql
CREATE TABLE IF NOT EXISTS error_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,              -- NULL for core pipeline
    run_id TEXT,
    error_source TEXT NOT NULL,   -- 'robinhood_login', 'tradier_options', 'personalization', etc.
    error_type TEXT NOT NULL,     -- exception class name
    error_message TEXT,           -- first 500 chars of str(exc)
    traceback_hash TEXT,          -- sha256 of traceback for dedup
    pipeline_stage TEXT,          -- 'login', 'discover_accounts', 'stock_fetch', 'options_fetch', etc.
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS error_journal_user ON error_journal (user_id, created_at);
CREATE INDEX IF NOT EXISTS error_journal_source ON error_journal (error_source, created_at);
```

Retention: 5000 rows FIFO (configurable). Write pattern: `try/except` blocks that currently `print()` also call `record_error()` — fire-and-forget, errors swallowed.

### 3b. Pipeline Timing Table

```sql
CREATE TABLE IF NOT EXISTS pipeline_timing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,           -- 'login', 'discover_accounts', 'stock_positions', 'crypto', 'options', 'analysis'
    started_at TEXT,
    completed_at TEXT,
    duration_ms INTEGER,
    row_count INTEGER,            -- positions/accounts/options returned
    created_at TEXT DEFAULT (datetime('now'))
);
```

Retention: 2000 rows FIFO. Write pattern: `_timer` context manager wrapping each pipeline stage in `fetch_positions_with_options()` and `run_personalization()`.

### 3c. Provider Health Aggregation Endpoint

```
GET /api/dev/error-journal-summary
```

Output: error counts by source, most frequent error types, errors per user in last 24h, pipeline stage timing percentiles.

### 3d. Migration Path

1. Add `error_journal` table + `record_error()` function to `app/db/telemetry.py`
2. Instrument 5-10 highest-value `print(...error...)` sites with `record_error()` calls
3. Add `pipeline_timing` table
4. Instrument `fetch_positions_with_options()` and `run_personalization()` with timing
5. Add aggregation endpoint
6. Activate `advisor_feedback` writes from a future UI, or remove the dead table

### 3e. Privacy / Security Constraints

- Error messages MUST be truncated (500 chars max) — Robinhood error messages can contain session tokens
- Traceback dedup via hash only — never store raw tracebacks (may contain decrypted passwords in local scope)
- user_id stored as integer reference, never username
- All tables: fire-and-forget writes, never block request path
- Retention enforced via FIFO deletion on every insert (same pattern as `usage_events`)
