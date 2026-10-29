/* Broker market performance charts (D3, same stack as Signal map). */
(() => {
  const SERIES_COLORS = {
    apex: "#0f5f63",
    harbor: "#2a5fa8",
    summit: "#b4362c",
  };

  const METRIC_LABELS = {
    equity: "Mark-to-market equity by day",
    pnl_vs_start: "P&L vs starting cash by day",
    cash: "Cash balance by day",
    inventory_units: "Inventory units on hand by day",
    realized_pnl: "Cumulative realized P&L by day",
  };

  const state = {
    data: null,
    metric: "equity",
  };

  function $(id) {
    return document.getElementById(id);
  }

  function money(n) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 0,
    }).format(n);
  }

  function moneyExact(n) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(n);
  }

  function initTheme() {
    const btn = $("theme-toggle");
    if (!btn) return;
    const saved = localStorage.getItem("ms-theme");
    if (saved) document.documentElement.setAttribute("data-theme", saved);
    btn.addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme");
      const next =
        cur === "dark" ? "light" : cur === "light" ? "dark" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      localStorage.setItem("ms-theme", next);
      if (state.data) drawChart();
    });
  }

  async function runSim(evt) {
    if (evt) evt.preventDefault();
    const days = Number($("f-days").value) || 30;
    const seed = Number($("f-seed").value) || 42;
    const live = $("f-live").checked;
    const status = $("status");
    status.hidden = false;
    status.dataset.tone = "";
    status.textContent = "Running simulation…";
    const url = new URL("/api/broker-sim", window.location.origin);
    url.searchParams.set("days", String(days));
    url.searchParams.set("seed", String(seed));
    url.searchParams.set("live", live ? "true" : "false");
    url.searchParams.set("starting_cash", "250000");
    try {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      state.data = await resp.json();
      status.hidden = true;
      renderAll();
    } catch (err) {
      status.dataset.tone = "error";
      status.textContent = `Failed to run simulation: ${err.message || err}`;
    }
  }

  function renderAll() {
    const data = state.data;
    if (!data) return;
    renderPriceMeta(data.prices);
    renderLeaderboard(data.brokers || []);
    renderQuotes(data.prices);
    drawChart();
  }

  function renderPriceMeta(prices) {
    const el = $("price-meta");
    if (!prices) {
      el.hidden = true;
      return;
    }
    const bits = [
      `prices=${prices.source}`,
      prices.session_utc ? `session=${prices.session_utc}` : null,
      `matched=${prices.matched_skus}`,
      prices.attribution || null,
    ].filter(Boolean);
    el.textContent = bits.join(" · ");
    el.hidden = false;
    el.title = [prices.detail, prices.license_note].filter(Boolean).join("\n");
  }

  function renderLeaderboard(brokers) {
    const root = $("leaderboard");
    root.innerHTML = "";
    brokers.forEach((b, i) => {
      const card = document.createElement("article");
      card.className = "broker-card";
      card.style.setProperty("--series", SERIES_COLORS[b.broker_id] || "#0f5f63");
      card.innerHTML = `
        <h3>${escapeHtml(b.name)}${i === 0 ? " · lead" : ""}</h3>
        <p class="meta">${escapeHtml(b.personality || "")}</p>
        <div class="stats">
          <div><span>Equity</span><br />${moneyExact(b.equity)}</div>
          <div><span>P&amp;L</span><br />${moneyExact(b.pnl_vs_start)}</div>
          <div><span>Cash</span><br />${moneyExact(b.cash)}</div>
          <div><span>Inventory</span><br />${Number(b.inventory_units).toLocaleString()} u</div>
        </div>
      `;
      root.appendChild(card);
    });
  }

  function renderQuotes(prices) {
    const section = $("quotes");
    const tbody = $("quote-table").querySelector("tbody");
    tbody.innerHTML = "";
    const quotes = (prices && prices.quotes) || [];
    if (!quotes.length) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    quotes.forEach((q) => {
      const tr = document.createElement("tr");
      const src = q.source_url
        ? `<a href="${escapeAttr(q.source_url)}" target="_blank" rel="noopener">${escapeHtml(q.source || q.source_url)}</a>`
        : escapeHtml(q.source || "—");
      tr.innerHTML = `
        <td>${escapeHtml(q.ticker || q.contract_id)}<br /><span style="color:var(--muted)">${escapeHtml(q.name || "")}</span></td>
        <td>${moneyExact(q.spot_usd)} <span style="color:var(--muted)">${escapeHtml(q.unit || "")}</span></td>
        <td>${escapeHtml(q.as_of || "—")}</td>
        <td>${src}</td>
      `;
      tbody.appendChild(tr);
    });
  }

  function seriesFromHistory(history, metric) {
    const byId = {};
    (history || []).forEach((row) => {
      const day = row.day;
      const brokers = row.brokers || {};
      Object.keys(brokers).forEach((id) => {
        if (!byId[id]) byId[id] = [];
        byId[id].push({ day, value: Number(brokers[id][metric]) });
      });
    });
    return byId;
  }

  function drawChart() {
    const data = state.data;
    const svg = d3.select("#broker-chart");
    svg.selectAll("*").remove();
    $("chart-caption").textContent = METRIC_LABELS[state.metric] || state.metric;

    const seriesMap = seriesFromHistory(data.history || [], state.metric);
    const ids = Object.keys(seriesMap);
    if (!ids.length) return;

    const wrap = document.querySelector(".chart-wrap");
    const width = Math.max(640, wrap.clientWidth - 16);
    const height = 360;
    const margin = { top: 24, right: 24, bottom: 40, left: 72 };
    svg.attr("viewBox", `0 0 ${width} ${height}`);

    const allPoints = ids.flatMap((id) => seriesMap[id]);
    const x = d3
      .scaleLinear()
      .domain(d3.extent(allPoints, (d) => d.day))
      .range([margin.left, width - margin.right]);
    const y0 = d3.min(allPoints, (d) => d.value);
    const y1 = d3.max(allPoints, (d) => d.value);
    const pad = (y1 - y0) * 0.08 || 1;
    const y = d3
      .scaleLinear()
      .domain([y0 - pad, y1 + pad])
      .nice()
      .range([height - margin.bottom, margin.top]);

    const g = svg.append("g");
    const grid = g.append("g").attr("class", "chart-grid");
    y.ticks(6).forEach((tick) => {
      grid
        .append("line")
        .attr("x1", margin.left)
        .attr("x2", width - margin.right)
        .attr("y1", y(tick))
        .attr("y2", y(tick));
    });

    if (y0 < 0 && y1 > 0) {
      g.append("line")
        .attr("class", "chart-zero")
        .attr("x1", margin.left)
        .attr("x2", width - margin.right)
        .attr("y1", y(0))
        .attr("y2", y(0));
    }

    const xAxis = d3.axisBottom(x).ticks(Math.min(10, data.days || 10)).tickFormat(d3.format("d"));
    const yAxis = d3
      .axisLeft(y)
      .ticks(6)
      .tickFormat((d) =>
        state.metric === "inventory_units" ? d3.format("~s")(d) : d3.format("$,~s")(d)
      );

    g.append("g")
      .attr("class", "chart-axis")
      .attr("transform", `translate(0,${height - margin.bottom})`)
      .call(xAxis);
    g.append("g")
      .attr("class", "chart-axis")
      .attr("transform", `translate(${margin.left},0)`)
      .call(yAxis);

    const line = d3
      .line()
      .x((d) => x(d.day))
      .y((d) => y(d.value));

    const nameById = {};
    (data.brokers || []).forEach((b) => {
      nameById[b.broker_id] = b.name;
    });

    ids.forEach((id) => {
      g.append("path")
        .datum(seriesMap[id])
        .attr("fill", "none")
        .attr("stroke", SERIES_COLORS[id] || "#0f5f63")
        .attr("stroke-width", 2.25)
        .attr("d", line);
    });

    const legend = g.append("g").attr("transform", `translate(${margin.left + 8},${margin.top - 8})`);
    ids.forEach((id, i) => {
      const row = legend.append("g").attr("transform", `translate(${i * 170},0)`);
      row
        .append("rect")
        .attr("width", 12)
        .attr("height", 3)
        .attr("y", -6)
        .attr("fill", SERIES_COLORS[id] || "#0f5f63");
      row
        .append("text")
        .attr("class", "chart-legend")
        .attr("x", 18)
        .attr("y", 0)
        .text(nameById[id] || id);
    });
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(s) {
    return escapeHtml(s).replace(/'/g, "&#39;");
  }

  function initMetricTabs() {
    document.querySelectorAll(".metric-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.metric = btn.dataset.metric;
        document.querySelectorAll(".metric-tab").forEach((b) => {
          b.setAttribute("aria-selected", b === btn ? "true" : "false");
        });
        if (state.data) drawChart();
      });
    });
  }

  function boot() {
    initTheme();
    initMetricTabs();
    $("sim-form").addEventListener("submit", runSim);
    window.addEventListener("resize", () => {
      if (state.data) drawChart();
    });
    runSim();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
