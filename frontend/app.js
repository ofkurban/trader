const API = "/api";

let priceChart = null;
let rsiChart = null;
let lineSeries = null;
let volumeSeries = null;
let rsiSeries = null;
let priceLines = [];
let currentSymbol = null;
let currentLevels = null;
let chatHistory = [];
// Bumped on every chat reset / stock load so late responses can tell they're stale
let chatSession = 0;
let stockRequestSeq = 0;

const els = {
  watchlist: document.getElementById("watchlist"),
  emptyState: document.getElementById("empty-state"),
  stockPanel: document.getElementById("stock-panel"),
  stockTitle: document.getElementById("stock-title"),
  stockMeta: document.getElementById("stock-meta"),
  stockPrice: document.getElementById("stock-price"),
  stockRec: document.getElementById("stock-rec"),
  stockTrend: document.getElementById("stock-trend"),
  recBanner: document.getElementById("rec-banner"),
  recAction: document.getElementById("rec-action"),
  recConfidence: document.getElementById("rec-confidence"),
  recSummary: document.getElementById("rec-summary"),
  recReasons: document.getElementById("rec-reasons"),
  perf1d: document.getElementById("perf-1d"),
  perf5d: document.getElementById("perf-5d"),
  perf1mo: document.getElementById("perf-1mo"),
  perf3mo: document.getElementById("perf-3mo"),
  perf6mo: document.getElementById("perf-6mo"),
  perf1y: document.getElementById("perf-1y"),
  chartPrice: document.getElementById("chart-price"),
  chartRsi: document.getElementById("chart-rsi"),
  metricRsi: document.getElementById("metric-rsi"),
  metricRsiHint: document.getElementById("metric-rsi-hint"),
  metricSma200: document.getElementById("metric-sma200"),
  metricSma200Hint: document.getElementById("metric-sma200-hint"),
  metricVolume: document.getElementById("metric-volume"),
  metricVolumeHint: document.getElementById("metric-volume-hint"),
  metricTrend: document.getElementById("metric-trend"),
  levelBuy: document.getElementById("level-buy"),
  levelTakeProfit: document.getElementById("level-take-profit"),
  levelStop: document.getElementById("level-stop"),
  levelTakeProfitHint: document.getElementById("level-take-profit-hint"),
  levelRr: document.getElementById("level-rr"),
  levelSupportResistance: document.getElementById("level-support-resistance"),
  inputBuy: document.getElementById("input-buy"),
  inputTakeProfit: document.getElementById("input-take-profit"),
  inputStop: document.getElementById("input-stop"),
  inputShares: document.getElementById("input-shares"),
  planMessage: document.getElementById("plan-message"),
  symbolSearch: document.getElementById("symbol-search"),
  symbolInput: document.getElementById("symbol-input"),
  symbolResults: document.getElementById("symbol-results"),
  searchStatus: document.getElementById("search-status"),
  resetWatchlistBtn: document.getElementById("reset-watchlist-btn"),
  watchToggleBtn: document.getElementById("watch-toggle-btn"),
  lastUpdated: document.getElementById("last-updated"),
  refreshBtn: document.getElementById("refresh-btn"),
  resetLevelsBtn: document.getElementById("reset-levels-btn"),
  savePlanBtn: document.getElementById("save-plan-btn"),
  newsList: document.getElementById("news-list"),
  sectorNews: document.getElementById("sector-news"),
  overnightBias: document.getElementById("overnight-bias"),
  overnightSummary: document.getElementById("overnight-summary"),
  overnightGrid: document.getElementById("overnight-grid"),
  overnightTip: document.getElementById("overnight-tip"),
  greedScore: document.getElementById("greed-score"),
  greedLabel: document.getElementById("greed-label"),
  greedHint: document.getElementById("greed-hint"),
  greedMarker: document.getElementById("greed-meter-marker"),
  greedComponents: document.getElementById("greed-components"),
  greedDisclaimer: document.getElementById("greed-disclaimer"),
  chipNewsTone: document.getElementById("chip-news-tone"),
  chipNewsSummary: document.getElementById("chip-news-summary"),
  chipNewsList: document.getElementById("chip-news-list"),
  chinaMemoryWatch: document.getElementById("china-memory-watch"),
  agentStatus: document.getElementById("agent-status"),
  agentAnalyzeBtn: document.getElementById("agent-analyze-btn"),
  chatProvider: document.getElementById("chat-provider"),
  chatKeyLink: document.getElementById("chat-key-link"),
  chatMessages: document.getElementById("chat-messages"),
  chatForm: document.getElementById("chat-form"),
  chatInput: document.getElementById("chat-input"),
  chatSendBtn: document.getElementById("chat-send-btn"),
};

const CHART_BASE = {
  layout: {
    background: { color: "#1a2332" },
    textColor: "#8b9cb3",
  },
  grid: {
    vertLines: { color: "#2d3a4f" },
    horzLines: { color: "#2d3a4f" },
  },
  rightPriceScale: { borderColor: "#2d3a4f" },
  timeScale: { borderColor: "#2d3a4f" },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
};

function formatPct(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const n = Number(value);
  const sign = n > 0 ? "+" : "";
  return `${sign}${n.toFixed(2)}%`;
}

function pctClass(value) {
  if (value == null || Number.isNaN(Number(value))) return "flat";
  if (Number(value) > 0) return "up";
  if (Number(value) < 0) return "down";
  return "flat";
}

function formatMoney(value) {
  return `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatVolume(value) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}

function trendLabel(vs) {
  if (vs === "above") return "▲ above";
  if (vs === "below") return "▼ below";
  return "— n/a";
}

function escapeHtml(value) {
  if (value == null) return "";
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function stripHtml(html) {
  if (!html) return "";
  return String(html)
    .replace(/<[^>]*>/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function safeUrl(url) {
  if (!url || typeof url !== "string") return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      return parsed.href;
    }
  } catch {
    /* ignore */
  }
  return null;
}

function renderNewsList(articles) {
  if (!articles?.length) {
    els.newsList.innerHTML = `<p class="news-empty">No recent news found.</p>`;
    return;
  }

  els.newsList.innerHTML = articles
    .map((article) => {
      const summary = article.summary ? stripHtml(article.summary).slice(0, 220) : "";
      const title = escapeHtml(article.title || "Untitled");
      const href = safeUrl(article.url);
      const link = href
        ? `<a href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer">${title}</a>`
        : `<span>${title}</span>`;
      const meta = [article.publisher, article.published, article.type]
        .filter(Boolean)
        .map(escapeHtml)
        .join(" · ");
      return `
        <article class="news-item">
          ${link}
          ${summary ? `<p class="summary">${escapeHtml(summary)}${summary.length >= 220 ? "…" : ""}</p>` : ""}
          <p class="meta">${meta}</p>
        </article>
      `;
    })
    .join("");
}

function renderSectorNews(items) {
  if (!items?.length) {
    els.sectorNews.innerHTML = `<p class="news-empty">No headlines available.</p>`;
    return;
  }

  els.sectorNews.innerHTML = items
    .map(({ symbol, headline }) => {
      const href = safeUrl(headline.url);
      const attrs = href
        ? `href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer"`
        : "";
      const meta = [
        headline.publisher,
        headline.published ? headline.published : null,
      ]
        .filter(Boolean)
        .map(escapeHtml)
        .join(" · ");
      return `
        <a class="sector-news-item" ${attrs} data-symbol="${escapeHtml(symbol)}">
          <span class="sym">${escapeHtml(symbol)}</span>
          <span class="title">${escapeHtml(headline.title || "")}</span>
          <span class="meta">${meta}</span>
        </a>
      `;
    })
    .join("");

  els.sectorNews.querySelectorAll(".sector-news-item[data-symbol]").forEach((el) => {
    el.addEventListener("click", (e) => {
      if (e.metaKey || e.ctrlKey || el.getAttribute("href")?.startsWith("http")) return;
      e.preventDefault();
      openStock(el.dataset.symbol);
    });
  });
}

async function loadSectorNews() {
  const data = await fetchJson(`${API}/news?limit=1${symbolsQuery("&")}`);
  renderSectorNews(data.news);
}

function renderGreed(greed) {
  if (!greed || !els.greedScore) return;
  const score = Number(greed.score ?? 50);
  const label = greed.label || "NEUTRAL";
  const labelClass = label.toLowerCase().replace(/\s+/g, "-");

  els.greedScore.textContent = score.toFixed(0);
  els.greedLabel.textContent = label;
  els.greedLabel.className = `greed-label ${labelClass}`;
  els.greedHint.textContent = greed.hint || "";
  els.greedMarker.style.left = `${Math.max(0, Math.min(100, score))}%`;
  els.greedDisclaimer.textContent = greed.disclaimer || "";

  els.greedComponents.innerHTML = (greed.components || [])
    .map(
      (c) => `
      <div class="greed-comp">
        <div class="name">${escapeHtml(c.name)}</div>
        <div class="val">${escapeHtml(Number(c.score).toFixed(0))}</div>
        <div class="detail">${escapeHtml(c.detail || "")}</div>
      </div>
    `,
    )
    .join("");
}

function renderOvernight(data) {
  const bias = (data.bias || "MIXED").toUpperCase();
  const biasClass =
    bias === "RISK-ON" ? "risk-on" : bias === "RISK-OFF" ? "risk-off" : "mixed";

  els.overnightBias.textContent = bias;
  els.overnightBias.className = `overnight-bias ${biasClass}`;
  els.overnightSummary.textContent = data.summary || "";
  els.overnightTip.textContent = data.tip || "";

  if (data.greed) renderGreed(data.greed);

  els.overnightGrid.innerHTML = (data.items || [])
    .map((item) => {
      if (item.error) {
        return `
          <div class="overnight-card">
            <div class="label">${escapeHtml(item.label)}</div>
            <span class="note">${escapeHtml(item.note || "")}</span>
            <div class="chg flat">n/a</div>
          </div>
        `;
      }
      return `
        <div class="overnight-card">
          <div class="label">${escapeHtml(item.label)}</div>
          <span class="note">${escapeHtml(item.note || "")}</span>
          <div class="chg ${pctClass(item.change_1d_pct)}">${formatPct(item.change_1d_pct)}</div>
        </div>
      `;
    })
    .join("");

  const chip = data.chip_news || {};
  const tone = (chip.tone || "MIXED").toUpperCase();
  const toneClass =
    tone === "CHIP STRESS" ? "chip-stress" : tone === "CALM" ? "calm" : "mixed";
  els.chipNewsTone.textContent = tone;
  els.chipNewsTone.className = `chip-news-tone ${toneClass}`;
  els.chipNewsSummary.textContent = chip.summary || "";

  const watch = chip.watch?.cxmt;
  const chinaArts = chip.china_memory_articles || [];
  if (watch) {
    const count = chip.china_memory_count || 0;
    els.chinaMemoryWatch.innerHTML = `
      <strong>China memory watch — ${escapeHtml(watch.name)} (${escapeHtml(watch.symbol)})</strong>
      <span class="why">${escapeHtml(watch.why)}</span>
      <span class="why">${
        count
          ? `${escapeHtml(String(count))} related headline(s) flagged (yellow bar below).`
          : "No CXMT/China-memory-supply headline matched in the latest feed — still watch this theme for MU."
      }</span>
    `;
  } else {
    els.chinaMemoryWatch.innerHTML = "";
  }

  const articles = chip.articles || [];
  if (!articles.length) {
    els.chipNewsList.innerHTML = `<p class="muted">No chip headlines right now.</p>`;
  } else {
    els.chipNewsList.innerHTML = articles
      .map((a) => {
        const href = safeUrl(a.url);
        const attrs = href
          ? `href="${escapeHtml(href)}" target="_blank" rel="noopener noreferrer"`
          : "";
        const tags = [
          a.is_china_memory ? "China memory/CXMT" : null,
          a.is_selloff ? "sell-off" : null,
        ]
          .filter(Boolean)
          .join(" · ");
        const cls = [
          "chip-news-item",
          a.is_china_memory ? "china-memory" : "",
          a.is_selloff && !a.is_china_memory ? "selloff" : "",
        ]
          .filter(Boolean)
          .join(" ");
        const meta = [
          a.source_symbol,
          a.publisher,
          a.published,
          tags || null,
        ]
          .filter(Boolean)
          .map(escapeHtml)
          .join(" · ");
        return `
          <a class="${cls}" ${attrs}>
            <span class="title">${escapeHtml(a.title || "")}</span>
            <span class="meta">${meta}</span>
          </a>
        `;
      })
      .join("");
  }
}

function planKey(symbol) {
  return `trader-plan-${symbol}`;
}

async function fetchJson(url) {
  const res = await fetch(url);
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function renderWatchlist(items) {
  els.watchlist.innerHTML = "";
  for (const item of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `watch-item${item.symbol === currentSymbol ? " active" : ""}`;

    if (item.error) {
      btn.innerHTML = `
        <span class="sym">${escapeHtml(item.symbol)}</span>
        <span class="chip below">err</span>
        <span class="name">${escapeHtml(item.name)} — ${escapeHtml(item.error)}</span>
      `;
    } else {
      const rec = item.recommendation?.action || "HOLD";
      const recClass = `rec-${rec.toLowerCase()}`;
      const rsiChip = item.rsi_signal
        ? `<span class="chip ${escapeHtml(item.rsi_signal)}">RSI ${escapeHtml(item.rsi14)}</span>`
        : "";
      const d1 = item.change_1d_pct;
      btn.innerHTML = `
        <span class="sym">${escapeHtml(item.symbol)}</span>
        <span class="price">${formatMoney(item.price)}</span>
        <span class="name">${escapeHtml(item.name)}</span>
        <span class="chg ${pctClass(d1)}">${formatPct(d1)} 1D</span>
        <span class="chip ${escapeHtml(recClass)}">${escapeHtml(rec)}</span>
        <span class="chip ${escapeHtml(item.trend_vs_sma20)}">${escapeHtml(item.trend_vs_sma20)} SMA20</span>
        ${rsiChip}
      `;
      btn.addEventListener("click", () => openStock(item.symbol));
    }

    const row = document.createElement("div");
    row.className = "watch-row";
    row.appendChild(btn);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "watch-remove";
    remove.textContent = "×";
    remove.title = `Remove ${item.symbol} from watchlist`;
    remove.setAttribute("aria-label", remove.title);
    remove.addEventListener("click", () => removeFromWatchlist(item.symbol));
    row.appendChild(remove);

    els.watchlist.appendChild(row);
  }
}

function ensureCharts() {
  if (priceChart) return;

  priceChart = LightweightCharts.createChart(els.chartPrice, {
    ...CHART_BASE,
    timeScale: { ...CHART_BASE.timeScale, visible: false },
  });

  rsiChart = LightweightCharts.createChart(els.chartRsi, {
    ...CHART_BASE,
    timeScale: { ...CHART_BASE.timeScale, timeVisible: true },
  });

  lineSeries = priceChart.addLineSeries({
    color: "#59c2ff",
    lineWidth: 2,
    priceScaleId: "right",
  });

  volumeSeries = priceChart.addHistogramSeries({
    priceFormat: { type: "volume" },
    priceScaleId: "volume",
  });

  priceChart.priceScale("right").applyOptions({
    scaleMargins: { top: 0.08, bottom: 0.28 },
  });

  priceChart.priceScale("volume").applyOptions({
    scaleMargins: { top: 0.75, bottom: 0 },
  });

  rsiSeries = rsiChart.addLineSeries({
    color: "#c792ea",
    lineWidth: 2,
  });

  rsiChart.priceScale("right").applyOptions({
    scaleMargins: { top: 0.1, bottom: 0.1 },
  });

  rsiSeries.createPriceLine({
    price: 70,
    color: "#f07178",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    axisLabelVisible: true,
    title: "Overbought",
  });
  rsiSeries.createPriceLine({
    price: 30,
    color: "#3dd68c",
    lineWidth: 1,
    lineStyle: LightweightCharts.LineStyle.Dotted,
    axisLabelVisible: true,
    title: "Oversold",
  });

  const syncCharts = (source, target) => {
    source.timeScale().subscribeVisibleLogicalRangeChange((range) => {
      if (range) target.timeScale().setVisibleLogicalRange(range);
    });
  };
  syncCharts(priceChart, rsiChart);
  syncCharts(rsiChart, priceChart);

  const resize = () => {
    priceChart.applyOptions({
      width: els.chartPrice.clientWidth,
      height: els.chartPrice.clientHeight,
    });
    rsiChart.applyOptions({
      width: els.chartRsi.clientWidth,
      height: els.chartRsi.clientHeight,
    });
  };

  const observer = new ResizeObserver(resize);
  observer.observe(els.chartPrice);
  observer.observe(els.chartRsi);
  resize();
}

function clearPriceLines() {
  for (const pl of priceLines) {
    lineSeries.removePriceLine(pl);
  }
  priceLines = [];
}

function addPriceLine(price, color, title) {
  if (!price) return;
  const pl = lineSeries.createPriceLine({
    price,
    color,
    lineWidth: 2,
    lineStyle: LightweightCharts.LineStyle.Dashed,
    axisLabelVisible: true,
    title,
  });
  priceLines.push(pl);
}

function applySuggestedToForm(levels) {
  els.inputBuy.value = levels.entry_mid;
  els.inputTakeProfit.value = levels.take_profit;
  els.inputStop.value = levels.stop_loss;
}

function loadSavedPlan(symbol) {
  const raw = localStorage.getItem(planKey(symbol));
  if (!raw) return null;
  try {
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

function updateLevelCards(levels) {
  els.levelBuy.textContent = `${formatMoney(levels.buy_low)} – ${formatMoney(levels.buy_high)}`;
  els.levelTakeProfit.textContent = formatMoney(levels.take_profit);
  els.levelStop.textContent = formatMoney(levels.stop_loss);
  els.levelTakeProfitHint.textContent =
    levels.take_profit_basis === "atr_projection"
      ? "Near 60-day high — projected 3× ATR above price"
      : "Near 60-day resistance";
  const rrZone = levels.risk_reward != null ? `${levels.risk_reward}:1` : "—";
  const rrNow = levels.risk_reward_now != null ? `${levels.risk_reward_now}:1` : "—";
  els.levelRr.textContent = `${rrZone} zone · ${rrNow} now`;
  els.levelSupportResistance.textContent = `Support ${formatMoney(levels.support_60d)} · Resistance ${formatMoney(levels.resistance_60d)}`;
}

function updateMetricCards(levels) {
  els.metricRsi.textContent = levels.rsi14 != null ? levels.rsi14 : "—";
  els.metricRsiHint.textContent =
    levels.rsi_signal === "oversold"
      ? "Oversold — possible bounce zone"
      : levels.rsi_signal === "overbought"
        ? "Overbought — caution on new buys"
        : "Neutral momentum";

  els.metricSma200.textContent = levels.sma200 ? formatMoney(levels.sma200) : "n/a";
  els.metricSma200Hint.textContent = levels.sma200
    ? `Price is ${levels.trend_vs_sma200} long-term average`
    : "Need 200 days of history";

  const volRatio =
    levels.volume_ratio != null
      ? `${levels.volume_ratio}x avg${levels.volume_partial_session ? " proj." : ""}`
      : levels.volume_partial_session
        ? "too early"
        : "—";
  els.metricVolume.textContent = `${formatVolume(levels.volume_last)} (${volRatio})`;
  els.metricVolumeHint.textContent =
    levels.volume_signal === "high"
      ? "High volume — move has conviction"
      : levels.volume_signal === "low"
        ? "Low volume — weak participation"
        : levels.volume_signal === "n/a"
          ? "Session just opened — volume read not available yet"
          : "Normal volume vs 20-day average";

  els.metricTrend.innerHTML = [
    trendLabel(levels.trend_vs_sma20) + " SMA20",
    trendLabel(levels.trend_vs_sma50) + " SMA50",
    trendLabel(levels.trend_vs_sma200) + " SMA200",
  ].join("<br>");
}

function updatePerformance(levels) {
  const map = [
    [els.perf1d, levels.change_1d_pct, levels.change_1d],
    [els.perf5d, levels.change_5d_pct, levels.change_5d],
    [els.perf1mo, levels.change_1mo_pct, levels.change_1mo],
    [els.perf3mo, levels.change_3mo_pct, levels.change_3mo],
    [els.perf6mo, levels.change_6mo_pct, levels.change_6mo],
    [els.perf1y, levels.change_1y_pct, levels.change_1y],
  ];
  for (const [el, pct, dollar] of map) {
    if (!el) continue;
    let text = formatPct(pct);
    if (dollar != null && !Number.isNaN(Number(dollar))) {
      const d = Number(dollar);
      text += ` · ${d >= 0 ? "+" : "-"}$${Math.abs(d).toFixed(2)}`;
    }
    el.textContent = text;
    el.className = `perf-value ${pctClass(pct)}`;
  }
}

function updateRecommendation(levels) {
  const rec = levels.recommendation || {
    action: "HOLD",
    confidence: "medium",
    summary: "No recommendation available.",
    reasons: [],
  };
  const action = (rec.action || "HOLD").toUpperCase();
  const cls = action.toLowerCase();

  els.stockRec.textContent = action;
  els.stockRec.className = `rec-badge ${cls}`;

  els.recBanner.className = `rec-banner ${cls}`;
  els.recAction.textContent = action;
  els.recConfidence.textContent = `Confidence: ${rec.confidence || "medium"}`;
  els.recSummary.textContent = rec.summary || "";
  els.recReasons.innerHTML = (rec.reasons || [])
    .map((r) => `<li>${escapeHtml(r)}</li>`)
    .join("");
}

function rungRange(rung) {
  if (rung.high == null) return `above ${formatMoney(rung.low)}`;
  if (rung.low == null) return `below ${formatMoney(rung.high)}`;
  return `${formatMoney(rung.low)} – ${formatMoney(rung.high)}`;
}

function renderChecks(el, checks) {
  el.innerHTML = checks
    .map(
      (c) => `<li class="${c.ok ? "pass" : "fail"}">
        <span class="check-icon" aria-hidden="true">${c.ok ? "✓" : "✕"}</span>
        <span><strong>${escapeHtml(c.label)}</strong> <span class="muted">${escapeHtml(c.detail)}</span></span>
      </li>`,
    )
    .join("");
}

function renderSystem(system, price) {
  const panel = document.getElementById("system-panel");
  if (!system) {
    panel.classList.add("hidden");
    return;
  }
  panel.classList.remove("hidden");
  const { ladder, day, ladder_check: trust } = system;
  document.getElementById("ladder").innerHTML = ladder.rungs
    .map((r) => {
      const here = r.key === ladder.current;
      return `<div class="rung ${r.side} ${here ? "current" : ""}">
        <span class="rung-label">${escapeHtml(r.label)}</span>
        <span class="rung-range">${rungRange(r)}</span>
        ${here ? `<span class="rung-price">${formatMoney(price)}</span>` : ""}
      </div>`;
    })
    .join("");
  document.getElementById("ladder-basis").textContent =
    `Centred on ${ladder.anchor_name} ${formatMoney(ladder.anchor)}, rungs ${formatMoney(ladder.step)} apart (2× ATR)`;

  const dayVerdict = document.getElementById("day-verdict");
  dayVerdict.textContent = day.text;
  dayVerdict.className = `verdict ${day.status}`;
  renderChecks(document.getElementById("day-checks"), day.checks);

  const ladderVerdict = document.getElementById("ladder-verdict");
  ladderVerdict.textContent = trust.text;
  ladderVerdict.className = `verdict ${trust.status}`;
  renderChecks(document.getElementById("ladder-checks"), trust.checks);
}

function renderStock(data) {
  const symbolChanged = data.symbol !== currentSymbol;
  currentSymbol = data.symbol;
  currentLevels = data.levels;

  els.emptyState.classList.add("hidden");
  els.stockPanel.classList.remove("hidden");

  els.stockTitle.textContent = `${data.symbol} — ${data.name}`;
  els.stockMeta.textContent = `SMA20 ${formatMoney(data.levels.sma20)} · SMA50 ${data.levels.sma50 ? formatMoney(data.levels.sma50) : "n/a"} · SMA200 ${data.levels.sma200 ? formatMoney(data.levels.sma200) : "n/a"} · ATR14 ${formatMoney(data.levels.atr14)} · Data: ${data.data_source === "alpaca" ? "Alpaca" : "Yahoo"}`;
  els.stockPrice.textContent = formatMoney(data.levels.price);
  els.stockTrend.textContent = `${data.levels.trend_vs_sma20} SMA20 · ${data.levels.trend_vs_sma50} SMA50 · ${data.levels.trend_vs_sma200} SMA200 · RSI ${data.levels.rsi14 ?? "—"}`;
  els.stockTrend.className = `trend-badge chip ${data.levels.trend_vs_sma20}`;

  updateRecommendation(data.levels);
  renderSystem(data.system, data.levels.price);
  updatePerformance(data.levels);
  updateMetricCards(data.levels);
  updateLevelCards(data.levels);

  const saved = loadSavedPlan(data.symbol);
  if (saved) {
    els.inputBuy.value = saved.buy;
    els.inputTakeProfit.value = saved.takeProfit;
    els.inputStop.value = saved.stop;
    els.inputShares.value = saved.shares;
    els.planMessage.textContent = `Loaded saved plan from ${new Date(saved.savedAt).toLocaleString()}`;
  } else {
    applySuggestedToForm(data.levels);
    els.planMessage.textContent = "";
  }

  ensureCharts();

  const priceData = data.history.map((bar) => ({
    time: bar.date,
    value: bar.close,
  }));

  const volumeData = data.history.map((bar) => ({
    time: bar.date,
    value: bar.volume,
    color: bar.close >= bar.open ? "rgba(61, 214, 140, 0.5)" : "rgba(240, 113, 120, 0.5)",
  }));

  const rsiData = data.history
    .filter((bar) => bar.rsi != null)
    .map((bar) => ({ time: bar.date, value: bar.rsi }));

  lineSeries.setData(priceData);
  volumeSeries.setData(volumeData);
  rsiSeries.setData(rsiData);

  priceChart.timeScale().fitContent();
  rsiChart.timeScale().fitContent();

  clearPriceLines();
  addPriceLine(data.levels.buy_high, "#3dd68c", "Buy high");
  addPriceLine(data.levels.buy_low, "#3dd68c", "Buy low");
  addPriceLine(data.levels.take_profit, "#59c2ff", "Take profit");
  addPriceLine(data.levels.stop_loss, "#f07178", "Stop loss");
  if (data.levels.sma20) addPriceLine(data.levels.sma20, "#ffcc66", "SMA20");
  if (data.levels.sma200) addPriceLine(data.levels.sma200, "#c792ea", "SMA200");

  renderNewsList(data.news);
  // Keep the conversation when Refresh reloads the same symbol
  if (symbolChanged) resetChat();
}

function chatSettingsKey() {
  return "trader-chat-settings";
}

let agentStatusCache = null;

function loadChatSettings() {
  try {
    return JSON.parse(localStorage.getItem(chatSettingsKey()) || "{}");
  } catch {
    return {};
  }
}

function saveChatSettings() {
  localStorage.setItem(
    chatSettingsKey(),
    JSON.stringify({
      provider: els.chatProvider.value,
    }),
  );
}

function providerReady(provider) {
  if (!agentStatusCache) return false;
  if (provider === "rules") return true;
  if (provider === "ollama") return Boolean(agentStatusCache.ollama_available);
  return Boolean(agentStatusCache.server_keys?.[provider]);
}

function updateChatSettingsUI() {
  const provider = els.chatProvider.value;
  if (provider === "groq") {
    els.chatKeyLink.href = "https://console.groq.com/keys";
    els.chatKeyLink.textContent = "Get free Groq key → put in server .env";
    els.chatKeyLink.style.display = "";
  } else if (provider === "gemini") {
    els.chatKeyLink.href = "https://aistudio.google.com/apikey";
    els.chatKeyLink.textContent = "Get free Gemini key → put in server .env";
    els.chatKeyLink.style.display = "";
  } else if (provider === "ollama") {
    els.chatKeyLink.href = "https://ollama.com";
    els.chatKeyLink.textContent = "Install Ollama";
    els.chatKeyLink.style.display = "";
  } else {
    els.chatKeyLink.style.display = "none";
  }
  saveChatSettings();
  refreshAgentStatusLine();
}

function refreshAgentStatusLine() {
  const provider = els.chatProvider.value;
  if (provider === "groq") {
    els.agentStatus.textContent = providerReady("groq")
      ? "Groq ready (server GROQ_API_KEY) — ask detailed questions"
      : "Set GROQ_API_KEY in server .env for ChatGPT-style answers";
  } else if (provider === "gemini") {
    els.agentStatus.textContent = providerReady("gemini")
      ? "Gemini ready (server GEMINI_API_KEY)"
      : "Set GEMINI_API_KEY in server .env to chat";
  } else if (provider === "ollama") {
    els.agentStatus.textContent = providerReady("ollama")
      ? "Ollama ready — local chat"
      : "Ollama not detected — run ollama and: ollama pull llama3.2";
  } else {
    els.agentStatus.textContent =
      "Template mode — short canned answers only. Switch to Groq/Ollama for real chat.";
  }
}

function resetChat() {
  chatSession += 1;
  chatHistory = [];
  const label = currentSymbol || "this stock";
  els.chatMessages.replaceChildren();
  const bubble = document.createElement("div");
  bubble.className = "chat-bubble assistant";
  bubble.textContent = `Ask detailed questions about ${label}. Example: "I bought at $470 — should I hold through this pullback?"`;
  els.chatMessages.appendChild(bubble);
}

function appendChatBubble(role, text, extraClass = "") {
  const bubble = document.createElement("div");
  bubble.className = `chat-bubble ${role}${extraClass ? ` ${extraClass}` : ""}`;
  bubble.textContent = text;
  els.chatMessages.appendChild(bubble);
  els.chatMessages.scrollTop = els.chatMessages.scrollHeight;
  return bubble;
}

async function sendChatMessage(message) {
  if (!currentSymbol || !message.trim()) return;

  const provider = els.chatProvider.value;
  const symbol = currentSymbol;
  const session = chatSession;
  const userMessage = message.trim();
  els.chatInput.value = "";
  appendChatBubble("user", userMessage);
  chatHistory.push({ role: "user", content: userMessage });

  const loading = appendChatBubble("assistant", "Thinking…", "loading");
  els.chatSendBtn.disabled = true;
  els.chatInput.disabled = true;

  try {
    const res = await fetch(`${API}/agent/chat/${symbol}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: userMessage,
        history: chatHistory.slice(0, -1),
        provider,
      }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    const data = await res.json();
    // User switched stocks while waiting: this reply belongs to a cleared chat
    if (session !== chatSession) return;
    loading.textContent = data.reply;
    loading.classList.remove("loading");
    chatHistory.push({ role: "assistant", content: data.reply });
  } catch (err) {
    if (session !== chatSession) return;
    loading.textContent = `Error: ${err.message}`;
    loading.classList.remove("loading");
    chatHistory.pop();
  } finally {
    els.chatSendBtn.disabled = false;
    els.chatInput.disabled = false;
    els.chatInput.focus();
  }
}

async function loadAgentStatus() {
  try {
    const status = await fetchJson(`${API}/agent/status`);
    agentStatusCache = status;
    const saved = loadChatSettings();
    els.chatProvider.value =
      saved.provider || status.provider || "ollama";
    if (status.ollama_available && !saved.provider) {
      els.chatProvider.value = "ollama";
    }
    updateChatSettingsUI();
  } catch {
    els.agentStatus.textContent = "Agent status unavailable";
    updateChatSettingsUI();
  }
}

async function runAgentAnalysis() {
  if (!currentSymbol) return;
  await sendChatMessage("Recommend buy, sell, or hold — explain why with levels.");
}

els.chatForm.addEventListener("submit", (e) => {
  e.preventDefault();
  sendChatMessage(els.chatInput.value);
});

els.chatProvider.addEventListener("change", updateChatSettingsUI);

els.agentAnalyzeBtn.addEventListener("click", runAgentAnalysis);

async function loadWatchlist() {
  const data = await fetchJson(`${API}/watchlist${symbolsQuery("?")}`);
  watchlistSymbols = data.watchlist.map((i) => i.symbol);
  renderWatchlist(data.watchlist);
  updateWatchToggle();
  els.lastUpdated.textContent = `Updated ${new Date().toLocaleTimeString()}`;
  return data.watchlist;
}

async function loadStock(symbol) {
  const seq = ++stockRequestSeq;
  const data = await fetchJson(`${API}/stock/${symbol}`);
  // A later click already started another load; don't let this one overwrite it
  if (seq !== stockRequestSeq) return;
  renderStock(data);
  updateWatchToggle();
  document.querySelectorAll(".watch-item").forEach((el) => el.classList.remove("active"));
  const items = els.watchlist.querySelectorAll(".watch-item");
  items.forEach((btn) => {
    if (btn.querySelector(".sym")?.textContent === symbol) btn.classList.add("active");
  });
}

els.refreshBtn.addEventListener("click", async () => {
  els.refreshBtn.disabled = true;
  try {
    await Promise.all([loadWatchlist(), loadSectorNews(), loadOvernight()]);
    if (currentSymbol) await loadStock(currentSymbol);
  } finally {
    els.refreshBtn.disabled = false;
  }
});

els.resetLevelsBtn.addEventListener("click", () => {
  if (!currentLevels) return;
  applySuggestedToForm(currentLevels);
  els.planMessage.textContent = "Reset to suggested levels.";
});

// Long-only plan: returns an error message, or null when the plan is usable
function validatePlan(plan) {
  const blank = [
    ["buy price", els.inputBuy],
    ["take profit", els.inputTakeProfit],
    ["stop loss", els.inputStop],
    ["shares", els.inputShares],
  ].find(([, input]) => input.value.trim() === "");
  if (blank) return `${blank[0]} is empty.`;
  if (![plan.buy, plan.takeProfit, plan.stop].every((v) => Number.isFinite(v) && v > 0)) {
    return "prices must be positive numbers.";
  }
  if (!(Number.isFinite(plan.shares) && plan.shares > 0)) return "shares must be more than 0.";
  if (plan.stop >= plan.buy) return "stop loss must be below the buy price.";
  if (plan.takeProfit <= plan.buy) return "take profit must be above the buy price.";
  return null;
}

els.savePlanBtn.addEventListener("click", () => {
  if (!currentSymbol) return;
  const plan = {
    buy: Number(els.inputBuy.value),
    takeProfit: Number(els.inputTakeProfit.value),
    stop: Number(els.inputStop.value),
    shares: Number(els.inputShares.value),
    savedAt: new Date().toISOString(),
  };
  const problem = validatePlan(plan);
  if (problem) {
    els.planMessage.textContent = `Not saved: ${problem}`;
    return;
  }
  localStorage.setItem(planKey(currentSymbol), JSON.stringify(plan));
  els.planMessage.textContent = `Saved plan for ${currentSymbol} (${plan.shares} shares).`;
});

async function loadOvernight() {
  try {
    const data = await fetchJson(`${API}/overnight`);
    renderOvernight(data);
    return data;
  } catch (err) {
    if (els.overnightSummary) {
      els.overnightSummary.textContent = `Overnight panel unavailable: ${err.message}`;
    }
    if (els.overnightBias) {
      els.overnightBias.textContent = "N/A";
      els.overnightBias.className = "overnight-bias mixed";
    }
    return null;
  }
}

// ---- Editable watchlist (saved per browser) ----

const WATCHLIST_KEY = "trader-watchlist";
const MAX_WATCHLIST = 30;
// Symbols currently shown; null until the first load returns the server default
let watchlistSymbols = null;

function savedWatchlist() {
  try {
    const list = JSON.parse(localStorage.getItem(WATCHLIST_KEY) || "null");
    return Array.isArray(list) ? list : null;
  } catch {
    return null;
  }
}

function saveWatchlist(symbols) {
  try {
    localStorage.setItem(WATCHLIST_KEY, JSON.stringify(symbols));
  } catch {
    /* storage unavailable: list lasts for this page only */
  }
  watchlistSymbols = symbols;
}

// Query-string fragment for the saved list ("" = server default list)
function symbolsQuery(sep) {
  const list = savedWatchlist();
  return list && list.length ? `${sep}symbols=${encodeURIComponent(list.join(","))}` : "";
}

async function reloadWatchlistViews() {
  try {
    await Promise.all([loadWatchlist(), loadSectorNews().catch(() => {})]);
  } catch (err) {
    setSearchStatus(`Watchlist failed to load: ${err.message}`);
  }
}

async function addToWatchlist(symbol) {
  const list = [...(watchlistSymbols || [])];
  if (list.includes(symbol)) return;
  if (list.length >= MAX_WATCHLIST) {
    setSearchStatus(`Watchlist is full (${MAX_WATCHLIST} symbols). Remove one first.`);
    return;
  }
  saveWatchlist([...list, symbol]);
  await reloadWatchlistViews();
}

async function removeFromWatchlist(symbol) {
  saveWatchlist((watchlistSymbols || []).filter((s) => s !== symbol));
  await reloadWatchlistViews();
}

function updateWatchToggle() {
  if (!currentSymbol) return;
  const watched = (watchlistSymbols || []).includes(currentSymbol);
  els.watchToggleBtn.textContent = watched ? "★ In watchlist · remove" : "☆ Add to watchlist";
  els.watchToggleBtn.dataset.watched = watched ? "1" : "";
}

els.watchToggleBtn.addEventListener("click", () => {
  if (!currentSymbol) return;
  if (els.watchToggleBtn.dataset.watched) removeFromWatchlist(currentSymbol);
  else addToWatchlist(currentSymbol);
});

els.resetWatchlistBtn.addEventListener("click", async () => {
  try {
    localStorage.removeItem(WATCHLIST_KEY);
  } catch {
    /* ignore */
  }
  await reloadWatchlistViews();
});

// ---- Open any symbol (search, watchlist, or typed) ----

function setSearchStatus(text) {
  els.searchStatus.textContent = text || "";
}

async function openStock(symbol) {
  setSearchStatus(`Loading ${symbol}…`);
  // The toggle still refers to the previous stock until this one renders
  els.watchToggleBtn.disabled = true;
  try {
    await loadStock(symbol);
    setSearchStatus("");
  } catch (err) {
    setSearchStatus(`${symbol}: ${err.message}`);
  } finally {
    els.watchToggleBtn.disabled = false;
  }
}

// ---- Symbol search with suggestions ----

let searchTimer = null;
let searchSeq = 0;
let searchResults = [];
let activeResult = -1;

function hideResults() {
  els.symbolResults.classList.add("hidden");
  els.symbolResults.replaceChildren();
  searchResults = [];
  activeResult = -1;
}

function renderResults() {
  els.symbolResults.replaceChildren();
  if (!searchResults.length) {
    els.symbolResults.classList.add("hidden");
    return;
  }
  searchResults.forEach((r, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `symbol-result${i === activeResult ? " active" : ""}`;
    btn.setAttribute("role", "option");
    const sym = document.createElement("span");
    sym.className = "sym";
    sym.textContent = r.symbol;
    const name = document.createElement("span");
    name.className = "name";
    name.textContent = r.name;
    const meta = document.createElement("span");
    meta.className = "meta";
    meta.textContent = [r.type, r.exchange].filter(Boolean).join(" · ");
    btn.append(sym, name, meta);
    // mousedown fires before the input's blur hides the list
    btn.addEventListener("mousedown", (e) => {
      e.preventDefault();
      chooseSymbol(r.symbol);
    });
    els.symbolResults.appendChild(btn);
  });
  els.symbolResults.classList.remove("hidden");
}

async function runSearch(query) {
  const seq = ++searchSeq;
  try {
    const data = await fetchJson(`${API}/search?q=${encodeURIComponent(query)}`);
    if (seq !== searchSeq) return;
    searchResults = data.results || [];
    activeResult = searchResults.length ? 0 : -1;
    renderResults();
    setSearchStatus(searchResults.length ? "" : "No matches. Press Enter to try it as a ticker.");
  } catch (err) {
    if (seq !== searchSeq) return;
    hideResults();
    setSearchStatus(`Search unavailable: ${err.message}`);
  }
}

function chooseSymbol(symbol) {
  clearTimeout(searchTimer);
  searchSeq++;
  hideResults();
  els.symbolInput.value = "";
  els.symbolInput.blur();
  openStock(symbol.trim().toUpperCase());
}

els.symbolInput.addEventListener("input", () => {
  const q = els.symbolInput.value.trim();
  clearTimeout(searchTimer);
  if (!q) {
    searchSeq++;
    hideResults();
    setSearchStatus("");
    return;
  }
  searchTimer = setTimeout(() => runSearch(q), 250);
});

els.symbolInput.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    if (!searchResults.length) return;
    e.preventDefault();
    const step = e.key === "ArrowDown" ? 1 : -1;
    activeResult = (activeResult + step + searchResults.length) % searchResults.length;
    renderResults();
  } else if (e.key === "Escape") {
    hideResults();
  }
});

els.symbolInput.addEventListener("blur", () => setTimeout(hideResults, 100));

els.symbolSearch.addEventListener("submit", (e) => {
  e.preventDefault();
  const typed = els.symbolInput.value.trim();
  if (activeResult >= 0 && searchResults[activeResult]) {
    chooseSymbol(searchResults[activeResult].symbol);
  } else if (typed) {
    chooseSymbol(typed);
  }
});

// ---- Tabs ----

const views = { dashboard: "view-dashboard", signals: "view-signals" };

function showView(name) {
  if (!views[name]) name = "dashboard";
  for (const [key, id] of Object.entries(views)) {
    document.getElementById(id).classList.toggle("hidden", key !== name);
    const tab = document.getElementById(`tab-${key}`);
    tab.classList.toggle("active", key === name);
    tab.setAttribute("aria-selected", String(key === name));
  }
  if (name === "signals" && !signalsLoaded) loadSignals();
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    history.replaceState(null, "", tab.dataset.view === "dashboard" ? location.pathname : `#${tab.dataset.view}`);
    showView(tab.dataset.view);
  });
});

window.addEventListener("hashchange", () => showView(location.hash.slice(1)));

// ---- Early signals ----

const signalsEls = {
  legend: document.getElementById("signals-legend"),
  status: document.getElementById("signals-status"),
  list: document.getElementById("signals-list"),
  footer: document.getElementById("signals-footer"),
  refreshBtn: document.getElementById("signals-refresh-btn"),
  promptBtn: document.getElementById("signals-prompt-btn"),
};

const SIGNAL_HELP = {
  revisions: "Analysts raised earnings estimates in the last 30 days",
  insiders: "2+ insiders bought shares on the open market in the last 90 days",
  volume: "Volume 1.5× normal while price moved less than 5% in 10 days",
  catalyst: "Earnings report within the next 3 months",
  strength: "Beat its sector ETF by 3+ points over the last month (or rose while it fell)",
  value: "Revenue growth speeding up while forward P/E is below sector peers",
  accumulation: "Up-day volume at least 1.3× down-day volume over 50 days",
  upgrades: "2+ analyst upgrades or price-target raises in 30 days, more than cuts",
  beats: "Beat EPS estimates in each of the last 4 quarters",
  shorts: "Shares sold short fell 10%+ vs the prior month",
};

let signalsLoaded = false;
let signalsSeq = 0;

function renderSignals(data) {
  const labels = Object.fromEntries(data.signals.map((s) => [s.key, s.label]));
  signalsEls.legend.innerHTML = data.signals
    .map((s) => `<span class="signal-chip hit" title="${escapeHtml(SIGNAL_HELP[s.key] || "")}">${escapeHtml(s.label)}</span>`)
    .join("");

  if (!data.top.length) {
    signalsEls.list.innerHTML = `<p class="muted">No candidates passed the screen right now.</p>`;
  } else {
    signalsEls.list.innerHTML = data.top
      .map((r, i) => {
        const chips = data.signals
          .map(({ key }) => {
            const s = r.signals[key];
            return `<span class="signal-chip ${s.hit ? "hit" : ""}" title="${escapeHtml(s.detail)}">${escapeHtml(labels[key])}</span>`;
          })
          .join("");
        const details = data.signals
          .filter(({ key }) => key !== "catalyst" && r.signals[key].hit)
          .map(({ key }) => `<li><strong>${escapeHtml(labels[key])}:</strong> ${escapeHtml(r.signals[key].detail)}</li>`)
          .join("");
        return `
          <article class="signal-card">
            <div class="signal-card-head">
              <span class="signal-rank">#${i + 1}</span>
              <div class="signal-name">
                <button type="button" class="link-btn signal-ticker" data-symbol="${escapeHtml(r.symbol)}">${escapeHtml(r.symbol)}</button>
                <span class="muted">${escapeHtml(r.name)}${r.sector ? ` · ${escapeHtml(r.sector)}` : ""}</span>
              </div>
              <div class="signal-price">
                <span>${formatMoney(r.price)}</span>
                <span class="${pctClass(r.ret_1m)}">${formatPct(r.ret_1m == null ? null : r.ret_1m * 100)} 1M</span>
              </div>
              ${r.ladder ? `<span class="ladder-chip ${r.ladder.side}" title="Signal ladder rung for today's price">${escapeHtml(r.ladder.label)}</span>` : ""}
              <span class="signal-count" title="Signals lined up (the catalyst counts half when ranking)">${r.signal_count}/${data.signals.length}</span>
            </div>
            <div class="signal-chips">${chips}</div>
            <ul class="signal-details">${details}</ul>
            <p class="signal-line"><span class="signal-tag catalyst">Catalyst</span>${escapeHtml(r.catalyst || "None found in the next 3 months")}</p>
            <p class="signal-line"><span class="signal-tag risk">Biggest risk</span>${escapeHtml(r.risk)}</p>
          </article>`;
      })
      .join("");
    signalsEls.list.querySelectorAll(".signal-ticker").forEach((btn) => {
      btn.addEventListener("click", () => {
        history.replaceState(null, "", location.pathname);
        showView("dashboard");
        openStock(btn.dataset.symbol);
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
  }

  const when = new Date(data.generated_at).toLocaleString();
  const hot = data.excluded_hot.length ? ` Excluded (up >30% in a month): ${data.excluded_hot.join(", ")}.` : "";
  signalsEls.footer.textContent =
    `Scanned ${data.scanned} liquid US stocks, deep-checked the ${data.deep_checked} with the best volume / relative-strength setups. ` +
    `Updated ${when}; results are cached for an hour.${hot}`;
}

async function loadSignals(refresh = false) {
  const seq = ++signalsSeq;
  signalsLoaded = true;
  signalsEls.refreshBtn.disabled = true;
  signalsEls.status.textContent = "Scanning ~200 stocks for early signals… this can take up to a minute.";
  if (refresh) signalsEls.list.innerHTML = "";
  try {
    const data = await fetchJson(`${API}/early-signals${refresh ? "?refresh=true" : ""}`);
    if (seq !== signalsSeq) return;
    signalsEls.status.textContent = "";
    renderSignals(data);
  } catch (err) {
    if (seq !== signalsSeq) return;
    signalsLoaded = false;
    signalsEls.status.textContent = `Scan failed: ${err.message}`;
  } finally {
    if (seq === signalsSeq) signalsEls.refreshBtn.disabled = false;
  }
}

function researchPrompt() {
  const today = new Date().toISOString().slice(0, 10);
  return `Today is ${today}. You are an equity research analyst. Search the web and find 10 stocks
showing early signs of a potential move up, BEFORE it is widely known.

Look for:
1. Upward earnings estimate revisions in the last 30 days
2. Insider buying (multiple insiders, open-market purchases)
3. Unusual volume with price still flat or consolidating
4. A specific catalyst in the next 1–3 months (with date) — earnings, FDA decision, product launch, index addition
5. Relative strength vs. its sector

For each stock: ticker, which signals it shows, the catalyst + date,
and the biggest risk. Rank them by how many signals line up.
Exclude stocks already up more than 30% in the last month.
Only use sources from the last 30 days and cite them.`;
}

signalsEls.refreshBtn.addEventListener("click", () => loadSignals(true));
signalsEls.promptBtn.addEventListener("click", async () => {
  const original = "Copy AI research prompt";
  try {
    await navigator.clipboard.writeText(researchPrompt());
    signalsEls.promptBtn.textContent = "Copied ✓";
  } catch {
    window.prompt("Copy this prompt:", researchPrompt());
  }
  setTimeout(() => (signalsEls.promptBtn.textContent = original), 2000);
});

async function init() {
  showView(location.hash.slice(1));
  try {
    const [items] = await Promise.all([
      loadWatchlist(),
      loadSectorNews(),
      loadAgentStatus(),
    ]);
    // Overnight is heavier / more fragile — don't block the whole dashboard
    loadOvernight().catch(() => {});
    const first = items.find((i) => !i.error);
    if (first) await openStock(first.symbol);
  } catch (err) {
    els.watchlist.innerHTML = `<p class="muted">Failed to load: ${escapeHtml(err.message)}</p>`;
  }
}

init();
