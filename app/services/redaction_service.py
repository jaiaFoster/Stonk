"""Recursive secret redaction for developer-facing exports."""

from __future__ import annotations

from typing import Any

from app import config

SENSITIVE_PARTS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "COOKIE", "SESSION", "PRIVATE", "BEARER", "CREDENTIAL", "ACCESS_TOKEN", "REFRESH_TOKEN")


def known_secrets() -> list[str]:
    names = ("ROBINHOOD_PASSWORD", "NEWS_API_KEY", "FINNHUB_API_KEY", "ALPHA_VANTAGE_API_KEY", "TRADIER_ACCESS_TOKEN", "RUN_TOKEN", "DEV_API_TOKEN", "NTFY_TOPIC")
    return [str(getattr(config, name, "") or "") for name in names if getattr(config, name, None)]


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if any(part in str(key).upper() for part in SENSITIVE_PARTS) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    if isinstance(value, str):
        output = value
        for secret in known_secrets():
            output = output.replace(secret, "[REDACTED]")
        return output
    return value
