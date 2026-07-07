"""
ASA Patch 30C — Earnings Calendar Universal Rows Tests

Covers:
  - build_earnings_calendar_universal_row() unit tests
  - build_earnings_lifecycle_universal_row() unit tests
  - Integration: production service emits universal rows
  - Row types, schema_version, details, gate groups, display
"""
from __future__ import annotations

import py_compile
from typing import Any


class TestCompile:
    def test_universal_module_compiles(self):
        py_compile.compile("app/strategies/earnings_calendar_universal.py", doraise=True)

    def test_strategy_service_compiles(self):
        py_compile.compile("app/services/earnings_calendar_strategy_service.py", doraise=True)

    def test_lifecycle_service_compiles(self):
        py_compile.compile("app/services/calendar_lifecycle_service.py", doraise=True)


# ─── Candidate row builder ─────────────────────────────────────────────────────

class TestBuildEarningsCalendarUniversalRow:
    def _builder(self):
        from app.strategies.earnings_calendar_universal import build_earnings_calendar_universal_row
        return build_earnings_calendar_universal_row

    def _base_row(self, action: str = "EARNINGS CALENDAR CANDIDATE") -> dict:
        return {
            "strategy_id": "earnings_calendar",
            "ticker": "BAC",
            "action": action,
            "score": 72.0,
            "earnings_date": "2026-07-22",
            "earnings_time": "before_open",
            "earnings_source": "finnhub",
            "earnings_sources_seen": ["finnhub", "tradier"],
            "earnings_trust_label": "multi_source_confirmed",
            "date_confidence": "high",
            "date_conflict": False,
            "earnings_relation": "long_leg_captures_earnings",
            "front_expiration": "2026-07-18",
            "back_expiration": "2026-07-25",
            "front_dte": 11,
            "back_dte": 18,
            "strike": 45.0,
            "option_type": "call",
            "underlying_price": 44.50,
            "front_iv": 0.42,
            "back_iv": 0.58,
            "iv_edge": 0.16,
            "iv_relationship_status": "favorable",
            "conservative_debit": 0.35,
            "debit_pct_underlying": 0.79,
            "max_leg_spread_pct": 3.2,
            "min_leg_open_interest": 120,
            "min_leg_volume": 35,
            "calendar_entry_allowed": True,
            "liquidity_status": "pass",
            "spread_status": "pass",
            "debit_status": "pass",
            "structure_status": "long_leg_captures_earnings",
            "is_preferred_setup": True,
            "reasons": ["Preferred structure: short leg expires before earnings."],
            "risks": [],
            "daily_opportunity_eligible": True,
            "daily_opportunity_reason": "Eligible for Daily Opportunity based on action and entry quality.",
        }

    def test_returns_same_dict_object(self):
        row = self._base_row()
        result = self._builder()(row)
        assert result is row

    def test_idempotent(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._base_row()
        self._builder()(row)
        v1 = row.get("schema_version")
        self._builder()(row)
        assert row.get("schema_version") == v1

    def test_schema_version_set(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._base_row()
        self._builder()(row)
        assert row.get("schema_version") == SCHEMA_VERSION

    def test_row_type_new_candidate_for_eligible(self):
        from app.strategies.schema import VALID_ROW_TYPES
        row = self._base_row("EARNINGS CALENDAR CANDIDATE")
        self._builder()(row)
        assert row["row_type"] == "new_candidate"
        assert row["row_type"] in VALID_ROW_TYPES

    def test_row_type_new_candidate_for_urgent(self):
        row = self._base_row("URGENT REVIEW / EARNINGS SOON")
        self._builder()(row)
        assert row["row_type"] == "new_candidate"

    def test_row_type_rejected_for_avoid(self):
        row = self._base_row("AVOID / SHORT LEG EVENT RISK")
        self._builder()(row)
        assert row["row_type"] == "rejected_candidate"

    def test_row_type_rejected_for_fail(self):
        row = self._base_row("FAIL / EARNINGS DATE CONFLICT")
        self._builder()(row)
        assert row["row_type"] == "rejected_candidate"

    def test_row_type_observation_for_near_miss(self):
        row = self._base_row("NEAR_MISS / EXPIRY_GAP")
        self._builder()(row)
        assert row["row_type"] == "observation"

    def test_row_type_observation_for_manual_review(self):
        row = self._base_row("MANUAL REVIEW / TIMESTAMP NEEDED")
        self._builder()(row)
        assert row["row_type"] == "observation"

    def test_row_type_observation_for_watch(self):
        row = self._base_row("WATCH / VERIFY EARNINGS DATE")
        self._builder()(row)
        assert row["row_type"] == "observation"

    def test_row_id_set_and_deterministic(self):
        row1 = self._base_row()
        row2 = self._base_row()
        self._builder()(row1)
        self._builder()(row2)
        assert isinstance(row1["row_id"], str)
        assert row1["row_id"] == row2["row_id"]

    def test_row_id_includes_ticker(self):
        row = self._base_row()
        self._builder()(row)
        assert "BAC" in row["row_id"]

    def test_details_namespace_exists(self):
        row = self._base_row()
        self._builder()(row)
        assert isinstance(row.get("details"), dict)
        assert "earnings_calendar" in row["details"]

    def test_details_required_fields(self):
        row = self._base_row()
        self._builder()(row)
        ec = row["details"]["earnings_calendar"]
        for field in (
            "earnings_date", "earnings_time", "earnings_source", "earnings_sources_seen",
            "earnings_trust_label", "date_confidence", "event_window_status",
            "underlying_price", "option_type", "front_expiration", "back_expiration",
            "front_dte", "back_dte", "expiration_pair_status",
            "strike", "strike_selection_status", "moneyness", "moneyness_status",
            "front_iv", "back_iv", "iv_relationship_status", "iv_edge_label",
            "estimated_debit", "estimated_max_risk", "target_profit_pct", "stop_loss_pct", "reward_risk_status",
            "liquidity_status", "spread_status", "open_interest_status", "volume_status",
            "structure_type", "structure_status", "calendar_entry_allowed",
        ):
            assert field in ec, f"Missing details.earnings_calendar field: {field!r}"

    def test_details_no_raw_chains(self):
        row = self._base_row()
        row["_raw_chain"] = [{"very": "big"}, {"option": "chain"}]
        self._builder()(row)
        ec = row["details"]["earnings_calendar"]
        assert "_raw_chain" not in ec
        assert "option_chain" not in ec

    def test_details_earnings_date_propagated(self):
        row = self._base_row()
        self._builder()(row)
        assert row["details"]["earnings_calendar"]["earnings_date"] == "2026-07-22"

    def test_details_trust_label_propagated(self):
        row = self._base_row()
        self._builder()(row)
        assert row["details"]["earnings_calendar"]["earnings_trust_label"] == "multi_source_confirmed"

    def test_details_expiration_pair_status_preferred(self):
        row = self._base_row()
        self._builder()(row)
        assert row["details"]["earnings_calendar"]["expiration_pair_status"] == "preferred"

    def test_details_iv_edge_label_favorable(self):
        row = self._base_row()
        self._builder()(row)
        assert row["details"]["earnings_calendar"]["iv_edge_label"] == "favorable"

    def test_details_calendar_entry_allowed_true(self):
        row = self._base_row()
        self._builder()(row)
        assert row["details"]["earnings_calendar"]["calendar_entry_allowed"] is True

    def test_display_object_exists(self):
        row = self._base_row()
        self._builder()(row)
        d = row.get("display")
        assert isinstance(d, dict)

    def test_display_required_fields(self):
        row = self._base_row()
        self._builder()(row)
        for field in ("title", "subtitle", "badge", "sort_key", "public_reason", "detail_lines"):
            assert field in row["display"], f"Missing display field: {field!r}"

    def test_display_title_is_ticker(self):
        row = self._base_row()
        self._builder()(row)
        assert row["display"]["title"] == "BAC"

    def test_display_subtitle_is_earnings_calendar(self):
        row = self._base_row()
        self._builder()(row)
        assert row["display"]["subtitle"] == "Earnings Calendar"

    def test_display_sort_key_is_score(self):
        row = self._base_row()
        row["score"] = 77.5
        self._builder()(row)
        assert row["display"]["sort_key"] == 77.5

    def test_gate_groups_exist(self):
        row = self._base_row()
        self._builder()(row)
        gg = row.get("gate_groups")
        assert isinstance(gg, dict)

    def test_gate_groups_contain_required_groups(self):
        row = self._base_row()
        self._builder()(row)
        gg = row["gate_groups"]
        for group in ("data", "event", "setup", "structure", "risk", "liquidity", "daily_opportunity"):
            assert group in gg, f"Missing gate group: {group!r}"

    def test_gate_groups_data_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        dg = row["gate_groups"]["data"]
        for g in ("quote", "options_chain", "earnings_event", "underlying_price"):
            assert g in dg, f"Missing data gate: {g!r}"

    def test_gate_groups_event_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        eg = row["gate_groups"]["event"]
        for g in ("earnings_date_available", "earnings_source_quality", "earnings_conflict", "event_window"):
            assert g in eg, f"Missing event gate: {g!r}"

    def test_gate_groups_setup_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        sg = row["gate_groups"]["setup"]
        for g in ("expiration_pair", "strike_selection", "moneyness", "iv_relationship"):
            assert g in sg, f"Missing setup gate: {g!r}"

    def test_gate_groups_structure_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        stg = row["gate_groups"]["structure"]
        for g in ("calendar_structure", "legs_complete", "estimated_debit"):
            assert g in stg, f"Missing structure gate: {g!r}"

    def test_gate_groups_risk_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        rg = row["gate_groups"]["risk"]
        for g in ("max_debit", "assignment", "event_gap", "account_guardrail"):
            assert g in rg, f"Missing risk gate: {g!r}"

    def test_gate_groups_liquidity_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        lg = row["gate_groups"]["liquidity"]
        for g in ("bid_ask_spread", "open_interest", "volume"):
            assert g in lg, f"Missing liquidity gate: {g!r}"

    def test_gate_has_required_fields(self):
        row = self._base_row()
        self._builder()(row)
        gate = row["gate_groups"]["setup"]["expiration_pair"]
        for field in ("status", "label", "reason", "blocking", "custom"):
            assert field in gate, f"Gate missing field: {field!r}"

    def test_gate_statuses_are_canonical(self):
        row = self._base_row()
        self._builder()(row)
        valid = {"pass", "watch", "fail", "unknown", "skipped", "dry_run"}
        for grp_name, grp in row["gate_groups"].items():
            for gate_name, gate in grp.items():
                s = gate.get("status")
                assert s in valid, f"{grp_name}.{gate_name}: invalid status {s!r}"

    def test_preferred_structure_passes_expiration_gate(self):
        row = self._base_row("EARNINGS CALENDAR CANDIDATE")
        self._builder()(row)
        assert row["gate_groups"]["setup"]["expiration_pair"]["status"] == "pass"

    def test_favorable_iv_passes_iv_gate(self):
        row = self._base_row()
        self._builder()(row)
        assert row["gate_groups"]["setup"]["iv_relationship"]["status"] == "pass"

    def test_conflict_fails_earnings_source_quality_gate(self):
        row = self._base_row()
        row["earnings_trust_label"] = "conflict_do_not_trade"
        row["date_conflict"] = True
        self._builder()(row)
        assert row["gate_groups"]["event"]["earnings_source_quality"]["status"] == "fail"
        assert row["gate_groups"]["event"]["earnings_conflict"]["status"] == "fail"

    def test_single_source_watches_earnings_source_quality(self):
        row = self._base_row()
        row["earnings_trust_label"] = "single_source_verify"
        row["earnings_sources_seen"] = ["finnhub"]
        self._builder()(row)
        assert row["gate_groups"]["event"]["earnings_source_quality"]["status"] == "watch"

    def test_multi_source_passes_earnings_source_quality(self):
        row = self._base_row()
        self._builder()(row)
        assert row["gate_groups"]["event"]["earnings_source_quality"]["status"] == "pass"

    def test_daily_opportunity_dict_exists(self):
        row = self._base_row()
        self._builder()(row)
        do = row.get("daily_opportunity")
        assert isinstance(do, dict)
        assert "eligible" in do

    def test_daily_opportunity_eligible_true(self):
        row = self._base_row("EARNINGS CALENDAR CANDIDATE")
        row["daily_opportunity_eligible"] = True
        self._builder()(row)
        assert row["daily_opportunity"]["eligible"] is True

    def test_daily_opportunity_eligible_false_for_avoid(self):
        row = self._base_row("AVOID / SHORT LEG EVENT RISK")
        row["daily_opportunity_eligible"] = False
        row["calendar_entry_allowed"] = False
        self._builder()(row)
        assert row["daily_opportunity"]["eligible"] is False

    def test_daily_opportunity_bucket_is_earnings_calendar(self):
        row = self._base_row()
        self._builder()(row)
        assert row["daily_opportunity"]["bucket"] == "earnings_calendar"

    def test_daily_opportunity_priority_set_when_eligible(self):
        row = self._base_row()
        row["daily_opportunity_eligible"] = True
        self._builder()(row)
        assert row["daily_opportunity"]["priority"] is not None

    def test_daily_opportunity_priority_none_when_ineligible(self):
        row = self._base_row("AVOID / SHORT LEG EVENT RISK")
        row["daily_opportunity_eligible"] = False
        self._builder()(row)
        assert row["daily_opportunity"]["priority"] is None

    def test_legacy_fields_preserved(self):
        row = self._base_row()
        orig_action = row["action"]
        orig_score = row["score"]
        orig_reasons = list(row["reasons"])
        self._builder()(row)
        assert row["action"] == orig_action
        assert row["score"] == orig_score
        assert row["reasons"] == orig_reasons


# ─── Lifecycle row builder ─────────────────────────────────────────────────────

class TestBuildEarningsLifecycleUniversalRow:
    def _builder(self):
        from app.strategies.earnings_calendar_universal import build_earnings_lifecycle_universal_row
        return build_earnings_lifecycle_universal_row

    def _base_check(self, action: str = "HOLD / MONITOR") -> dict:
        return {
            "ticker": "SBUX",
            "underlying": "SBUX",
            "action": action,
            "option_type": "call",
            "strike": 80.0,
            "front_expiration": "2026-07-18",
            "back_expiration": "2026-07-25",
            "front_dte": 11,
            "back_dte": 18,
            "underlying_price": 79.50,
            "current_mid_debit": 0.40,
            "entry_debit_estimate": 0.35,
            "estimated_pnl_pct": 14.3,
            "assignment_risk_level": "Low",
            "assignment_risk_reasons": ["Short leg OTM by 0.63%."],
            "short_leg_itm": False,
            "lifecycle_priority_score": 30.0,
            "reasons": ["Current spread value is available from detected leg quotes."],
            "risks": [],
        }

    def test_returns_same_dict_object(self):
        check = self._base_check()
        result = self._builder()(check)
        assert result is check

    def test_schema_version_set(self):
        from app.strategies.schema import SCHEMA_VERSION
        check = self._base_check()
        self._builder()(check)
        assert check.get("schema_version") == SCHEMA_VERSION

    def test_row_type_open_position_for_hold(self):
        from app.strategies.schema import VALID_ROW_TYPES
        check = self._base_check("HOLD / MONITOR")
        self._builder()(check)
        assert check["row_type"] == "open_position"
        assert check["row_type"] in VALID_ROW_TYPES

    def test_row_type_lifecycle_check_for_take_profit(self):
        check = self._base_check("TAKE PROFIT / REVIEW EXIT")
        self._builder()(check)
        assert check["row_type"] == "lifecycle_check"

    def test_row_type_lifecycle_check_for_cut(self):
        check = self._base_check("CUT / REVIEW EXIT")
        self._builder()(check)
        assert check["row_type"] == "lifecycle_check"

    def test_strategy_id_defaults_to_earnings_calendar(self):
        check = self._base_check()
        self._builder()(check)
        assert check.get("strategy_id") == "earnings_calendar"

    def test_row_id_set(self):
        check = self._base_check()
        self._builder()(check)
        assert isinstance(check.get("row_id"), str)
        assert check["row_id"]

    def test_row_id_includes_ticker(self):
        check = self._base_check()
        self._builder()(check)
        assert "SBUX" in check["row_id"]

    def test_details_namespace_exists(self):
        check = self._base_check()
        self._builder()(check)
        assert isinstance(check.get("details"), dict)
        assert "earnings_calendar" in check["details"]

    def test_details_lifecycle_fields_present(self):
        check = self._base_check()
        self._builder()(check)
        ec = check["details"]["earnings_calendar"]
        for field in (
            "underlying_price", "option_type", "front_expiration", "back_expiration",
            "front_dte", "strike", "current_debit", "entry_debit", "pnl_pct",
            "assignment_risk", "structure_type",
        ):
            assert field in ec, f"Missing lifecycle details field: {field!r}"

    def test_details_current_debit_propagated(self):
        check = self._base_check()
        self._builder()(check)
        assert check["details"]["earnings_calendar"]["current_debit"] == 0.40

    def test_details_pnl_pct_propagated(self):
        check = self._base_check()
        self._builder()(check)
        assert check["details"]["earnings_calendar"]["pnl_pct"] == 14.3

    def test_details_calendar_entry_allowed_false_for_open_position(self):
        check = self._base_check()
        self._builder()(check)
        assert check["details"]["earnings_calendar"]["calendar_entry_allowed"] is False

    def test_display_exists(self):
        check = self._base_check()
        self._builder()(check)
        assert isinstance(check.get("display"), dict)

    def test_display_title_is_ticker(self):
        check = self._base_check()
        self._builder()(check)
        assert check["display"]["title"] == "SBUX"

    def test_display_subtitle_is_earnings_calendar(self):
        check = self._base_check()
        self._builder()(check)
        assert check["display"]["subtitle"] == "Earnings Calendar"

    def test_gate_groups_exist(self):
        check = self._base_check()
        self._builder()(check)
        gg = check.get("gate_groups")
        assert isinstance(gg, dict)

    def test_lifecycle_gate_groups_have_risk_and_do(self):
        check = self._base_check()
        self._builder()(check)
        gg = check["gate_groups"]
        assert "risk" in gg
        assert "daily_opportunity" in gg

    def test_lifecycle_gate_statuses_canonical(self):
        check = self._base_check()
        self._builder()(check)
        valid = {"pass", "watch", "fail", "unknown", "skipped", "dry_run"}
        for grp_name, grp in check["gate_groups"].items():
            for gate_name, gate in grp.items():
                s = gate.get("status")
                assert s in valid, f"{grp_name}.{gate_name}: invalid status {s!r}"

    def test_daily_opportunity_always_false_for_lifecycle(self):
        check = self._base_check()
        self._builder()(check)
        assert check["daily_opportunity"]["eligible"] is False

    def test_low_assignment_risk_passes_gate(self):
        check = self._base_check()
        self._builder()(check)
        assert check["gate_groups"]["risk"]["assignment"]["status"] == "pass"

    def test_high_assignment_risk_fails_gate(self):
        check = self._base_check()
        check["assignment_risk_level"] = "High"
        self._builder()(check)
        assert check["gate_groups"]["risk"]["assignment"]["status"] == "fail"

    def test_legacy_fields_preserved(self):
        check = self._base_check()
        orig_action = check["action"]
        orig_strike = check["strike"]
        self._builder()(check)
        assert check["action"] == orig_action
        assert check["strike"] == orig_strike


# ─── Integration: production service emits universal rows ──────────────────────

class TestProductionServiceUniversalOutput:
    def _run(self, candidates=None, earnings=None) -> dict:
        from app.services.earnings_calendar_strategy_service import evaluate_earnings_calendar_candidates
        if candidates is None:
            candidates = [_base_candidate()]
        return evaluate_earnings_calendar_candidates(candidates, earnings or _earnings_events())

    def test_items_produced(self):
        result = self._run()
        assert isinstance(result.get("items"), list)

    def test_item_has_schema_version(self):
        from app.strategies.schema import SCHEMA_VERSION
        result = self._run()
        for item in result.get("items") or []:
            assert item.get("schema_version") == SCHEMA_VERSION, \
                f"Item {item.get('ticker')} missing schema_version"

    def test_item_has_row_type(self):
        from app.strategies.schema import VALID_ROW_TYPES
        result = self._run()
        for item in result.get("items") or []:
            assert item.get("row_type") in VALID_ROW_TYPES, \
                f"Item {item.get('ticker')}: row_type {item.get('row_type')!r} invalid"

    def test_item_has_details_namespace(self):
        result = self._run()
        for item in result.get("items") or []:
            assert isinstance(item.get("details"), dict)
            assert "earnings_calendar" in item["details"]

    def test_item_has_gate_groups(self):
        result = self._run()
        for item in result.get("items") or []:
            gg = item.get("gate_groups")
            assert isinstance(gg, dict), f"gate_groups missing for {item.get('ticker')}"

    def test_item_has_display(self):
        result = self._run()
        for item in result.get("items") or []:
            d = item.get("display")
            assert isinstance(d, dict), f"display missing for {item.get('ticker')}"
            assert d.get("title") == item.get("ticker")

    def test_item_has_daily_opportunity_dict(self):
        result = self._run()
        for item in result.get("items") or []:
            do = item.get("daily_opportunity")
            assert isinstance(do, dict), f"daily_opportunity missing for {item.get('ticker')}"
            assert "eligible" in do

    def test_legacy_action_still_present(self):
        result = self._run()
        for item in result.get("items") or []:
            assert "action" in item

    def test_legacy_score_still_present(self):
        result = self._run()
        for item in result.get("items") or []:
            assert "score" in item

    def test_gate_statuses_canonical(self):
        valid = {"pass", "watch", "fail", "unknown", "skipped", "dry_run"}
        result = self._run()
        for item in result.get("items") or []:
            for grp_name, grp in (item.get("gate_groups") or {}).items():
                for gate_name, gate in (grp or {}).items():
                    s = gate.get("status")
                    assert s in valid, f"{item.get('ticker')}.{grp_name}.{gate_name}: bad status {s!r}"

    def test_no_raw_chain_in_details(self):
        result = self._run()
        for item in result.get("items") or []:
            ec = (item.get("details") or {}).get("earnings_calendar") or {}
            assert "option_chain" not in ec
            assert "_raw_chain" not in ec


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _base_candidate() -> dict:
    return {
        "ticker": "BAC",
        "score": 72.0,
        "front_expiration": "2026-07-18",
        "back_expiration": "2026-07-25",
        "front_dte": 11,
        "back_dte": 18,
        "strike": 45.0,
        "option_type": "call",
        "underlying_price": 44.50,
        "front_iv": 0.42,
        "back_iv": 0.58,
        "iv_edge": 0.16,
        "conservative_debit": 0.35,
        "mid_debit": 0.38,
        "debit_pct_underlying": 0.79,
        "max_leg_spread_pct": 3.2,
        "min_leg_open_interest": 120,
        "min_leg_volume": 35,
    }


def _earnings_events() -> dict:
    return {
        "BAC": {
            "ticker": "BAC",
            "has_data": True,
            "earnings_date": "2026-07-22",
            "date": "2026-07-22",
            "time_of_day": "before_open",
            "session_label": "Before Open",
            "is_timestamp_confirmed": True,
            "earnings_date_confidence": "multi_source",
            "date_confidence": "high",
            "date_conflict": False,
            "date_sources": ["finnhub", "tradier"],
            "sources_seen": ["finnhub", "tradier"],
            "earnings_source_count": 2,
            "earnings_source_conflict": False,
        }
    }
