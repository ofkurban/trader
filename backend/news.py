from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import yfinance as yf

from backend.cache import ttl_cache

CHIP_NEWS_SYMBOLS = [
    "MU",
    "SMH",
    "SOXX",
    "NVDA",
    "AMD",
    "000660.KS",
    "005930.KS",
    "TSM",
    "ARM",
    "688825.SS",  # CXMT (ChangXin Memory) — Shanghai; may be sparse on Yahoo
]

# Negative-price / liquidation language only — not sector nouns (chip/semi/memory).
SELL_OFF_KEYWORDS = (
    "selloff",
    "sell-off",
    "sell off",
    "crash",
    "plunge",
    "slump",
    "rout",
    "tumble",
    "nosedive",
    "freefall",
    "free fall",
    "bloodbath",
    "capitulation",
    "dump",
    "dumped",
    "tanking",
    "tanked",
    "collapse",
    "collapses",
    "sharp drop",
    "sharp decline",
    "heavy selling",
    "selling pressure",
    "profit taking",
    "profit-taking",
    "risk-off",
    "risk off",
)

# China memory supply / CXMT — can pressure MU & Korea memory names.
# Keep phrases specific: generic ones ("chip prices", "shanghai") flag unrelated stories.
CHINA_MEMORY_KEYWORDS = (
    "cxmt",
    "changxin",
    "chang xin",
    "688825",
    "chinese memory",
    "china memory",
    "memory supply",
    "memory oversupply",
    "expanding memory",
    "dram supply",
    "nand supply",
    "asp pressure",
    "domestic memory",
)

def _keyword_pattern(keywords: tuple[str, ...]) -> re.Pattern:
    # Whole words only, so "rout" doesn't match "router" and "dump" not "dumpling"
    return re.compile(r"\b(?:" + "|".join(re.escape(k) for k in keywords) + r")\b")


SELL_OFF_RE = _keyword_pattern(SELL_OFF_KEYWORDS)
CHINA_MEMORY_RE = _keyword_pattern(CHINA_MEMORY_KEYWORDS)


def _parse_pub_date(raw: str | None) -> str | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        return raw


def _article_url(content: dict) -> str | None:
    for key in ("clickThroughUrl", "canonicalUrl"):
        url_obj = content.get(key)
        if isinstance(url_obj, dict) and url_obj.get("url"):
            return url_obj["url"]
    return None


def _normalize_article(item: dict, source_symbol: str) -> dict | None:
    content = item.get("content") if isinstance(item.get("content"), dict) else item
    if not isinstance(content, dict):
        return None

    title = content.get("title")
    if not title:
        return None

    provider = content.get("provider") or {}
    summary = content.get("summary") or content.get("description") or ""
    text = f"{title} {summary}".lower()
    is_selloff = bool(SELL_OFF_RE.search(text))
    is_china_memory = bool(CHINA_MEMORY_RE.search(text))

    return {
        "title": title,
        "summary": summary,
        "published": _parse_pub_date(content.get("pubDate")),
        "publisher": provider.get("displayName") or "Yahoo Finance",
        "url": _article_url(content),
        "type": content.get("contentType", "STORY"),
        "source_symbol": source_symbol,
        "is_selloff": is_selloff,
        "is_china_memory": is_china_memory,
    }


@ttl_cache(seconds=300)
def _raw_news(symbol: str) -> list[dict]:
    return yf.Ticker(symbol).news or []


def fetch_news(symbol: str, limit: int = 8) -> list[dict]:
    raw = _raw_news(symbol)
    articles = []

    for item in raw[:limit]:
        article = _normalize_article(item, symbol)
        if article:
            articles.append(article)

    return articles


def fetch_chip_news(limit: int = 12) -> dict:
    """Aggregate semiconductor / chip sell-off headlines across peers and ETFs."""
    seen: set[str] = set()
    articles: list[dict] = []

    def safe_raw_news(symbol: str) -> list[dict]:
        try:
            return _raw_news(symbol)
        except Exception:
            return []

    with ThreadPoolExecutor(max_workers=len(CHIP_NEWS_SYMBOLS)) as pool:
        raw_by_symbol = list(pool.map(safe_raw_news, CHIP_NEWS_SYMBOLS))

    for symbol, raw in zip(CHIP_NEWS_SYMBOLS, raw_by_symbol):
        for item in raw[:6]:
            article = _normalize_article(item, symbol)
            if not article:
                continue
            key = article["title"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            articles.append(article)

    selloff = [a for a in articles if a.get("is_selloff")]
    china_memory = [a for a in articles if a.get("is_china_memory")]

    # Prefer China memory + sell-off themes, then the rest
    ranked = []
    seen_titles: set[str] = set()
    for bucket in (china_memory, selloff, articles):
        for a in bucket:
            key = a["title"].strip().lower()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            ranked.append(a)

    top = ranked[:limit]

    tone = "CHIP STRESS" if len(selloff) >= 3 else ("MIXED" if selloff else "CALM")
    if china_memory:
        tone = "CHIP STRESS" if tone != "CALM" else "MIXED"

    summary = (
        f"{len(selloff)} chip/sell-off headlines"
        + (f", {len(china_memory)} China/CXMT memory-supply related" if china_memory else "")
        + "."
        if selloff or china_memory
        else "No clear chip sell-off or China memory-supply headlines in the latest Yahoo feed."
    )

    return {
        "tone": tone,
        "summary": summary,
        "selloff_count": len(selloff),
        "china_memory_count": len(china_memory),
        "china_memory_articles": china_memory[:5],
        "articles": top,
        "watch": {
            "cxmt": {
                "name": "CXMT (ChangXin Memory)",
                "symbol": "688825.SS",
                "why": (
                    "Chinese DRAM maker — expanding supply can pressure memory ASPs "
                    "and weigh on MU / SK Hynix / Samsung."
                ),
            }
        },
    }
