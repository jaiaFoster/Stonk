# ASA Patch 30D.1 — Payload Consumer Inventory

**Purpose:** Document every active consumer of the `report_summary_json` payload so
that the payload can be safely shrunk without breaking existing readers.

---

## 1. Payload Sources

| Source | Storage column | Typical size (30D baseline) |
|---|---|---|
| `report_summary_json` (hot summary) | `report_snapshots.summary_json` | ~1.0 MB (warning tier) |
| `full_summary_blob` (gzipped full) | `report_snapshots.full_summary_blob` | compressed; not served over HTTP |
| `raw_provider_blob` | `report_snapshots.raw_provider_blob` | redacted by default |

**Target (30D.1):** `report_summary_json` below 750 KB (healthy tier), ideally < 500 KB.

---

## 2. Active Consumers

### 2.1 Dashboard route (`/`, `app/main.py`)
- **Reads:** `report_summary_json` (hot summary) via `repo.load_summary(snapshot, full=False)`
- **What it uses:** positions, recommendations, tradier_snapshot (for format_html)
- **Impact of size reduction:** Safe — only needs compact strategy rows and summary sections

### 2.2 Developer snapshot (`/api/dev/snapshot`, `app/main.py`)
- **Reads:** `report_summary_json` or `full_summary_blob` depending on `?detail=full`
- **What it uses:** compact strategy summaries, daily opportunity, positions overview
- **Impact:** Safe — already filtered by `developer_snapshot_service.py`

### 2.3 Advisor API (`/api/advisor/*`, `app/api/advisor.py`)
- **Reads:** `full_summary_blob` (always `include_full=True`)
- **What it uses:** daily opportunity actions, open positions, calendar lifecycle
- **Impact of hot summary shrinking:** None — advisor reads full blob

### 2.4 Strategy rows API (`/api/strategies/<id>/rows`, `app/main.py`)
- **Reads:** `report_summary_json` (hot summary, `include_full=False`)
- **What it uses:** `_strategy_results` section for strategy rows
- **Impact:** Safe — already reads compact rows; universal enrichment is additive

### 2.5 Calendar pipeline trace (`/api/dev/calendar-pipeline-trace`)
- **Reads:** `full_summary_blob` (`include_full=True`)
- **Impact:** None — reads full blob

### 2.6 RunManifestRepository
- **Reads:** separate `run_manifests` table (not `report_snapshots`)
- **Impact:** None — independent storage path

---

## 3. New 30D.1 API Endpoints (read from hot summary or manifest)

| Endpoint | Auth | Source | Read path |
|---|---|---|---|
| `GET /api/dashboard/summary` | Dev token | `RunManifestRepository.latest()` | manifest table |
| `GET /api/daily-opportunity` | Dev token | hot summary `_daily_opportunity_engine` | `full=False` |
| `GET /api/open-positions` | Dev token | hot summary `_open_options_positions` | `full=False` |
| `GET /api/runs/latest` | Dev token | `RunManifestRepository.latest()` | manifest table |
| `GET /api/run/status/<job_id>` | Dev token | in-memory `RUN_JOBS` | no DB read |
| `POST /api/run/refresh` | RUN_TOKEN | triggers pipeline run | write path |

All new read endpoints return `provider_calls_triggered: false, read_only: true`.

---

## 4. Write Paths (MUST NOT change)

These paths write to persistent storage and are explicitly out of scope for 30D.1:

| Path | What it writes |
|---|---|
| `ReportSnapshotRepository.save()` | `report_snapshots` table |
| `RunManifestRepository.save()` | `run_manifests` table |
| `CalendarOpportunityRepository` | `calendar_opportunities.sqlite3` |
| `SkewVerticalOpportunityRepository` | `skew_vertical_opportunities.sqlite3` |
| `StrategyObservationJournal` | `strategy_observations.db` |
| `FFObservationJournal` | `ff_observations.db` |

---

## 5. Config Flags Added (30D.1)

| Flag | Default | Purpose |
|---|---|---|
| `REPORT_FULL_DEBUG_PAYLOAD_ENABLED` | `False` | Gate full debug payload in normal report |
| `BROKER_DEBUG_RAW_LOGS_ENABLED` | `False` | Gate raw broker API log attachment |

**Existing flags preserved:**
- `REPORT_INCLUDE_RAW_PROVIDER_PAYLOADS=False`
- `REPORT_INCLUDE_HEAVY_DEBUG=False`
- `DEV_SNAPSHOT_INCLUDE_RAW_PROVIDER_PAYLOADS=False`
- `REPORT_SNAPSHOT_STORE_COMPRESSED_FULL=True`
- `FORWARD_FACTOR_DRY_RUN=True`

---

## 6. CAVEMAN MODE Safety Invariants (unchanged by 30D.1)

- No trade execution endpoint exists or is created
- `FORWARD_FACTOR_DRY_RUN=True` — FF strategy never places orders
- All new API endpoints: `provider_calls_triggered=False, read_only=True`
- `POST /api/run/refresh` triggers pipeline but NO broker writes; only report storage
- Raw broker log redaction: `BROKER_DEBUG_RAW_LOGS_ENABLED=False` by default
- Universal enrichment wrapped in `try/except` — never blocks legacy output
