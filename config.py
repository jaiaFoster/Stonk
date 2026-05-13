"""
config.py — All credentials and settings.
On Railway, set these as Environment Variables in the project dashboard.
Never commit real keys to GitHub.
"""

import os

# --- Robinhood ---
ROBINHOOD_USERNAME = os.environ.get("ROBINHOOD_USERNAME")
ROBINHOOD_PASSWORD = os.environ.get("ROBINHOOD_PASSWORD")

# --- NewsAPI ---
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")

# --- ntfy.sh ---
NTFY_TOPIC = os.environ.get("NTFY_TOPIC")

# --- Flask trigger endpoint ---
RUN_TOKEN = os.environ.get("RUN_TOKEN")
