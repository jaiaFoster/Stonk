"""
app/providers/earnings_provider.py — Earnings timestamp/data provider.

Earnings Provider v1 is intentionally read-only and defensive. It currently
uses Finnhub's earnings calendar endpoint when FINNHUB_API_KEY is available.
If Finnhub denies access, returns no data, or does not include an earnings
hour, the app still completes and marks the event as unavailable/unknown.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import requests

from app import config
from app.utils.log_safety import sanitize_for_log

REQUEST_TIMEOUT_SECONDS = 15


class EarningsProviderError(RuntimeError):
    """Base exception for earnings provider failures."""


class EarningsAuthError(EarningsProviderError):
    """Raised when the configured earnings provider denies access."""


class EarningsRateLimitError(EarningsProviderError):
    """Raised when the configured earnings provider rate limits requests."""


class FinnhubEarningsProvider:
    """Small client for Finnhub earnings calendar data."""

    base_url = "https://finnhub.io/api/v1"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.FINNHUB_API_KEY

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_earnings_calendar(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Return normalized earnings-calendar entries for one symbol."""
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return []
        if not self.api_key:
            raise EarningsAuthError("FINNHUB_API_KEY is not set.")

        params = {
            "symbol": symbol,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        }
        data = self._request_json("GET", "/calendar/earnings", params=params)
        raw_items = []
        if isinstance(data, dict):
            raw_items = _as_list(data.get("earningsCalendar"))
        return [self._normalize_item(symbol, item) for item in raw_items if isinstance(item, dict)]

    def _request_json(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise EarningsAuthError("FINNHUB_API_KEY is not set.")

        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
            # Finnhub supports token-based auth; header form avoids logging query tokens.
            "X-Finnhub-Token": self.api_key,
        }

        try:
            response = requests.request(
                method=method.upper(),
                url=url,
                params=params or {},
                headers=headers,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise EarningsProviderError(
                sanitize_for_log(f"Earnings provider request failed before response: {e}", [self.api_key])
            ) from e

        if response.status_code in {401, 403}:
            raise EarningsAuthError(
                f"Finnhub earnings calendar returned HTTP {response.status_code}. "
                "The key is present, but this endpoint may be unavailable for the current plan/key."
            )
        if response.status_code == 429:
            raise EarningsRateLimitError("Finnhub earnings calendar returned HTTP 429 Too Many Requests.")
        if response.status_code >= 400:
            message = _safe_response_message(response)
            raise EarningsProviderError(
                sanitize_for_log(f"Finnhub earnings calendar returned HTTP {response.status_code}: {message}", [self.api_key])
            )

        try:
            parsed = response.json()
        except ValueError as e:
            raise EarningsProviderError("Finnhub earnings calendar returned a non-JSON response.") from e
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _normalize_item(symbol: str, raw: dict[str, Any]) -> dict[str, Any]:
        earnings_date = raw.get("date")
        hour_raw = str(raw.get("hour") or "").lower().strip()
        time_of_day, session_label = normalize_earnings_hour(hour_raw)

        return {
            "ticker": symbol,
            "symbol": symbol,
            "source": "finnhub",
            "earnings_date": earnings_date,
            "date": earnings_date,
            "hour": hour_raw or None,
            "time_of_day": time_of_day,
            "session_label": session_label,
            "is_timestamp_confirmed": bool(hour_raw and hour_raw not in {"unknown", "na", "n/a"}),
            "quarter": raw.get("quarter"),
            "year": raw.get("year"),
            "eps_estimate": _float_or_none(raw.get("epsEstimate")),
            "eps_actual": _float_or_none(raw.get("epsActual")),
            "revenue_estimate": _float_or_none(raw.get("revenueEstimate")),
            "revenue_actual": _float_or_none(raw.get("revenueActual")),
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "raw": raw,
        }


def normalize_earnings_hour(hour: str | None) -> tuple[str, str]:
    """Normalize provider hour strings into stable app categories."""
    value = str(hour or "").lower().strip()
    if value in {"bmo", "before market open", "before open", "pre-market", "premarket"}:
        return "before_open", "Before market open"
    if value in {"amc", "after market close", "after close", "post-market", "postmarket"}:
        return "after_close", "After market close"
    if value in {"dmh", "during market hours", "market hours", "during"}:
        return "during_market", "During market hours"
    return "unknown", "Unknown"


def get_provider() -> FinnhubEarningsProvider:
    """Return the configured earnings provider client."""
    # Provider switch exists so a later FMP/Benzinga implementation can be added
    # without changing analysis_service/report_service.
    provider_name = str(config.EARNINGS_PROVIDER or "finnhub").strip().lower()
    if provider_name not in {"finnhub", "default"}:
        # For now, unsupported provider names gracefully fall back to Finnhub.
        return FinnhubEarningsProvider()
    return FinnhubEarningsProvider()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _float_or_none(value: Any) -> float | None:
    try:
        if value in {None, ""}:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_response_message(response: requests.Response) -> str:
    try:
        data = response.json()
        if isinstance(data, dict):
            return str(data.get("message") or data.get("error") or data)
        return str(data)
    except Exception:
        return response.text[:300]
