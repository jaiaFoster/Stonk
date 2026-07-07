# ASA Patch 30A — Universal Strategy Row Schema + Strategy Spec Registry

**Status:** Complete  
**Schema Version:** `30A.v1`  
**Branch:** `claude/tkt-035-options-implementation-49zxlh`

---

## Summary

Patch 30A establishes the foundational schema layer for the ASA Strategy Unification project. It defines a universal row shape, a shared gate model, and a central strategy spec registry — all without changing any strategy's trade eligibility, dry-run policy, or execution behavior.

This patch is read-only and observer-safe. No trading logic, broker writes, or position mutations were added or altered.

---

## What Was Built

### Lane 1 — Universal Strategy Row Schema (`app/services/strategy_row_schema.py`)

Defines the canonical field names and constants used by the normalization layer:

- **`STRATEGY_ROW_SCHEMA_VERSION = "30A.v1"`** — stamped on every normalized row
- **`CANONICAL_REQUIRED_FIELDS`** — tuple of fields every normalized row must carry
- **`NORMALIZED_ROW_EXCLUDE`** — fields stripped from rows before returning from `normalize_strategy_rows()`; prevents large blobs from surfacing in snapshots
- **Strategy family constants** — `STRATEGY_FAMILY_OPTIONS_EVENT`, `STRATEGY_FAMILY_OPTIONS_SKEW`, `STRATEGY_FAMILY_OPTIONS_FORWARD`, `STRATEGY_FAMILY_EQUITY_MOMENTUM`

### Lane 2 — Shared Gate Model (`app/services/strategy_gate_service.py`)

New dict-based gate helpers appended to the existing file (which retains its existing model-based `StrategyGate` class gates unchanged):

| Function | Purpose |
|---|---|
| `make_gate(label, status, ...)` | Create a canonical gate dict with backward-compat `name`/`detail` aliases |
| `normalize_gate_status(status)` | Map any status string → canonical: pass/watch/fail/unknown/skipped/not_applicable/dry_run/error |
| `gate_status_rank(status)` | Numeric rank (lower = worse) for sorting |
| `has_blocking_gate_failure(gates)` | True if any gate is blocking with status fail/error |
| `summarize_gates(gates)` | Compact summary: total, worst_status, fail_count, pass_count, has_blocking_failure |

**Gate shape** (backward compatible):
```json
{
  "id": "liquidity",
  "label": "Liquidity",
  "name": "Liquidity",
  "status": "pass",
  "value": null,
  "reason": "Option liquidity passes bounds.",
  "detail": "Option liquidity passes bounds.",
  "blocking": false,
  "sort_order": 50
}
```

Legacy consumers reading `gate["name"]` and `gate["detail"]` continue to work without changes.

### Lane 3 — Strategy Spec Registry (`app/services/strategy_spec_registry.py`)

Central read-only registry for all four strategies:

| Strategy ID | Family | Status | Daily Opportunity | Dry Run |
|---|---|---|---|---|
| `earnings_calendar` | options_event_volatility | active | ✅ allowed | ❌ |
| `skew_momentum_vertical` | options_skew_momentum | active | ✅ allowed | ❌ |
| `forward_factor_calendar` | options_forward_volatility | **dry_run** | ❌ **excluded** | ✅ |
| `stock_momentum` | equity_momentum | active | ✅ allowed | ❌ |

Each spec defines: `strategy_goal`, `gate_ids`, `inputs_required`, `primary_outputs`, `requires_options_chain`, `requires_earnings_date`, `requires_broker_positions`.

**CAVEMAN MODE:** `forward_factor_calendar` has `dry_run=True` and `daily_opportunity_allowed=False` hardcoded in the registry. These values cannot be overridden by row data.

Accessors: `get_spec()`, `all_strategy_ids()`, `all_specs()`, `is_daily_opportunity_allowed()`, `is_dry_run()`

### Lane 4 — Enhanced Normalization Service (`app/services/strategy_row_normalization_service.py`)

Complete rewrite of the normalization service. The public API is backward compatible.

**New fields stamped on every normalized row:**

| Field | Description |
|---|---|
| `strategy_row_schema_version` | Always `"30A.v1"` |
| `strategy_name` | From spec registry; falls back to strategy_id |
| `strategy_family` | From spec registry; falls back to `"unknown"` |
| `strategy_goal` | From spec registry; falls back to `""` |
| `metrics` | Strategy-specific numeric/status extraction |
| `data_quality` | Inferred tier: `ok`, `degraded`, `missing`, `unknown` |
| `daily_opportunity_eligible` | Per-strategy logic; FF always `False` |
| `daily_opportunity_reason` | Human-readable reason string |
| `dry_run` | From spec; FF always `True` (enforced, not defaulted) |
| `can_trade_live` | FF always `False` (enforced, not defaulted) |
| `journal_eligible` | `True` if ticker + verdict/action present |
| `observation_key` | `strategy_id:ticker:candidate_type:structure_type[:expiration]` |
| `observation_refs` | Empty list; reserved for 30B journal linkage |
| `gates` | Canonical gate list via `make_gate()` |

**Daily Opportunity eligibility logic:**

| Strategy | Eligible when |
|---|---|
| `earnings_calendar` | `calendar_entry_allowed` is truthy |
| `skew_momentum_vertical` | `verdict` starts with `"PASS"` |
| `stock_momentum` | `action` in `{CONSIDER ADDING, ADD ON PULLBACK}` |
| `forward_factor_calendar` | **Always `False`** |

**FF policy enforcement (dual-layer):**

Both `forward_factor_service.py` (via `setdefault` before calling normalizer) and `normalize_strategy_row()` (via forced assignment) enforce `can_trade_live=False` and `dry_run=True`. The normalization layer's enforcement cannot be bypassed by pre-set field values.

**`normalize_strategy_rows(rows, strategy_id, spec=None)`** — new batch function:
- Works on shallow copies (`{**row}`) — never mutates original strategy state
- Strips all fields in `NORMALIZED_ROW_EXCLUDE` from output
- Skips non-dict elements silently

### Lane 5 — Dev Snapshot Integration (`app/services/developer_snapshot_service.py`)

Added `normalized_strategy_rows` to the `strategies` detail section:

```
GET /dev/snapshot/detail?section=strategies
→ {
    "status": "ok",
    "detail": { ... raw strategy results ... },
    "normalized_strategy_rows": {
      "earnings_calendar": [ ... normalized rows ... ],
      "skew_momentum_vertical": [ ... ],
      ...
    }
  }
```

- Wrapped in `try/except` — normalization failure never breaks the snapshot endpoint
- Limited to 20 rows per strategy (same as the detail section cap)
- Not included in the base `latest`/`full` snapshot; detail endpoint only

### Lane 10 — This Document

---

## What Was NOT Changed

- No strategy thresholds modified
- No trade execution code added or altered
- No broker writes
- No FF live trading paths opened
- `FORWARD_FACTOR_DRY_RUN` policy preserved
- FF exclusion from Daily Opportunity preserved
- Public `/screener` endpoint unchanged — no private data exposure
- Single-source earnings → warning (not block) preserved
- Earnings source conflict → block preserved
- `trade_execution_enabled=False` preserved
- All read-only endpoints remain `provider_calls_triggered=False, read_only=True`

---

## Test Coverage

**`tests/test_patch30a_strategy_unification_schema.py`** — 148 tests across 14 test classes:

| Class | What it covers |
|---|---|
| `TestCompile` | All new/modified files compile clean |
| `TestStrategyRowSchema` | Schema version constant, canonical fields, exclude set, family constants |
| `TestGateModel` | `make_gate`, `normalize_gate_status`, rank, `has_blocking_gate_failure`, `summarize_gates` |
| `TestStrategySpecRegistry` | All 4 specs, FF dry_run, DO eligibility, accessor functions |
| `TestRowNormalizationShared` | Schema version stamped, spec metadata populated, shared field presence |
| `TestEarningsCalendarMapping` | Calendar-specific fields, DO eligibility via `calendar_entry_allowed` |
| `TestSkewMomentumMapping` | Skew-specific fields, DO eligibility via verdict |
| `TestForwardFactorMapping` | FF dry_run enforcement, can_trade_live=False, DO always False |
| `TestStockMomentumMapping` | Stock-specific fields, DO eligibility via action |
| `TestNormalizeStrategyRows` | Batch function, copy safety, exclude field stripping |
| `TestDailyOpportunityRegression` | FF excluded, per-strategy eligibility regression |
| `TestObservationJournalReadiness` | `journal_eligible`, `observation_key` format |
| `TestPublicScreenerRegression` | Screener still renders, no private data leaked |
| `TestCavemanModeSafetyInvariants` | All CAVEMAN MODE safety invariants |

---

## What Comes Next (30B)

30B will add the journal layer:
- `observation_refs` field population (currently always `[]`)
- Journal entry creation from `observation_key`
- FF observation persistence (signal-only, no execution)
- 30C: live strategy wiring to the universal row shape

The spec registry, gate model, and normalization service established here are designed to be stable anchors for 30B without requiring schema changes.
