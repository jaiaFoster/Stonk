"""Strategy 2: read-only Skew Momentum Vertical Spread scanner."""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Callable

from app import config
from app.providers.tradier_provider import TradierProvider
from app.services.skew_momentum_vertical_ranking_service import rank_skew_momentum_vertical
from app.services.skew_momentum_vertical_verdict_service import apply_skew_momentum_vertical_verdict
from app.utils.log_safety import sanitize_for_log

LogFn = Callable[[str], None]


def _compute_exit_signal(position: dict[str, Any]) -> tuple[str, str | None]:
    """
    TKT-035: Advisory exit signal for open vertical spread.
    Returns (signal, reason). Never triggers any order or broker action.
    """
    pct_of_max = _first_num(position.get("pct_of_max_profit")) or 0.0
    dte = position.get("dte")
    pnl_pct = _first_num(position.get("unrealized_pnl_pct")) or 0.0
    profit_target = float(getattr(config, "SKEW_PROFIT_TARGET_PCT", 50.0))
    stop_loss = float(getattr(config, "SKEW_STOP_LOSS_PCT", 50.0))
    exit_dte = int(getattr(config, "SKEW_EXIT_DTE_THRESHOLD", 5))

    if pct_of_max >= profit_target:
        return "EXIT_TARGET", f"Profit target reached ({pct_of_max:.1f}% of max profit)"
    if pnl_pct <= -stop_loss:
        return "EXIT_STOP", f"Stop loss triggered ({pnl_pct:.1f}% loss)"
    if isinstance(dte, int) and dte <= exit_dte:
        return "EXIT_EXPIRY", f"Near expiration ({dte} DTE)"
    return "HOLD", None


STALE_STRUCTURE_MOVE_THRESHOLD = float(os.environ.get("SKEW_STALE_STRUCTURE_THRESHOLD_PCT", "0.03"))


def _is_structure_stale(long_strike: float, current_price: float, threshold: float = STALE_STRUCTURE_MOVE_THRESHOLD) -> bool:
    if not long_strike or not current_price:
        return False
    move = abs(current_price - long_strike) / long_strike
    return move > threshold


def _staleness_note(long_strike: float, current_price: float) -> str:
    move_pct = (current_price - long_strike) / long_strike * 100
    direction = "above" if move_pct > 0 else "below"
    return f"Stock now {abs(move_pct):.1f}% {direction} long strike — structure may need rebuilding"


def _has_conflicting_open_vertical(ticker: str, expiration: str, open_verticals: list[dict[str, Any]]) -> bool:
    if not open_verticals:
        return False
    for pos in open_verticals:
        pos_ticker = str(pos.get("underlying") or pos.get("ticker") or "").upper().strip()
        pos_exp = str(pos.get("expiration") or "")[:10]
        if (pos_ticker == ticker and pos_exp == expiration[:10] and
                pos.get("strategy_type") in ("skew_vertical", "vertical")):
            return True
    return False


def build_skew_momentum_vertical_strategy(
    positions: list[dict[str, Any]] | None,
    watchlist_candidates: dict[str, Any] | None,
    portfolio_gap_analysis: dict[str, Any] | None,
    market_metrics: dict[str, dict[str, Any]] | None,
    earnings_events: dict[str, dict[str, Any]] | None = None,
    account_context: dict[str, Any] | None = None,
    run_mode: str = "prod",
    log_print: LogFn | None = None,
    provider: TradierProvider | None = None,
    data_hub: Any | None = None,
    open_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger = log_print or (lambda msg: None)
    result = {
        "source": "skew_momentum_vertical_strategy_v1",
        "strategy_id": "skew_momentum_vertical",
        "strategy_label": "Skew Momentum Vertical",
        "enabled": bool(config.SKEW_VERTICAL_STRATEGY_ENABLED),
        "has_data": False,
        "items": [],
        "pass_items": [],
        "watch_items": [],
        "blocked_items": [],
        "active_items": [],
        "active_count": 0,
        "errors": [],
        "summary": {},
        "lifecycle_status": "deferred",
        "run_mode": str(run_mode or "prod").lower(),
        "scanned_tickers": [],
        "configured_max_tickers": int(config.SKEW_VERTICAL_MAX_TICKERS_PER_RUN),
        "runtime_ticker_cap": int(config.SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN if str(run_mode).lower() == "dev" else config.SKEW_VERTICAL_MAX_TICKERS_PER_RUN),
    }
    # TKT-035: populate active_rows from detected open verticals (advisory, read-only)
    if open_options:
        verticals = open_options.get("verticals") or []
        active_rows = []
        for v in verticals:
            exit_signal, exit_reason = _compute_exit_signal(v)
            row = dict(v)
            row["exit_signal"] = exit_signal
            row["exit_reason"] = exit_reason
            # TKT-057: staleness check — flag if underlying moved significantly from long strike
            vticker = str(v.get("underlying") or v.get("ticker") or "").upper().strip()
            long_strike = _first_num(v.get("long_strike"))
            current_price = _first_num(v.get("underlying_price"))
            if current_price is None and vticker:
                vmetrics = (market_metrics or {}).get(vticker) or {}
                current_price = _first_num(vmetrics.get("last_price"), vmetrics.get("close"), vmetrics.get("current_price"))
            if long_strike and current_price and _is_structure_stale(long_strike, current_price):
                row["stale_structure"] = True
                row["stale_structure_note"] = _staleness_note(long_strike, current_price)
            else:
                row["stale_structure"] = False
            active_rows.append(row)
        result["active_items"] = active_rows
        result["active_rows"] = active_rows
        result["active_count"] = len(active_rows)
        result["lifecycle_status"] = "active" if active_rows else "inactive"
        if active_rows:
            logger(f"Strategy 2: {len(active_rows)} active vertical(s) detected from open options.")

    if not result["enabled"]:
        result["errors"].append("SKEW_VERTICAL_STRATEGY_ENABLED=false")
        return _finalize(result)
    provider = provider or TradierProvider()
    if not provider.is_configured and data_hub is None:
        result["errors"].append("Tradier token unavailable; Strategy 2 requires live option-chain data.")
        return _finalize(result)

    universe = build_skew_vertical_universe(positions, watchlist_candidates, portfolio_gap_analysis, market_metrics, run_mode, log_print=logger)
    result["scanned_tickers"] = list(universe)
    logger(f"Strategy 2 Skew Momentum Vertical scanning {len(universe)} capped ticker(s): {universe}")
    for ticker in universe:
        metrics = (market_metrics or {}).get(ticker) or {}
        if not metrics.get("has_data") and data_hub is not None:
            shared = data_hub.get_derived_metrics(
                ticker,
                metrics=["momentum_3m", "momentum_6m", "momentum_12m", "sma_50", "sma_200", "relative_strength_vs_QQQ"],
                required=True,
                strategy_id="skew_momentum_vertical",
            )
            quote_record = data_hub.get_quote(ticker, required=True, strategy_id="skew_momentum_vertical")
            quote_payload = _record_payload(quote_record)
            last = _first_num(quote_payload.get("last"), quote_payload.get("close"), quote_payload.get("bid"))
            if any(shared.get(key) is not None for key in ("momentum_3m", "momentum_6m", "sma_50", "sma_200")):
                metrics = {
                    "has_data": True,
                    "return_3m_pct": shared.get("momentum_3m"),
                    "return_6m_pct": shared.get("momentum_6m"),
                    "return_12m_pct": shared.get("momentum_12m"),
                    "relative_strength_6m_pct": shared.get("relative_strength_vs_QQQ"),
                    "above_sma_50": last > shared["sma_50"] if last is not None and shared.get("sma_50") else None,
                    "above_sma_200": last > shared["sma_200"] if last is not None and shared.get("sma_200") else None,
                    "data_source": "market_data_hub",
                }
        direction = momentum_direction(metrics)
        if not direction["direction"]:
            if not metrics.get("has_data"):
                result["items"].append(_blocked_data_row(ticker, direction, "Required momentum/candle data unavailable; strategy signal was not evaluated."))
            else:
                result["items"].append(_watch_momentum_row(ticker, direction, metrics))
            continue
        try:
            quote = _record_payload(data_hub.get_quote(ticker, required=True, strategy_id="skew_momentum_vertical")) if data_hub is not None else (provider.get_quotes([ticker], greeks=False) or {}).get(ticker, {})
            underlying = _first_num(quote.get("last"), quote.get("close"), quote.get("bid"))
            if not underlying or underlying < float(config.SKEW_VERTICAL_MIN_UNDERLYING_PRICE):
                result["items"].append(_blocked_data_row(ticker, direction, "Underlying quote missing or below configured minimum."))
                continue
            average_volume = _first_num(quote.get("average_volume"))
            if average_volume is not None and average_volume < float(config.SKEW_VERTICAL_MIN_AVERAGE_VOLUME):
                result["items"].append(_blocked_data_row(ticker, direction, f"Average stock volume {average_volume:.0f} is below the configured minimum."))
                continue
            shared_chain = data_hub.get_options_chain(
                ticker,
                min_dte=config.SKEW_VERTICAL_MIN_DTE,
                max_dte=config.SKEW_VERTICAL_MAX_DTE,
                expirations=config.SKEW_VERTICAL_EXPIRATIONS_PER_TICKER,
                required=True,
                strategy_id="skew_momentum_vertical",
            ) if data_hub is not None else None
            chain_payload = _record_payload(shared_chain)
            raw_expirations = chain_payload.get("expirations") if data_hub is not None else provider.get_expirations(ticker)
            expirations = _eligible_expirations(raw_expirations or [])[: max(1, int(config.SKEW_VERTICAL_EXPIRATIONS_PER_TICKER))]
            candidates: list[dict[str, Any]] = []
            for expiration in expirations:
                chain = (chain_payload.get("chains") or {}).get(expiration, []) if data_hub is not None else provider.get_option_chain(ticker, expiration, greeks=True)
                candidates.extend(
                    construct_vertical_candidates(
                        ticker=ticker,
                        direction=direction,
                        underlying_price=underlying,
                        expiration=expiration,
                        chain=chain,
                        metrics=metrics,
                        earnings_event=(earnings_events or {}).get(ticker) or {},
                        account_context=account_context or {},
                    )
                )
            if not candidates:
                result["items"].append(_blocked_no_vertical_row(ticker, direction, "No valid same-expiration vertical survived structure and quote checks."))
            else:
                candidates.sort(key=lambda row: float(row.get("score") or 0), reverse=True)
                result["items"].extend(candidates[: max(1, int(config.SKEW_VERTICAL_MAX_CANDIDATES_PER_TICKER))])
        except Exception as exc:
            safe_error = sanitize_for_log(exc, [config.TRADIER_ACCESS_TOKEN, config.RUN_TOKEN])
            result["errors"].append(f"{ticker}: {safe_error}")
            result["items"].append(_blocked_data_row(ticker, direction, f"Tradier options data unavailable: {safe_error}"))
    seen_tickers: dict[str, dict[str, Any]] = {}
    dedup_before = len(result["items"])
    for row in result["items"]:
        ticker = str(row.get("ticker") or "")
        existing = seen_tickers.get(ticker)
        if existing is None:
            seen_tickers[ticker] = row
        elif (float(row.get("score") or 0)) > (float(existing.get("score") or 0)):
            seen_tickers[ticker] = row
    result["items"] = list(seen_tickers.values())
    if len(result["items"]) < dedup_before:
        logger(f"[skew] dedup: {dedup_before} rows → {len(result['items'])} unique tickers")
    result["items"].sort(key=lambda row: (0 if str(row.get("verdict")).startswith("PASS") else 1, -float(row.get("score") or 0)))

    # TKT-056: Conflict check — downgrade PASS to WATCH if user has open vertical in same ticker+expiry
    open_verticals = (open_options or {}).get("verticals") or []
    if open_verticals:
        for row in result["items"]:
            if not str(row.get("verdict") or "").startswith("PASS"):
                continue
            spread = row.get("possible_spread") or {}
            exp = str(spread.get("expiration") or "")
            ticker = str(row.get("ticker") or "")
            if _has_conflicting_open_vertical(ticker, exp, open_verticals):
                row["verdict"] = "WATCH / OPEN VERTICAL CONFLICT"
                row["display_state"] = "WATCH_OPEN_VERTICAL_CONFLICT"
                row["display_state_label"] = "Watch Open Vertical Conflict"
                row["display_tone"] = "warn"
                row["conflict_note"] = "Open vertical already exists in this ticker/expiry — new structure would create overlapping legs"
                row["primary_blocker"] = row["conflict_note"]
                row["next_action"] = "Wait for existing vertical to close before entering a new one."
                logger(f"[skew] {ticker}: PASS downgraded to WATCH — open vertical conflict detected")
    elif not (open_options or {}).get("has_open_options"):
        _conflict_check_skipped = getattr(build_skew_momentum_vertical_strategy, "_conflict_skip_logged", False)
        if not _conflict_check_skipped:
            logger("[skew] conflict check skipped — no options data available (broker may not support options)")
            build_skew_momentum_vertical_strategy._conflict_skip_logged = True

    logger(f"Strategy 2 produced {len(result['items'])} decision row(s).")
    return _finalize(result)


def build_skew_vertical_universe(positions, watchlist_candidates, portfolio_gap_analysis, market_metrics, run_mode="prod", log_print=None) -> list[str]:
    crypto = {"BTC", "ETH", "SOL", "DOGE"}
    ordered: list[str] = []

    def _add(rows: list[Any]) -> None:
        for row in rows:
            ticker = str((row or {}).get("ticker") or "").upper().strip()
            if ticker and ticker not in crypto and ticker not in ordered:
                ordered.append(ticker)

    if config.SKEW_VERTICAL_INCLUDE_WATCHLIST:
        _add((watchlist_candidates or {}).get("items", []) or [])

    if getattr(config, "UNIVERSE_DISCOVERY_ENABLED", True):
        try:
            from app.services.universe_discovery_service import get_skew_candidates
            logger = log_print or (lambda msg: None)
            held_tickers = [str((p or {}).get("ticker") or "").upper().strip() for p in (positions or [])]
            discovery = get_skew_candidates(
                exclude_held=held_tickers,
                max_tickers=int(getattr(config, "SKEW_UNIVERSE_MAX_CANDIDATES", 50) or 50),
                log_print=logger,
            )
            _add([{"ticker": t} for t in discovery])
        except Exception:
            pass

    if config.SKEW_VERTICAL_INCLUDE_HOLDINGS:
        _add(positions or [])
    if config.SKEW_VERTICAL_INCLUDE_PORTFOLIO_GAP:
        _add((portfolio_gap_analysis or {}).get("suggestions", []) or [])
    _add([{"ticker": ticker} for ticker in (market_metrics or {})])

    cap = int(config.SKEW_VERTICAL_DEV_MAX_TICKERS_PER_RUN if str(run_mode).lower() == "dev" else config.SKEW_VERTICAL_MAX_TICKERS_PER_RUN)
    return ordered[: max(1, cap)]


def momentum_direction(metrics: dict[str, Any]) -> dict[str, Any]:
    if not metrics.get("has_data"):
        return {"direction": None, "score": 0.0, "confirmed": False, "reason": "Momentum data unavailable.", "components": {}}
    components = {
        "above_50d": 15 if metrics.get("above_sma_50") is True else -15,
        "above_200d": 20 if metrics.get("above_sma_200") is True else -20,
        "return_3m": _signed_component(metrics.get("return_3m_pct"), 15),
        "return_6m": _signed_component(metrics.get("return_6m_pct"), 20),
        "relative_strength": _signed_component(metrics.get("relative_strength_6m_pct"), 15),
        "return_12m": _signed_component(metrics.get("return_12m_pct"), 15),
    }
    net = sum(components.values())
    bullish_score = max(0.0, min(100.0, 50 + net))
    bearish_score = max(0.0, min(100.0, 50 - net))
    if config.SKEW_VERTICAL_ALLOW_BULLISH and bullish_score >= float(config.SKEW_VERTICAL_MIN_MOMENTUM_SCORE):
        direction, score = "bullish", bullish_score
    elif config.SKEW_VERTICAL_ALLOW_BEARISH and bearish_score >= float(config.SKEW_VERTICAL_MIN_BEARISH_MOMENTUM_SCORE):
        direction, score = "bearish", bearish_score
    else:
        direction, score = None, max(bullish_score, bearish_score)
    reason = (
        f"{direction.title()} momentum confirmed: 3M {_signed(metrics.get('return_3m_pct'))}, "
        f"6M {_signed(metrics.get('return_6m_pct'))}, "
        f"{'above' if metrics.get('above_sma_50') else 'below'} 50D and "
        f"{'above' if metrics.get('above_sma_200') else 'below'} 200D."
        if direction else "Momentum is mixed or below the configured directional threshold."
    )
    return {"direction": direction, "score": round(score, 1), "confirmed": bool(direction), "reason": reason, "components": components}


def construct_vertical_candidates(
    ticker: str,
    direction: dict[str, Any],
    underlying_price: float,
    expiration: str,
    chain: list[dict[str, Any]],
    metrics: dict[str, Any] | None = None,
    earnings_event: dict[str, Any] | None = None,
    account_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    option_type = "call" if direction.get("direction") == "bullish" else "put"
    dte = _dte(expiration)
    if dte is None or dte < int(config.SKEW_MIN_SPREAD_DTE):
        detail = f"Expiration DTE {dte if dte is not None else 'unavailable'} is below hard minimum {config.SKEW_MIN_SPREAD_DTE}."
        return [
            apply_skew_momentum_vertical_verdict(
                {
                    "strategy_id": "skew_momentum_vertical",
                    "strategy_label": "Skew Momentum Vertical",
                    "source": "skew_momentum_vertical_strategy_v1",
                    "ticker": ticker,
                    "direction": direction.get("direction"),
                    "score": 0,
                    "momentum_confirmed": bool(direction.get("confirmed")),
                    "skew_pass": False,
                    "primary_reason": detail,
                    "requirements": [_req("DTE gate", False, detail, "dte")],
                    "risk_notes": [],
                    "provider_notes": [detail],
                    "payload": {"market_metrics": metrics or {}},
                }
            )
        ]
    options = [row for row in chain or [] if str(row.get("option_type") or "").lower() == option_type and _usable_quote(row)]
    options.sort(key=lambda row: float(row.get("strike") or 0))
    raw_skew_score = _compute_chain_skew(options)
    options, lottery_calls_stripped_count = _apply_lottery_filter(options)
    adjusted_skew_score = _compute_chain_skew(options)
    skew_filter_applied = lottery_calls_stripped_count > 0
    out: list[dict[str, Any]] = []
    for long_leg in options:
        long_strike = float(long_leg.get("strike") or 0)
        if abs(long_strike - underlying_price) / underlying_price * 100 > float(config.SKEW_VERTICAL_MAX_ATM_DISTANCE_PCT):
            continue
        long_delta_ok, long_delta_approximated = _delta_eligible(long_leg, underlying_price, "long")
        if not long_delta_ok:
            continue
        for short_leg in options:
            short_strike = float(short_leg.get("strike") or 0)
            valid_order = short_strike > long_strike if option_type == "call" else short_strike < long_strike
            width = abs(short_strike - long_strike)
            if not valid_order or not (float(config.SKEW_VERTICAL_MIN_WIDTH_DOLLARS) <= width <= float(config.SKEW_VERTICAL_MAX_WIDTH_DOLLARS)):
                continue
            short_delta_ok, short_delta_approximated = _delta_eligible(short_leg, underlying_price, "short")
            if not short_delta_ok:
                continue
            row = _candidate_row(ticker, direction, underlying_price, expiration, dte, option_type, long_leg, short_leg, metrics or {}, earnings_event or {}, account_context or {}, raw_skew_score=raw_skew_score, adjusted_skew_score=adjusted_skew_score, lottery_calls_stripped_count=lottery_calls_stripped_count, skew_filter_applied=skew_filter_applied)
            row["delta_approximated"] = bool(long_delta_approximated or short_delta_approximated)
            if row["delta_approximated"]:
                row["provider_notes"].append("Delta unavailable for one or more legs; strike moneyness approximation used.")
            row["ranking"] = rank_skew_momentum_vertical(row)
            row["score"] = row["ranking"]["total_score"]
            out.append(apply_skew_momentum_vertical_verdict(row))
    return out


def _candidate_row(ticker, direction, underlying, expiration, dte, option_type, long_leg, short_leg, metrics, earnings_event, account_context, *, raw_skew_score=0.0, adjusted_skew_score=0.0, lottery_calls_stripped_count=0, skew_filter_applied=False):
    from app.services.earnings_trust_service import normalize_earnings_trust

    earnings_trust = normalize_earnings_trust(earnings_event)
    width = abs(float(short_leg["strike"]) - float(long_leg["strike"]))
    conservative_debit = float(long_leg["ask"]) - float(short_leg["bid"])
    mid_debit = float(long_leg["mid"]) - float(short_leg["mid"])
    max_risk = conservative_debit * 100
    max_profit = max(0.0, width - conservative_debit) * 100
    rr = max_profit / max(max_risk, 0.01)
    financing = float(short_leg["bid"]) / max(float(long_leg["ask"]), 0.01) * 100
    iv_edge = float(short_leg.get("iv") or 0) - float(long_leg.get("iv") or 0)
    debit_pct = conservative_debit / width * 100
    long_spread = _spread_pct(long_leg)
    short_spread = _spread_pct(short_leg)
    spread_market_width_pct = max(0.0, conservative_debit - mid_debit) / max(mid_debit, 0.01) * 100
    liquidity_pass = all([
        _liquid_leg(long_leg), _liquid_leg(short_leg),
        long_spread <= float(config.SKEW_VERTICAL_MAX_LEG_SPREAD_PCT),
        short_spread <= float(config.SKEW_VERTICAL_MAX_LEG_SPREAD_PCT),
        spread_market_width_pct <= float(config.SKEW_VERTICAL_MAX_SPREAD_MARKET_WIDTH_PCT),
    ])
    # TKT-029: gate on adjusted chain skew richness (post-lottery-filter) not raw per-leg metrics.
    richness_threshold = float(getattr(config, "SKEW_RICHNESS_THRESHOLD", 12.5))
    skew_pass = adjusted_skew_score >= richness_threshold
    event_risk = _event_inside(earnings_event, dte)
    account_value = _first_num(account_context.get("account_value_estimate"))
    account_risk_pct = max_risk / account_value * 100 if account_value else None
    requirements = [
        _req("Momentum confirmation", direction.get("confirmed"), direction.get("reason"), "momentum"),
        _req("Usable options chain", True, "Same-expiration quoted legs found.", "no_chain"),
        _req("Liquidity", liquidity_pass, f"Leg spreads {long_spread:.1f}% / {short_spread:.1f}%; OI {long_leg.get('open_interest')}/{short_leg.get('open_interest')}.", "liquidity"),
        _req("Skew richness", skew_pass, f"Adjusted chain skew {adjusted_skew_score:.1f} vs threshold {richness_threshold}; raw {raw_skew_score:.1f}; {lottery_calls_stripped_count} lottery call(s) stripped.", "skew"),
        _req("Debit limit", max_risk <= float(config.SKEW_VERTICAL_MAX_DEBIT_DOLLARS) and debit_pct <= float(config.SKEW_VERTICAL_MAX_DEBIT_PCT_OF_WIDTH), f"Conservative debit ${conservative_debit:.2f}; {debit_pct:.1f}% of width.", "debit"),
        _req("Reward/risk", rr >= float(config.SKEW_VERTICAL_MIN_REWARD_RISK), f"Conservative reward/risk {rr:.2f}.", "reward_risk"),
        _req("Data quality", bool(long_leg.get("iv") is not None and short_leg.get("iv") is not None), "Tradier quotes and IV present for both legs.", "data_quality"),
    ]
    if event_risk and earnings_trust["earnings_trust_label"] == "conflict_do_not_trade":
        requirements.append(_req("Earnings date trust", False, earnings_trust["earnings_trust_reason"], "earnings_trust"))
    if account_risk_pct is not None:
        requirements.append(_req("Account risk", account_risk_pct <= float(config.SKEW_VERTICAL_MAX_ACCOUNT_RISK_PCT), f"Max risk is {account_risk_pct:.2f}% of estimated account value.", "account_risk"))
    breakeven = float(long_leg["strike"]) + conservative_debit if option_type == "call" else float(long_leg["strike"]) - conservative_debit
    is_stale = _is_structure_stale(float(long_leg["strike"]), underlying)
    spread = {
        "expiration": expiration,
        "option_type": option_type,
        "long_strike": float(long_leg["strike"]),
        "short_strike": float(short_leg["strike"]),
        "width": width,
        "conservative_debit": round(conservative_debit, 2),
        "mid_debit": round(mid_debit, 2),
    }
    return {
        "strategy_id": "skew_momentum_vertical",
        "strategy_label": "Skew Momentum Vertical",
        "source": "skew_momentum_vertical_strategy_v1",
        "ticker": ticker,
        "direction": direction.get("direction"),
        "momentum_confirmed": direction.get("confirmed"),
        "momentum_score": direction.get("score"),
        "momentum_reason": direction.get("reason"),
        "skew_pass": skew_pass,
        "skew_reason": f"Short {option_type} IV edge {iv_edge:.3f}; financing {financing:.1f}% of long premium.",
        "short_iv_edge": round(iv_edge, 4),
        "short_premium_financing_pct": round(financing, 1),
        "raw_skew_score": raw_skew_score,
        "adjusted_skew_score": adjusted_skew_score,
        "lottery_calls_stripped_count": lottery_calls_stripped_count,
        "skew_filter_applied": skew_filter_applied,
        "skew_gap_to_pass": round(float(getattr(config, "SKEW_RICHNESS_THRESHOLD", 12.5)) - adjusted_skew_score, 2),
        "would_pass_at_threshold": round(adjusted_skew_score, 2),
        "possible_spread": spread,
        "dte": dte,
        "underlying_price": underlying,
        "conservative_debit": round(conservative_debit, 2),
        "mid_debit": round(mid_debit, 2),
        "max_risk": round(max_risk, 2),
        "max_profit": round(max_profit, 2),
        "reward_risk": round(rr, 2),
        "breakeven": round(breakeven, 2),
        "debit_pct_of_width": round(debit_pct, 1),
        "long_leg_spread_pct": round(long_spread, 1),
        "short_leg_spread_pct": round(short_spread, 1),
        "spread_market_width_pct": round(spread_market_width_pct, 1),
        "liquidity_pass": liquidity_pass,
        "data_quality_pass": bool(long_leg.get("iv") is not None and short_leg.get("iv") is not None),
        "event_risk": event_risk,
        "event_risk_allowed": bool(config.SKEW_VERTICAL_ALLOW_EARNINGS_EVENT_RISK),
        "requirements": requirements,
        "risk_notes": ([f"Earnings event may fall inside the {dte}-DTE position window."] if event_risk else []) + ([earnings_trust["earnings_trust_reason"]] if event_risk and earnings_trust["earnings_trust_label"] in {"single_source_verify", "unknown_research_only", "conflict_do_not_trade"} else []) + ["Defined risk equals conservative debit."],
        **earnings_trust,
        "provider_notes": ["Tradier option-chain quotes; conservative debit uses long ask minus short bid."],
        "primary_reason": f"{direction.get('reason')} {f'Short-wing financing {financing:.1f}% with IV edge {iv_edge:.3f}.'}",
        "long_leg": long_leg,
        "short_leg": short_leg,
        "account_risk_pct": round(account_risk_pct, 2) if account_risk_pct is not None else None,
        "payload": {"long_leg": long_leg, "short_leg": short_leg, "market_metrics": metrics},
        "stale_structure": is_stale,
        "stale_structure_note": _staleness_note(float(long_leg["strike"]), underlying) if is_stale else None,
    }


def _apply_lottery_filter(options: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    if not getattr(config, "SKEW_LOTTERY_CALL_FILTER_ENABLED", True):
        return options, 0
    delta_threshold = float(getattr(config, "SKEW_LOTTERY_CALL_DELTA_THRESHOLD", 0.15) or 0.15)
    premium_threshold = float(getattr(config, "SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD", 0.10) or 0.10)
    kept = []
    stripped = 0
    for row in options:
        delta_abs = abs(_first_num(row.get("delta")) or 1.0)
        mid = _first_num(row.get("mid")) or 0.0
        volume = int(row.get("volume") or 0)
        is_lottery = delta_abs < delta_threshold and mid < premium_threshold and volume > 0
        if is_lottery:
            stripped += 1
        else:
            kept.append(row)
    return kept, stripped


def _compute_chain_skew(options: list[dict[str, Any]]) -> float:
    otm_ivs = [float(o.get("iv") or 0) for o in options if (o.get("iv") or 0) > 0 and abs(_first_num(o.get("delta")) or 0) < 0.30]
    atm_ivs = [float(o.get("iv") or 0) for o in options if (o.get("iv") or 0) > 0 and 0.30 <= abs(_first_num(o.get("delta")) or 0) <= 0.55]
    if not atm_ivs:
        return 0.0
    avg_otm = sum(otm_ivs) / len(otm_ivs) if otm_ivs else 0.0
    avg_atm = sum(atm_ivs) / len(atm_ivs)
    ratio = avg_otm / max(avg_atm, 0.001)
    return round(min(25.0, max(0.0, (ratio - 1.0) * 50)), 1)


def _finalize(result):
    for row in result["items"]:
        row.setdefault("priority", row.get("score"))
        row.setdefault("risk_notes", [])
        row.setdefault("provider_notes", [])
        row.setdefault("payload", {})
        row.setdefault("stale_structure", False)
        row.setdefault("stale_structure_note", None)
    result["pass_items"] = [row for row in result["items"] if str(row.get("verdict") or "").startswith("PASS")]
    result["watch_items"] = [row for row in result["items"] if str(row.get("verdict") or "").startswith("WATCH")]
    result["blocked_items"] = [row for row in result["items"] if str(row.get("verdict") or "").startswith("FAIL")]
    result.setdefault("active_rows", result.get("active_items") or [])
    result["has_data"] = bool(result["items"] or result.get("active_rows"))
    result["summary"] = {
        "candidate_count": len(result["items"]),
        "pass_count": len(result["pass_items"]),
        "watch_count": len(result["watch_items"]),
        "blocked_count": len(result["blocked_items"]),
        "active_count": len(result.get("active_items") or []),
        "lifecycle_status": result.get("lifecycle_status"),
        "enabled": bool(result.get("enabled")),
        "run_mode": result.get("run_mode"),
        "scanned_ticker_count": len(result.get("scanned_tickers") or []),
        "scanned_tickers": list(result.get("scanned_tickers") or []),
        "configured_max_tickers": result.get("configured_max_tickers"),
        "runtime_ticker_cap": result.get("runtime_ticker_cap"),
    }
    return result


def _watch_momentum_row(ticker, direction, metrics):
    return apply_skew_momentum_vertical_verdict({"strategy_id": "skew_momentum_vertical", "strategy_label": "Skew Momentum Vertical", "source": "skew_momentum_vertical_strategy_v1", "ticker": ticker, "direction": None, "score": direction.get("score"), "momentum_score": direction.get("score"), "momentum_confirmed": False, "momentum_reason": direction.get("reason"), "skew_pass": False, "primary_reason": direction.get("reason"), "requirements": [_req("Momentum confirmation", False, direction.get("reason"), "momentum")], "risk_notes": [], "provider_notes": [], "payload": {"market_metrics": metrics}})


def _blocked_data_row(ticker, direction, detail):
    return apply_skew_momentum_vertical_verdict({"strategy_id": "skew_momentum_vertical", "strategy_label": "Skew Momentum Vertical", "source": "skew_momentum_vertical_strategy_v1", "ticker": ticker, "direction": direction.get("direction"), "score": 0, "momentum_confirmed": bool(direction.get("confirmed")), "skew_pass": False, "primary_reason": detail, "requirements": [_req("Data quality", False, detail, "data_quality")], "risk_notes": [], "provider_notes": [detail]})


def _blocked_no_vertical_row(ticker, direction, detail):
    return apply_skew_momentum_vertical_verdict({"strategy_id": "skew_momentum_vertical", "strategy_label": "Skew Momentum Vertical", "source": "skew_momentum_vertical_strategy_v1", "ticker": ticker, "direction": direction.get("direction"), "score": direction.get("score"), "momentum_confirmed": bool(direction.get("confirmed")), "skew_pass": False, "primary_reason": detail, "requirements": [_req("Valid vertical", False, detail, "no_vertical")], "risk_notes": [], "provider_notes": []})


def _record_payload(record):
    if not isinstance(record, dict):
        return {}
    payload = record.get("payload")
    return payload if isinstance(payload, dict) else record


def _eligible_expirations(expirations):
    eligible = [(abs(_dte(raw) - int(config.SKEW_VERTICAL_TARGET_DTE)), str(raw)) for raw in expirations or [] if int(config.SKEW_VERTICAL_MIN_DTE) <= _dte(raw) <= int(config.SKEW_VERTICAL_MAX_DTE)]
    eligible.sort()
    return [raw for _, raw in eligible]


def _event_inside(event, dte):
    raw = event.get("earnings_date") or event.get("date")
    if not raw:
        return False
    event_dte = _dte(str(raw))
    return 0 <= event_dte <= dte and event_dte <= int(config.SKEW_VERTICAL_AVOID_EARNINGS_WITHIN_DAYS)


def _dte(raw):
    try:
        return (datetime.strptime(str(raw)[:10], "%Y-%m-%d").date() - date.today()).days
    except ValueError:
        return -1


def _usable_quote(row):
    return all(_first_num(row.get(key)) and _first_num(row.get(key)) > 0 for key in ("strike", "bid", "ask", "mid"))


def _delta_eligible(row, underlying, leg):
    delta = _first_num(row.get("delta"))
    if delta is not None:
        absolute = abs(delta)
        low = float(config.SKEW_VERTICAL_LONG_DELTA_MIN if leg == "long" else config.SKEW_VERTICAL_SHORT_DELTA_MIN)
        high = float(config.SKEW_VERTICAL_LONG_DELTA_MAX if leg == "long" else config.SKEW_VERTICAL_SHORT_DELTA_MAX)
        return low <= absolute <= high, False
    distance = abs(float(row.get("strike") or 0) - underlying) / max(underlying, 0.01) * 100
    if leg == "long":
        return distance <= float(config.SKEW_VERTICAL_MAX_ATM_DISTANCE_PCT), True
    return distance > 0 and distance <= max(float(config.SKEW_VERTICAL_MAX_ATM_DISTANCE_PCT) * 3, 10), True


def _liquid_leg(row):
    return int(row.get("open_interest") or 0) >= int(config.SKEW_VERTICAL_MIN_OPEN_INTEREST) or int(row.get("volume") or 0) >= int(config.SKEW_VERTICAL_MIN_VOLUME)


def _spread_pct(row):
    return (float(row["ask"]) - float(row["bid"])) / max(float(row["mid"]), 0.01) * 100


def _req(name, passed, detail, code):
    return {"name": name, "status": "PASS" if passed else "FAIL", "detail": str(detail or ""), "code": code}


def _signed_component(value, points):
    number = _first_num(value)
    if number is None:
        return 0
    return points if number > 0 else -points if number < 0 else 0


def _signed(value):
    number = _first_num(value)
    return "unavailable" if number is None else f"{number:+.1f}%"


def _first_num(*values):
    for value in values:
        try:
            if value not in (None, ""):
                return float(value)
        except (TypeError, ValueError):
            continue
    return None
