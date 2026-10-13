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

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/watchlist` | All watchlist symbols with levels |
| `GET /api/early-signals[?refresh=true]` | Top 10 early-signal stocks (cached 1 h) |
| `GET /api/stock/{symbol}?period=1y` | History + levels for one symbol (`3mo`, `6mo`, `1y`, `2y`, `5y`, `ytd`, `max`) |
| `POST /api/agent/chat/{symbol}` | Stock chat (used by the **Analyze setup** button) |
| `POST /api/agent/analyze/{symbol}` | One-shot structured setup analysis |

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Tests stub out Yahoo, so they run offline.

## Next steps

- Connect [Alpaca paper trading](https://alpaca.markets/) for real limit/stop orders
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
