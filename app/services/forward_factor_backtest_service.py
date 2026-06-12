"""Honest Forward Factor backtest status until point-in-time option history exists."""


def forward_factor_backtest_status() -> dict:
    return {
        "status": "BLOCKED / HISTORICAL OPTIONS DATA UNAVAILABLE",
        "source_reported_threshold": 0.20,
        "source_reported_cagr": "approximately 27%",
        "source_reported_sharpe": "approximately 2.4",
        "source_reported_pair": "approximately 60/90 DTE",
        "source_reported_structure": "approximately ±35-delta double calendar",
        "known_methodology_differences": ["No point-in-time multi-expiration option-chain history is configured."],
    }
