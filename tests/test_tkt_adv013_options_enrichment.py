"""
tests/test_tkt_adv013_options_enrichment.py — TKT-ADV-013 options position enrichment.

Tests:
  Item 1: Vertical detection in detect_open_options_positions + unrealized_pnl/exit_signal fields
  Item 2: open_options_positions in dev snapshot detail sections
  Item 3: Knowledge API positions endpoint + thresholds wiring
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Mock robin_stocks before importing open_options_service
# ---------------------------------------------------------------------------

_ROBIN_MODS = [
    "robin_stocks", "robin_stocks.robinhood", "robin_stocks.robinhood.orders",
    "robin_stocks.robinhood.stocks", "robin_stocks.robinhood.options",
    "robin_stocks.robinhood.authentication", "robin_stocks.robinhood.profiles",
    "robin_stocks.robinhood.account", "robin_stocks.robinhood.helper",
    "robin_stocks.gemini", "robin_stocks.tda", "robin_stocks.tda.authentication",
]
for _mod in _ROBIN_MODS:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()


# ---------------------------------------------------------------------------
# Item 1: _detect_vertical_spreads includes unrealized_pnl + exit_signal
# ---------------------------------------------------------------------------

def _make_leg(underlying, option_type, expiration, strike, side, qty=1,
              avg_cost_per_share=None, mid=None, dte=22):
    return {
        "underlying": underlying, "option_type": option_type,
        "expiration": expiration, "strike": strike, "side": side,
        "abs_quantity": qty, "quantity": qty if side == "long" else -qty,
        "dte": dte, "avg_cost_per_share": avg_cost_per_share, "mid": mid,
        "broker": "robinhood", "source": "robinhood",
    }


class TestVerticalEnrichmentFields:

    def test_unrealized_pnl_populated(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", avg_cost_per_share=4.00, mid=6.00),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short", avg_cost_per_share=1.50, mid=2.00),
        ]
        verticals = _detect_vertical_spreads(legs)
        assert len(verticals) == 1
        v = verticals[0]
        assert v["net_debit"] == pytest.approx(2.50, abs=0.01)
        assert v["current_value"] == pytest.approx(4.00, abs=0.01)
        assert v["unrealized_pnl"] == pytest.approx(150.00, abs=0.01)
        assert v["unrealized_pnl_pct"] == pytest.approx(60.0, abs=0.1)
        assert v["exit_signal"] in ("HOLD", "EXIT_TARGET", "EXIT_STOP", "EXIT_EXPIRY")

    def test_null_fields_when_no_quotes(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", avg_cost_per_share=4.00, mid=None),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short", avg_cost_per_share=1.50, mid=None),
        ]
        verticals = _detect_vertical_spreads(legs)
        v = verticals[0]
        assert v["current_value"] is None
        assert v["unrealized_pnl"] is None
        assert v["unrealized_pnl_pct"] is None
        assert v["exit_signal"] == "HOLD"

    def test_exit_target_signal(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", avg_cost_per_share=4.00, mid=14.00),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short", avg_cost_per_share=1.50, mid=2.00),
        ]
        with patch("app.config.SKEW_PROFIT_TARGET_PCT", 50):
            verticals = _detect_vertical_spreads(legs)
        assert verticals[0]["exit_signal"] == "EXIT_TARGET"
        assert verticals[0]["pct_of_max_profit"] == pytest.approx(80.0, abs=0.1)

    def test_exit_stop_signal(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", avg_cost_per_share=4.00, mid=1.50),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short", avg_cost_per_share=1.50, mid=1.20),
        ]
        with patch("app.config.SKEW_PROFIT_TARGET_PCT", 50), \
             patch("app.config.SKEW_STOP_LOSS_PCT", 50):
            verticals = _detect_vertical_spreads(legs)
        assert verticals[0]["exit_signal"] == "EXIT_STOP"

    def test_exit_expiry_signal(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", avg_cost_per_share=4.00, mid=5.00, dte=2),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short", avg_cost_per_share=1.50, mid=2.00, dte=2),
        ]
        with patch("app.config.SKEW_EXIT_DTE_THRESHOLD", 3):
            verticals = _detect_vertical_spreads(legs)
        assert verticals[0]["exit_signal"] == "EXIT_EXPIRY"

    def test_hold_signal_default(self):
        from app.services.open_options_service import _detect_vertical_spreads
        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", avg_cost_per_share=4.00, mid=5.00),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short", avg_cost_per_share=1.50, mid=2.50),
        ]
        with patch("app.config.SKEW_PROFIT_TARGET_PCT", 90), \
             patch("app.config.SKEW_STOP_LOSS_PCT", 90), \
             patch("app.config.SKEW_EXIT_DTE_THRESHOLD", 1):
            verticals = _detect_vertical_spreads(legs)
        assert verticals[0]["exit_signal"] == "HOLD"


# ---------------------------------------------------------------------------
# Item 1: detect_open_options_positions includes verticals
# ---------------------------------------------------------------------------

class TestMainDetectorIncludesVerticals:

    def test_verticals_key_in_result(self):
        from app.services.open_options_service import detect_open_options_positions
        mock_provider = MagicMock()
        mock_provider.is_configured = False
        logs = []
        with patch("app.services.open_options_service.TradierProvider", return_value=mock_provider), \
             patch("app.config.OPEN_OPTIONS_DETECTOR_ENABLED", True), \
             patch("app.config.ROBINHOOD_OPTIONS_DETECTOR_ENABLED", False):
            result = detect_open_options_positions(log_print=logs.append)
        assert "verticals" in result
        assert isinstance(result["verticals"], list)
        assert "vertical_count" in result.get("summary", {})

    def test_vertical_log_line_emitted(self):
        from app.services.open_options_service import detect_open_options_positions
        mock_provider = MagicMock()
        mock_provider.is_configured = True
        fake_v = {
            "ticker": "NVDA", "long_strike": 215.0, "short_strike": 230.0,
            "option_type": "call", "expiration": "2026-07-10",
            "current_value": 4.0, "pct_of_max_profit": 26.67,
            "unrealized_pnl": 150.0, "exit_signal": "HOLD",
        }
        logs = []
        with patch("app.services.open_options_service.TradierProvider", return_value=mock_provider), \
             patch("app.services.open_options_service._resolve_account_ids", return_value=[]), \
             patch("app.services.open_options_service._detect_vertical_spreads", return_value=[fake_v]), \
             patch("app.config.OPEN_OPTIONS_DETECTOR_ENABLED", True), \
             patch("app.config.ROBINHOOD_OPTIONS_DETECTOR_ENABLED", False):
            result = detect_open_options_positions(log_print=logs.append)
        vertical_logs = [l for l in logs if "[open_options]" in l]
        assert len(vertical_logs) == 1
        assert "NVDA" in vertical_logs[0]
        assert "signal=HOLD" in vertical_logs[0]

    def test_summary_log_includes_verticals(self):
        from app.services.open_options_service import detect_open_options_positions
        mock_provider = MagicMock()
        mock_provider.is_configured = False
        logs = []
        with patch("app.services.open_options_service.TradierProvider", return_value=mock_provider), \
             patch("app.config.OPEN_OPTIONS_DETECTOR_ENABLED", True), \
             patch("app.config.ROBINHOOD_OPTIONS_DETECTOR_ENABLED", False):
            detect_open_options_positions(log_print=logs.append)
        summary_logs = [l for l in logs if "vertical spread(s) detected" in l]
        assert len(summary_logs) == 1


# ---------------------------------------------------------------------------
# Item 2: open_options_positions in dev snapshot detail sections
# ---------------------------------------------------------------------------

class TestDevSnapshotOpenOptionsSection:

    def test_available_detail_sections_includes_open_options(self):
        from app.services.developer_snapshot_service import build_developer_snapshot
        mock_repo = MagicMock()
        mock_manifest_repo = MagicMock()
        mock_manifest_repo.latest.return_value = {}
        mock_repo.latest_success.return_value = {"run_id": "test", "status": "success"}
        mock_repo.load_summary.return_value = {"report_data": {"tradier_snapshot": {}}}
        mock_repo.snapshot_profile.return_value = {}
        with patch("app.services.developer_snapshot_service.build_commit_identity",
                    return_value={"source_of_truth": "abc123", "git_branch": "main", "deploy_label": "test"}), \
             patch("app.services.developer_snapshot_service.build_data_freshness_summary", return_value={}):
            snapshot = build_developer_snapshot(
                report_repository=mock_repo, manifest_repository=mock_manifest_repo,
            )
        assert "open_options_positions" in snapshot.get("available_detail_sections", [])

    def test_detail_section_returns_open_options_data(self):
        from app.services.developer_snapshot_service import build_snapshot_detail
        mock_repo = MagicMock()
        fake_open_opts = {"summary": {"vertical_count": 2}, "verticals": [{"ticker": "NVDA"}]}
        mock_repo.latest_success.return_value = {"run_id": "test", "status": "success"}
        mock_repo.load_summary.return_value = {
            "report_data": {"tradier_snapshot": {"_open_options_positions": fake_open_opts}}
        }
        result = build_snapshot_detail("open_options_positions", report_repository=mock_repo)
        assert result["status"] == "ok"
        assert result["detail"] == fake_open_opts


# ---------------------------------------------------------------------------
# Item 3: Knowledge API — thresholds include open_options_lifecycle
# ---------------------------------------------------------------------------

class TestKnowledgeThresholdsLifecycle:

    def _make_authed_client(self):
        from app.api.knowledge import knowledge_bp
        from flask import Flask
        app = Flask(__name__)
        app.config["TESTING"] = True
        app.register_blueprint(knowledge_bp)
        with patch("app.config.LEGACY_DEV_TOKEN_ENABLED", True), \
             patch("app.config.RUN_TOKEN", "test-token"), \
             patch("app.config.DEV_API_TOKEN", "test-token"):
            return app.test_client(), app

    def test_open_options_lifecycle_in_thresholds(self):
        client, app = self._make_authed_client()
        with app.app_context():
            with patch("app.config.LEGACY_DEV_TOKEN_ENABLED", True), \
                 patch("app.config.RUN_TOKEN", "test-token"), \
                 patch("app.config.DEV_API_TOKEN", "test-token"):
                resp = client.get("/api/advisor/knowledge/thresholds",
                                  headers={"Authorization": "Bearer test-token"})
                assert resp.status_code == 200
                data = resp.get_json()
                assert "open_options_lifecycle" in data
                lc = data["open_options_lifecycle"]
                assert "profit_target_pct" in lc
                assert "stop_loss_pct" in lc
                assert "exit_dte_threshold" in lc
                assert lc["exit_signals"] == ["HOLD", "EXIT_TARGET", "EXIT_STOP", "EXIT_EXPIRY"]

    def test_agent_prompt_morning_brief_includes_positions(self):
        client, app = self._make_authed_client()
        with app.app_context():
            with patch("app.config.LEGACY_DEV_TOKEN_ENABLED", True), \
                 patch("app.config.RUN_TOKEN", "test-token"), \
                 patch("app.config.DEV_API_TOKEN", "test-token"):
                resp = client.get("/api/advisor/knowledge/agent-prompt",
                                  headers={"Authorization": "Bearer test-token"})
                assert resp.status_code == 200
                data = resp.get_json()
                morning_steps = data.get("morning_brief_sequence", [])
                pos_steps = [s for s in morning_steps if "knowledge/positions" in s.get("action", "")]
                assert len(pos_steps) == 1

    def test_interpretation_rule_references_knowledge_positions(self):
        client, app = self._make_authed_client()
        with app.app_context():
            with patch("app.config.LEGACY_DEV_TOKEN_ENABLED", True), \
                 patch("app.config.RUN_TOKEN", "test-token"), \
                 patch("app.config.DEV_API_TOKEN", "test-token"):
                resp = client.get("/api/advisor/knowledge/agent-prompt",
                                  headers={"Authorization": "Bearer test-token"})
                assert resp.status_code == 200
                data = resp.get_json()
                rules = data.get("interpretation_rules", [])
                position_rules = [r for r in rules if "knowledge/positions" in r]
                assert len(position_rules) >= 1


# ---------------------------------------------------------------------------
# Gap 1: _overlay_enriched_marks overlays live marks from core run
# ---------------------------------------------------------------------------

class TestOverlayEnrichedMarks:

    def test_overlay_populates_null_fields(self):
        from app.api.advisor import _overlay_enriched_marks

        options_positions = [{
            "ticker": "NVDA", "strategy_type": "skew_vertical",
            "expiration": "2026-07-10",
            "legs": [
                {"strike": 215.0, "position": "long", "current_price": None},
                {"strike": 230.0, "position": "short", "current_price": None},
            ],
            "current_value": None, "exit_signal": None,
        }]
        report = {
            "tradier_snapshot": {
                "_open_options_positions": {
                    "verticals": [{
                        "ticker": "NVDA", "option_type": "call",
                        "long_strike": 215.0, "short_strike": 230.0,
                        "expiration": "2026-07-10",
                        "current_value": 4.05, "unrealized_pnl": 155.0,
                        "unrealized_pnl_pct": 62.0, "pct_of_max_profit": 27.0,
                        "exit_signal": "EXIT_STOP",
                        "legs": [
                            {"strike": 215.0, "position": "long", "current_price": 6.10},
                            {"strike": 230.0, "position": "short", "current_price": 2.05},
                        ],
                    }],
                }
            }
        }
        _overlay_enriched_marks(options_positions, report)
        op = options_positions[0]
        assert op["current_value"] == 4.05
        assert op["unrealized_pnl"] == 155.0
        assert op["exit_signal"] == "EXIT_STOP"
        assert op["legs"][0]["current_price"] == 6.10
        assert op["legs"][1]["current_price"] == 2.05

    def test_overlay_skips_non_verticals(self):
        from app.api.advisor import _overlay_enriched_marks

        options_positions = [{
            "ticker": "NVDA", "strategy_type": "earnings_calendar",
            "expiration": "2026-07-10", "current_value": None, "legs": [],
        }]
        report = {
            "tradier_snapshot": {
                "_open_options_positions": {
                    "verticals": [{
                        "ticker": "NVDA", "long_strike": 215.0, "short_strike": 230.0,
                        "expiration": "2026-07-10", "current_value": 4.05,
                        "exit_signal": "HOLD", "legs": [],
                    }],
                }
            }
        }
        _overlay_enriched_marks(options_positions, report)
        assert options_positions[0]["current_value"] is None

    def test_overlay_handles_no_report(self):
        from app.api.advisor import _overlay_enriched_marks

        options_positions = [{"ticker": "NVDA", "strategy_type": "skew_vertical", "current_value": None}]
        _overlay_enriched_marks(options_positions, None)
        assert options_positions[0]["current_value"] is None

    def test_overlay_matches_when_expiration_missing(self):
        from app.api.advisor import _overlay_enriched_marks

        options_positions = [{
            "ticker": "NVDA", "strategy_type": "skew_vertical",
            "legs": [
                {"strike": 215.0, "position": "long", "current_price": None},
                {"strike": 230.0, "position": "short", "current_price": None},
            ],
            "current_value": None, "exit_signal": None,
        }]
        report = {
            "tradier_snapshot": {
                "_open_options_positions": {
                    "verticals": [{
                        "ticker": "NVDA", "option_type": "call",
                        "long_strike": 215.0, "short_strike": 230.0,
                        "expiration": "2026-07-10",
                        "current_value": 0.39, "unrealized_pnl": -211.0,
                        "unrealized_pnl_pct": -84.4, "pct_of_max_profit": 2.6,
                        "exit_signal": "EXIT_STOP",
                        "legs": [
                            {"strike": 215.0, "position": "long", "current_price": 6.10},
                            {"strike": 230.0, "position": "short", "current_price": 5.71},
                        ],
                    }],
                }
            }
        }
        _overlay_enriched_marks(options_positions, report)
        op = options_positions[0]
        assert op["current_value"] == 0.39
        assert op["exit_signal"] == "EXIT_STOP"

    def test_overlay_no_match_leaves_null(self):
        from app.api.advisor import _overlay_enriched_marks

        options_positions = [{
            "ticker": "AAPL", "strategy_type": "skew_vertical",
            "expiration": "2026-07-10",
            "legs": [
                {"strike": 200.0, "position": "long"},
                {"strike": 210.0, "position": "short"},
            ],
            "current_value": None,
        }]
        report = {
            "tradier_snapshot": {
                "_open_options_positions": {
                    "verticals": [{
                        "ticker": "NVDA", "long_strike": 215.0, "short_strike": 230.0,
                        "expiration": "2026-07-10", "current_value": 4.05,
                        "exit_signal": "HOLD", "legs": [],
                    }],
                }
            }
        }
        _overlay_enriched_marks(options_positions, report)
        assert options_positions[0]["current_value"] is None


# ---------------------------------------------------------------------------
# Gap 2: _detect_vertical_spreads deduplicates by natural key
# ---------------------------------------------------------------------------

class TestVerticalDedup:

    def test_dedup_removes_cross_broker_duplicates(self):
        from app.services.open_options_service import _detect_vertical_spreads

        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long",
                      avg_cost_per_share=4.00, mid=6.00),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short",
                      avg_cost_per_share=1.50, mid=2.00),
            # Duplicate from second broker source
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long",
                      avg_cost_per_share=4.00, mid=6.00),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short",
                      avg_cost_per_share=1.50, mid=2.00),
        ]
        legs[2]["broker"] = "tradier"
        legs[3]["broker"] = "tradier"
        verticals = _detect_vertical_spreads(legs)
        assert len(verticals) == 1
        assert verticals[0]["ticker"] == "NVDA"

    def test_dedup_keeps_distinct_structures(self):
        from app.services.open_options_service import _detect_vertical_spreads

        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long",
                      avg_cost_per_share=4.00, mid=6.00),
            _make_leg("NVDA", "call", "2026-07-10", 235.0, "short",
                      avg_cost_per_share=1.00, mid=1.50),
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long",
                      avg_cost_per_share=4.00, mid=6.00),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short",
                      avg_cost_per_share=1.50, mid=2.00),
        ]
        verticals = _detect_vertical_spreads(legs)
        short_strikes = sorted(v["short_strike"] for v in verticals)
        assert len(verticals) == 2
        assert short_strikes == [230.0, 235.0]

    def test_cartesian_product_deduped_to_two(self):
        """Simulates the NVDA scenario: 2 longs × 2 shorts = 4 raw, dedup to 2."""
        from app.services.open_options_service import _detect_vertical_spreads

        legs = [
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", qty=1,
                      avg_cost_per_share=4.00, mid=6.00),
            _make_leg("NVDA", "call", "2026-07-10", 215.0, "long", qty=1,
                      avg_cost_per_share=4.00, mid=6.00),
            _make_leg("NVDA", "call", "2026-07-10", 230.0, "short", qty=1,
                      avg_cost_per_share=1.50, mid=2.00),
            _make_leg("NVDA", "call", "2026-07-10", 235.0, "short", qty=1,
                      avg_cost_per_share=1.00, mid=1.50),
        ]
        verticals = _detect_vertical_spreads(legs)
        # 2 longs × 2 shorts = 4 raw, but dedup by (ticker, type, long, short, exp) → 2
        assert len(verticals) == 2
