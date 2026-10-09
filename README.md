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
| **Groq** | `llama-3.3-70b-versatile` | Free cloud API, fast, strong reasoning |
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

## Deploy free (Render)

1. Push this folder to a GitHub repo (private is fine). `.env` is git-ignored,
   so your API keys stay local.
2. On [render.com](https://render.com) (free account): **New → Blueprint**,
   pick the repo. Render reads `render.yaml`.
3. When asked, set:
   - `APP_PASSWORD` — the password for the site (browser login prompt; any username)
   - `GROQ_API_KEY` — your Groq key
4. Open `https://<your-service>.onrender.com`.

Free tier notes: the service sleeps after ~15 min without visits and takes
~30–60 s to wake on the next one. Yahoo Finance sometimes rate-limits cloud
server IPs; if prices fail to load there, wait a few minutes and refresh.

**Always set `APP_PASSWORD` on a public deployment** — without it anyone who
finds the URL can use your Groq quota.

## Disclaimer

Levels are illustrative and based on simple technical rules. Not financial advice. Use paper trading before risking real money.
