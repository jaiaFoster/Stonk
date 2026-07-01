from types import SimpleNamespace

from app.strategies.adapters import EarningsCalendarStrategy, ForwardFactorCalendarStrategy
from app.strategies.base import LegacyStrategyAdapterV1, StrategyV1
from app.strategies.registry import STRATEGY_REGISTRY


def test_existing_adapters_still_register():
    assert {plugin.strategy_id for plugin in STRATEGY_REGISTRY} >= {
        "earnings_calendar", "skew_momentum_vertical", "forward_factor_calendar", "stock_momentum"
    }


def test_legacy_adapter_satisfies_v1_contract_and_required_data():
    adapter = LegacyStrategyAdapterV1(EarningsCalendarStrategy())
    context = SimpleNamespace(analysis_tickers=["AAPL"])
    assert isinstance(adapter, StrategyV1)
    assert adapter.required_data(context).strategy_id == "earnings_calendar"


def test_normalizer_available_for_each_strategy():
    for plugin in STRATEGY_REGISTRY:
        rows = LegacyStrategyAdapterV1(plugin).normalize_result({"rows": [{"ticker": "TEST", "verdict": "WATCH"}]})
        assert rows[0].strategy_id == plugin.strategy_id


def test_ff_contract_never_enters_daily_opportunity():
    adapter = LegacyStrategyAdapterV1(ForwardFactorCalendarStrategy())
    opportunity = adapter.normalize_result({"rows": [{
        "ticker": "TEST", "verdict": "PASS", "can_enter_daily_opportunity": True,
    }]})[0]
    assert adapter.can_enter_daily_opportunity(opportunity) is False
