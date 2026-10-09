"""Trader agent — LLM-powered setup analysis from indicators + news."""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from backend.config import WATCHLIST

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=True)

# Anything an LLM call can raise for network, HTTP, timeout, bad JSON or an
# unexpected response shape — all handled by falling back to rule-based output.
# (URLError/HTTPError/TimeoutError are OSError; JSONDecodeError is ValueError.)
LLM_ERRORS = (OSError, ValueError, RuntimeError, KeyError, IndexError, TypeError)

AGENT_PROVIDERS = {
    "ollama": {
        "label": "Ollama (local, free)",
        "default_model": "llama3.2",
        "env_key": None,
    },
    "groq": {
        "label": "Groq (free cloud tier)",
        "default_model": "llama-3.3-70b-versatile",
        "env_key": "GROQ_API_KEY",
    },
    "gemini": {
        "label": "Google Gemini (free tier)",
        "default_model": "gemini-2.0-flash",
        "env_key": "GEMINI_API_KEY",
    },
    "rules": {
        "label": "Rule-based (no LLM)",
        "default_model": "builtin",
        "env_key": None,
    },
}

SYSTEM_PROMPT = """You are a disciplined trading analyst assistant for a personal stock watchlist.
You analyze technical indicators and recent news to suggest trade setups.

Rules:
- This is educational analysis, NOT financial advice.
- Be concise and structured.
- Use the provided numeric levels; you may refine them slightly with reasoning.
- If setup is weak, say "wait" and explain why.
- Consider news sentiment alongside technicals.
- Focus on pullback vs breakout style setups.

Respond in this exact format:

SETUP: [pullback | breakout | wait | avoid]
BIAS: [bullish | neutral | bearish]
CONFIDENCE: [low | medium | high]

BUY ZONE: $X – $Y
STOP LOSS: $Z
TAKE PROFIT: $W
RISK/REWARD: N:1

TECHNICAL READ:
(2-3 bullets)

NEWS READ:
(1-2 bullets)

PLAN:
(2-3 sentences — what to watch for before entering)
"""

CHAT_SYSTEM_PROMPT = """You are a helpful stock trading research assistant (like ChatGPT for stocks).
The user asks questions about stocks they are tracking.

Rules:
- Answer in plain, conversational language.
- Use ONLY the market data and news provided in the context — do not invent prices.
- This is educational research, NOT financial advice. Say so if asked to guarantee returns.
- Be concise but thorough. Use bullet points when comparing levels or scenarios.
- You can discuss: buy/sell/stop levels, trend, RSI, volume, news impact, risk/reward, sector context.
- The user's default watchlist is {watchlist}, but they can open any ticker.
- You only have live data for the symbol in the context below. If asked about a different
  ticker, say so and suggest opening it in the app.
""".format(watchlist=", ".join(WATCHLIST))


def get_provider() -> str:
    return os.getenv("TRADER_AGENT_PROVIDER", "ollama").lower()


def ollama_available() -> bool:
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_model(provider: str | None = None) -> str:
    provider = provider or get_provider()
    cfg = AGENT_PROVIDERS.get(provider, AGENT_PROVIDERS["ollama"])
    env_model = os.getenv(f"{provider.upper()}_MODEL")
    return env_model or cfg["default_model"]


def agent_status() -> dict[str, Any]:
    provider = get_provider()
    cfg = AGENT_PROVIDERS.get(provider, AGENT_PROVIDERS["ollama"])
    api_key_env = cfg.get("env_key")
    ollama_ok = ollama_available()
    configured = (
        provider == "rules"
        or (provider == "ollama" and ollama_ok)
        or bool(api_key_env and os.getenv(api_key_env))
    )

    server_keys = {
        "groq": bool(os.getenv("GROQ_API_KEY")),
        "gemini": bool(os.getenv("GEMINI_API_KEY")),
    }

    return {
        "provider": provider,
        "provider_label": cfg["label"],
        "model": get_model(provider),
        "configured": configured,
        "ollama_available": ollama_ok,
        "server_keys": server_keys,
        "available_providers": [
            {
                "id": pid,
                "label": pcfg["label"],
                "default_model": pcfg["default_model"],
                "requires_key": bool(pcfg.get("env_key")),
                "key_configured": (
                    True
                    if not pcfg.get("env_key")
                    else server_keys.get(pid, False)
                ),
            }
            for pid, pcfg in AGENT_PROVIDERS.items()
        ],
        "recommended_free": [
            {
                "provider": "groq",
                "model": "llama-3.3-70b-versatile",
                "why": "Easiest free ChatGPT-style chat — get a free key at console.groq.com",
                "signup": "https://console.groq.com/keys",
            },
            {
                "provider": "gemini",
                "model": "gemini-2.0-flash",
                "why": "Free Google AI Studio key",
                "signup": "https://aistudio.google.com/apikey",
            },
            {
                "provider": "ollama",
                "model": "llama3.2",
                "why": "Local & private — install ollama.com then: ollama pull llama3.2",
                "signup": "https://ollama.com",
            },
        ],
    }


def build_context(symbol: str, name: str, levels: dict, news: list[dict]) -> str:
    headlines = "\n".join(
        f"- {n['title']} ({n.get('publisher', 'Unknown')})" for n in news[:5]
    ) or "- No recent headlines"
    rec = levels.get("recommendation") or {}

    return f"""Symbol: {symbol} ({name})

PRICE & TREND:
- Price: ${levels['price']}
- vs SMA20: {levels['trend_vs_sma20']} (SMA20 ${levels['sma20']})
- vs SMA50: {levels['trend_vs_sma50']} (SMA50 ${levels.get('sma50', 'n/a')})
- vs SMA200: {levels['trend_vs_sma200']} (SMA200 ${levels.get('sma200', 'n/a')})

MOMENTUM & VOLUME:
- RSI(14): {levels.get('rsi14', 'n/a')} ({levels.get('rsi_signal', 'n/a')})
- ATR(14): ${levels['atr14']}
- Volume: {levels.get('volume_last', 'n/a')} ({levels.get('volume_ratio', 'n/a')}x 20d avg, {levels.get('volume_signal', 'n/a')}{', session in progress — ratio is projected to full day' if levels.get('volume_partial_session') else ''})

KEY LEVELS (pre-calculated):
- Support (60d): ${levels['support_60d']}
- Resistance (60d): ${levels['resistance_60d']}
- Suggested buy zone: ${levels['buy_low']} – ${levels['buy_high']}
- Suggested stop: ${levels['stop_loss']}
- Suggested take profit: ${levels['take_profit']} ({'ATR projection above recent high' if levels.get('take_profit_basis') == 'atr_projection' else '60d resistance'})
- Risk/reward from buy zone: {levels.get('risk_reward', 'n/a')}:1
- Risk/reward from current price: {levels.get('risk_reward_now', 'n/a')}:1
- Recommendation: {rec.get('action', 'n/a')} ({rec.get('confidence', 'n/a')} confidence) — {rec.get('summary', '')}

RECENT NEWS:
{headlines}
"""


def build_context_with_stats(
    symbol: str, name: str, levels: dict, news: list[dict], price_stats: dict | None = None
) -> str:
    base = build_context(symbol, name, levels, news)
    if not price_stats:
        return base

    stats_lines = []
    for label, key in [
        ("1 day", "change_1d_pct"),
        ("5 days", "change_5d_pct"),
        ("1 month", "change_1mo_pct"),
        ("3 months", "change_3mo_pct"),
        ("6 months", "change_6mo_pct"),
        ("1 year", "change_1y_pct"),
    ]:
        if price_stats.get(key) is not None:
            stats_lines.append(f"- {label}: {price_stats[key]:+.2f}%")
    stats_lines.append(f"- 1-year range: ${price_stats.get('low_1y')} – ${price_stats.get('high_1y')}")

    return base + "\nPRICE HISTORY:\n" + "\n".join(stats_lines) + "\n"


def _http_post_json(url: str, payload: dict, headers: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            # Cloudflare / Groq block bare Python urllib without a browser-like UA
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36 TraderApp/0.1"
            ),
            **(headers or {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_history(history: list[dict]) -> list[dict]:
    cleaned = []
    for msg in history[-10:]:
        role = msg.get("role", "")
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            cleaned.append({"role": role, "content": content})
    return cleaned


def _call_ollama_messages(messages: list[dict], model: str) -> str:
    base = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.4},
    }
    result = _http_post_json(f"{base}/api/chat", payload)
    return result["message"]["content"].strip()


def _call_groq_messages(messages: list[dict], model: str, api_key: str | None = None) -> str:
    api_key = api_key or os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not set — paste your free key in Stock Chat settings")

    payload = {"model": model, "messages": messages, "temperature": 0.4}
    result = _http_post_json(
        "https://api.groq.com/openai/v1/chat/completions",
        payload,
        headers={"Authorization": f"Bearer {api_key}"},
    )
    return result["choices"][0]["message"]["content"].strip()


def _call_gemini_messages(
    messages: list[dict], model: str, api_key: str | None = None
) -> str:
    api_key = api_key or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set — paste your free key in Stock Chat settings")

    contents = []
    for msg in messages:
        if msg["role"] == "system":
            continue
        role = "user" if msg["role"] == "user" else "model"
        contents.append({"role": role, "parts": [{"text": msg["content"]}]})

    system = next((m["content"] for m in messages if m["role"] == "system"), CHAT_SYSTEM_PROMPT)
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.4},
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{urllib.parse.quote(model, safe='')}:generateContent?key={api_key}"
    )
    result = _http_post_json(url, payload)
    return result["candidates"][0]["content"]["parts"][0]["text"].strip()


def _call_llm_messages(
    messages: list[dict],
    provider: str,
    model: str,
    api_key: str | None = None,
) -> str:
    if provider == "ollama":
        return _call_ollama_messages(messages, model)
    if provider == "groq":
        return _call_groq_messages(messages, model, api_key=api_key)
    if provider == "gemini":
        return _call_gemini_messages(messages, model, api_key=api_key)
    raise RuntimeError(f"Provider {provider} does not support chat")


def _call_ollama(prompt: str, model: str) -> str:
    return _call_ollama_messages(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        model,
    )


def _call_groq(prompt: str, model: str) -> str:
    return _call_groq_messages(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        model,
    )


def _call_gemini(prompt: str, model: str) -> str:
    return _call_gemini_messages(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
        model,
    )


def _rules_analysis(levels: dict, news: list[dict]) -> str:
    """Deterministic fallback when no LLM is available."""
    rsi = levels.get("rsi14")
    rsi_sig = levels.get("rsi_signal", "neutral")
    vol_sig = levels.get("volume_signal", "normal")
    below20 = levels.get("trend_vs_sma20") == "below"
    below50 = levels.get("trend_vs_sma50") == "below"
    above200 = levels.get("trend_vs_sma200") == "above"

    if below20 and below50 and rsi_sig == "overbought":
        setup, bias, conf = "wait", "bearish", "medium"
    elif below20 and below50 and rsi_sig == "oversold" and above200:
        setup, bias, conf = "pullback", "bullish", "medium"
    elif below20 and below50:
        setup, bias, conf = "pullback", "neutral", "low"
    elif not below20 and not below50:
        setup, bias, conf = "breakout", "bullish", "medium"
    else:
        setup, bias, conf = "wait", "neutral", "low"

    news_note = news[0]["title"] if news else "No major headlines in feed"
    vol_note = (
        "High volume supports the move."
        if vol_sig == "high"
        else "Low volume — wait for confirmation."
        if vol_sig == "low"
        else "Volume is normal."
    )

    return f"""SETUP: {setup}
BIAS: {bias}
CONFIDENCE: {conf}

BUY ZONE: ${levels['buy_low']} – ${levels['buy_high']}
STOP LOSS: ${levels['stop_loss']}
TAKE PROFIT: ${levels['take_profit']}
RISK/REWARD: {levels.get('risk_reward', 'n/a')}:1

TECHNICAL READ:
- Price is {levels['trend_vs_sma20']} SMA20 and {levels['trend_vs_sma50']} SMA50; {levels['trend_vs_sma200']} SMA200.
- RSI {rsi} ({rsi_sig}). {vol_note}

NEWS READ:
- Latest: {news_note}

PLAN:
Rule-based agent (no LLM). Prefer entries in the buy zone with stop below support.
If price is below SMA20/50, wait for support or RSI oversold before sizing in.
Install Ollama or set GROQ_API_KEY / GEMINI_API_KEY for AI analysis."""


_NUMBER = r"(\d[\d,]*(?:\.\d+)?)"
# Numbers that are clearly not prices: share counts, percents, time spans
_NOT_PRICE_SUFFIX = r"(?!\s*(?:shares?|%|percent|x\b|days?|weeks?|months?|years?|yrs?)\b|\s*%|\d|,\d|\.\d)"


def _extract_prices(question: str) -> list[float]:
    """Likely prices in the question, best guess first.

    Order of preference: "$470", then "at/@/paid 470", then any other number that
    isn't a share count, percent or time span ("I bought 100 shares at 470" -> 470).
    """
    def nums(pattern: str) -> list[float]:
        found = [float(m.replace(",", "")) for m in re.findall(pattern, question, re.IGNORECASE)]
        return [n for n in found if n > 1]

    dollar = nums(r"\$\s*" + _NUMBER)
    anchored = nums(r"(?:\b(?:at|paid|price of|cost of|basis of|entry of|around)\b|@)\s*\$?\s*" + _NUMBER)
    other = nums(r"(?<![\d.,$])" + _NUMBER + _NOT_PRICE_SUFFIX)

    ordered: list[float] = []
    for n in dollar + anchored + other:
        if n not in ordered:
            ordered.append(n)
    return ordered


def _answer_entry_price(
    entry: float, symbol: str, levels: dict, price_stats: dict | None
) -> str:
    price = levels["price"]
    pnl_pct = round((price - entry) / entry * 100, 1)
    pnl_usd = round(price - entry, 2)
    stop = levels["stop_loss"]
    target = levels["take_profit"]

    if pnl_pct >= 0:
        pnl_line = f"You're up ${pnl_usd} (+{pnl_pct}%) from your ${entry} entry."
    else:
        pnl_line = f"You're down ${abs(pnl_usd)} ({pnl_pct}%) from your ${entry} entry."

    lines = [f"Your {symbol} entry: ${entry}. Current price: ${price}.", pnl_line]

    if entry <= stop and price > stop:
        lines.append(
            f"Your entry is near/below the suggested stop (${stop}). Price recovered above it — "
            f"consider whether your original risk level still makes sense."
        )
    elif price <= stop:
        lines.append(
            f"Price (${price}) is at or below suggested stop (${stop}). Your thesis may be broken — review news before holding."
        )
    elif pnl_pct >= 30:
        lines.append(
            f"You have a large open gain. Consider partial profit near ${target} (resistance) "
            f"or trailing a stop below SMA20 (${levels['sma20']})."
        )
    else:
        lines.append(
            f"Suggested stop from current levels: ${stop}. Take-profit zone: ${target}. "
            f"Risk/reward on new entries is ~{levels.get('risk_reward', 'n/a')}:1."
        )

    if price_stats and price_stats.get("change_1mo_pct") is not None:
        lines.append(f"1-month move: {price_stats['change_1mo_pct']:+.1f}%.")

    return "\n".join(lines)


def _answer_decline(question: str, symbol: str, levels: dict, price_stats: dict | None) -> str:
    change_1mo = price_stats.get("change_1mo_pct") if price_stats else None
    change_3mo = price_stats.get("change_3mo_pct") if price_stats else None

    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%|%\s*(\d+(?:\.\d+)?)", question)
    user_pct = float(pct_match.group(1) or pct_match.group(2)) if pct_match else None

    lines = [f"{symbol} decline context:"]

    if change_1mo is not None:
        lines.append(f"• Actual 1-month change: {change_1mo:+.1f}%")
        if user_pct is not None:
            diff = abs(abs(change_1mo) - user_pct)
            if diff < 5:
                lines.append(f"  (Close to your ~{user_pct}% estimate.)")
            else:
                lines.append(f"  (Your ~{user_pct}% estimate differs — data shows {change_1mo:+.1f}%.)")
    if change_3mo is not None:
        lines.append(f"• 3-month change: {change_3mo:+.1f}%")

    lines.append(
        f"• Trend: {levels['trend_vs_sma20']} SMA20, {levels['trend_vs_sma50']} SMA50, "
        f"{levels['trend_vs_sma200']} SMA200"
    )
    lines.append(f"• RSI {levels.get('rsi14')} ({levels.get('rsi_signal')}) — volume {levels.get('volume_signal')}")

    if change_1mo is not None and change_1mo <= -15:
        lines.append(
            "Sharp drop — check news for sector selloff or company-specific issues. "
            f"Support to watch: ${levels['support_60d']}. Don't catch a falling knife unless RSI hits oversold at support."
        )
    elif change_1mo is not None and change_1mo < 0:
        lines.append(
            f"Moderate pullback. Buy zone if looking to add: ${levels['buy_low']} – ${levels['buy_high']}. "
            f"Stop below ${levels['stop_loss']}."
        )
    else:
        lines.append(
            f"Despite the concern, 1-month change isn't deeply negative. "
            f"Current price ${levels['price']} vs support ${levels['support_60d']}."
        )

    return "\n".join(lines)


def _format_recommendation(levels: dict) -> str:
    rec = levels.get("recommendation") or {}
    action = rec.get("action", "HOLD")
    conf = rec.get("confidence", "medium")
    summary = rec.get("summary", "")
    reasons = rec.get("reasons") or []
    reason_lines = "\n".join(f"• {r}" for r in reasons[:5])
    return (
        f"Recommendation: {action} (confidence: {conf})\n"
        f"{summary}\n\n"
        f"Why:\n{reason_lines}\n\n"
        f"Levels — Buy: ${levels['buy_low']}–${levels['buy_high']} · "
        f"Stop: ${levels['stop_loss']} · Target: ${levels['take_profit']}\n\n"
        f"Educational signal only — not financial advice."
    )


def _rules_chat_answer(
    question: str,
    symbol: str,
    name: str,
    levels: dict,
    news: list[dict],
    price_stats: dict | None = None,
) -> str:
    q = question.lower()
    prices = _extract_prices(question)

    if any(
        w in q
        for w in (
            "recommend",
            "buy or sell",
            "sell or buy",
            "buy sell hold",
            "should i buy",
            "should i sell",
            "should i hold",
            "hold or sell",
            "keep or sell",
            "what to do",
            "good time",
            "worth buying",
            "rating",
        )
    ):
        return _format_recommendation(levels)

    if any(w in q for w in ("bought", "buy at", "entry", "paid", "cost basis", "my entry", "i got in")):
        if prices:
            return _answer_entry_price(prices[0], symbol, levels, price_stats)
        return (
            f"What price did you buy {symbol} at? Include the number (e.g. 'I bought at $470') "
            f"and I can calculate your gain/loss vs current ${levels['price']}."
        )

    if any(w in q for w in ("declin", "dropped", "fell", "selloff", "crash", "tank")) or (
        "down" in q and ("month" in q or "week" in q or "%" in q)
    ):
        return _answer_decline(question, symbol, levels, price_stats)

    if any(w in q for w in ("profit", "loss", "p/l", "pnl", "gain", "up ", "down ")) and prices:
        return _answer_entry_price(prices[0], symbol, levels, price_stats)

    if any(w in q for w in ("buy", "where should i buy")) and "bought" not in q:
        return (
            f"For {symbol} ({name}), the suggested buy zone is ${levels['buy_low']} – ${levels['buy_high']} "
            f"(pullback toward support / SMA20). Current price is ${levels['price']}. "
            f"Price is {levels['trend_vs_sma20']} SMA20 — consider waiting for the zone if it's not there yet."
        )

    if any(w in q for w in ("stop", "stop loss", "stop-loss")):
        return (
            f"Suggested stop loss for {symbol}: ${levels['stop_loss']} (below 60-day support, ATR-adjusted). "
            f"Support level is ${levels['support_60d']}. If price closes below that, the bounce thesis may be invalid."
        )

    if any(w in q for w in ("sell", "take profit", "target", "exit")):
        return (
            f"Suggested take profit for {symbol}: ${levels['take_profit']} (near 60-day resistance ${levels['resistance_60d']}). "
            f"Risk/reward from mid buy zone is about {levels.get('risk_reward', 'n/a')}:1."
        )

    if "news" in q or "headline" in q:
        if not news:
            return f"No recent headlines found for {symbol} in the Yahoo feed."
        lines = "\n".join(f"• {n['title']}" for n in news[:5])
        return f"Latest news for {symbol}:\n{lines}\n\nCheck if headlines explain today's volume ({levels.get('volume_signal', 'n/a')})."

    if "rsi" in q:
        return (
            f"{symbol} RSI(14) is {levels.get('rsi14', 'n/a')} — {levels.get('rsi_signal', 'neutral')}. "
            f"Below 30 = oversold (possible bounce), above 70 = overbought (caution)."
        )

    if any(w in q for w in ("trend", "sma", "moving average")):
        return (
            f"{symbol} trend: {levels['trend_vs_sma20']} SMA20 (${levels['sma20']}), "
            f"{levels['trend_vs_sma50']} SMA50, {levels['trend_vs_sma200']} SMA200 (${levels.get('sma200', 'n/a')})."
        )

    if any(w in q for w in ("should i", "good time", "worth", "hold or sell", "keep or sell")):
        return _format_recommendation(levels)

    if re.search(r"\b(?:hello|hi|hey|help|what can you)\b", q):
        return (
            f"I can answer specific questions about {symbol} using live data:\n"
            f"• 'Recommend buy sell or hold?' — action rating\n"
            f"• 'I bought at $470' — your P/L and stop advice\n"
            f"• 'MU declined 30% last month' — verify the drop + levels\n"
            f"• 'Where to buy?' / 'Stop loss?' / 'News?' / 'RSI?'\n\n"
            f"For free-form ChatGPT-style chat, install Ollama: ollama pull llama3.2"
        )

    return (
        f"I didn't match that question to a specific topic for {symbol}.\n\n"
        f"Try asking about:\n"
        f"• Your entry price ('I bought at $X')\n"
        f"• Recent decline ('down 30% last month')\n"
        f"• Buy / stop / target levels\n"
        f"• News, RSI, or trend\n\n"
        f"Current price: ${levels['price']} · RSI {levels.get('rsi14')} · "
        f"{levels['trend_vs_sma20']} SMA20\n\n"
        f"For conversational answers on any question, enable Ollama or a Groq/Gemini API key "
        f"(see .env.example)."
    )


def chat_about_symbol(
    symbol: str,
    name: str,
    levels: dict,
    news: list[dict],
    message: str,
    history: list[dict] | None = None,
    price_stats: dict | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    provider = (provider or get_provider()).lower()
    if provider not in AGENT_PROVIDERS:
        provider = "rules"
    model = model or get_model(provider)
    history = _normalize_history(history or [])
    context = build_context_with_stats(symbol, name, levels, news, price_stats)

    user_content = f"""CURRENT MARKET DATA (use these numbers):
{context}

USER QUESTION: {message.strip()}

Answer conversationally with specifics. If the user mentions their buy price, calculate P/L.
If they ask about declines, use the 1-month / 3-month change numbers from the data."""

    messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_content})

    used_fallback = False
    error = None

    try:
        if provider == "rules":
            reply = _rules_chat_answer(message, symbol, name, levels, news, price_stats)
            reply += (
                "\n\n— Still in template mode. For LLM chat, set TRADER_AGENT_PROVIDER "
                "and GROQ_API_KEY or GEMINI_API_KEY in the server .env (see .env.example)."
            )
        else:
            # API keys come only from server environment — never from the client.
            reply = _call_llm_messages(messages, provider, model)
    except LLM_ERRORS as exc:
        used_fallback = True
        error = str(exc)
        reply = _rules_chat_answer(message, symbol, name, levels, news, price_stats)
        reply += (
            f"\n\n_(AI chat failed ({provider}): {error}. "
            "Check server .env keys / Ollama, or switch provider.)_"
        )

    return {
        "symbol": symbol,
        "provider": provider,
        "model": model,
        "reply": reply,
        "used_fallback": used_fallback,
        "error": error,
    }


def analyze_symbol(
    symbol: str,
    name: str,
    levels: dict,
    news: list[dict],
) -> dict[str, Any]:
    provider = get_provider()
    model = get_model(provider)
    context = build_context(symbol, name, levels, news)

    used_fallback = False
    error = None

    try:
        if provider == "rules":
            analysis = _rules_analysis(levels, news)
        elif provider == "ollama":
            analysis = _call_ollama(context, model)
        elif provider == "groq":
            analysis = _call_groq(context, model)
        elif provider == "gemini":
            analysis = _call_gemini(context, model)
        else:
            raise RuntimeError(f"Unknown provider: {provider}")
    except LLM_ERRORS as exc:
        used_fallback = True
        error = str(exc)
        analysis = _rules_analysis(levels, news)
        analysis += f"\n\n[LLM unavailable ({provider}): {error}. Showing rule-based analysis.]"

    return {
        "symbol": symbol,
        "provider": provider,
        "model": model,
        "analysis": analysis,
        "used_fallback": used_fallback,
        "error": error,
    }
