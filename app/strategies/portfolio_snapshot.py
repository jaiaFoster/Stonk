"""
app/strategies/portfolio_snapshot.py — Aggressive Quality-Momentum scoring.

This strategy now has two layers:

V1 fallback signals:
- current position value
- unrealized gain/loss
- allocation weight
- duplicate ticker exposure across accounts
- asset/account type
- relevance-scored news

V2 market-data signals from Finnhub:
- 1/3/6/12 month returns
- relative strength versus QQQ or configured benchmark
- 50-day and 200-day trend state
- distance from 52-week high/low
- 30-day annualized volatility proxy
- 30-day average volume

The philosophy remains aggressive but evidence-based: add to strength, avoid
blindly averaging down, let strong winners run, and cut/review broken names when
trend, momentum, and risk all deteriorate.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from app.models.recommendation import Recommendation
from app.services.data_state_message_service import data_state_message, required_market_metrics_complete


class PortfolioSnapshotStrategy:
    name = "Aggressive Quality-Momentum Snapshot v2"

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
        market_metrics: dict[str, dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Return one recommendation per current position row."""
        news_map = news_map or {}
        market_metrics = market_metrics or {}
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
                market_metrics=market_metrics,
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
        market_metrics: dict[str, dict[str, Any]],
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
        metrics = dict(market_metrics.get(ticker, {}) or {})
        has_market_data = required_market_metrics_complete(metrics)

        score = 50.0
        breakdown: dict[str, float] = {"base": 50.0}
        reasons: list[str] = []
        risks: list[str] = []

        market_delta, market_breakdown, market_reasons, market_risks = self._score_market_metrics(ticker, metrics)
        score += market_delta
        breakdown.update(market_breakdown)
        reasons.extend(market_reasons)
        risks.extend(market_risks)

        performance_delta, performance_reasons, performance_risks = self._score_performance(gain_loss_pct)
        score += performance_delta
        breakdown["cost_basis_performance"] = performance_delta
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

        if has_market_data:
            score = max(0.0, min(100.0, score))
        else:
            raw_score = score
            score = max(0.0, min(88.0, score))
            if raw_score > score:
                risks.append(
                    "Score capped because trend and relative-strength data were unavailable for this ticker."
                )

        action = self._action_for_score(score)
        confidence = self._confidence_for_position(
            score=score,
            articles=articles,
            gain_loss_pct=gain_loss_pct,
            allocation_pct=allocation_pct,
            has_market_data=has_market_data,
        )
        next_check = self._next_check_for_action(action, has_market_data=has_market_data)

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
            market_metrics=metrics,
            reasons=reasons[:6],
            risks=risks[:6],
            next_check=next_check,
        )

    def _score_market_metrics(
        self,
        ticker: str,
        metrics: dict[str, Any],
    ) -> tuple[float, dict[str, float], list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        breakdown: dict[str, float] = {}

        if not required_market_metrics_complete(metrics):
            risks.append(data_state_message(metrics.get("data_state") if metrics else None, fetched_at=(metrics or {}).get("fetched_at"), reason=(metrics or {}).get("error")))
            breakdown["market_data"] = 0.0
            return 0.0, breakdown, reasons, risks

        momentum_delta, momentum_reasons, momentum_risks = self._score_momentum(metrics)
        trend_delta, trend_reasons, trend_risks = self._score_trend(metrics)
        risk_delta, risk_reasons, risk_risks = self._score_market_risk(metrics)

        breakdown["momentum_relative_strength"] = momentum_delta
        breakdown["trend_health"] = trend_delta
        breakdown["liquidity_volatility"] = risk_delta

        reasons.extend(momentum_reasons)
        reasons.extend(trend_reasons)
        reasons.extend(risk_reasons)
        risks.extend(momentum_risks)
        risks.extend(trend_risks)
        risks.extend(risk_risks)

        return momentum_delta + trend_delta + risk_delta, breakdown, reasons, risks

    def _score_momentum(self, metrics: dict[str, Any]) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        delta = 0.0

        r3 = self._optional_float(metrics.get("return_3m_pct"))
        r6 = self._optional_float(metrics.get("return_6m_pct"))
        r12 = self._optional_float(metrics.get("return_12m_pct"))
        rs6 = self._optional_float(metrics.get("relative_strength_6m_pct"))
        rs12 = self._optional_float(metrics.get("relative_strength_12m_pct"))

        if r6 is not None:
            if r6 >= 30:
                delta += 10.0
                reasons.append("Strong 6-month absolute momentum.")
            elif r6 >= 12:
                delta += 7.0
                reasons.append("Positive 6-month momentum.")
            elif r6 >= 0:
                delta += 3.0
                reasons.append("6-month momentum is positive but not exceptional.")
            elif r6 <= -20:
                delta -= 12.0
                risks.append("Weak 6-month momentum; avoid averaging down without a reversal.")
            else:
                delta -= 6.0
                risks.append("6-month momentum is negative.")

        if r12 is not None:
            if r12 >= 40:
                delta += 8.0
                reasons.append("Strong 12-month momentum confirms longer-term leadership.")
            elif r12 >= 10:
                delta += 4.0
                reasons.append("12-month momentum is positive.")
            elif r12 <= -15:
                delta -= 8.0
                risks.append("12-month momentum is negative enough to question the thesis.")

        if r3 is not None:
            if r3 >= 15:
                delta += 4.0
                reasons.append("Recent 3-month momentum is accelerating.")
            elif r3 <= -12:
                delta -= 5.0
                risks.append("Recent 3-month momentum is deteriorating.")

        if rs6 is not None:
            if rs6 >= 8:
                delta += 6.0
                reasons.append("6-month relative strength is beating the benchmark.")
            elif rs6 <= -8:
                delta -= 7.0
                risks.append("6-month relative strength is lagging the benchmark.")

        if rs12 is not None:
            if rs12 >= 10:
                delta += 4.0
                reasons.append("12-month relative strength confirms benchmark leadership.")
            elif rs12 <= -10:
                delta -= 5.0
                risks.append("12-month relative strength is weak versus the benchmark.")

        return max(-25.0, min(28.0, delta)), reasons, risks

    def _score_trend(self, metrics: dict[str, Any]) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        delta = 0.0

        above_50 = metrics.get("above_sma_50")
        above_200 = metrics.get("above_sma_200")
        price_vs_200 = self._optional_float(metrics.get("price_vs_sma_200_pct"))
        dist_high = self._optional_float(metrics.get("distance_from_52w_high_pct"))
        dist_low = self._optional_float(metrics.get("distance_from_52w_low_pct"))

        if above_200 is True:
            delta += 12.0
            reasons.append("Price is above the 200-day trend filter.")
        elif above_200 is False:
            delta -= 18.0
            risks.append("Price is below the 200-day trend filter; no adding until trend improves.")

        if above_50 is True:
            delta += 4.0
            reasons.append("Price is above the 50-day moving average.")
        elif above_50 is False:
            delta -= 5.0
            risks.append("Price is below the 50-day moving average.")

        if price_vs_200 is not None:
            if price_vs_200 >= 20:
                delta += 3.0
                reasons.append("Price is strongly above the 200-day average, confirming trend leadership.")
            elif price_vs_200 <= -15:
                delta -= 6.0
                risks.append("Price is materially below the 200-day average.")

        if dist_high is not None:
            if dist_high >= -8:
                delta += 5.0
                reasons.append("Trading near 52-week highs, consistent with momentum leadership.")
            elif dist_high <= -35:
                delta -= 8.0
                risks.append("Far from 52-week highs; this is not currently acting like a leader.")
            elif dist_high <= -20:
                delta -= 4.0
                risks.append("Meaningfully below 52-week highs.")

        if dist_low is not None and dist_low >= 60:
            delta += 2.0
            reasons.append("Well above 52-week lows, showing recovery/leadership from the base.")

        return max(-28.0, min(26.0, delta)), reasons, risks

    def _score_market_risk(self, metrics: dict[str, Any]) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []
        delta = 0.0

        vol = self._optional_float(metrics.get("volatility_30d_pct"))
        avg_volume = self._optional_float(metrics.get("avg_volume_30d"))

        if avg_volume is not None:
            if avg_volume >= 10_000_000:
                delta += 3.0
                reasons.append("Strong trading liquidity based on 30-day average volume.")
            elif avg_volume >= 1_000_000:
                delta += 1.0
                reasons.append("Acceptable trading liquidity based on 30-day average volume.")
            elif avg_volume < 500_000:
                delta -= 8.0
                risks.append("Low average volume; liquidity risk is elevated.")
            else:
                delta -= 4.0
                risks.append("Thin average volume; size entries carefully.")

        if vol is not None:
            if vol >= 90:
                delta -= 8.0
                risks.append("Very high recent volatility; aggressive sizing should be capped.")
            elif vol >= 65:
                delta -= 4.0
                risks.append("High recent volatility; avoid oversized adds.")
            elif vol <= 40:
                delta += 2.0
                reasons.append("Recent volatility is controlled relative to aggressive-growth risk.")

        return max(-14.0, min(8.0, delta)), reasons, risks

    def _score_performance(
        self, gain_loss_pct: float | None
    ) -> tuple[float, list[str], list[str]]:
        reasons: list[str] = []
        risks: list[str] = []

        if gain_loss_pct is None:
            return 0.0, reasons, ["Missing gain/loss percentage; cost-basis score is neutral."]

        # Cost basis is useful, but market trend now matters more than whether we
        # personally bought at a good price.
        if gain_loss_pct >= 50:
            reasons.append("Major winner versus cost basis; let winners run if trend remains healthy.")
            return 10.0, reasons, []
        if gain_loss_pct >= 25:
            reasons.append("Strong winner versus cost basis.")
            return 8.0, reasons, []
        if gain_loss_pct >= 10:
            reasons.append("Positive winner versus cost basis.")
            return 5.0, reasons, []
        if gain_loss_pct >= 0:
            reasons.append("Position is green versus cost basis.")
            return 2.0, reasons, []
        if gain_loss_pct >= -10:
            risks.append("Small unrealized loss; add only if trend/momentum confirms.")
            return -2.0, reasons, risks
        if gain_loss_pct >= -25:
            risks.append("Meaningful unrealized loss; avoid averaging down without evidence.")
            return -5.0, reasons, risks
        if gain_loss_pct >= -40:
            risks.append("Large drawdown; thesis should be reviewed before adding capital.")
            return -9.0, reasons, risks

        risks.append("Severe drawdown; candidate for deep review or risk reduction.")
        return -12.0, reasons, risks

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
            delta -= 1.0
        elif allocation_pct < 3:
            reasons.append("Small position size leaves room to scale if future factors confirm strength.")
            delta += 2.0
        elif allocation_pct < 8:
            reasons.append("Position size is meaningful but still flexible for an aggressive portfolio.")
            delta += 5.0
        elif allocation_pct < 15:
            reasons.append("High-conviction position size; monitor concentration before adding more.")
            delta += 2.0
        elif allocation_pct < 20:
            risks.append("Large single-position allocation; adding more requires exceptional evidence.")
            delta -= 4.0
        else:
            risks.append("Very high allocation; trim/rebalance risk should be considered.")
            delta -= 12.0

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
                delta += 1.0
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
            delta += 4.0
            reasons.append("Multiple relevant recent articles found; catalyst visibility is elevated.")
        elif avg_relevance >= 0.45:
            delta += 2.0
            reasons.append("Relevant recent article found; catalyst visibility is modestly positive.")

        positive_hits = [word for word in self.POSITIVE_NEWS_WORDS if word in combined_text]
        negative_hits = [word for word in self.NEGATIVE_NEWS_WORDS if word in combined_text]

        if positive_hits:
            delta += min(4.0, len(positive_hits) * 1.5)
            reasons.append("Recent headlines contain positive catalyst language.")

        if negative_hits:
            penalty = min(8.0, len(negative_hits) * 2.5)
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
            delta -= 5.0
            risks.append("Speculative growth/theme exposure; require stronger confirmation before adding.")
        elif ticker in {"NVDA", "GOOGL", "AMZN", "META", "ORCL", "IBM"}:
            delta += 2.0
            reasons.append("Large-cap technology/AI-related exposure fits the aggressive growth mandate.")

        return delta, reasons, risks

    @staticmethod
    def _action_for_score(score: float) -> str:
        if score >= 90:
            return "STRONG ADD / HOLD"
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
        has_market_data: bool,
    ) -> str:
        evidence_points = 0
        if has_market_data:
            evidence_points += 2
        if gain_loss_pct is not None:
            evidence_points += 1
        if allocation_pct > 0:
            evidence_points += 1
        if articles:
            evidence_points += 1

        if has_market_data and evidence_points >= 4 and (score >= 72 or score <= 45):
            return "High"
        if evidence_points >= 3:
            return "Medium"
        if evidence_points >= 2:
            return "Low-Medium"
        return "Low"

    @staticmethod
    def _next_check_for_action(action: str, has_market_data: bool) -> str:
        if not has_market_data:
            return "Market metrics unavailable; recheck after shared provider data is available."
        if action in {"STRONG ADD / HOLD", "ADD / HOLD", "HOLD / WATCH ADD"}:
            return "Before adding, confirm price still holds trend/relative-strength leadership."
        if action == "HOLD":
            return "Continue monitoring trend, relative strength, and position size."
        if action == "WATCH / REVIEW":
            return "Review thesis and avoid new buys until trend/momentum improves."
        if action == "AVOID ADDING / REDUCE RISK":
            return "Avoid averaging down; review whether position size should be reduced."
        return "Deep review required; consider cutting if trend/fundamental data continues to confirm weakness."

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
