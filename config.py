"""Compatibility shim.

The real configuration lives in app.config. Keep this root module tiny so older
imports such as `import config` do not drift from the app package settings.
"""

from app.config import *  # noqa: F401,F403
