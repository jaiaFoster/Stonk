"""
ASA Patch 30B — Production Stock Momentum Universal Rows Tests

Covers:
  - app/strategies/stock_momentum_universal.py (universal row builder)
  - Integration with production stock_momentum_strategy_service.py
  - Universal fields: row_type, schema_version, row_id, details, display, gate_groups
  - Legacy compatibility: all original fields still present
"""
from __future__ import annotations

import py_compile
from typing import Any


# ─── Compile guard ────────────────────────────────────────────────────────────

class TestCompile:
    def test_universal_module_compiles(self):
        py_compile.compile("app/strategies/stock_momentum_universal.py", doraise=True)

    def test_stock_momentum_service_compiles(self):
        py_compile.compile("app/services/stock_momentum_strategy_service.py", doraise=True)


# ─── Universal row builder unit tests ─────────────────────────────────────────

class TestBuildStockMomentumUniversalRow:
    def _builder(self):
        from app.strategies.stock_momentum_universal import build_stock_momentum_universal_row
        return build_stock_momentum_universal_row

    def _base_row(self, action: str = "CONSIDER ADDING", score: float = 80.0) -> dict:
        return {
            "strategy_id": "stock_momentum",
            "ticker": "AAPL",
            "action": action,
            "score": score,
            "momentum_score": score,
            "add_allowed_boolean": action == "CONSIDER ADDING",
            "add_blockers": [],
            "reasons": ["Strong 6-month momentum.", "Above 200-day trend."],
            "risks": [],
            "portfolio_status": "Not currently held",
            "market_metrics": {
                "above_sma_50": True,
                "above_sma_200": True,
                "return_3m_pct": 8.5,
                "return_6m_pct": 18.2,
                "relative_strength_6m_pct": 5.3,
                "average_volume_30d": 80_000_000,
                "current_price": 195.0,
            },
            "daily_opportunity_eligible": action in ("CONSIDER ADDING", "ADD ON PULLBACK"),
            "friendly_verdict": "Momentum Pass",
            "primary_reason": "Strong 6-month momentum.",
        }

    def test_returns_same_dict_object(self):
        builder = self._builder()
        row = self._base_row()
        result = builder(row)
        assert result is row  # mutates in-place

    def test_idempotent(self):
        from app.strategies.schema import SCHEMA_VERSION
        builder = self._builder()
        row = self._base_row()
        builder(row)
        schema_v1 = row.get("schema_version")
        row_type_v1 = row.get("row_type")
        builder(row)  # second call
        assert row.get("schema_version") == schema_v1
        assert row.get("row_type") == row_type_v1

    def test_schema_version_set(self):
        from app.strategies.schema import SCHEMA_VERSION
        row = self._base_row()
        self._builder()(row)
        assert row.get("schema_version") == SCHEMA_VERSION

    def test_row_type_new_candidate(self):
        from app.strategies.schema import VALID_ROW_TYPES
        row = self._base_row("CONSIDER ADDING")
        self._builder()(row)
        assert row.get("row_type") == "new_candidate"
        assert row["row_type"] in VALID_ROW_TYPES

    def test_row_type_rejected_candidate(self):
        row = self._base_row("AVOID ADDING")
        self._builder()(row)
        assert row.get("row_type") == "rejected_candidate"

    def test_row_type_observation_for_watch(self):
        row = self._base_row("WATCH / CONFIRM TREND")
        self._builder()(row)
        assert row.get("row_type") == "observation"

    def test_row_id_set(self):
        row = self._base_row()
        self._builder()(row)
        assert isinstance(row.get("row_id"), str)
        assert row["row_id"]

    def test_row_id_includes_ticker(self):
        row = self._base_row()
        self._builder()(row)
        assert "AAPL" in row.get("row_id", "")

    def test_row_id_deterministic(self):
        row1 = self._base_row()
        row2 = self._base_row()
        self._builder()(row1)
        self._builder()(row2)
        assert row1["row_id"] == row2["row_id"]

    def test_details_namespace_exists(self):
        row = self._base_row()
        self._builder()(row)
        assert isinstance(row.get("details"), dict)
        assert "stock_momentum" in row["details"]

    def test_details_stock_momentum_fields(self):
        row = self._base_row()
        self._builder()(row)
        sm = row["details"]["stock_momentum"]
        for field in ("momentum_score", "relative_strength", "trend_status",
                      "volume_status", "price_action_status", "risk_status",
                      "already_held", "benchmark", "lookback_window"):
            assert field in sm, f"Missing details.stock_momentum field: {field!r}"

    def test_details_benchmark_is_qqq(self):
        row = self._base_row()
        self._builder()(row)
        assert row["details"]["stock_momentum"]["benchmark"] == "QQQ"

    def test_details_already_held_false_for_new(self):
        row = self._base_row()
        row["portfolio_status"] = "Not currently held"
        self._builder()(row)
        assert row["details"]["stock_momentum"]["already_held"] is False

    def test_details_already_held_true_for_existing(self):
        row = self._base_row()
        row["portfolio_status"] = "Already held"
        self._builder()(row)
        assert row["details"]["stock_momentum"]["already_held"] is True

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
        assert row["display"]["title"] == "AAPL"

    def test_display_subtitle_is_stock_momentum(self):
        row = self._base_row()
        self._builder()(row)
        assert row["display"]["subtitle"] == "Stock Momentum"

    def test_display_sort_key_is_score(self):
        row = self._base_row(score=82.5)
        self._builder()(row)
        assert row["display"]["sort_key"] == 82.5

    def test_gate_groups_exist(self):
        row = self._base_row()
        self._builder()(row)
        gg = row.get("gate_groups")
        assert isinstance(gg, dict)

    def test_gate_groups_contain_required_groups(self):
        row = self._base_row()
        self._builder()(row)
        gg = row["gate_groups"]
        for group in ("data", "setup", "risk", "portfolio", "daily_opportunity"):
            assert group in gg, f"Missing gate group: {group!r}"

    def test_gate_groups_data_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        data_grp = row["gate_groups"]["data"]
        for gate_name in ("quote", "candles", "benchmark"):
            assert gate_name in data_grp, f"Missing data gate: {gate_name!r}"

    def test_gate_groups_setup_has_expected_gates(self):
        row = self._base_row()
        self._builder()(row)
        setup_grp = row["gate_groups"]["setup"]
        for gate_name in ("momentum", "relative_strength", "trend", "volume", "price_action"):
            assert gate_name in setup_grp

    def test_gate_groups_gate_has_required_fields(self):
        row = self._base_row()
        self._builder()(row)
        gate = row["gate_groups"]["setup"]["momentum"]
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

    def test_gate_custom_has_metrics(self):
        row = self._base_row()
        self._builder()(row)
        custom = row["gate_groups"]["setup"]["momentum"].get("custom", {})
        assert "momentum_score" in custom

    def test_daily_opportunity_dict_exists(self):
        row = self._base_row("CONSIDER ADDING")
        self._builder()(row)
        do = row.get("daily_opportunity")
        assert isinstance(do, dict)

    def test_daily_opportunity_eligible_true(self):
        row = self._base_row("CONSIDER ADDING")
        row["daily_opportunity_eligible"] = True
        self._builder()(row)
        assert row["daily_opportunity"]["eligible"] is True

    def test_daily_opportunity_eligible_false_for_watch(self):
        row = self._base_row("WATCH / CONFIRM TREND")
        row["daily_opportunity_eligible"] = False
        self._builder()(row)
        assert row["daily_opportunity"]["eligible"] is False

    def test_daily_opportunity_priority_set_when_eligible(self):
        row = self._base_row("CONSIDER ADDING", score=82.0)
        row["daily_opportunity_eligible"] = True
        self._builder()(row)
        assert row["daily_opportunity"]["priority"] is not None

    def test_daily_opportunity_priority_none_when_ineligible(self):
        row = self._base_row("WATCH / CONFIRM TREND")
        row["daily_opportunity_eligible"] = False
        self._builder()(row)
        assert row["daily_opportunity"]["priority"] is None

    def test_daily_opportunity_exclusion_reason_set_when_ineligible(self):
        row = self._base_row("AVOID ADDING")
        row["daily_opportunity_eligible"] = False
        row["daily_opportunity_reason"] = "Not eligible for Daily Opportunity."
        self._builder()(row)
        assert row["daily_opportunity"]["exclusion_reason"]

    def test_legacy_fields_preserved(self):
        row = self._base_row("CONSIDER ADDING", score=80.0)
        orig_action = row["action"]
        orig_score = row["score"]
        orig_reasons = list(row["reasons"])
        self._builder()(row)
        assert row["action"] == orig_action
        assert row["score"] == orig_score
        assert row["reasons"] == orig_reasons


# ─── Integration: production service produces universal rows ───────────────────

class TestProductionServiceUniversalOutput:
    """These tests call build_stock_momentum_strategy and verify universal fields
    appear on the result rows, confirming the service calls the universal builder."""

    def _run(self, positions=None, watchlist=None, metrics=None) -> dict:
        from app.services.stock_momentum_strategy_service import build_stock_momentum_strategy
        return build_stock_momentum_strategy(
            positions=positions or [],
            watchlist_candidates=watchlist or {"items": [{"ticker": "AAPL"}]},
            recommendations=None,
            market_metrics=metrics or {
                "AAPL": {
                    "above_sma_50": True,
                    "above_sma_200": True,
                    "return_3m_pct": 12.0,
                    "return_6m_pct": 22.0,
                    "return_12m_pct": 30.0,
                    "relative_strength_6m_pct": 8.0,
                    "distance_from_52w_high_pct": -5.0,
                    "average_volume_30d": 50_000_000,
                    "realized_volatility_30d": 25.0,
                    "price_vs_sma_50_pct": 5.0,
                    "current_price": 190.0,
                }
            },
            portfolio_gap_analysis=None,
            news_map=None,
        )

    def test_items_produced(self):
        result = self._run()
        assert isinstance(result.get("items"), list)

    def test_item_has_schema_version(self):
        from app.strategies.schema import SCHEMA_VERSION
        result = self._run()
        for item in result.get("items") or []:
            assert item.get("schema_version") == SCHEMA_VERSION, \
                f"Item for {item.get('ticker')} missing schema_version"

    def test_item_has_row_type(self):
        from app.strategies.schema import VALID_ROW_TYPES
        result = self._run()
        for item in result.get("items") or []:
            assert item.get("row_type") in VALID_ROW_TYPES, \
                f"Item for {item.get('ticker')}: row_type {item.get('row_type')!r} invalid"

    def test_item_has_details_namespace(self):
        result = self._run()
        for item in result.get("items") or []:
            assert isinstance(item.get("details"), dict)
            assert "stock_momentum" in item["details"]

    def test_item_has_gate_groups(self):
        result = self._run()
        for item in result.get("items") or []:
            gg = item.get("gate_groups")
            assert isinstance(gg, dict), f"gate_groups missing for {item.get('ticker')}"
            assert "setup" in gg

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
            assert isinstance(do, dict), f"daily_opportunity dict missing for {item.get('ticker')}"
            assert "eligible" in do

    def test_legacy_action_still_present(self):
        result = self._run()
        for item in result.get("items") or []:
            assert "action" in item, f"Legacy 'action' field missing for {item.get('ticker')}"

    def test_legacy_score_still_present(self):
        result = self._run()
        for item in result.get("items") or []:
            assert "score" in item, f"Legacy 'score' field missing for {item.get('ticker')}"

    def test_gate_groups_statuses_canonical(self):
        valid = {"pass", "watch", "fail", "unknown", "skipped", "dry_run"}
        result = self._run()
        for item in result.get("items") or []:
            for grp_name, grp in (item.get("gate_groups") or {}).items():
                for gate_name, gate in (grp or {}).items():
                    s = gate.get("status")
                    assert s in valid, f"{item.get('ticker')}.{grp_name}.{gate_name}: bad status {s!r}"
