"""Strategy requirement declarations for current strategy modules."""

from __future__ import annotations

from app import config
from app.models.market_data_models import StrategyDataRequirement


def stock_momentum_requirement(tickers: list[str]) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        strategy_id="stock_momentum", tickers=tickers, needs_daily_candles=True,
        min_daily_bars=240,
        required_derived_metrics=["momentum_3m", "momentum_6m", "sma_50", "sma_200", "relative_strength_vs_QQQ"],
        priority=70, reason="Stock adds require shared trend and relative-strength facts.",
    )


def skew_vertical_requirement(tickers: list[str]) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        strategy_id="skew_momentum_vertical", tickers=tickers, needs_quote=True,
        needs_daily_candles=True, min_daily_bars=240, needs_options_chain=True,
        min_dte=config.SKEW_VERTICAL_MIN_DTE, max_dte=config.SKEW_VERTICAL_MAX_DTE,
        expirations_per_ticker=config.SKEW_VERTICAL_EXPIRATIONS_PER_TICKER,
        needs_earnings_event=True, earnings_lookahead_days=config.SKEW_VERTICAL_AVOID_EARNINGS_WITHIN_DAYS,
        required_derived_metrics=["momentum_3m", "momentum_6m", "sma_50", "sma_200", "relative_strength_vs_QQQ"],
        priority=80, reason="Skew vertical requires momentum, options liquidity, quote, and earnings-risk facts.",
    )


def earnings_calendar_requirement(tickers: list[str]) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        strategy_id="earnings_calendar", tickers=tickers, needs_quote=True,
        needs_daily_candles=True, min_daily_bars=240, needs_options_chain=True,
        needs_earnings_event=True, earnings_lookahead_days=config.EARNINGS_LOOKAHEAD_DAYS,
        required_derived_metrics=["average_volume_30d", "realized_volatility_30d"],
        priority=90, reason="Calendar scanner requires event, liquidity, candle, and option-chain facts.",
    )


def forward_factor_requirement(tickers: list[str]) -> StrategyDataRequirement:
    return StrategyDataRequirement(
        strategy_id="forward_factor_calendar", tickers=tickers, needs_quote=True,
        needs_daily_candles=True, min_daily_bars=240, needs_options_chain=True,
        min_dte=config.FF_FRONT_DTE_MIN, max_dte=config.FF_BACK_DTE_MAX,
        expirations_per_ticker=6, needs_earnings_event=True, earnings_lookahead_days=120,
        required_derived_metrics=["average_volume_30d", "realized_volatility_30d"],
        priority=85,
        reason="Forward Factor requires two term expirations, ex-earnings IV, matched ±35-delta calendars, and liquidity data.",
    )
