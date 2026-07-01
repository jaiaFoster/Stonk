from app.services.strategy_gate_service import (
    AccountRiskGate,
    DebitGate,
    LiquidityGate,
    SourceConfidenceGate,
    enforce_dry_run_actionability,
)


def test_liquidity_pass_and_fail():
    assert LiquidityGate.evaluate(open_interest=100, volume=10, spread_pct=10).status == "PASS"
    failed = LiquidityGate.evaluate(open_interest=0, volume=0, spread_pct=40)
    assert failed.status == "FAIL"
    assert failed.reason_code == "OPTIONS_ILLIQUID"


def test_debit_too_large():
    gate = DebitGate.evaluate(debit=501, max_debit=500)
    assert gate.status == "FAIL"
    assert gate.reason_code == "DEBIT_TOO_LARGE"


def test_account_unknown_warns_not_false_ok():
    gate = AccountRiskGate.evaluate(max_risk=100, account_value=None, max_risk_pct=2)
    assert gate.status == "WARN"
    assert gate.reason_code == "ACCOUNT_VALUE_UNKNOWN"


def test_diagnostic_source_blocks_live_actionability():
    assert SourceConfidenceGate.evaluate("diagnostic", "forward_factor_calendar").status == "FAIL"


def test_ff_dry_run_blocks_daily_opportunity():
    gate = enforce_dry_run_actionability("forward_factor_calendar", "source_qualified", True)
    assert gate.status == "FAIL"
    assert gate.reason_code == "FF_DRY_RUN_EXCLUDED"
