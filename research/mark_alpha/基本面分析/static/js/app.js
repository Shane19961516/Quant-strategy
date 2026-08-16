(() => {
  const $ = (id) => document.getElementById(id);
  const codeInput = $("codeInput");
  const rangeSelect = $("rangeSelect");
  const analyzeBtn = $("analyzeBtn");
  const exportMdBtn = $("exportMdBtn");
  const exportPdfBtn = $("exportPdfBtn");
  const statusEl = $("status");
  const quoteBar = $("quoteBar");
  const metaBox = $("metaBox");
  const ratingBox = $("ratingBox");
  const reportView = $("reportView");
  const reportStamp = $("reportStamp");
  const chartEl = $("chart");

  let chart;
  let candleSeries;
  let volumeSeries;
  let lastPayload = null;

  function setStatus(text, kind) {
    statusEl.textContent = text;
    statusEl.className = "status" + (kind ? ` ${kind}` : "");
  }

  function fmt(n, d = 2) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
    const v = Number(n);
    if (Math.abs(v) >= 1e12) return (v / 1e12).toFixed(d) + "T";
    if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(d) + "亿";
    if (Math.abs(v) >= 1e9) return (v / 1e9).toFixed(d) + "B";
    if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(d) + "M";
    return v.toLocaleString(undefined, { maximumFractionDigits: d });
  }

  function pct(n, ratio = true) {
    if (n === null || n === undefined || Number.isNaN(Number(n))) return "—";
    let v = Number(n);
    if (ratio && Math.abs(v) <= 5) v *= 100;
    return `${v.toFixed(1)}%`;
  }

  function ensureChart() {
    if (chart) return;
    chart = LightweightCharts.createChart(chartEl, {
      autoSize: true,
      layout: {
        background: { color: "#fffaf0" },
        textColor: "#5d5a52",
        fontFamily: "Georgia, Songti SC, serif",
      },
      grid: {
        vertLines: { color: "#efe7d6" },
        horzLines: { color: "#efe7d6" },
      },
      rightPriceScale: { borderColor: "#c9c1b0" },
      timeScale: { borderColor: "#c9c1b0", timeVisible: false },
      crosshair: { mode: 0 },
    });
    candleSeries = chart.addCandlestickSeries({
      upColor: "#d9362b",
      downColor: "#1b1b18",
      borderUpColor: "#d9362b",
      borderDownColor: "#1b1b18",
      wickUpColor: "#d9362b",
      wickDownColor: "#1b1b18",
    });
    volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });
  }

  function renderChart(candles) {
    ensureChart();
    const rows = candles.map((c) => ({
      time: c.time,
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
    }));
    const vols = candles.map((c) => ({
      time: c.time,
      value: c.volume || 0,
      color: c.close >= c.open ? "rgba(217,54,43,0.35)" : "rgba(27,27,24,0.28)",
    }));
    candleSeries.setData(rows);
    volumeSeries.setData(vols);
    chart.timeScale().fitContent();
  }

  function renderMeta(data) {
    const r = data.resolved;
    const s = data.snapshot;
    quoteBar.textContent = `${s.name || r.display}  ${fmt(s.price)} ${s.currency || ""}  (${pct(s.changePct, false)})`;
    metaBox.classList.remove("muted");
    metaBox.textContent = [
      `输入: ${r.input}`,
      `识别: ${r.display} → Yahoo ${r.yahoo}`,
      `市场: ${r.name_hint} / ${r.market}`,
      `交易所: ${s.exchange || "—"}`,
      `市值: ${fmt(s.marketCap)}`,
      `TTM PE / 前瞻 PE: ${fmt(s.trailingPE, 1)}x / ${fmt(s.forwardPE, 1)}x`,
      `PB: ${fmt(s.priceToBook, 1)}x`,
      `ROE: ${pct(s.returnOnEquity)}`,
      `毛利率 / 净利率: ${pct(s.grossMargins)} / ${pct(s.profitMargins)}`,
      `收入增长 / 盈利增长: ${pct(s.revenueGrowth)} / ${pct(s.earningsGrowth)}`,
      `目标价: ${fmt(s.targetMeanPrice)}`,
      `数据时点: ${s.asOf}`,
    ].join("\n");

    ratingBox.innerHTML = `<strong>${data.report.rating}</strong><br/>仓位建议：${data.report.position}<br/>${data.report.oneLiner}`;
    reportView.textContent = data.report.markdown;
    reportStamp.textContent = data.report.generatedAt || "";
  }

  async function analyze() {
    const code = codeInput.value.trim();
    if (!code) {
      setStatus("请输入股票代码。", "error");
      return;
    }
    analyzeBtn.disabled = true;
    exportMdBtn.disabled = true;
    exportPdfBtn.disabled = true;
    setStatus(`正在拉取 ${code} 的行情与基本面…`);
    try {
      const resp = await fetch("/api/analyze", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, range: rangeSelect.value }),
      });
      const data = await resp.json();
      if (!data.ok) throw new Error(data.error || "分析失败");
      lastPayload = data;
      renderChart(data.candles || []);
      renderMeta(data);
      exportMdBtn.disabled = false;
      exportPdfBtn.disabled = false;
      setStatus(`完成：${data.resolved.display}（${data.resolved.yahoo}），K线 ${data.candles.length} 根。`, "ok");
    } catch (err) {
      setStatus(err.message || String(err), "error");
    } finally {
      analyzeBtn.disabled = false;
    }
  }

  async function exportFile(kind) {
    if (!lastPayload) return;
    const endpoint = kind === "pdf" ? "/api/export/pdf" : "/api/export/markdown";
    setStatus(`正在导出 ${kind.toUpperCase()}…`);
    try {
      const resp = await fetch(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          code: lastPayload.resolved.display,
          title: `${lastPayload.resolved.display} / ${lastPayload.snapshot.name} / INVESTMENT RESEARCH`,
          markdown: lastPayload.report.markdown,
          date: lastPayload.report.generatedAt,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.error || `导出失败 HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      const cd = resp.headers.get("Content-Disposition") || "";
      const match = /filename=([^;]+)/i.exec(cd);
      const filename = match ? match[1].replace(/"/g, "") : `${lastPayload.resolved.display}_report.${kind === "pdf" ? "pdf" : "md"}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setStatus(`已导出 ${filename}`, "ok");
    } catch (err) {
      setStatus(err.message || String(err), "error");
    }
  }

  analyzeBtn.addEventListener("click", analyze);
  exportMdBtn.addEventListener("click", () => exportFile("md"));
  exportPdfBtn.addEventListener("click", () => exportFile("pdf"));
  codeInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") analyze();
  });
  document.querySelectorAll("#chips button").forEach((btn) => {
    btn.addEventListener("click", () => {
      codeInput.value = btn.getAttribute("data-code");
      analyze();
    });
  });

  // Deep link ?code=688008
  const params = new URLSearchParams(location.search);
  if (params.get("code")) {
    codeInput.value = params.get("code");
    analyze();
  }
})();
