# Trader

Stock watchlist dashboard with auto-suggested **buy**, **take profit**, and **stop loss** levels.

Default watchlist: **MU, WDC, SNDK, STX, ARM, MRVL, VRT, PLTR**

## Setup

```bash
cd trader
python -m venv .venv

# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

## Run

```bash
uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000)

## Features

- Live watchlist prices from Yahoo Finance (`yfinance`)
- 1-year price chart per stock with **volume bars**
- **RSI (14)** sub-chart with overbought (70) / oversold (30) lines
- **SMA200** long-term trend on chart and in metrics
- Suggested levels:
  - **Buy zone** — support to SMA20
  - **Take profit** — 60-day resistance, or 3× ATR above price when price is already near the 60-day high
  - **Risk / reward** — shown both from the buy zone and from the current price
- Intraday volume is projected to a full session before comparing with the 20-day average
  - **Stop loss** — ATR-based below support
- Editable order plan (saved in browser local storage; checks stop < buy < take profit)
- Yahoo responses cached in-process (prices 2 min, news 5 min) and fetched in parallel
- Dashed lines on chart for each level
- **Backtest API** (no longer shown as a tab): replays the BUY score and suggested buy zone / stop / take profit
  day by day (no look-ahead) and reports win rate, expectancy in R, profit factor,
  drawdown, buy & hold vs SPY, and forward returns by action and score — so you
  can see whether the signal actually has an edge before trusting it
- BUY/HOLD/SELL score includes **relative strength vs SPY** (6-month return, ±2 points
  beyond ±10 pts) and gives no "oversold bounce" credit below SMA200. Tested on ~180
  US stocks split into 2021–24 and 2024–26: it was the only change that improved both
  halves. The backtest also favors a **4× ATR trailing stop** over a fixed take profit
- **Position sizing** in the order plan: enter account size and risk % (default 1%) and
  it sizes shares as risk ÷ (entry − stop), warning when a plan breaks your rule
- **Alpaca paper trading** (paper keys only — live keys are refused): "Place paper order"
  sends a limit buy with a stop loss that arms on fill. Manage orders in your
  Alpaca paper account. On a public deploy this needs `APP_PASSWORD` set, so
  strangers can't trade the account.

- **Top analysts** panel per stock: firms whose past calls on that stock were right at
  least 80% of the time (70/90% selectable; 5+ graded calls), with their current rating,
  price target and upside. A call is graded once it is a year old: a Buy is correct if
  the stock beat SPY over the next 12 months, a Sell if it lagged. Also shows how often
  targets were reached, and the "blind Buy" baseline — when that is high too, the firms
  rode the stock more than they showed skill. Data: Yahoo rating history since ~2012,
  targets adjusted for splits

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/watchlist` | All watchlist symbols with levels |
| `GET /api/early-signals[?refresh=true]` | Setup radar candidates and top 10 early-signal stocks (cached 1 h) |
| `GET /api/setups?symbols=AAPL,MSFT` | Completed-bar setup monitoring for watched stocks (cached 5 min) |
| `GET /api/backtest?symbols=&period=5y&entry=buy_zone&exit=trail&min_score=3&max_hold=250&cost_bps=5` | Replay the BUY signal + levels on past data (cached 1 h) |
| `GET /api/stock/{symbol}?period=1y` | History + levels for one symbol (`3mo`, `6mo`, `1y`, `2y`, `5y`, `ytd`, `max`) |
| `GET /api/stock/{symbol}/analysts?min_accuracy=0.8&min_calls=5` | Firms with a ≥80% correct track record on the stock + their current rating/target (cached 6 h) |
| `POST /api/journal/review` | Trailing stops for open journal trades + stats for closed ones |
| `POST /api/agent/chat/{symbol}` | Stock chat (used by the **Analyze setup** button) |
| `POST /api/agent/analyze/{symbol}` | One-shot structured setup analysis |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests stub out Yahoo, so they run offline.

## Next steps

- Add price alerts when a symbol enters the buy zone
- Optional: replace Yahoo delayed quotes with Alpaca market data

## Trader Agent (AI analysis)

Analyzes indicators + news and suggests setup (buy zone, stop, take profit).

### Best free options

| Provider | Model | Best for |
|----------|-------|----------|
| **Ollama** (default) | `llama3.2` or `qwen2.5:7b` | 100% free, private, runs locally |
| **Groq** | `openai/gpt-oss-120b` | Free cloud API, fast, strong reasoning |
| **Gemini** | `gemini-2.0-flash` | Free tier, good summaries |
| **rules** | built-in | No LLM — instant rule-based analysis |

**Recommended:** Start with **Ollama** (no API key). For cloud without local GPU, use **Groq**.

```bash
# Ollama setup
# 1. Install from https://ollama.com
# 2. Pull a model:
ollama pull llama3.2

# Optional: copy .env.example to .env and set provider/keys
copy .env.example .env
```

Set provider via environment variable:

```bash
set TRADER_AGENT_PROVIDER=ollama
set OLLAMA_MODEL=llama3.2
```

Or for Groq (free key at console.groq.com):

```bash
set TRADER_AGENT_PROVIDER=groq
set GROQ_API_KEY=your_key
```

Click **Analyze setup** in the app (sends a recommendation question to the stock chat), or call `POST /api/agent/analyze/MU` for the one-shot structured format.

## Any ticker

Use the search box at the top of the sidebar to open any Yahoo Finance symbol
(stocks, ETFs, indices like `^SOX`, futures like `NQ=F`, foreign listings like
`005930.KS`). **☆ Add to watchlist** / the **×** on a watchlist card edits your
list, saved in this browser; **Reset** restores the default. The Greedy Risk
Index and Overnight panels always use the default semis/memory list in
`backend/config.py`.

## Signal ladder + investment system

Each stock page shows two panels under the recommendation:

- **Signal ladder — the price to go in at.** Six rungs (TRIM MORE, TRIM, HOLD,
  BUY A LITTLE, BUY, BUY A LOT) centred on the SMA50 and spaced 2× ATR apart
  (at least 2% of price), with today's price marked on its rung. The Early
  signals cards show the same rung.
- **Investment system.** *Is today the right day?* checks the market (SPY vs
  its 50-day), chasing (today < +3%), RSI, earnings within 5 days and heavy
  selling volume. *Is the ladder right?* checks the long-term trend (SMA200),
  the stop, buy-zone risk/reward and data freshness.

## Early signals tab

The tab now opens **Setup radar**, with filters for Forming, Near breakout,
Confirmed, Pullback, Extended, Failed, Watch and Unavailable. Forming or nearby
setups receive priority for the detailed fundamentals check. Cards show the
trigger (prior 20-session high), invalidation (base low), distance, completed-bar
date and missing checks. Forming bases have a range of at most 8% and sit above a
rising 50-day average; Near breakout also requires relative strength and a trigger
within 3%. Confirmed breakouts require 1.5x prior 20-session average volume. Recent
qualifying breakouts retain their original base for ten sessions. These rules
have **not** been backtested; the retained backtest API tests the separate BUY/HOLD/SELL system.

**Watch setup** saves fixed trigger/invalidation levels in this browser. The app
checks `/api/setups?symbols=AAPL,MSFT` every five minutes while open and on reopen,
replaying up to 60 completed sessions to catch intervening failures. Remove and
re-add a failed watch to arm a new setup. Watches are checked even after leaving
the radar. Stale bars or data errors do not trigger alerts. Optional desktop
notifications require HTTPS/localhost and browser permission; the in-app feed
keeps 50 changes. Closing the app pauses checks. Stock charts show current setup
levels and separate saved watched levels. The radar is cached for an hour; watched
prices are cached for five minutes. This is a daily-close system, not live alerts.

The **Early signals · Top 10** tab screens ~225 liquid US stocks (a built-in
list plus Yahoo's growth / undervalued / most-active screens) for ten signals
and ranks them by how many line up (the earnings catalyst counts half, since
nearly every stock reports within three months):

| Signal | Rule |
|--------|------|
| Estimates rising | Analysts' upward EPS revisions in 30 days outnumber cuts (≥3 up, 2:1), or FY estimate +2% |
| Insider buying | 2+ insiders made open-market purchases in 90 days (Form 4) |
| Unusual volume | 5-day volume ≥1.5× the prior 50 days while price moved <5% in 10 days |
| Catalyst soon | Earnings report within ~3 months |
| Relative strength | 1-month return beats its sector ETF by 3+ points (or rose while the sector fell) |
| Growth at a discount | Revenue YoY growth expected to speed up this quarter, forward P/E below sector peers |
| Quiet accumulation | Up-day volume ≥1.3× down-day volume over 50 days |
| Analyst upgrades | 2+ upgrades or price-target raises in 30 days, more than cuts |
| Earnings-beat streak | Beat EPS estimates in each of the last 4 quarters |
| Shorts covering | Shares sold short down 10%+ vs the prior month |

Stocks up more than 30% in the last month are excluded. Prices for all stocks
come from one batched download; the 45 best volume / relative-strength setups
get the full fundamentals check. Results are cached for an hour (**Rescan**
forces a new run, ~10–60 s). Yahoo only knows earnings dates, so FDA decisions,
launches and index additions aren't detected — **Copy AI research prompt**
copies a dated research prompt for an AI with web search.

## Deploy free (Render)

1. Push this folder to a GitHub repo (private is fine). `.env` is git-ignored,
   so your API keys stay local.
2. On [render.com](https://render.com) (free account): **New → Blueprint**,
   pick the repo. Render reads `render.yaml`.
3. When asked, set:
   - `APP_PASSWORD` — the password for the site (browser login prompt; any username)
   - `GROQ_API_KEY` — your Groq key
4. Open the deployed site: [https://trader-88ql.onrender.com](https://trader-88ql.onrender.com)

Free tier notes: the service sleeps after ~15 min without visits and takes
~30–60 s to wake on the next one. Yahoo Finance sometimes rate-limits cloud
server IPs; if prices fail to load there, wait a few minutes and refresh.

**Always set `APP_PASSWORD` on a public deployment** — without it anyone who
finds the URL can use your Groq quota.

## Disclaimer

Levels are illustrative and based on simple technical rules. Not financial advice. Use paper trading before risking real money.
