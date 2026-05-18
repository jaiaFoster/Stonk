# 📈 Daily Stock Briefing

A personal daily stock briefing tool. Hit an endpoint, get a full HTML report of your portfolio with positions, G/L, and news headlines — formatted as a ready-to-paste Claude prompt.

---

## How It Works

1. You hit `https://your-railway-url/run?token=YOUR_TOKEN` (manually or via a scheduled ping)
2. The server logs into Robinhood, fetches your crypto positions
3. Pulls recent news headlines for each ticker via NewsAPI
4. Formats everything into a Claude prompt
5. Returns a full HTML page in your browser with:
   - A positions table (ticker, account, quantity, avg cost, current price, G/L, market value)
   - The full Claude prompt, ready to copy
   - A run log for debugging
6. You copy the prompt and paste it into Claude

---

## File Structure

```
stock-briefing/
├── main.py           # Flask app — orchestrator + /run endpoint + HTML rendering
├── robinhood.py      # Position fetching (robin_stocks for crypto, account map for IRAs)
├── news.py           # NewsAPI headline fetching
├── config.py         # Credentials loaded from environment variables
├── requirements.txt  # Python dependencies
└── Dockerfile        # Container config for Railway deployment
```

---

## Environment Variables

Set these in Railway (never commit them to GitHub):

| Variable | Description |
|----------|-------------|
| `ROBINHOOD_USERNAME` | Your Robinhood email |
| `ROBINHOOD_PASSWORD` | Your Robinhood password |
| `NEWS_API_KEY` | From newsapi.org (free tier) |
| `RUN_TOKEN` | Any secret string — protects the /run endpoint |

---

## Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /run?token=YOUR_TOKEN` | Runs the full pipeline, returns HTML report |
| `GET /health` | Returns `OK` — used by Railway to confirm the app is alive |

---

## Local Development

**1. Clone and set up a virtual environment:**
```bash
git clone <your-repo-url>
cd stock-briefing
python -m venv venv

# Mac/Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

**2. Install dependencies:**
```bash
pip install -r requirements.txt
```

**3. Set environment variables locally:**

Either export them in your terminal:
```bash
export ROBINHOOD_USERNAME="your@email.com"
export ROBINHOOD_PASSWORD="yourpassword"
export NEWS_API_KEY="your-newsapi-key"
export RUN_TOKEN="any-secret-string"
```

Or temporarily hardcode them in `config.py` for local testing (switch back to `os.environ.get()` before pushing to GitHub).

**4. Run locally:**
```bash
python main.py
```

Then open `http://localhost:5000/run?token=your-secret-string` in your browser.

---

## Railway Deployment

**1. Push to GitHub:**
```bash
git init
git add .
git commit -m "initial commit"
git remote add origin <your-github-repo-url>
git push -u origin main
```

**2. Create a Railway project:**
- Go to [railway.app](https://railway.app)
- New Project → Deploy from GitHub repo → select your repo
- Railway auto-detects the Dockerfile and builds it

**3. Set environment variables in Railway:**
- Go to your project → Variables tab
- Add each variable from the table above

**4. Get your public URL:**
- Railway → Settings → Networking → Generate Domain
- Your run endpoint will be `https://your-app.railway.app/run?token=YOUR_TOKEN`

**5. Trigger it:**
- Hit the URL in your browser whenever you want a briefing
- Or set up a scheduled ping using Railway's cron feature or a free service like [cron-job.org](https://cron-job.org)

---

## Account Coverage

| Account | Source | Status |
|---------|--------|--------|
| Crypto (BTC, SOL) | robin_stocks | ✅ Working |
| Roth IRA | robin_stocks (account map) | ⚠️ Robinhood API returns empty — known limitation |
| Rollover | robin_stocks (account map) | ⚠️ Robinhood API returns empty — known limitation |
| Individual (margin) | robin_stocks | ✅ Returns positions if any exist |

> **Note:** Robinhood deliberately restricts API access to retirement accounts through their unofficial API. The `ACCOUNT_MAP` in `robinhood.py` targets those account numbers directly but Robinhood's backend still returns empty. A Plaid integration is the long-term fix — see the roadmap below.

---

## Roadmap

- [x] Crypto position fetching via robin_stocks
- [x] NewsAPI headline integration
- [x] Flask endpoint with HTML report rendering
- [x] Railway deployment via Docker
- [ ] Plaid integration for full IRA/Rollover position access
- [ ] Scheduled automatic runs via Railway cron
- [ ] Direct Claude API call (skip the copy-paste step entirely)

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `robin_stocks` | Unofficial Robinhood API wrapper |
| `requests` | HTTP calls to NewsAPI and ntfy |
| `flask` | Web server for the trigger endpoint |
| `gunicorn` | Production WSGI server (used by Railway) |
| `pyotp` | TOTP support for Robinhood MFA |

---

## Notes

- **MFA:** On first run, Robinhood will send a device approval to your phone. Approve it in the Robinhood app. The session is cached in `robinhood_session.pickle` so subsequent runs don't require re-approval.
- **NewsAPI free tier:** 100 requests/day. If you have 10 tickers and run twice a day that's 20 requests — well within limits.
- **Session persistence on Railway:** The pickle file is written inside the container and lost on redeploy. If the app seems stuck on login, a redeploy forces a fresh auth cycle.
