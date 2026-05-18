"""
config.py — All credentials and settings.
On Railway, set these as Environment Variables in the project dashboard.
Never commit real keys to GitHub — all values come from environment variables.
"""

import os

# --- Robinhood ---
ROBINHOOD_USERNAME = os.environ.get("ROBINHOOD_USERNAME")
ROBINHOOD_PASSWORD = os.environ.get("ROBINHOOD_PASSWORD")

# --- NewsAPI ---
# Free tier at newsapi.org — 100 requests/day
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")

# --- Endpoint security ---
# A secret token to protect the /run endpoint from being triggered by anyone.
# Set this to any long random string e.g. "jaia-stonk-abc123xyz"
RUN_TOKEN = os.environ.get("RUN_TOKEN")
