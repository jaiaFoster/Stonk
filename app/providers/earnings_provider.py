"""
app/providers/earnings_provider.py — Earnings timestamp/data provider.

Earnings Provider v2 is intentionally read-only and defensive. It can use
multiple provider sources and merge/dedupe their results:

- Finnhub: JSON earnings calendar, often includes session/hour when available.
- Alpha Vantage: CSV earnings calendar, useful as a secondary universe source.

If one provider fails, rate-limits, or returns no data, the app still completes
and falls back to the next configured provider when possible.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone
from typing import Any, Protocol

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


class EarningsProvider(Protocol):
    name: str

    @property
    def is_configured(self) -> bool: ...

    def get_earnings_calendar(self, symbol: str, start_date: date, end_date: date) -> list[dict[str, Any]]: ...

    def get_earnings_calendar_range(self, start_date: date, end_date: date) -> list[dict[str, Any]]: ...


class FinnhubEarningsProvider:
    """Small client for Finnhub earnings calendar data."""

    name = "finnhub"
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

    def get_earnings_calendar_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """Return normalized earnings-calendar entries for all symbols in a date range."""
        if not self.api_key:
            raise EarningsAuthError("FINNHUB_API_KEY is not set.")

        params = {
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        }
        data = self._request_json("GET", "/calendar/earnings", params=params)
        raw_items = []
        if isinstance(data, dict):
            raw_items = _as_list(data.get("earningsCalendar"))

        normalized: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            symbol = str(item.get("symbol") or item.get("ticker") or "").upper().strip()
            if not symbol:
                continue
            normalized.append(self._normalize_item(symbol, item))
        return normalized

    def _request_json(self, method: str, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.api_key:
            raise EarningsAuthError("FINNHUB_API_KEY is not set.")

        url = f"{self.base_url}{path}"
        headers = {
            "Accept": "application/json",
            # Header form avoids logging query tokens.
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
                sanitize_for_log(f"Finnhub earnings request failed before response: {e}", [self.api_key])
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


class AlphaVantageEarningsProvider:
    """Client for Alpha Vantage earnings calendar CSV data."""

    name = "alphavantage"
    base_url = "https://www.alphavantage.co/query"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or config.ALPHA_VANTAGE_API_KEY

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def get_earnings_calendar(
        self,
        symbol: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        symbol = str(symbol or "").upper().strip()
        if not symbol:
            return []
        rows = self._download_calendar_rows(symbol=symbol)
        return [self._normalize_item(row) for row in rows if self._row_in_window(row, start_date, end_date)]

    def get_earnings_calendar_range(
        self,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        rows = self._download_calendar_rows(symbol=None)
        return [self._normalize_item(row) for row in rows if self._row_in_window(row, start_date, end_date)]

    def _download_calendar_rows(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if not self.api_key:
            raise EarningsAuthError("ALPHA_VANTAGE_API_KEY is not set.")

        params = {
            "function": "EARNINGS_CALENDAR",
            "horizon": _safe_alpha_horizon(config.ALPHA_VANTAGE_EARNINGS_HORIZON),
            "apikey": self.api_key,
        }
        if symbol:
            params["symbol"] = symbol

        try:
            response = requests.get(
                self.base_url,
                params=params,
                headers={"Accept": "text/csv,application/json;q=0.9,*/*;q=0.8"},
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise EarningsProviderError(
                sanitize_for_log(f"Alpha Vantage earnings request failed before response: {e}", [self.api_key])
            ) from e

        if response.status_code in {401, 403}:
            raise EarningsAuthError(f"Alpha Vantage earnings calendar returned HTTP {response.status_code}.")
        if response.status_code == 429:
            raise EarningsRateLimitError("Alpha Vantage earnings calendar returned HTTP 429 Too Many Requests.")
        if response.status_code >= 400:
            raise EarningsProviderError(
                sanitize_for_log(
                    f"Alpha Vantage earnings calendar returned HTTP {response.status_code}: {response.text[:300]}",
                    [self.api_key],
                )
            )

        text = response.text or ""
        stripped = text.strip()
        if not stripped:
            return []
        # Alpha Vantage sometimes returns JSON-ish error/rate-limit payloads.
        lowered = stripped.lower()
        if stripped.startswith("{") or "thank you for using alpha vantage" in lowered or "our standard api call frequency" in lowered:
            if "standard api call frequency" in lowered or "thank you" in lowered:
                raise EarningsRateLimitError("Alpha Vantage earnings calendar returned an API frequency/limit message.")
            raise EarningsProviderError(f"Alpha Vantage earnings calendar returned non-CSV payload: {stripped[:200]}")

        reader = csv.DictReader(io.StringIO(text))
        return [row for row in reader if isinstance(row, dict)]

    @staticmethod
    def _row_in_window(row: dict[str, Any], start_date: date, end_date: date) -> bool:
        event_date = _parse_date(row.get("reportDate") or row.get("date") or row.get("earnings_date"))
        if not event_date:
            return False
        return start_date <= event_date <= end_date

    @staticmethod
    def _normalize_item(raw: dict[str, Any]) -> dict[str, Any]:
        symbol = str(raw.get("symbol") or raw.get("ticker") or "").upper().strip()
        earnings_date = raw.get("reportDate") or raw.get("date") or raw.get("earnings_date")
        return {
            "ticker": symbol,
            "symbol": symbol,
            "source": "alphavantage",
            "earnings_date": earnings_date,
            "date": earnings_date,
            "hour": None,
            "time_of_day": "unknown",
            "session_label": "Unknown",
            # Alpha Vantage's earnings-calendar endpoint gives dates, but not a
            # before-open/after-close timestamp in this v1 integration.
            "is_timestamp_confirmed": False,
            "quarter": None,
            "year": None,
            "company_name": raw.get("name"),
            "fiscal_date_ending": raw.get("fiscalDateEnding"),
            "eps_estimate": _float_or_none(raw.get("estimate")),
            "eps_actual": None,
            "revenue_estimate": None,
            "revenue_actual": None,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "raw": raw,
        }


class CompositeEarningsProvider:
    """Provider that merges/falls back across configured earnings sources."""

    name = "composite"

    def __init__(self, providers: list[EarningsProvider]):
        self.providers = [provider for provider in providers if provider.is_configured]

    @property
    def is_configured(self) -> bool:
        return bool(self.providers)

    @property
    def provider_names(self) -> list[str]:
        return [provider.name for provider in self.providers]

    def get_earnings_calendar(self, symbol: str, start_date: date, end_date: date) -> list[dict[str, Any]]:
        return self._call_and_merge("get_earnings_calendar", symbol, start_date, end_date)

    def get_earnings_calendar_range(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        return self._call_and_merge("get_earnings_calendar_range", start_date, end_date)

    def _call_and_merge(self, method_name: str, *args: Any) -> list[dict[str, Any]]:
        if not self.providers:
            raise EarningsAuthError("No earnings providers are configured.")

        collected: list[dict[str, Any]] = []
        provider_errors: list[str] = []

        for provider in self.providers:
            try:
                items = getattr(provider, method_name)(*args)
            except EarningsRateLimitError as e:
                provider_errors.append(f"{provider.name}: {e}")
                continue
            except EarningsProviderError as e:
                provider_errors.append(f"{provider.name}: {e}")
                continue
            except Exception as e:
                provider_errors.append(f"{provider.name}: {e}")
                continue

            if items:
                collected.extend(items)
                if not config.EARNINGS_MERGE_PROVIDER_EVENTS:
                    break

        if collected:
            return _merge_dedupe_events(collected)

        # Avoid hard-failing the whole run just because both calendars are empty.
        # Raise only when every configured provider errored, which helps logs show
        # access/limit problems clearly.
        if provider_errors and len(provider_errors) >= len(self.providers):
            raise EarningsProviderError("; ".join(provider_errors[:3]))
        return []


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


def get_provider() -> CompositeEarningsProvider:
    """Return the configured earnings provider client."""
    providers_by_name: dict[str, EarningsProvider] = {
        "finnhub": FinnhubEarningsProvider(),
        "alphavantage": AlphaVantageEarningsProvider(),
        "alpha_vantage": AlphaVantageEarningsProvider(),
        "av": AlphaVantageEarningsProvider(),
    }

    ordered: list[EarningsProvider] = []
    for name in config.EARNINGS_PROVIDER_ORDER or [config.EARNINGS_PROVIDER or "finnhub"]:
        provider = providers_by_name.get(str(name).strip().lower())
        if provider and provider not in ordered:
            ordered.append(provider)

    # Backward compatibility: if EARNINGS_PROVIDER_ORDER is misconfigured, use
    # the legacy EARNINGS_PROVIDER value.
    if not ordered:
        ordered.append(providers_by_name.get(config.EARNINGS_PROVIDER, FinnhubEarningsProvider()))

    return CompositeEarningsProvider(ordered)


def configured_provider_names() -> list[str]:
    provider = get_provider()
    return provider.provider_names


def earnings_provider_secret_values() -> list[str | None]:
    return [
        config.FINNHUB_API_KEY,
        config.ALPHA_VANTAGE_API_KEY,
        config.RUN_TOKEN,
    ]


def _merge_dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        ticker = str(event.get("ticker") or event.get("symbol") or "").upper().strip()
        event_date = str(event.get("earnings_date") or event.get("date") or "")[:10]
        if not ticker or not event_date:
            continue
        key = (ticker, event_date)
        existing = merged.get(key)
        if not existing:
            merged[key] = dict(event)
            merged[key]["sources_seen"] = [str(event.get("source") or "unknown")]
            continue

        source = str(event.get("source") or "unknown")
        sources = list(existing.get("sources_seen") or [])
        if source not in sources:
            sources.append(source)
        existing["sources_seen"] = sources

        # Prefer entries with confirmed session/hour data; otherwise keep the
        # earlier provider result and fill missing estimate/company fields.
        if event.get("is_timestamp_confirmed") and not existing.get("is_timestamp_confirmed"):
            replacement = dict(event)
            replacement["sources_seen"] = sources
            replacement["secondary_sources"] = [s for s in sources if s != str(replacement.get("source") or "unknown")]
            merged[key] = replacement
        else:
            for field in ["eps_estimate", "company_name", "fiscal_date_ending"]:
                if existing.get(field) in {None, ""} and event.get(field) not in {None, ""}:
                    existing[field] = event.get(field)
            existing["secondary_sources"] = [s for s in sources if s != str(existing.get("source") or "unknown")]

    result = list(merged.values())

    # TKT-025: require ≥2 source agreement to confirm timestamp.
    if getattr(config, "EARNINGS_CONFIRM_REQUIRE_MULTI_SOURCE", True):
        for ev in result:
            if len(ev.get("sources_seen") or []) < 2:
                ev["is_timestamp_confirmed"] = False
                ev["is_timestamp_single_source"] = True

    # TKT-025: flag cross-source date disagreements within threshold.
    conflict_threshold = int(getattr(config, "EARNINGS_DATE_CONFLICT_THRESHOLD_DAYS", 2) or 2)
    by_ticker: dict[str, list[dict[str, Any]]] = {}
    for ev in result:
        t = str(ev.get("ticker") or "").upper().strip()
        by_ticker.setdefault(t, []).append(ev)
    for ticker_events in by_ticker.values():
        if len(ticker_events) < 2:
            continue
        dated = [(ev, _parse_date(ev.get("earnings_date") or ev.get("date"))) for ev in ticker_events]
        dated = [(ev, d) for ev, d in dated if d is not None]
        dated.sort(key=lambda pair: pair[1])
        for i in range(len(dated) - 1):
            ev_a, d_a = dated[i]
            ev_b, d_b = dated[i + 1]
            if (d_b - d_a).days <= conflict_threshold:
                ev_a["earnings_source_conflict"] = True
                ev_b["earnings_source_conflict"] = True

    for ev in result:
        ev["earnings_date_confidence"] = _compute_earnings_confidence(ev)

    return sorted(result, key=lambda item: (str(item.get("earnings_date") or "9999-99-99"), str(item.get("ticker") or "")))


def _compute_earnings_confidence(event: dict[str, Any]) -> str:
    sources = event.get("sources_seen") or []
    has_conflict = bool(event.get("earnings_source_conflict"))
    if has_conflict:
        return "disputed"
    if len(sources) >= 2:
        return "confirmed"
    if len(sources) == 1:
        return "single_source"
    return "no_data"


def _safe_alpha_horizon(value: str | None) -> str:
    normalized = str(value or "3month").strip().lower()
    if normalized in {"3month", "6month", "12month"}:
        return normalized
    return "3month"


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


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
