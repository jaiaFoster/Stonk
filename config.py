"""
config.py — All credentials and settings.
On Railway, set these as Environment Variables in the project dashboard.
Never commit real keys to GitHub.
"""

import os

# --- Robinhood ---
ROBINHOOD_USERNAME = os.environ.get("jaiafoster10@gmail.com")
ROBINHOOD_PASSWORD = os.environ.get("Robinhoodp@s5")

# --- NewsAPI ---
NEWS_API_KEY = os.environ.get("292dde84bb564c4b8b808f374fb2637e")

# --- ntfy.sh ---
NTFY_TOPIC = os.environ.get("jaa-stonks-2768")

# --- Flask trigger endpoint ---
RUN_TOKEN = os.environ.get("jaa-stonks")
