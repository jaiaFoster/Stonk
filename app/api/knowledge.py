"""Knowledge API — static + dynamic strategy reference for agents and users.

All endpoints read-only. No provider calls. No writes.
Auth: @require_auth from app.auth (sets g.current_user).
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from app import config
from app.auth import require_auth

knowledge_bp = Blueprint("knowledge", __name__, url_prefix="/api/advisor/knowledge")


def _ff_live_summary() -> dict:
    """Pull FF PASS/WATCH tickers from the latest snapshot for thresholds + agent context."""
    try:
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return {"ff_pass_tickers": [], "ff_watch_tickers": [], "ff_latest_pass": None}
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}
        ff = strategies.get("forward_factor_calendar", {}) or {}
        rows = ff.get("rows", []) or []
        pass_rows = [r for r in rows if r.get("is_positive_signal")]
        watch_rows = [r for r in rows if str(r.get("verdict") or "").startswith("WATCH")]
        latest = None
        if pass_rows:
            top = pass_rows[0]
            latest = {
                "ticker": top.get("ticker"),
                "forward_factor": top.get("forward_factor"),
                "signal_score": top.get("signal_score"),
                "signal_tier": top.get("signal_tier"),
                "verdict": top.get("verdict"),
                "front_expiration": top.get("front_expiration"),
                "back_expiration": top.get("back_expiration"),
                "conservative_debit": top.get("conservative_debit"),
                "edge_on_margin": top.get("edge_on_margin"),
            }
        return {
            "ff_pass_tickers": [r.get("ticker") for r in pass_rows],
            "ff_watch_tickers": [r.get("ticker") for r in watch_rows],
            "ff_latest_pass": latest,
        }
    except Exception:
        return {"ff_pass_tickers": [], "ff_watch_tickers": [], "ff_latest_pass": None}


def _ff_agent_context() -> list[str]:
    """Build human-readable FF signal lines for agent-prompt context."""
    try:
        ff = _ff_live_summary()
        if not ff.get("ff_latest_pass"):
            return []
        lines = []
        from app.services.report_snapshot_service import ReportSnapshotRepository
        repo = ReportSnapshotRepository()
        snapshot = repo.latest_success(include_full=True)
        if not snapshot:
            return []
        summary = repo.load_summary(snapshot, full=True)
        report = summary.get("report_data", {}) or {}
        tradier = report.get("tradier_snapshot", {}) or {}
        strategies = tradier.get("_strategy_results", {}) or summary.get("strategy_results", {}) or {}
        ff_result = strategies.get("forward_factor_calendar", {}) or {}
        rows = ff_result.get("rows", []) or []
        pass_rows = [r for r in rows if r.get("is_positive_signal")]
        for r in pass_rows:
            ff_val = r.get("forward_factor")
            ff_str = f"{ff_val:.3f}" if ff_val is not None else "?"
            score = r.get("signal_score", "?")
            front = r.get("front_expiration", "?")
            back = r.get("back_expiration", "?")
            debit = r.get("conservative_debit")
            debit_str = f"${debit:.2f}" if debit is not None else "?"
            eom = r.get("edge_on_margin", "?")
            lines.append(
                f"FF CALENDAR SIGNAL: {r.get('ticker', '?')} — {r.get('verdict', '?')} "
                f"(FF={ff_str}, score={score}, front={front}, back={back}, "
                f"debit={debit_str}, edge_on_margin={eom}%)"
            )
        return lines
    except Exception:
        return []


@knowledge_bp.route("/strategies")
@require_auth
def knowledge_strategies():
    return jsonify({
        "strategies": [
            {
                "id": "earnings_calendar",
                "name": "Earnings Calendar Spread",
                "core_idea": (
                    "Long calendar spread around earnings. Buy farther-dated call, sell nearer-dated call, "
                    "same strike. Monetizes IV crush, pinning, small move/pop-and-fade."
                ),
                "edge_source": "Event vol/pinning/IV crush",
                "best_when": "Event move overpriced, stock stays near strike, near IV rich relative to long IV",
                "main_risks": [
                    "Large directional gap",
                    "Earnings timestamp error",
                    "Early assignment on short leg",
                    "Liquidity",
                ],
                "status": "active",
            },
            {
                "id": "skew_momentum_vertical",
                "name": "Skew Momentum Vertical Spread",
                "core_idea": (
                    "Short-dated debit vertical. Buy ATM/slightly OTM option, sell farther OTM option same expiry. "
                    "Momentum-confirmed direction + overpriced wing skew finances the trade."
                ),
                "edge_source": "Momentum + overpriced wing skew",
                "best_when": "Strong momentum confirmed, wing rich enough to finance debit",
                "main_risks": [
                    "Momentum reversal",
                    "Wing not actually rich",
                    "High debit",
                    "Short DTE theta decay",
                ],
                "status": "active",
            },
            {
                "id": "forward_factor_calendar",
                "name": "Forward Factor Double Calendar",
                "core_idea": (
                    "Dry-run research strategy. +/-35-delta put+call double calendar, ~60/90 DTE. "
                    "Forward Factor = front_ex_earnings_iv / forward_iv - 1. Target FF > 0.20."
                ),
                "edge_source": "Ex-earnings front vol rich vs forward vol",
                "best_when": "Source-qualified FF > threshold, structure liquid",
                "main_risks": [
                    "Source IV wrong",
                    "Structure illiquid",
                    "Model unvalidated",
                ],
                "status": "dry_run_research_only",
            },
            {
                "id": "sector_rotation",
                "name": "Sector Rotation / Macro Relative Strength",
                "core_idea": (
                    "Not yet an options strategy. Portfolio construction using sector ETF relative strength, "
                    "macro regime, breadth, portfolio gap."
                ),
                "edge_source": "Macro/relative strength/portfolio gap",
                "best_when": "Leadership clear and portfolio gap exists",
                "main_risks": [
                    "Whipsaw",
                    "Overfitting",
                    "Macro false signal",
                ],
                "status": "not_started",
            },
        ]
    })


@knowledge_bp.route("/signals")
@require_auth
def knowledge_signals():
    return jsonify({
        "signals": [
            {
                "term": "iv_edge",
                "definition": "Front IV minus back IV on a calendar spread. Positive = favorable (front rich). Negative = unfavorable.",
                "used_by": ["earnings_calendar"],
            },
            {
                "term": "debit_pct_underlying",
                "definition": (
                    "Debit as % of underlying share price. Tiered cap by price band "
                    "-- not a signal failure when blocked, a sizing constraint."
                ),
                "used_by": ["earnings_calendar"],
            },
            {
                "term": "skew_richness",
                "definition": "How overpriced the short wing option is relative to fair value. Financing source for the vertical's debit.",
                "used_by": ["skew_momentum_vertical"],
            },
            {
                "term": "raw_skew_score",
                "definition": "Skew richness before lottery-call filter applied.",
                "used_by": ["skew_momentum_vertical"],
            },
            {
                "term": "adjusted_skew_score",
                "definition": (
                    "Skew richness after stripping lottery-call contracts (delta<0.15, premium<$0.10). "
                    "Used for PASS/FAIL gate."
                ),
                "used_by": ["skew_momentum_vertical"],
            },
            {
                "term": "forward_factor",
                "definition": (
                    "front_ex_earnings_iv / forward_iv - 1. Measures whether front vol is rich vs "
                    "forward term structure, stripped of earnings distortion."
                ),
                "used_by": ["forward_factor_calendar"],
            },
            {
                "term": "pct_of_max_profit",
                "definition": "Current vertical spread value as % of max theoretical profit. Drives exit signal.",
                "used_by": ["skew_momentum_vertical"],
            },
            {
                "term": "exit_signal",
                "definition": (
                    "HOLD / EXIT_TARGET / EXIT_STOP / EXIT_EXPIRY -- advisory only, "
                    "never triggers any broker action."
                ),
                "used_by": ["skew_momentum_vertical"],
            },
            {
                "term": "momentum_score",
                "definition": (
                    "Composite directional momentum from 3m/6m/12m price returns, SMA crossovers, "
                    "and relative strength vs QQQ. Minimum threshold required for vertical entry."
                ),
                "used_by": ["skew_momentum_vertical"],
            },
            {
                "term": "reward_risk_ratio",
                "definition": "Max profit / max loss on a vertical spread. Minimum 1.5x required, 2.0x preferred.",
                "used_by": ["skew_momentum_vertical"],
            },
        ]
    })


@knowledge_bp.route("/thresholds")
@require_auth
def knowledge_thresholds():
    return jsonify({
        "earnings_calendar": {
            "debit_cap_tier_1_max_price": config.CALENDAR_DEBIT_CAP_TIER_1_MAX_PRICE,
            "debit_cap_tier_1_pct": config.CALENDAR_DEBIT_CAP_TIER_1_PCT,
            "debit_cap_tier_2_max_price": config.CALENDAR_DEBIT_CAP_TIER_2_MAX_PRICE,
            "debit_cap_tier_2_pct": config.CALENDAR_DEBIT_CAP_TIER_2_PCT,
            "debit_cap_tier_3_pct": config.CALENDAR_DEBIT_CAP_TIER_3_PCT,
            "max_debit_pct_of_account": config.CALENDAR_MAX_DEBIT_PCT_OF_ACCOUNT,
            "max_debit_dollars": config.CALENDAR_MAX_DEBIT_DOLLARS,
            "max_account_risk_pct": config.CALENDAR_MAX_ACCOUNT_RISK_PCT,
            "profit_target_pct": config.CALENDAR_LIFECYCLE_PROFIT_TARGET_PCT,
            "stop_loss_pct": config.CALENDAR_LIFECYCLE_MAX_LOSS_PCT,
            "price_freshness_threshold": config.CALENDAR_PRICE_FRESHNESS_THRESHOLD,
            "min_open_interest": config.CALENDAR_MIN_OPEN_INTEREST,
            "min_volume": config.CALENDAR_MIN_VOLUME,
        },
        "skew_momentum_vertical": {
            "lottery_call_delta_threshold": config.SKEW_LOTTERY_CALL_DELTA_THRESHOLD,
            "lottery_call_premium_threshold": config.SKEW_LOTTERY_CALL_PREMIUM_THRESHOLD,
            "richness_threshold": config.SKEW_RICHNESS_THRESHOLD,
            "profit_target_pct": config.SKEW_PROFIT_TARGET_PCT,
            "stop_loss_pct": config.SKEW_STOP_LOSS_PCT,
            "exit_dte_threshold": config.SKEW_EXIT_DTE_THRESHOLD,
            "min_momentum_score": config.SKEW_VERTICAL_MIN_MOMENTUM_SCORE,
            "min_reward_risk": config.SKEW_VERTICAL_MIN_REWARD_RISK,
            "preferred_reward_risk": config.SKEW_VERTICAL_PREFERRED_REWARD_RISK,
            "max_debit_pct_of_width": config.SKEW_VERTICAL_MAX_DEBIT_PCT_OF_WIDTH,
            "max_debit_dollars": config.SKEW_VERTICAL_MAX_DEBIT_DOLLARS,
            "max_account_risk_pct": config.SKEW_VERTICAL_MAX_ACCOUNT_RISK_PCT,
            "min_dte": config.SKEW_VERTICAL_MIN_DTE,
            "target_dte": config.SKEW_VERTICAL_TARGET_DTE,
            "max_dte": config.SKEW_VERTICAL_MAX_DTE,
        },
        "forward_factor_calendar": {
            "pass_threshold": config.FF_MIN_FORWARD_FACTOR,
            "dry_run": True,
            "chain_dte_range": "50-105",
            "structure_delta_target": 0.35,
            **_ff_live_summary(),
        },
        "open_options_lifecycle": {
            "profit_target_pct": config.SKEW_PROFIT_TARGET_PCT,
            "stop_loss_pct": config.SKEW_STOP_LOSS_PCT,
            "exit_dte_threshold": config.SKEW_EXIT_DTE_THRESHOLD,
            "exit_signals": ["HOLD", "EXIT_TARGET", "EXIT_STOP", "EXIT_EXPIRY"],
            "note": "Exit signals are advisory only. No broker action is ever triggered.",
        },
        "run": {
            "earnings_discovery_window_days": config.EARNINGS_DISCOVERY_END_DAYS,
            "user_run_rate_limit_per_hour": config.USER_RUN_RATE_LIMIT_PER_HOUR,
        },
        "ff_strategy": _ff_live_summary(),
    })


@knowledge_bp.route("/gates")
@require_auth
def knowledge_gates():
    return jsonify({
        "gates": {
            "earnings_calendar": {
                "pass": (
                    "Short expires before earnings, long captures event, debit within tiered cap, "
                    "account risk OK, liquidity sufficient"
                ),
                "watch": "Signal valid but one non-fatal gate unresolved (e.g. research-only mode)",
                "fail_reasons": {
                    "DEBIT_TOO_LARGE": (
                        "Structure valid, sizing blocked. NOT a signal failure "
                        "-- reconsider at smaller size or wait for price movement."
                    ),
                    "ACCOUNT_RISK_TOO_LARGE": "Debit exceeds % of total account value cap.",
                    "EARNINGS_DATE_UNCONFIRMED": (
                        "Single-source earnings date, cannot confirm structure timing."
                    ),
                    "NO_VALID_EXPIRATION_PAIR": (
                        "No expiration satisfies short-before-earnings AND "
                        "long-captures-event simultaneously."
                    ),
                },
            },
            "skew_momentum_vertical": {
                "pass": (
                    "Momentum confirmed, adjusted skew score exceeds richness threshold, "
                    "debit/liquidity acceptable"
                ),
                "watch": (
                    "Momentum confirmed but skew not rich enough to finance debit "
                    "at acceptable reward/risk"
                ),
                "fail_reasons": {
                    "DEBIT_TOO_LARGE": "Wing financing insufficient relative to debit.",
                    "OPTIONS_ILLIQUID": (
                        "Spread/OI/volume unacceptable. Wait, not a directional signal failure."
                    ),
                },
            },
            "forward_factor_calendar": {
                "note": (
                    "Dry-run only. No PASS state currently actionable. "
                    "All output is research/diagnostic."
                ),
                "stages": (
                    "selected -> cheap_eligible -> chain_approved -> structure_built "
                    "-> [diagnostic_model | source_qualified]"
                ),
            },
        }
    })


@knowledge_bp.route("/sources")
@require_auth
def knowledge_sources():
    return jsonify({
        "sources": [
            {
                "name": "Volvibes -- Earnings Calendar Spread Strategy",
                "type": "video",
                "topic": "earnings_calendar foundation, term structure, IV crush mechanics",
            },
            {
                "name": "Volvibes -- Skew Momentum methodology",
                "type": "video",
                "topic": "skew_momentum_vertical foundation",
            },
            {
                "name": "Volvibes -- Forward Factor research",
                "type": "video",
                "topic": "forward_factor_calendar foundation, FF formula origin",
            },
            {
                "name": "tellmefrankie/ai-investment-skills",
                "type": "github_repo",
                "topic": "lottery-call filter methodology applied to skew richness calculation",
            },
        ],
        "disclaimer": (
            "Educational and product-design documentation. Not financial advice. "
            "ASA remains read-only decision support."
        ),
    })


@knowledge_bp.route("/status")
@require_auth
def knowledge_status():
    return jsonify({
        "earnings_calendar": {
            "actionable": bool(config.EARNINGS_CALENDAR_STRATEGY_ENABLED),
            "notes": "Live, tiered debit cap active",
        },
        "skew_momentum_vertical": {
            "actionable": bool(config.SKEW_VERTICAL_STRATEGY_ENABLED),
            "lifecycle_enabled": bool(config.SKEW_VERTICAL_LIFECYCLE_ENABLED),
            "notes": "PASS rows enter Daily Opportunity. Lottery-call filter active.",
        },
        "forward_factor_calendar": {
            "actionable": not bool(config.FORWARD_FACTOR_DRY_RUN),
            "dry_run": bool(config.FORWARD_FACTOR_DRY_RUN),
            "source_qualification_enabled": True,
            "notes": (
                "FF can produce PASS/WATCH verdicts for source-qualified readings (no earnings contamination). "
                "Earnings-contaminated readings remain diagnostic only. "
                "FF_DRY_RUN=True means signals are real but execution is gated — treat PASS/WATCH as actionable for manual review."
            ),
        },
        "sector_rotation": {
            "actionable": False,
            "notes": "Not implemented.",
        },
    })


@knowledge_bp.route("/positions")
@require_auth
def knowledge_positions():
    """Open options position summary for agent context. Read-only, no provider calls."""
    from app.services.report_snapshot_service import ReportSnapshotRepository

    repo = ReportSnapshotRepository()
    snapshot = repo.latest_success(include_full=True)
    if not snapshot:
        return jsonify({
            "has_open_verticals": False,
            "has_open_calendars": False,
            "has_single_legs": False,
            "vertical_count": 0,
            "calendar_count": 0,
            "single_leg_count": 0,
            "verticals": [],
            "calendars": [],
            "single_legs": [],
        })

    summary_data = repo.load_summary(snapshot, full=True)
    report = summary_data.get("report_data", {}) or {}
    tradier = report.get("tradier_snapshot", {}) or {}
    open_opts = tradier.get("_open_options_positions", {}) or {}

    raw_verticals = open_opts.get("verticals", []) or []
    raw_calendars = open_opts.get("calendars", []) or []
    opts_summary = open_opts.get("summary", {}) or {}

    compact_verticals = []
    for v in raw_verticals:
        if not isinstance(v, dict):
            continue
        compact_verticals.append({
            "ticker": v.get("ticker"),
            "option_type": v.get("option_type"),
            "long_strike": v.get("long_strike"),
            "short_strike": v.get("short_strike"),
            "expiration": v.get("expiration"),
            "dte": v.get("dte"),
            "quantity": v.get("quantity"),
            "net_debit": v.get("net_debit"),
            "current_value": v.get("current_value"),
            "pct_of_max_profit": v.get("pct_of_max_profit"),
            "unrealized_pnl": v.get("unrealized_pnl"),
            "unrealized_pnl_pct": v.get("unrealized_pnl_pct"),
            "exit_signal": v.get("exit_signal"),
            "broker": v.get("broker"),
        })

    compact_calendars = []
    for c in raw_calendars:
        if not isinstance(c, dict):
            continue
        compact_calendars.append({
            "ticker": c.get("ticker"),
            "option_type": c.get("option_type"),
            "strike": c.get("strike"),
            "front_expiration": c.get("front_expiration"),
            "back_expiration": c.get("back_expiration"),
            "front_dte": c.get("front_dte"),
            "back_dte": c.get("back_dte"),
            "quantity": c.get("quantity"),
            "current_mid_debit": c.get("current_mid_debit"),
            "pnl_pct_estimate": c.get("pnl_pct_estimate"),
            "action": c.get("action"),
            "broker": c.get("broker"),
        })

    raw_singles = open_opts.get("single_legs", []) or []
    compact_singles = []
    for s in raw_singles:
        if not isinstance(s, dict):
            continue
        compact_singles.append({
            "ticker": s.get("ticker"),
            "option_type": s.get("option_type"),
            "position": s.get("position"),
            "strike": s.get("strike"),
            "expiration": s.get("expiration"),
            "dte": s.get("dte"),
            "quantity": s.get("quantity"),
            "average_price": s.get("average_price"),
            "current_price": s.get("current_price"),
            "unrealized_pnl": s.get("unrealized_pnl"),
            "broker": s.get("broker"),
        })

    return jsonify({
        "has_open_verticals": opts_summary.get("has_open_verticals", bool(raw_verticals)),
        "has_open_calendars": opts_summary.get("has_open_calendars", bool(raw_calendars)),
        "has_single_legs": bool(raw_singles),
        "vertical_count": opts_summary.get("vertical_count", len(raw_verticals)),
        "calendar_count": opts_summary.get("calendar_count", len(raw_calendars)),
        "single_leg_count": len(raw_singles),
        "verticals": compact_verticals,
        "calendars": compact_calendars,
        "single_legs": compact_singles,
    })


@knowledge_bp.route("/agent-prompt")
@require_auth
def knowledge_agent_prompt():
    return jsonify({
        "version": "1.0",
        "purpose": (
            "Self-bootstrapping instruction set for an agent acting as a personal "
            "investment advisor using ASA as its data source."
        ),
        "bootstrap_sequence": [
            {"step": 1, "action": "GET /api/advisor/knowledge/strategies", "purpose": "Load strategy philosophies"},
            {"step": 2, "action": "GET /api/advisor/knowledge/signals", "purpose": "Load signal/metric definitions"},
            {
                "step": 3,
                "action": "GET /api/advisor/knowledge/thresholds",
                "purpose": "Load current live thresholds -- re-fetch each session, do not cache across days",
            },
            {"step": 4, "action": "GET /api/advisor/knowledge/gates", "purpose": "Load gate interpretation rules"},
            {
                "step": 5,
                "action": "GET /api/advisor/knowledge/status",
                "purpose": "Confirm which strategies are actionable right now",
            },
        ],
        "morning_brief_sequence": [
            {"step": 1, "action": "GET /api/advisor/status", "purpose": "Run health check"},
            {
                "step": 2,
                "action": "POST /api/user/run",
                "purpose": "Trigger personalization if needed, or check freshness first via core-run-status",
            },
            {"step": 3, "action": "GET /api/advisor/daily", "purpose": "Pull personalized Daily Opportunity"},
            {
                "step": 4,
                "action": "GET /api/advisor/positions",
                "purpose": "Pull positions including open options/verticals",
            },
            {"step": 5, "action": "GET /api/advisor/knowledge/positions", "purpose": "Load open options P&L and exit signals"},
            {"step": 6, "action": "GET /api/user/core-run-status", "purpose": "Check shared signal freshness"},
        ],
        "interpretation_rules": [
            (
                "Debit-blocked calendars (DEBIT_TOO_LARGE) are NOT signal failures. "
                "Structure may be valid, only sizing is constrained."
            ),
            (
                "FF (forward_factor_calendar) source-qualified PASS/WATCH readings are actionable for manual review. "
                "Earnings-contaminated readings (source_qualification='earnings_contaminated') are diagnostic only — do not treat as entry signals. "
                "FF_DRY_RUN=True means trade execution is gated, but the signal quality is real."
            ),
            (
                "When ff_pass_tickers is non-empty in thresholds, always surface FF PASS signals in section 4 "
                "(Strategy Signals) with ticker, structure, debit, and edge_on_margin. These are source-qualified "
                "live signals — present them as actionable for manual review, not as research. "
                "FF_DRY_RUN=True means execution is gated; the signal itself is real."
            ),
            (
                "Skew WATCH rows with SKEW_NOT_RICH_ENOUGH have confirmed momentum but "
                "insufficient wing financing -- report as 'worth monitoring', not as a directional miss."
            ),
            (
                "Always check has_open_verticals/has_open_calendars via GET /api/advisor/knowledge/positions "
                "before recommending a new position on the same ticker -- flag conflicts, do not suppress the recommendation."
            ),
            (
                "Exit signals (EXIT_TARGET, EXIT_STOP, EXIT_EXPIRY) are advisory only. "
                "Never imply any automatic action was taken."
            ),
            (
                "Re-fetch /api/advisor/knowledge/thresholds each session -- thresholds can change "
                "via config without code deploys."
            ),
        ],
        "ff_strategy_context": _ff_agent_context(),
        "ff_signal_instruction": (
            "When ff_strategy_context is non-empty, always surface these in section 4 "
            "(Strategy Signals) of the morning brief. These are source-qualified live "
            "signals — present as actionable for manual review. FF_DRY_RUN=True means "
            "execution is gated; the signal itself is real and reviewed."
        ),
        "active_ff_signals": _ff_agent_context(),
        "options_trading_philosophy": {
            "core": (
                "Options are volatility products. Edge comes from the gap between implied volatility "
                "and realized volatility (VRP, the variance risk premium), never from theta itself."
            ),
            "signal_framing": (
                "Calendar: front implied volatility rich vs forward variance. Skew: put tail overpriced. "
                "FF: forward variance cheap relative to realized."
            ),
            "exit_conditions": (
                "Close when implied volatility drops to the realized volatility forecast (thesis validated) "
                "OR when realized volatility rises toward sold implied volatility (thesis invalidated)."
            ),
            "theta_clarification": (
                "Theta is payment for gamma risk. Positive theta is compensation for negative gamma, "
                "not an edge source on its own."
            ),
        },
        "volatility_framing": {
            "principle": (
                "Frame every options trade in volatility-pricing language, not directional language. "
                "The edge is in the vol surface — IV vs RV, term structure slope, skew richness — "
                "not in predicting stock direction."
            ),
            "terminology": [
                "Use 'vol is rich/cheap' instead of 'stock will go up/down'.",
                "Use 'harvesting IV crush' instead of 'betting on earnings'.",
                "Use 'selling rich front vol, buying cheap back vol' for calendar rationale.",
                "Use 'VRP (volatility risk premium) positive' when IV > RV historically.",
                "Use 'term structure in backwardation' when front IV > back IV (favorable for calendars).",
                "Use 'skew financing the debit' for vertical spread rationale.",
            ],
            "never_say": [
                "Never say 'the stock will move X%' — say 'the market is pricing X% move'.",
                "Never say 'bullish/bearish on earnings' — say 'front vol is rich relative to realized'.",
                "Never frame calendar spreads as directional bets — they are volatility structure trades.",
            ],
        },
        "storage_guidance": (
            "Cache knowledge endpoint responses (strategies, signals, gates, sources) for the session "
            "-- static content. Re-fetch thresholds and status each session. Never cache positions or "
            "daily opportunity data across sessions -- always pull fresh."
        ),
        "output_format_reference": (
            "See /api/advisor/knowledge/status for current actionability before making "
            "any recommendation language."
        ),
        "brief_format": {
            "default": "prose",
            "target_duration": "under 3 minutes listening",
            "lead_with": "action_items_only",
            "position_tables": "on_request_only",
            "ticker_lists": "on_request_only",
            "rules": [
                "Never include position tables in default brief output.",
                "Mention broad movers in prose: 'IBM led the portfolio at +13.8%'.",
                "If a position needs action, call it out explicitly in plain language.",
                "Strategy signals: one sentence per strategy. Details on request.",
                "Market context: 2-3 sentences max on what matters today.",
                "Advisor take: what to do and why. No hedging. Direct.",
                "End with any tickets to raise — just the title and one sentence.",
                "Total brief should be readable aloud in under 3 minutes.",
            ],
            "table_trigger_phrases": [
                "show me my positions",
                "give me the full table",
                "what does the grid look like",
            ],
        },
    })
