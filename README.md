# Algo Stock Advisor

A modular portfolio and trading-decision assistant.

Algo Stock Advisor collects brokerage, market, news, options, earnings, and historical behavior data so it can eventually evaluate positions and trade opportunities using defined numerical and strategic qualifiers.

The current version successfully fetches Robinhood stock and crypto positions, pulls recent ticker news, and renders a browser-based portfolio report. The long-term goal is to turn this into a full decision-support system for portfolio review, options strategy scanning, trade lifecycle tracking, and rule-based recommendations.

> This project is for personal research and decision support only. It is not financial advice and should not place trades automatically without explicit future safeguards.

---

## Current Status

The app currently supports:

- Robinhood login through `robin_stocks`
- Roth IRA stock positions
- Rollover IRA stock positions
- Crypto positions
- Current price lookup
- Average cost, market value, gain/loss, and gain/loss percentage
- Basic NewsAPI headline lookup
- Flask `/run` endpoint
- Browser-rendered HTML report
- Copyable advisor payload
- Run logs for debugging
- Railway deployment

The current app is stable enough to use as the foundation for the larger roadmap.

---

## Project Direction

This is not just a daily stock briefing app.

The intended direction is:

1. Gather reliable portfolio and market data.
2. Normalize the data into consistent internal models.
3. Store snapshots and trade history.
4. Add strategy-specific analysis modules.
5. Score opportunities using numerical and strategic qualifiers.
6. Track open trades through entry, check-in, and exit logic.
7. Produce clear recommendations such as:

```text
ENTER
WATCH
HOLD
EXIT
AVOID
```

The first serious strategy module will likely be an earnings calendar spread scanner, but the architecture should remain modular enough to support other strategies later.

---

## Current Workflow

1. User visits:

```text
https://your-app-url/run?token=YOUR_RUN_TOKEN
```

2. Flask validates the token.
3. The app logs into Robinhood.
4. The app fetches stock positions from configured accounts.
5. The app fetches crypto positions.
6. The app fetches recent news headlines for each ticker.
7. The app formats a structured advisor payload.
8. The app renders an HTML report in the browser.
9. The user can review positions, copy the payload, and inspect logs.

---

## Current File Structure

```text
stock-advisor/
├── main.py           # Flask app, /run endpoint, report renderer, orchestration
├── robinhood.py      # Robinhood login and position fetching
├── news.py           # NewsAPI headline fetching
├── config.py         # Environment variable loading
├── notifier.py       # Optional notification helper
├── requirements.txt  # Python dependencies
└── README.md         # Project documentation
```

This structure works for the current stage, but it will eventually be refactored into a more modular architecture.

---

## Planned Architecture

Target future structure:

```text
stock-advisor/
├── app/
│   ├── main.py
│   ├── config.py
│   │
│   ├── models/
│   │   ├── position.py
│   │   ├── market_event.py
│   │   ├── recommendation.py
│   │   └── trade.py
│   │
│   ├── providers/
│   │   ├── robinhood_provider.py
│   │   ├── news_provider.py
│   │   ├── tradier_provider.py
│   │   └── earnings_provider.py
│   │
│   ├── services/
│   │   ├── portfolio_service.py
│   │   ├── market_data_service.py
│   │   ├── analysis_service.py
│   │   └── report_service.py
│   │
│   ├── strategies/
│   │   ├── base.py
│   │   ├── portfolio_snapshot.py
│   │   └── earnings_calendar_spread.py
│   │
│   ├── storage/
│   │   ├── db.py
│   │   ├── schema.sql
│   │   └── trade_repository.py
│   │
│   └── templates/
│       └── report.html
│
├── requirements.txt
└── README.md
```

The goal is to keep data providers, strategy logic, storage, and reporting separate.

---

## Environment Variables

Set these in Railway or your local environment.

| Variable | Required | Purpose |
|---|---:|---|
| `ROBINHOOD_USERNAME` | Yes | Robinhood account email/username |
| `ROBINHOOD_PASSWORD` | Yes | Robinhood account password |
| `NEWS_API_KEY` | Yes | NewsAPI key for headline lookup |
| `RUN_TOKEN` | Yes | Secret token used to protect `/run` |

Never commit real credentials to GitHub.

---

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /run?token=YOUR_RUN_TOKEN` | Runs the full portfolio pipeline and returns the HTML report |
| `GET /health` | Returns `OK` for deployment health checks |

---

## Local Development

### 1. Clone the repo

```bash
git clone <your-repo-url>
cd <your-repo-folder>
```

### 2. Create and activate a virtual environment

Mac/Linux:

```bash
python -m venv venv
source venv/bin/activate
```

Windows:

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Set environment variables

Mac/Linux:

```bash
export ROBINHOOD_USERNAME="your@email.com"
export ROBINHOOD_PASSWORD="your-password"
export NEWS_API_KEY="your-newsapi-key"
export RUN_TOKEN="your-secret-token"
```

Windows PowerShell:

```powershell
$env:ROBINHOOD_USERNAME="your@email.com"
$env:ROBINHOOD_PASSWORD="your-password"
$env:NEWS_API_KEY="your-newsapi-key"
$env:RUN_TOKEN="your-secret-token"
```

### 5. Run locally

```bash
python main.py
```

Then open:

```text
http://localhost:5000/run?token=your-secret-token
```

---

## Railway Deployment

This project is currently designed to deploy from GitHub to Railway.

Basic workflow:

1. Push changes to GitHub.
2. Railway detects the change.
3. Railway rebuilds and redeploys the app.
4. Visit the `/run` URL with your `RUN_TOKEN`.

Railway variables should be set in the Railway dashboard, not in the code.

Required Railway variables:

```text
ROBINHOOD_USERNAME
ROBINHOOD_PASSWORD
NEWS_API_KEY
RUN_TOKEN
```

---

## Current Dependencies

| Package | Purpose |
|---|---|
| `robin_stocks` | Unofficial Robinhood API wrapper |
| `requests` | HTTP requests for NewsAPI and optional notifications |
| `pyotp` | TOTP support for Robinhood login flows |
| `flask` | Web server and routing |
| `gunicorn` | Production WSGI server |

---

## Current Limitations

### News quality

The current news lookup searches ticker symbols directly. This can create irrelevant results for ambiguous tickers.

Examples:

- `HOOD` can return kitchen hood articles.
- `META` can return unrelated research or package results.
- Crypto tickers may return weak or unrelated results.

Planned fix:

- Use company-aware search queries.
- Add ticker-to-company-name mapping.
- Filter headlines by market/business relevance.
- Consider a separate crypto news source later.

### Robinhood API reliability

This project uses the unofficial `robin_stocks` library. Robinhood may change login behavior, require device approval, or restrict access.

Potential issues:

- Login approval may be required.
- Session persistence may break after redeploy.
- Multiple overlapping runs can interfere with login state.
- Account access may vary by account type.

### No persistent database yet

The app currently renders a live snapshot but does not store historical results.

This means it cannot yet:

- Compare today vs yesterday
- Track portfolio drift
- Track open trades
- Store strategy scores
- Record backtest results
- Store earnings events

SQLite is planned as the first storage layer.

---

## Roadmap

### Phase 0 — Stabilize deployed portfolio collection

Status: Mostly complete.

- [x] Deploy Flask app
- [x] Add `/run` endpoint
- [x] Fetch Robinhood stock positions
- [x] Fetch Robinhood crypto positions
- [x] Fetch basic news headlines
- [x] Render HTML report
- [x] Add copyable advisor payload
- [x] Fix HTML formatting crash
- [x] Add basic run locking
- [ ] Rotate exposed `RUN_TOKEN`
- [ ] Improve news relevance

---

### Phase 1 — Improve data quality

Goal: make the existing data more reliable before adding strategy complexity.

Planned work:

- [ ] Improve NewsAPI search queries
- [ ] Add ticker-to-company-name mapping
- [ ] Filter irrelevant news
- [ ] Separate stock news and crypto news behavior
- [ ] Add better error handling for missing prices
- [ ] Add cleaner logging
- [ ] Add favicon or suppress favicon noise in logs

---

### Phase 2 — Refactor into modular architecture

Goal: separate providers, services, models, strategies, and reporting.

Planned work:

- [ ] Move Robinhood code into `providers/robinhood_provider.py`
- [ ] Move NewsAPI code into `providers/news_provider.py`
- [ ] Create `models/position.py`
- [ ] Create `services/portfolio_service.py`
- [ ] Create `services/report_service.py`
- [ ] Keep `/run` behavior unchanged during refactor
- [ ] Keep app deployable after every step

---

### Phase 3 — Add SQLite persistence

Goal: give the app memory.

Planned tables:

| Table | Purpose |
|---|---|
| `portfolio_snapshots` | Store each app run |
| `positions` | Store normalized positions from each run |
| `news_items` | Store headlines linked to tickers |
| `strategy_runs` | Store strategy evaluations |
| `recommendations` | Store generated recommendations |
| `trades` | Track open and closed trades |
| `trade_checkpoints` | Track trade check-ins and exit reviews |
| `earnings_events` | Store earnings dates and timestamps |
| `earnings_backtests` | Store earnings reaction summaries |

---

### Phase 4 — Portfolio Snapshot Advisor v1

Goal: generate useful portfolio-level recommendations before adding options strategies.

Possible qualifiers:

- Position size
- Account exposure
- Unrealized gain/loss
- Concentration
- Duplicate ticker exposure across accounts
- Large winners
- Large losers
- Volatility proxy
- Recent news relevance
- Watchlist status

Example recommendation:

```text
Ticker: SMR
Action: REVIEW RISK
Reason:
- Position is down more than 30%.
- It remains a meaningful part of the portfolio.
- Strategy thesis should be reviewed before adding more.
```

---

### Phase 5 — Add Tradier options provider

Goal: collect live options chain data for strategy scanning.

Provider:

```text
providers/tradier_provider.py
```

Planned functions:

- `get_quote(ticker)`
- `get_expirations(ticker)`
- `get_options_chain(ticker, expiration)`
- `get_atm_options(ticker)`
- `find_calendar_candidates(ticker)`
- `get_option_liquidity_metrics(...)`

Important data points:

- Bid
- Ask
- Mark
- Volume
- Open interest
- Implied volatility
- Delta
- Theta
- Expiration
- Strike
- Underlying price

---

### Phase 6 — Add earnings timestamp provider

Goal: accurately know whether earnings are before market open, after market close, or unknown.

Possible sources:

- Finnhub
- Financial Modeling Prep
- Nasdaq earnings calendar
- Other dedicated earnings APIs

Needed fields:

- Ticker
- Earnings date
- Time of day
- Confirmed vs estimated
- Source
- Last updated timestamp

This matters because options strategy timing depends heavily on whether earnings are before open or after close.

---

### Phase 7 — Earnings calendar spread strategy module

Goal: build the first serious options strategy module.

Strategy:

```text
strategies/earnings_calendar_spread.py
```

The module should evaluate:

- Upcoming earnings date
- Earnings time
- Stock liquidity
- Options liquidity
- Available expirations
- Front expiration
- Back expiration
- Strike selection
- Debit paid
- Bid/ask spread
- IV relationship
- Estimated max risk
- Assignment risk
- Entry timing
- Exit timing
- Pre-close check requirement

Possible actions:

```text
WATCH
ENTER
HOLD
EXIT
AVOID
```

---

### Phase 8 — Last-10-earnings mini-backtest

Goal: understand how a stock behaved around recent earnings events.

Using candle data, calculate:

- Average earnings move
- Median earnings move
- Largest positive move
- Largest negative move
- Gap direction
- Gap magnitude
- Intraday fade behavior
- Post-earnings drift
- Pullback frequency
- Directional bias

This will help the app determine whether a ticker is historically suitable for a strategy like an earnings calendar spread.

---

### Phase 9 — Trade lifecycle tracking

Goal: track trades after entry instead of only scanning for new ones.

The app should support:

- Entry recommendation
- Entry confirmation
- Open trade tracking
- Daily check-in
- Final pre-close check
- Profit target check
- Loss/risk check
- Exit recommendation
- Trade close record
- Post-trade review

For calendar spreads, this should include:

- Target profit percentage
- Current spread value
- Short leg moneyness
- Long leg value
- Time to front expiration
- Assignment risk
- Earnings timing proximity

---

### Phase 10 — Unified recommendation engine

Goal: combine portfolio data, strategy scores, news, market context, and trade status into one recommendation layer.

Example output:

```text
Ticker: ZIM
Strategy: Earnings Calendar Spread
Action: WATCH
Score: 78 / 100
Confidence: Medium

Reasons:
- Earnings date is approaching.
- Options chain supports a roughly 30-day calendar structure.
- Liquidity appears acceptable.
- Historical earnings moves are large enough to justify attention.

Risks:
- Bid/ask spread may reduce expected return.
- Earnings timestamp must be confirmed.
- Avoid entry if short leg becomes too deep ITM.

Next Check:
- Recheck before market close.
```

---

## Strategy Design Principles

The app should follow these principles:

1. Data first.
2. No strategy logic inside provider files.
3. No provider-specific assumptions inside strategy files.
4. Every strategy should be modular.
5. Every recommendation should explain its reasoning.
6. Every score should be traceable to inputs.
7. Every trade should have entry, check-in, and exit logic.
8. The app should support multiple strategies over time.
9. The app should not place trades automatically unless that is explicitly added later with safeguards.
10. The app should remain useful even before automated trading exists.

---

## Near-Term Development Order

Recommended next steps:

1. Improve `news.py` relevance.
2. Add better ticker/company mapping.
3. Refactor providers without changing behavior.
4. Add `Position` model.
5. Add SQLite.
6. Store portfolio snapshots.
7. Add portfolio-level scoring.
8. Add Tradier provider.
9. Add earnings provider.
10. Build earnings calendar spread scanner.
11. Add mini-backtest.
12. Add trade lifecycle tracking.
13. Build recommendation dashboard.

---

## Security Notes

- Do not commit credentials.
- Keep `RUN_TOKEN` private.
- Rotate `RUN_TOKEN` if it appears in logs or screenshots.
- Be careful with Robinhood credentials.
- Avoid exposing `/run` publicly without token protection.
- Consider adding stronger authentication later.
- Be cautious with any future trade execution features.

---

## Disclaimer

This software is a personal research and analysis tool.

It does not provide financial advice. It does not guarantee returns. Any trading or investing decision remains the responsibility of the user. Options trading can involve substantial risk, including loss of the full debit paid and assignment risk on short options.

Use this app as a decision-support tool, not as an automated authority.
