"""
config.py — All credentials and settings.

On Railway, set these as Environment Variables in your project dashboard
(never commit real keys to GitHub). The os.environ.get() calls below
read them from Railway’s environment at runtime.
config.py — All credentials and settings.

On Railway, set these as Environment Variables in your project dashboard
(never commit real keys to GitHub). The os.environ.get() calls below
read them from Railway’s environment at runtime.

For local testing, you can temporarily hardcode values here,
but switch back to env vars before pushing.
"""

import os

# — Robinhood —

ROBINHOOD_USERNAME = os.environ.get("ROBINHOOD_USERNAME", "jaiafoster10@gmail.com")
ROBINHOOD_PASSWORD = os.environ.get("ROBINHOOD_PASSWORD", "Robinhoodp@s5")

# If you use MFA, robin_stocks will prompt on first run and cache a token.

# On Railway (no interactive terminal), you’ll need to generate a token locally first

# and set ROBINHOOD_MFA_CODE or use a TOTP secret. See README for details.

# — NewsAPI —

NEWS_API_KEY = os.environ.get("NEWS_API_KEY", "292dde84bb564c4b8b808f374fb2637e")

# — ntfy.sh —

# Pick any unique topic name — e.g. “jaia-stocks-abc123”

# Make it hard to guess so others can’t subscribe to your reports
NTFY_TOPIC = "jaa-stonks-2768"

PLAID_CLIENT_ID = "69fe1169ef2ece000e241e65"
PLAID_SECRET = "c4407178db6af940215cc269f7bb2a"
PLAID_ENV = "production"
PLAID_ACCESS_TOKEN_ROBINHOOD = "access-production-7d125525-705f-4a23-99c9-79ff0e523038"

For local testing, you can temporarily hardcode values here,
but switch back to env vars before pushing.
"""

import os

# — Robinhood —

ROBINHOOD_USERNAME = os.environ.get(“ROBINHOOD_USERNAME”, “your@email.com”)
ROBINHOOD_PASSWORD = os.environ.get(“ROBINHOOD_PASSWORD”, “yourpassword”)

# If you use MFA, robin_stocks will prompt on first run and cache a token.

# On Railway (no interactive terminal), you’ll need to generate a token locally first

# and set ROBINHOOD_MFA_CODE or use a TOTP secret. See README for details.

# — NewsAPI —

NEWS_API_KEY = os.environ.get(“NEWS_API_KEY”, “292dde84bb564c4b8b808f374fb2637e”)

# — ntfy.sh —

# Pick any unique topic name — e.g. “jaia-stocks-abc123”

# Make it hard to guess so others can’t subscribe to your reports

NTFY_TOPIC = os.environ.get(“NTFY_TOPIC”, “your-unique-topic-name”)
