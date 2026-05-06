# Daily Stock Reporter

Fetches your Robinhood positions every weekday morning, pulls relevant news,
generates a Claude AI report, and pushes it to your iPhone via ntfy.

-----

## File Structure

```
stock-reporter/
├── main.py           # Orchestrator — run this
├── robinhood.py      # Fetches positions
├── news.py           # Fetches news per ticker
├── claude_report.py  # Calls Claude API
├── notifier.py       # Pushes to ntfy.sh
├── config.py         # Keys & settings (use env vars on Railway)
├── requirements.txt
└── railway.toml      # Cron schedule config
```

-----

## Setup

### 1. Get your API keys

|Service   |Where to get it                                 |
|----------|------------------------------------------------|
|Robinhood |Your own login credentials                      |
|Anthropic |https://console.anthropic.com                   |
|NewsAPI   |https://newsapi.org (free tier)                 |
|ntfy topic|Just pick a unique name, e.g. `jaia-stocks-x7k2`|

-----

### 2. Handle Robinhood MFA (important)

Robinhood requires MFA. On a server there’s no interactive terminal,
so you need to pre-generate a session token locally:

1. Run `python main.py` locally once
1. It will prompt for your MFA code — enter it
1. robin_stocks saves a session token to `~/.tokens/robinhood.pickle`
1. Upload that pickle file to Railway as a mounted file, OR
1. Use a TOTP authenticator app secret (if you use an authenticator app, not SMS):
- Get your TOTP secret from Robinhood’s 2FA setup
- Add it to config.py as `ROBINHOOD_TOTP_SECRET`
- In robinhood.py, replace `by_sms=True` with:
  
  ```python
  import pyotp
  totp = pyotp.TOTP(config.ROBINHOOD_TOTP_SECRET).now()
  r.login(..., mfa_code=totp)
  ```

The TOTP approach is cleaner for automated servers. Recommended.

-----

### 3. Deploy to Railway

1. Push this folder to a GitHub repo (private)
1. Go to https://railway.app → New Project → Deploy from GitHub
1. Select your repo
1. Go to Variables tab, add:
- `ROBINHOOD_USERNAME`
- `ROBINHOOD_PASSWORD`
- `ROBINHOOD_TOTP_SECRET` (if using TOTP)
- `ANTHROPIC_API_KEY`
- `NEWS_API_KEY`
- `NTFY_TOPIC`
1. Railway reads `railway.toml` and sets up the cron automatically
1. Adjust the cron time in `railway.toml` to your preferred morning time

-----

### 4. Set up ntfy on iPhone

1. Download the **ntfy** app from the App Store (free)
1. Tap + → Subscribe to topic
1. Enter your `NTFY_TOPIC` name (e.g. `jaia-stocks-x7k2`)
1. Done — reports will appear as push notifications

-----

### 5. Optional: Apple Shortcuts integration

If you want Shortcuts to trigger on-demand (not just wait for the cron):

1. Create a new Shortcut
1. Add action: **Get Contents of URL**
- URL: `https://railway.app` (your Railway deployment webhook URL)
- Method: POST
1. Add action: **Show Notification** with the result

Or skip Shortcuts entirely — ntfy handles delivery natively.

-----

## Adjusting the report

Edit the prompt in `claude_report.py` to change tone, length, or focus.
Edit the cron schedule in `railway.toml` to change the time.
Edit `news.py` to change how many headlines per ticker.
