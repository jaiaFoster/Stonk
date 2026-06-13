import unittest

from app.services.forward_factor_signal_gate_service import (
    DIAGNOSTIC_POSITIVE,
    NEGATIVE_OR_BLOCKED,
    SOURCE_QUALIFIED_POSITIVE,
    WATCH_NEAR_POSITIVE,
    evaluate_forward_factor_signal_gate,
)
from app.services.daily_opportunity_engine_service import build_daily_opportunity_engine


def complete_row(**changes):
    row = {
        "verdict": "WATCH / EX-EARNINGS IV UNAVAILABLE",
        "diagnostic_raw_iv_forward_factor": .31,
        "structure_status": "COMPLETE",
        "liquidity_status": "PASS",
        "debit_at_risk": 200,
        "distance_from_target": 0,
        "data_eligibility": {"data_state": "COMPLETE"},
    }
    row.update(changes)
    return row


class ForwardFactorSignalGateTests(unittest.TestCase):
    def test_diagnostic_positive_is_review_only_and_not_daily_actionable(self):
        gate = evaluate_forward_factor_signal_gate(complete_row())
        self.assertEqual(gate["signal_tier"], DIAGNOSTIC_POSITIVE)
        self.assertEqual(gate["verdict"], "DIAGNOSTIC POSITIVE FF SIGNAL / REVIEW ONLY")
        self.assertTrue(gate["is_positive_signal"])
        self.assertFalse(gate["is_source_qualified"])
        self.assertFalse(gate["can_enter_daily_opportunity"])
        self.assertEqual(gate["actionability_score"], 0)
        self.assertGreater(gate["signal_score"], 70)

    def test_source_qualified_positive_is_distinct(self):
        gate = evaluate_forward_factor_signal_gate(complete_row(
            forward_factor=.25, front_ex_earnings_iv=.5, back_ex_earnings_iv=.45,
        ))
        self.assertEqual(gate["signal_tier"], SOURCE_QUALIFIED_POSITIVE)
        self.assertTrue(gate["is_source_qualified"])
        self.assertEqual(gate["source_iv_status"], "SOURCE_QUALIFIED")

    def test_illiquid_strong_ff_is_not_positive(self):
        gate = evaluate_forward_factor_signal_gate(complete_row(liquidity_status="FAIL", package_slippage_pct=53.3))
        self.assertEqual(gate["signal_tier"], NEGATIVE_OR_BLOCKED)
        self.assertEqual(gate["verdict"], "FAIL / OPTIONS ILLIQUID")
        self.assertFalse(gate["is_positive_signal"])
        self.assertEqual(gate["actionability_score"], 0)

    def test_liquidity_watch_or_debit_warning_is_near_positive(self):
        liquidity = evaluate_forward_factor_signal_gate(complete_row(liquidity_status="WATCH"))
        debit = evaluate_forward_factor_signal_gate(complete_row(debit_at_risk=300))
        self.assertEqual(liquidity["signal_tier"], WATCH_NEAR_POSITIVE)
        self.assertEqual(debit["signal_tier"], WATCH_NEAR_POSITIVE)

    def test_below_threshold_and_missing_structure_fail(self):
        below = evaluate_forward_factor_signal_gate(complete_row(diagnostic_raw_iv_forward_factor=.1))
        missing = evaluate_forward_factor_signal_gate(complete_row(structure_status="NO_MATCHED_DOUBLE_CALENDAR"))
        self.assertEqual(below["verdict"], "FAIL / FORWARD FACTOR BELOW THRESHOLD")
        self.assertFalse(missing["is_positive_signal"])

    def test_daily_opportunity_has_no_forward_factor_input_path(self):
        result = build_daily_opportunity_engine({}, {}, {}, [], skew_momentum_vertical_strategy={})
        self.assertEqual(result["actions"], [])
        gate = evaluate_forward_factor_signal_gate(complete_row())
        self.assertFalse(gate["can_enter_daily_opportunity"])


if __name__ == "__main__":
    unittest.main()
