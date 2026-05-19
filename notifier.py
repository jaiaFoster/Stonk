"""
notifier.py — Compatibility wrapper.

Existing code that imports `send_to_phone` will continue to work. The real
notification helper now lives in `app/providers/notification_provider.py`.
"""

from app.providers.notification_provider import send_to_phone  # noqa: F401
