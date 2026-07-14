<!-- ROADMAP_META
patch: 33C.2
last_updated: 2026-07-13
roadmap_json: config/roadmap.json
-->

# ASA Agent Reference — Patch 33C.2

**Patch title:** Canonical Status Semantics, Explicit Eligibility, and Verified Snapshot Promotion
**Sprint status:** in_progress
**Machine-readable roadmap:** `config/roadmap.json` (schema version 33C.2.v1)

---

## Current Product Definition

Algo Stock Advisor (ASA) is a Flask-based pipeline that runs daily market analysis across
multiple option strategies, stores results in SQLite, and surfaces them via a developer API
and dashboard. The system tracks earnings calendars, open positions, and forward-factor
opportunity scores. All strategy output flows through a canonical row store; no field should
exist internally without appearing in the API surface (see Serialization Policy below).

---

## Current Architecture

| Component | Role |
|---|---|
| `StrategyRowRepository` | Primary data source for all strategy rows |
| `compact_manifest` | Dashboard summary source (not the full report blob) |
| `data_provenance.db` | Field-level provenance tracking |
| `strategy_opportunity_history.db` | Cross-run opportunity evolution (Patch 33A) |
| `app/main.py` | Flask routes — web layer only, no pipeline logic |
| `app/config.py` | All environment-driven configuration |
| `app/services/` | Pipeline and diagnostic services |
| `app/services/options_structure_builder.py` | Universal Options Structure Builder (Patch 33A) |
| `app/models/options_structure_spec.py` | Declarative spec model for structure construction (Patch 33A) |
| `app/models/strategy_opportunity_lifecycle.py` | Generic lifecycle enums and models (Patch 33A.1) |
| `app/models/calendar_evolution_policy.py` | CalendarEvolutionPolicy — immutable timing thresholds (Patch 33A.1) |
| `app/services/strategy_opportunity_lifecycle_service.py` | Generic lifecycle invariant validation and canonical construction (Patch 33A.1) |
| `app/services/calendar_opportunity_lifecycle_adapter.py` | Earnings-calendar lifecycle classifier and opportunity_id builder (Patch 33A.1) |
| `app/services/calendar_scan_result_service.py` | Run-scoped calendar scan results and scanner status contract (Patch 33B) |
| `app/services/calendar_decision_service.py` | Sole owner of final calendar decision fields: evaluation state, trade verdict, recommended action, and entry permission (Patch 33C.2) |
| `app/services/calendar_opportunity_projection_service.py` | Sole calendar row projection path; parent rows own nested structure attempts and pre-persistence invariant validation (Patch 33C.2) |
| `app/services/open_options_position_reconciliation_service.py` | Sole open child-calendar and double-calendar parent grouping service (Patch 33C.1) |
| `app/services/calendar_risk_fact_service.py` | Pure account-risk fact helper; no verdict/action ownership (Patch 33C.1) |
| `app/services/calendar_trade_type_service.py` | Pure calendar trade-type fact helper; no verdict/action ownership (Patch 33C.1) |
| `app/services/run_finalization_coordinator.py` | Required semantic validation and strategy artifact persistence before snapshot/manifest finalization (Patch 33C.2) |
| `app/api/` | Blueprints for advisor, admin, user, knowledge, plaid, auth, telemetry, custom strategy |

Provider call isolation: no `/api/dev/*` endpoint may trigger a live provider call unless
explicitly documented (e.g., `/api/dev/trigger-run`).

---

## Built-in Strategies

| ID | Label | Status |
|---|---|---|
| `earnings_calendar` | Earnings Calendar | live |
| `forward_factor_calendar` | Forward Factor Calendar | live_recommendation |
| `skew_momentum_vertical` | Skew Momentum Vertical | live |
| `stock_momentum` | Stock Momentum | live |

---

## Current Sprint — Patch 33C.2

**Focus areas:**
- Strategy Registry summaries use canonical `evaluation_state` and `trade_verdict`; `NOT_EVALUATED` is not a failure.
- Calendar rows use explicit eligibility dimensions. Generic compatibility fields may be derived, but entry behavior depends on `entry_allowed`.
- `STRUCTURE_UNAVAILABLE`, `DEFERRED_BUDGET`, and pre-window rows must never become entry-allowed.
- Calendar semantic validation runs before StrategyRowRepository persistence; invariant failures block hot-row writes and mark the run invalid.
- Endpoint verification runs before canonical snapshot promotion. Required failures produce `FAILED_VALIDATION` and preserve the prior canonical snapshot.
- Data-confidence `failed` counts represent hard failures only and must reconcile with hard failure codes.
- Calendar reconciliation logs use explicit opportunity parent, open-position parent, and open-position child terminology.
- Policy source attribution reports `railway_env:<VAR>` when an environment variable is present, even if it equals the approved default.

**Canonical Calendar Pipeline:** raw earnings events → parent opportunity creation → lifecycle classification → data-requirement planning → structure building → quantitative facts → `CalendarDecisionService` → `CalendarOpportunityProjectionService` → `StrategyRowRepository` → repository-backed APIs.

**Legacy retirement rules:** no permanent dual-read or dual-write calendar path; no API-side business reconstruction; no legacy fallback that labels stale/prior-run rows as current; retained compatibility adapters must be read-only and derive from canonical state.

**Previous sprint note:** Patch 33B added current-run scan barrier and durable first-class lifecycle fields. Patch 33C introduced canonical services; Patch 33C.1 deleted the remaining legacy live pipeline; Patch 33C.2 hardens canonical status and promotion semantics.

---

## Previous Sprint — Patch 33A.1

**Focus areas:**
- Generic Strategy Opportunity Lifecycle kernel (LifecycleStage, EvaluationState, Verdict, RecommendedAction)
- CalendarEvolutionPolicy — immutable policy with validated ordering invariants
- Earnings-calendar lifecycle migration: classify DISCOVERED/DEVELOPING/SURFACED/ACTIONABLE stages
- Config fix: EARNINGS_DISCOVERY_END_DAYS now reads from Railway env (default 35, was hardcoded 21)
- Discovery horizon changed from 4–21 DTE to 0–35 event DTE
- Structure building starts at 24 event DTE; surfacing at 14 event DTE
- Budget skips and expected-missing data are NOT strategy failures (EvaluationState.DEFERRED_BUDGET / EXPECTED_MISSING)
- Stable parent opportunity_id across structure changes
- `/api/dev/strategy-lifecycle` read-only lifecycle summary endpoint
- 90 new behavioral tests covering all DTE boundaries and lifecycle invariants

**Previously completed (Patch 33A):**
- Universal Options Structure Builder (one shared engine for all option strategies)
- Evolutionary calendar-entry evidence model (CalendarStage taxonomy, low-DTE persistence)
- Row-aware data-confidence validation profiles (skipped/rejected/lifecycle/ranked/candidate)
- Persistent opportunity evolution history with 5-day and 14-day score changes
- Calendar discovery audit trail — every expiration pair recorded with disposition
- Forward Factor config reporting (no contradictions between flags)
- PipelineStage enum eliminating `exit_stage=unknown`

**Patch governance rule:** Every patch MUST update AGENTS.md before merging. A PR without
an AGENTS.md commit is incomplete by definition — update sprint focus, feature flags, and
architecture table to reflect changes actually made.

---

## Recently Completed Patches

| Patch | Title |
|---|---|
| 33A | Evolutionary Opportunity Tracking and Calendar Discovery Repair |
| 32B | Data Confidence UI Integration, Provider Reconciliation, Calendar Discovery Audit |
| 32A | Data Confidence Completion — FieldProvenanceRecord, validation service |
| 31B | Calendar scan barrier, pre-scan quality filter, expiration pair precheck |
| 30C | Universal row enrichment for earnings calendar |

---

## Active Tickets

| ID | Title | Priority | Status | Category | Next Action |
|---|---|---|---|---|---|
| TKT-STRATEGY-LIFECYCLE-KERNEL | Generic lifecycle kernel — enums, models, validation service | P0 | completed | lifecycle | Done in Patch 33A.1 |
| TKT-CALENDAR-LIFECYCLE-MIGRATION | Migrate earnings_calendar to lifecycle contract | P0 | completed | lifecycle | Done in Patch 33A.1 |
| TKT-DISCOVERY-HORIZON-FIX | Fix EARNINGS_DISCOVERY_END_DAYS hardcoded to 21 | P0 | completed | config | Done in Patch 33A.1 — now reads Railway env, default 35 |
| TKT-CALENDAR-PREWINDOW | Calendar PRE_WINDOW stage and early discovery | P0 | in_progress | calendar | Wire lifecycle_rows_from_discovery into scanner output |
| TKT-LEGACY-SUMMARY-DEPRECATION | Remove legacy report summary from normal hot path | P0 | in_progress | performance | Add LEGACY_REPORT_SUMMARY_ARCHIVE_ENABLED=False config flag |
| TKT-CALENDAR-ENTRY-WINDOW | Calendar entry-window transition and expiration enumeration | P1 | in_progress | calendar | Add explicit expiration list enumeration before pair selection |
| TKT-ADV-006 | Weekly expiration discovery / expiration stepping failure | P1 | in_progress | calendar | Audit TRADIER_CHAIN_EXPIRATIONS_PER_TICKER=1 effect on calendar discovery |
| TKT-ADV-013 | Open positions active calendar count and lifecycle disconnect | P1 | in_progress | open_positions | Add parent double-calendar structure grouping |
| TKT-OPEN-OPTIONS-DEDUP | Open options account alias deduplication | P1 | in_progress | open_positions | Connect canonical account identity to dedup logic |
| TKT-DOUBLE-CALENDAR-PARENT | SBUX four-leg double calendar missing parent structure | P1 | in_progress | open_positions | Add parent structure detection for call+put calendars on same ticker |
| TKT-FF-PROMOTION | Promote Forward Factor to live ranked recommendations | P1 | in_progress | strategy | Add FF_RECOMMENDATIONS_ENABLED flag and update daily opportunity routing |
| TKT-OPPORTUNITY-EVOLUTION | Persistent opportunity evolution tracking | P1 | in_progress | persistence | Create strategy_opportunity_history DB table and evolution service |
| TKT-DATA-CONFIDENCE-UI | Data confidence UI provenance interactions | P1 | partial | ui | Wire data_confidence_popover into earnings date display points in report_service |
| TKT-OPTION-CHAIN-CONFIDENCE | Option chain data provenance and diagnostics | P1 | in_progress | provenance | Add chain provenance fields to strategy rows and UI disclosures |
| TKT-ROADMAP-AUTOSYNC | Roadmap metadata autosync check | P2 | in_progress | developer_tooling | Add regression test that detects stale patch ID in roadmap.json |

---

## Deferred Tickets

| ID | Title | Priority | Status | Reason |
|---|---|---|---|---|
| TKT-CALENDAR-HISTORICAL-BACKTEST | Calendar historical backtest simulator | P2 | deferred | Patch 33A records the history required; full simulator deferred to later sprint |
| TKT-CUSTOM-STRATEGY-BUILDER | Custom strategy builder | Future | planned | Depends on trusted row store, field catalog, provenance, history |
| TKT-CAL-001 | CAG/LEVI/EPAC outside earnings discovery window | P2 | deferred | Re-verify when earnings dates enter the 21-day discovery window |
| TKT-CAL-002 | Mid-caps reaching IV check after volume gate lowered | P2 | deferred | Re-check after EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME=250000 settles |
| TKT-CAL-003 | Wire date_confidence to Saku morning brief | P2 | deferred | Blocked on morning brief refactor in 33A |
| TKT-BROKER-RAW-LOGS | Broker raw log archival | P1 | verify | Verify whether already addressed in Patch 32B or earlier |

---

## Known Data-Confidence Issues

- Alpha Vantage CSV endpoint cannot provide earnings session/hour data — by design, labeled `ok_no_session`
- `TRADIER_CHAIN_EXPIRATIONS_PER_TICKER=1` limits calendar scanner to one expiration per ticker
- Robinhood chain retrieval not implemented for bulk calendar scanning

---

## Strategy Thresholds

All threshold changes require user approval before merging.

| Key | Value | Change Requires |
|---|---|---|
| `FF_MIN_FORWARD_FACTOR` | 0.20 | user_approval |
| `CALENDAR_EARNINGS_FRONT_MIN_DTE` | 14 | user_approval |
| `CALENDAR_EARNINGS_FRONT_MAX_DTE` | 35 | user_approval |
| `CALENDAR_MIN_EXPIRATION_GAP_DAYS` | 14 | user_approval |
| `EARNINGS_DISCOVERY_MIN_AVERAGE_VOLUME` | (see config) | user_approval |

---

## Feature Flags

| Key | Default | Description |
|---|---|---|
| `FF_RECOMMENDATIONS_ENABLED` | true | Forward Factor live recommendation mode |
| `FF_EXECUTION_ENABLED` | false | FF execution — must remain false |
| `FORWARD_FACTOR_DRY_RUN` | true | Legacy dry-run flag (deprecated — use FF_RECOMMENDATIONS_ENABLED) |
| `LEGACY_REPORT_SUMMARY_ARCHIVE_ENABLED` | false | Generate and store legacy full report summary blob |
| `CALENDAR_SCANNER_ENABLED` | true | Enable earnings calendar scanning |
| `DATA_CONFIDENCE_ENABLED` | true | Enable data confidence provenance tracking |
| `OPPORTUNITY_HISTORY_ENABLED` | true | Enable cross-run opportunity history tracking |
| `UNIVERSAL_STRUCTURE_BUILDER_ENABLED` | true | Use universal builder for expiration pair audit logging (Patch 33A) |

## Discovery Horizon Config (Patch 33A.1)

| Key | Default | Was | Notes |
|---|---|---|---|
| `EARNINGS_DISCOVERY_START_DAYS` | 0 | 4 | Same-day events included in discovery |
| `EARNINGS_DISCOVERY_END_DAYS` | 35 | 21 (hardcoded) | Now reads from Railway env var |
| `EARNINGS_DISCOVERY_WINDOW_END_DAYS` | (alias) | missing | Alias for quality-filter entry-window gate |
| `CALENDAR_STRUCTURE_BUILD_START_EVENT_DTE` | 24 | — | Structure building starts ≤24 event DTE |
| `CALENDAR_SURFACE_START_EVENT_DTE` | 14 | — | API-visible (surfaced) ≤14 event DTE |

---

## Universal Options Structure Builder

`app/services/options_structure_builder.py` is the single shared engine for all option
strategies. Strategies declare requirements via `OptionsStructureSpec`; the builder:

1. Enumerates **all** valid expiration pairs — no silent discards.
2. Records a `PairStatus` disposition for **every** pair considered.
3. Matches legs per spec (delta-target, ATM, same-strike).
4. Computes conservative and mid debit.
5. Checks liquidity thresholds.
6. Returns a `StructureBuildResult` with full audit trail.

**Rules:**
- The builder does NOT call any provider. It operates on pre-fetched chain data.
- The legacy strategy-specific pair selection remains until parity tests pass.
- Integration is behind `UNIVERSAL_STRUCTURE_BUILDER_ENABLED=True`.
- Every expiration pair must receive a disposition — `NO_SILENT_DISCARDS` is a hard rule.
- Log token: `UNIVERSAL_STRUCTURE_BUILDER` for per-ticker output, `CALENDAR_DISCOVERY_AUDIT` for per-run summary.

---

## Strategy Opportunity Lifecycle Framework (Patch 33A.1)

Three independent state dimensions for every opportunity:

| Dimension | Values |
|---|---|
| `lifecycle_stage` | OUTSIDE_WINDOW, DISCOVERED, DEVELOPING, SURFACED, ACTIONABLE, OPEN_POSITION, POST_EVENT, INVALIDATED, TERMINAL |
| `evaluation_state` | NOT_REQUESTED, EXPECTED_MISSING, DEFERRED_BUDGET, DATA_INCOMPLETE, BUILDING, STRUCTURE_COMPLETE, STRUCTURE_UNAVAILABLE, FULLY_EVALUATED, STALE, ERROR |
| `verdict` | NOT_EVALUATED, PASS, WATCH, NEAR_MISS, FAIL, BLOCKED |

**Calendar DTE mapping (event DTE = days until earnings date):**
- 35–25 DTE → DISCOVERED / EXPECTED_MISSING (early stage, no structure attempted)
- 24–15 DTE → DEVELOPING / BUILDING (structure building phase)
- 14–13 DTE → SURFACED / approaching entry window
- 12–4 DTE → ACTIONABLE / entry evaluation (6–12 ideal, 4–5 late)
- 0–3 DTE → still ACTIONABLE but past late-entry cutoff
- < 0 DTE → POST_EVENT

**Hard invariants (enforced in tests):**
- `EXPECTED_MISSING` and `DEFERRED_BUDGET` evaluation states MUST NOT produce verdict=`FAIL`
- `entry_allowed=True` requires `surface_eligible=True`
- `surface_eligible=True` requires `build_eligible=True`
- POST_EVENT / INVALIDATED / TERMINAL stages MUST NOT be `entry_allowed`
- `opportunity_id` (`earnings_calendar:<TICKER>:<YYYY-MM-DD>`) is stable across structure changes
- No silent opportunity deletion: every in-window ticker must produce a lifecycle row

**Key files:**
- `app/models/strategy_opportunity_lifecycle.py` — generic enums and dataclasses
- `app/models/calendar_evolution_policy.py` — CalendarEvolutionPolicy (immutable, validated)
- `app/services/strategy_opportunity_lifecycle_service.py` — invariant validation + construction
- `app/services/calendar_opportunity_lifecycle_adapter.py` — calendar-specific classifier

---

## Strategy Threshold Governance

**Hard rule:** No agent, PR, or patch may change a strategy threshold without explicit user approval.

Approving a threshold change requires the user to say (in chat) "I approve changing X from Y to Z" before the change is committed. A general "fix the bug" or "clean up the code" instruction is NOT approval to adjust thresholds.

The approved seven-DTE threshold (`CALENDAR_EARNINGS_FRONT_MIN_DTE` = 7) is a trading rule — do not change it as part of any code cleanup, refactor, or patch unless the user explicitly directs you to.

---

## Verification Commands

```bash
# Check strategy rows surface correctly
curl $BASE/api/dev/snapshot/detail/strategies?token=$DEV

# Check dev status and latest run
curl $BASE/api/dev/status?token=$DEV

# Check roadmap endpoint
curl $BASE/api/dev/roadmap?token=$DEV

# Check feature health
curl $BASE/api/dev/feature-health?token=$DEV

# Check universal builder spec for a strategy (Patch 33A)
curl $BASE/api/options-structures/calendar_spread?token=$DEV

# Check calendar discovery audit (Patch 33A)
curl $BASE/api/calendar/discovery-audit?token=$DEV

# Check calendar history for a ticker (Patch 33A)
curl $BASE/api/calendar/history/NVDA?token=$DEV

# Check opportunity evolution history (Patch 33A)
curl $BASE/api/opportunities/calendar_spread/NVDA/history?token=$DEV

# Check strategy lifecycle summary (Patch 33A.1)
curl $BASE/api/dev/strategy-lifecycle?token=$DEV

# Syntax check app/main.py
python -m py_compile app/main.py

# Run focused tests (when available)
python -m pytest tests/ -x -q

# Run Patch 33A + 33A.1 tests
python -m pytest tests/test_patch33a_workstreams.py tests/test_patch33a1_strategy_lifecycle_kernel.py tests/test_patch33a1_calendar_lifecycle.py tests/test_patch33a1_calendar_lifecycle_integration.py -v
```

---

## Latest Passing Validation Packet

```
last_run:            2026-07-13
patch:               33B
result:              in_progress
focused_tests_passed: 114 (33B lifecycle/finalization regression packet)
full_suite_passed:   3228 passed, 1 skipped, 2 subtests passed
```

---

## Next Roadmap Stages

1. Complete calendar PRE_WINDOW monitoring and expiration enumeration repair
2. Complete opportunity evolution history and score-change explainability
3. Complete open-options double-calendar parent structure
4. Verify legacy summary removal end-to-end
5. Add chain provenance to all option strategy rows
6. Backtesting preparation using accumulated history

---

## Serialization Policy

Every field added to a strategy row, position dict, or knowledge context MUST appear
in the API surface without a separate fix. The rule:

- `_strategy_summary` in `app/services/developer_snapshot_service.py` uses an EXCLUDE
  list, not a whitelist. Adding a field to a strategy row automatically surfaces it in
  all API endpoints. To suppress a field, add it to `_STRATEGY_SUMMARY_EXCLUDE` with a
  comment explaining why.

- Any new `row["field"] = value` in a strategy service must be verified with:
  `curl $BASE/api/dev/snapshot/detail/strategies?token=$DEV`
  and confirmed present before the PR is closed.

This policy eliminates the "field exists internally but disappears" class of bugs.

---

## Robinhood Re-Auth

Every Railway deploy invalidates the Robinhood OAuth token.
After each deploy: open the Robinhood app and re-approve the device login.

ASA surfaces `broker_auth_status: EXPIRED` in `/api/dev/status` (under `latest_run`)
when the token is stale. Check this first if position data looks wrong after a deploy.

To set up proactive notification: Railway Project Settings → Webhooks → add your
notification channel (Slack, SMS, email). This is a Railway config change, not a code
change — no PR needed.
