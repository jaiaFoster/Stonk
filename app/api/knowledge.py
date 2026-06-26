"""Knowledge API — static + dynamic strategy reference for agents and users.

All endpoints read-only. No provider calls. No writes.
Auth: @require_auth from app.auth (sets g.current_user).
"""

from __future__ import annotations

from flask import Blueprint, jsonify

from app import config
from app.auth import require_auth

knowledge_bp = Blueprint("knowledge", __name__, url_prefix="/api/advisor/knowledge")


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
        "storage_guidance": (
            "Cache knowledge endpoint responses (strategies, signals, gates, sources) for the session "
            "-- static content. Re-fetch thresholds and status each session. Never cache positions or "
            "daily opportunity data across sessions -- always pull fresh."
        ),
        "output_format_reference": (
            "See /api/advisor/knowledge/status for current actionability before making "
            "any recommendation language."
        ),
    })
