<!-- ROADMAP_META
patch: 33A
last_updated: 2026-07-13
roadmap_json: config/roadmap.json
-->

# ASA Agent Reference — Patch 33A

**Patch title:** Evolutionary Opportunity Tracking and Calendar Discovery Repair
**Sprint status:** in_progress
**Machine-readable roadmap:** `config/roadmap.json` (schema version 33A.v1)

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

## Current Sprint — Patch 33A

**Focus areas:**
- Calendar PRE_WINDOW stage and early discovery repair
- Persistent opportunity evolution history (`strategy_opportunity_history` DB)
- Open-positions double-calendar parent structure
- Forward Factor promotion to live ranked recommendations
- Legacy report summary removal from hot path
- Option chain provenance fields

---

## Recently Completed Patches

| Patch | Title |
|---|---|
| 32B | Data Confidence UI Integration, Provider Reconciliation, Calendar Discovery Audit |
| 32A | Data Confidence Completion — FieldProvenanceRecord, validation service |
| 31B | Calendar scan barrier, pre-scan quality filter, expiration pair precheck |
| 30C | Universal row enrichment for earnings calendar |
| 30B | Universal strategy observation journal |

---

## Active Tickets

| ID | Title | Priority | Status | Category | Next Action |
|---|---|---|---|---|---|
| TKT-CALENDAR-PREWINDOW | Calendar PRE_WINDOW stage and early discovery | P0 | in_progress | calendar | Implement PRE_WINDOW stage in calendar scanner |
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

# Syntax check app/main.py
python -m py_compile app/main.py

# Run focused tests (when available)
python -m pytest tests/ -x -q
```

---

## Latest Passing Validation Packet

```
last_run:            2026-07-13
patch:               33A
result:              in_progress
focused_tests_passed: null
full_suite_passed:   null
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
