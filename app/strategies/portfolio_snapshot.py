"""
app/strategies/portfolio_snapshot.py — Aggressive Quality-Momentum snapshot v1.

This is the first practical advisor layer. It is intentionally built on the data
available today:
- current position value
- unrealized gain/loss
- allocation weight
- duplicate ticker exposure across accounts
- asset/account type
- relevance-scored news

It does NOT yet use the stronger research-backed factors we want later:
- 3/6/12 month momentum
- 200-day trend
- relative strength vs SPY/QQQ
- revenue growth
- margins/free cash flow
- earnings surprises/revisions

Because of that, this v1 score is useful for portfolio triage but conservative
about true "strong add" recommendations. Later factor modules can raise or lower
these scores with better evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.models.recommendation import Recommendation


class PortfolioSnapshotStrategy:
    name = "Aggressive Quality-Momentum Snapshot v1"

    # Speculative/riskier names get stricter position-size handling until the app
    # has stronger trend/fundamental data.
    SPECULATIVE_TICKERS = {
        "BTC",
        "SOL",
        "QBTS",
        "SMR",
    }

    POSITIVE_NEWS_WORDS = {
        "upgrade",
        "upgraded",
        "beats",
        "beat",
        "raises",
        "raised",
        "record",
        "surge",
        "surges",
        "partnership",
        "approval",
        "approved",
        "buyback",
        "outperform",
        "bullish",
        "growth",
    }

    NEGATIVE_NEWS_WORDS = {
        "downgrade",
        "downgraded",
        "miss",
        "misses",
        "cuts",
        "cut",
        "lawsuit",
        "probe",
        "investigation",
        "sec",
        "falls",
        "fall",
        "plunge",
        "plunges",
        "bearish",
        "guidance cut",
        "layoffs",
        "fraud",
    }

    def evaluate_portfolio(
        self,
        positions: list[dict[str, Any]],
        news_map: dict[str, list[dict[str, Any]]] | None = None,
    ) -> list[dict[str, Any]]:
        """Return one recommendation per current position row."""
        news_map = news_map or {}
        total_value = self._safe_sum(p.get("market_value") for p in positions)
        ticker_counts = Counter(str(p.get("ticker", "UNKNOWN")).upper() for p in positions)
        ticker_values: dict[str, float] = defaultdict(float)

        for p in positions:
            ticker = str(p.get("ticker", "UNKNOWN")).upper()
            ticker_values[ticker] += self._safe_float(p.get("market_value"), 0.0)

        recommendations: list[dict[str, Any]] = []
        for position in positions:
            rec = self.evaluate_position(
                position=position,
                news_map=news_map,
                total_value=total_value,
                ticker_counts=ticker_counts,
                ticker_values=ticker_values,
            )
            recommendations.append(rec.to_dict())

        recommendations.sort(
            key=lambda item: (
                float(item.get("score", 0.0) or 0.0),
                float(item.get("position_value", 0.0) or 0.0),
            ),
            reverse=True,
        )
        return recommendations

    def evaluate_position(
        self,
        position: dict[str, Any],
        news_map: dict[str, list[dict[str, Any]]],
        total_value: float,
        ticker_counts: Counter,
        ticker_values: dict[str, float],
    ) -> Recommendation:
        ticker = str(position.get("ticker", "UNKNOWN")).upper()
        account = str(position.get("account", "Unknown"))
        market_value = self._safe_float(position.get("market_value"), 0.0)
        gain_loss_pct = self._optional_float(position.get("gain_loss_pct"))
        allocation_pct = (market_value / total_value * 100.0) if total_value > 0 else 0.0
        combined_ticker_pct = (
            ticker_values.get(ticker, 0.0) / total_value * 100.0 if total_value > 0 else 0.0
        )
        duplicate_count = ticker_counts.get(ticker, 0)
        articles = news_map.get(ticker, []) or []

        score = 50.0
        breakdown: dict[str, float] = {"base": 50.0}
        reasons: list[str] = []
        risks: list[str] = []

        performance_delta, performance_reasons, performance_risks = self._score_performance(gain_loss_pct)
        score += performance_delta
        breakdown["performance"] = performance_delta
        reasons.extend(performance_reasons)
        risks.extend(performance_risks)

        allocation_delta, allocation_reasons, allocation_risks = self._score_allocation(
            ticker=ticker,
            allocation_pct=allocation_pct,
            combined_ticker_pct=combined_ticker_pct,
            duplicate_count=duplicate_count,
        )
        score += allocation_delta
        breakdown["allocation"] = allocation_delta
        reasons.extend(allocation_reasons)
        risks.extend(allocation_risks)

        news_delta, news_reasons, news_risks = self._score_news(ticker, articles)
        score += news_delta
        breakdown["news"] = news_delta
        reasons.extend(news_reasons)
        risks.extend(news_risks)

        risk_delta, risk_reasons, risk_risks = self._score_asset_risk(ticker, account)
        score += risk_delta
        breakdown["asset_risk"] = risk_delta
        reasons.extend(risk_reasons)
        risks.extend(risk_risks)

        # Cap v1 enthusiasm because we do not yet have trend/fundamental evidence.
        raw_score = score
        score = max(0.0, min(88.0, score))
        if raw_score > score:
            risks.append(
                "Score capped because v1 does not yet include trend, relative strength, or fundamental quality data."
            )

        action = self._action_for_score(score)
        confidence = self._confidence_for_position(
            score=score,
            articles=articles,
            gain_loss_pct=gain_loss_pct,
            allocation_pct=allocation_pct,
        )
        next_check = self._next_check_for_action(action)

        if not reasons:
            reasons.append("Neutral snapshot: no strong positive or negative signal from current live data.")

        return Recommendation(
            ticker=ticker,
            account=account,
            strategy=self.name,
            action=action,
            score=score,
            confidence=confidence,
            allocation_pct=allocation_pct,
            position_value=market_value,
            gain_loss_pct=gain_loss_pct,
            score_breakdown=breakdown,
            reasons=reasons[:5],
            risks=risks[:5],
            next_check=next_check,
        )

    def _score_performance(
        self, gain_loss_pct: float | None
    ) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []

        if gain_loss_pct is None:
            return 0.0, reasons, ["Missing gain/loss percentage; performance score is neutral."]

        if gain_loss_pct >= 50:
            reasons.append("Major winner: current position is up more than 50% from cost basis.")
            return 22.0, reasons, []
        if gain_loss_pct >= 25:
            reasons.append("Strong winner: current position is up more than 25% from cost basis.")
            return 18.0, reasons, []
        if gain_loss_pct >= 10:
            reasons.append("Positive winner: current position is up more than 10% from cost basis.")
            return 12.0, reasons, []
        if gain_loss_pct >= 0:
            reasons.append("Position is green versus cost basis.")
            return 5.0, reasons, []
        if gain_loss_pct >= -10:
            risks.append("Small unrealized loss; do not add unless stronger trend/fundamental data confirms the thesis.")
            return -4.0, reasons, risks
        if gain_loss_pct >= -25:
            risks.append("Meaningful unrealized loss; avoid averaging down without new evidence.")
            return -14.0, reasons, risks
        if gain_loss_pct >= -40:
            risks.append("Large drawdown; thesis should be reviewed before adding capital.")
            return -24.0, reasons, risks

        risks.append("Severe drawdown; candidate for deep review or risk reduction.")
        return -32.0, reasons, risks

    def _score_allocation(
        self,
        ticker: str,
        allocation_pct: float,
        combined_ticker_pct: float,
        duplicate_count: int,
    ) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        delta = 0.0

        if allocation_pct < 0.5:
            risks.append("Tiny position; signal may not matter much at current size.")
            delta -= 2.0
        elif allocation_pct < 3:
            reasons.append("Small position size leaves room to scale if future factors confirm strength.")
            delta += 3.0
        elif allocation_pct < 8:
            reasons.append("Position size is meaningful but still flexible for an aggressive portfolio.")
            delta += 8.0
        elif allocation_pct < 15:
            reasons.append("High-conviction position size; monitor concentration before adding more.")
            delta += 4.0
        elif allocation_pct < 20:
            risks.append("Large single-position allocation; adding more requires exceptional evidence.")
            delta -= 3.0
        else:
            risks.append("Very high allocation; trim/rebalance risk should be considered.")
            delta -= 10.0

        if duplicate_count > 1:
            risks.append(f"Duplicate exposure: {ticker} appears across {duplicate_count} accounts.")
            if combined_ticker_pct > 10:
                delta -= 5.0
                risks.append("Combined ticker allocation is above 10% across accounts.")

        if ticker in self.SPECULATIVE_TICKERS:
            if combined_ticker_pct > 10:
                delta -= 14.0
                risks.append("Speculative ticker is above 10% combined allocation.")
            elif combined_ticker_pct > 6:
                delta -= 8.0
                risks.append("Speculative ticker is above the preferred 3–6% aggressive-risk band.")
            elif combined_ticker_pct <= 6:
                delta += 2.0
                reasons.append("Speculative ticker is sized within a controlled risk band.")

        return delta, reasons, risks

    def _score_news(
        self, ticker: str, articles: list[dict[str, Any]]
    ) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []

        if not articles:
            return 0.0, reasons, ["No relevant recent company news found; catalyst score is neutral."]

        scores = [self._safe_float(article.get("relevance_score"), 0.0) for article in articles]
        avg_relevance = sum(scores) / len(scores) if scores else 0.0
        combined_text = " ".join(str(article.get("title", "")) for article in articles).lower()

        delta = 0.0
        if len(articles) >= 2 and avg_relevance >= 0.55:
            delta += 6.0
            reasons.append("Multiple relevant recent articles found; catalyst visibility is elevated.")
        elif avg_relevance >= 0.45:
            delta += 3.0
            reasons.append("Relevant recent article found; catalyst visibility is modestly positive.")

        positive_hits = [word for word in self.POSITIVE_NEWS_WORDS if word in combined_text]
        negative_hits = [word for word in self.NEGATIVE_NEWS_WORDS if word in combined_text]

        if positive_hits:
            delta += min(5.0, len(positive_hits) * 2.0)
            reasons.append("Recent headlines contain positive catalyst language.")

        if negative_hits:
            penalty = min(9.0, len(negative_hits) * 3.0)
            delta -= penalty
            risks.append("Recent headlines contain negative catalyst/risk language.")

        return delta, reasons, risks

    def _score_asset_risk(self, ticker: str, account: str) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        delta = 0.0

        if ticker in {"BTC", "SOL"} or account.lower() == "crypto":
            delta -= 8.0
            risks.append("Crypto position: high volatility and different risk profile from equities.")
        elif ticker in {"QBTS", "SMR"}:
            delta -= 6.0
            risks.append("Speculative growth/theme exposure; require stronger confirmation before adding.")
        elif ticker in {"NVDA", "GOOGL", "AMZN", "META", "ORCL", "IBM"}:
            delta += 3.0
            reasons.append("Large-cap technology/AI-related exposure fits the aggressive growth mandate.")

        return delta, reasons, risks

    @staticmethod
    def _action_for_score(score: float) -> str:
        if score >= 82:
            return "ADD / HOLD"
        if score >= 72:
            return "HOLD / WATCH ADD"
        if score >= 60:
            return "HOLD"
        if score >= 45:
            return "WATCH / REVIEW"
        if score >= 30:
            return "AVOID ADDING / REDUCE RISK"
        return "CUT / DEEP REVIEW"

    @staticmethod
    def _confidence_for_position(
        score: float,
        articles: list[dict[str, Any]],
        gain_loss_pct: float | None,
        allocation_pct: float,
    ) -> str:
        evidence_points = 0
        if gain_loss_pct is not None:
            evidence_points += 1
        if allocation_pct > 0:
            evidence_points += 1
        if articles:
            evidence_points += 1

        if evidence_points >= 3 and 45 <= score <= 82:
            return "Medium"
        if evidence_points >= 2:
            return "Low-Medium"
        return "Low"

    @staticmethod
    def _next_check_for_action(action: str) -> str:
        if action in {"ADD / HOLD", "HOLD / WATCH ADD"}:
            return "Before adding, confirm trend/relative strength once price-history data is connected."
        if action == "HOLD":
            return "Continue monitoring; upgrade or downgrade once trend and fundamentals are available."
        if action == "WATCH / REVIEW":
            return "Review thesis and avoid new buys until stronger evidence appears."
        if action == "AVOID ADDING / REDUCE RISK":
            return "Avoid averaging down; review whether position size should be reduced."
        return "Deep review required; consider cutting if future trend/fundamental data confirms weakness."

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @classmethod
    def _optional_float(cls, value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _safe_sum(cls, values: Any) -> float:
        total = 0.0
        for value in values:
            total += cls._safe_float(value, 0.0)
        return total
