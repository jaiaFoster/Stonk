"""
app/utils/log_safety.py — Redact secrets from logs and provider errors.

Railway/Flask access logs and requests exceptions can include full URLs with
query parameters. This helper masks tokens, API keys, passwords, and known
secret values before they are printed or displayed in the run log.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SENSITIVE_PARAM_NAMES = {
    "token",
    "apikey",
    "api_key",
    "key",
    "password",
    "passwd",
    "secret",
    "access_token",
    "refresh_token",
}

SENSITIVE_QUERY_RE = re.compile(
    r"(?i)(?P<name>token|apikey|api_key|key|password|passwd|secret|access_token|refresh_token)=(?P<value>[^&\s\"']+)"
)

# Catches header-like or JSON-like secret appearances.
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)(?P<name>token|apikey|api_key|key|password|passwd|secret|access_token|refresh_token)(?P<sep>[\"'\s:=]+)(?P<value>[A-Za-z0-9_\-\.]{8,})"
)

EMAIL_RE = re.compile(r"(?i)\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}\b")


def redact_text(text: Any, known_secrets: Iterable[str | None] | None = None) -> str:
    """Return a string with likely secrets masked."""
    value = str(text)

    value = SENSITIVE_QUERY_RE.sub(lambda m: f"{m.group('name')}=<redacted>", value)
    value = SENSITIVE_ASSIGNMENT_RE.sub(
        lambda m: f"{m.group('name')}{m.group('sep')}<redacted>",
        value,
    )

    for secret in known_secrets or []:
        if secret:
            value = value.replace(str(secret), "<redacted>")

    return value


def redact_email(text: Any) -> str:
    """Mask email addresses in logs."""
    return EMAIL_RE.sub("<redacted-email>", str(text))


def redact_url(url: str) -> str:
    """Mask sensitive query parameters in a URL."""
    try:
        parts = urlsplit(url)
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        safe_pairs = [
            (name, "<redacted>" if name.lower() in SENSITIVE_PARAM_NAMES else value)
            for name, value in query_pairs
        ]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(safe_pairs), parts.fragment))
    except Exception:
        return redact_text(url)


def sanitize_for_log(value: Any, known_secrets: Iterable[str | None] | None = None) -> str:
    """Mask secrets and emails for safe printing."""
    return redact_email(redact_text(value, known_secrets=known_secrets))


class SensitiveLogFilter(logging.Filter):
    """Logging filter that redacts sensitive query params from access logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_obj(record.msg)
        record.args = _redact_obj(record.args)
        return True


def install_werkzeug_redaction_filter() -> None:
    """Install a redaction filter on Flask/Werkzeug access logs."""
    logger = logging.getLogger("werkzeug")

    # Avoid adding duplicate filters on hot reloads / repeated imports.
    for existing in logger.filters:
        if isinstance(existing, SensitiveLogFilter):
            return

    logger.addFilter(SensitiveLogFilter())


def _redact_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return redact_text(obj)
    if isinstance(obj, tuple):
        return tuple(_redact_obj(item) for item in obj)
    if isinstance(obj, list):
        return [_redact_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _redact_obj(value) for key, value in obj.items()}
    return obj
