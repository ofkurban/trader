from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

import yfinance as yf
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.auth import basic_auth_middleware
from backend.cache import ttl_cache
from backend.config import WATCHLIST
from backend.greed_index import compute_greedy_risk_index
from backend.levels import compute_levels, compute_price_stats, history_to_records
from backend.news import fetch_chip_news, fetch_news
from backend.overnight import fetch_overnight_signals
from backend.symbols import lookup_name, normalize_symbol, parse_symbols, search_symbols
from backend.trader_agent import agent_status, analyze_symbol, chat_about_symbol

app = FastAPI(title="Trader", version="0.2.0")
app.middleware("http")(basic_auth_middleware)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


Period = Literal["3mo", "6mo", "1y", "2y", "5y", "ytd", "max"]


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    # Server only uses the last 10; cap what clients can send
    history: list[ChatMessage] = Field(default_factory=list, max_length=50)
    provider: str | None = Field(default=None, pattern=r"^[a-z]{1,20}$")
    model: str | None = Field(default=None, pattern=r"^[\w.:/-]{1,100}$")


@ttl_cache(seconds=120, maxsize=300)
def fetch_history(symbol: str, period: str = "1y"):
    ticker = yf.Ticker(symbol)
    hist = ticker.history(period=period)
    if hist.empty:
        raise HTTPException(status_code=404, detail=f"No price data for {symbol}")
    return hist


def safe_fetch_news(symbol: str) -> list[dict]:
    """News is optional context — a Yahoo news hiccup shouldn't fail the request."""
    try:
        return fetch_news(symbol)
    except Exception:
        return []


def load_levels(symbol: str, period: str = "1y"):
    """History + computed levels, with 'too little history' reported as a 422."""
    hist = fetch_history(symbol, period=period)
    try:
        return hist, compute_levels(hist)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"{symbol} ({period}): {exc}") from exc


def _watchlist_item(symbol: str) -> dict:
    item = {"symbol": symbol, "name": lookup_name(symbol)}
    try:
        item.update(compute_levels(fetch_history(symbol)))
    except HTTPException as exc:
        item["error"] = exc.detail
    except Exception as exc:
        item["error"] = str(exc)
    return item


def fetch_watchlist_items(symbols: list[str]) -> list[dict]:
    """Levels for every symbol, fetched in parallel (order preserved)."""
    with ThreadPoolExecutor(max_workers=min(len(symbols), 10) or 1) as pool:
        return list(pool.map(_watchlist_item, symbols))


@app.get("/healthz")
def healthz():
    return {"ok": True}


# The greed index and overnight panel are built for the semis/memory default
# list (Korea memory peers, SOX), so they ignore the user's custom watchlist.
@app.get("/api/greed")
def get_greed_index():
    overnight = fetch_overnight_signals()
    greed = compute_greedy_risk_index(fetch_watchlist_items(WATCHLIST), overnight)
    return {"greed": greed, "overnight_bias": overnight.get("bias")}


@app.get("/api/overnight")
def get_overnight():
    overnight = fetch_overnight_signals()
    items = [i for i in fetch_watchlist_items(WATCHLIST) if not i.get("error")]
    try:
        overnight["greed"] = compute_greedy_risk_index(items, overnight)
    except Exception as exc:
        overnight["greed"] = {
            "score": 50,
            "label": "NEUTRAL",
            "hint": f"Greed index unavailable: {exc}",
            "components": [],
        }
    return overnight


@app.get("/api/news/chips")
def get_chip_news(limit: int = 12):
    return fetch_chip_news(limit=limit)


@app.get("/api/search")
def search(q: str = Query(min_length=1, max_length=40)):
    try:
        return {"results": search_symbols(q.strip())}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Symbol search failed: {exc}") from exc


@app.get("/api/watchlist")
def get_watchlist(symbols: str | None = None):
    return {"watchlist": fetch_watchlist_items(parse_symbols(symbols))}


@app.get("/api/stock/{symbol}")
def get_stock(symbol: str, period: Period = "1y"):
    symbol = normalize_symbol(symbol)
    hist, levels = load_levels(symbol, period)

    return {
        "symbol": symbol,
        "name": lookup_name(symbol),
        "period": period,
        "history": history_to_records(hist),
        "levels": levels,
        "news": safe_fetch_news(symbol),
    }


@app.get("/api/news")
def get_watchlist_news(limit: int = 5, symbols: str | None = None):
    def top_headline(symbol: str) -> dict | None:
        try:
            articles = fetch_news(symbol, limit=limit)
        except Exception:
            return None
        if not articles:
            return None
        return {
            "symbol": symbol,
            "name": lookup_name(symbol),
            "headline": articles[0],
        }

    symbol_list = parse_symbols(symbols)
    with ThreadPoolExecutor(max_workers=min(len(symbol_list), 10)) as pool:
        items = [i for i in pool.map(top_headline, symbol_list) if i]
    return {"news": items}


@app.get("/api/stock/{symbol}/news")
def get_stock_news(symbol: str, limit: int = 10):
    symbol = normalize_symbol(symbol)
    return {"symbol": symbol, "news": fetch_news(symbol, limit=limit)}


@app.get("/api/agent/status")
def get_agent_status():
    return agent_status()


@app.post("/api/agent/analyze/{symbol}")
def run_agent_analysis(symbol: str):
    symbol = normalize_symbol(symbol)
    _, levels = load_levels(symbol)

    return analyze_symbol(
        symbol=symbol,
        name=lookup_name(symbol),
        levels=levels,
        news=safe_fetch_news(symbol),
    )


@app.post("/api/agent/chat/{symbol}")
def agent_chat(symbol: str, body: ChatRequest):
    symbol = normalize_symbol(symbol)
    hist, levels = load_levels(symbol)

    return chat_about_symbol(
        symbol=symbol,
        name=lookup_name(symbol),
        levels=levels,
        news=safe_fetch_news(symbol),
        message=body.message,
        history=[m.model_dump() for m in body.history],
        price_stats=compute_price_stats(hist),
        provider=body.provider,
        model=body.model,
    )


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
