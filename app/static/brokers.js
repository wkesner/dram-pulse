/* Broker market performance charts — baseline vs scored twins. */
(() => {
  const SERIES_COLORS = {
    apex: "#0f5f63",
    apex_scored: "#0f5f63",
    harbor: "#2a5fa8",
    harbor_scored: "#2a5fa8",
    summit: "#b4362c",
    summit_scored: "#b4362c",
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
    cohort: "all",
  };

  function $(id) {
    return document.getElementById(id);
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

  function apiHeaders() {
    const h = {};
    if (window.msClerk?.authHeaders) {
      Object.assign(h, window.msClerk.authHeaders());
    }
    // Optional single-mode shared secret (set via localStorage for curl-less demos).
    try {
      const tok = localStorage.getItem("ms-broker-sim-token");
      if (tok) h["X-Broker-Sim-Token"] = tok;
    } catch (_) {
      /* ignore */
    }
    return h;
  }

  async function runSim(evt) {
    if (evt) evt.preventDefault();
    const days = Math.min(90, Math.max(1, Number($("f-days").value) || 30));
    const seed = Number($("f-seed").value) || 42;
    const live = $("f-live").checked;
    const includeScored = $("f-scored").checked;
    const scoreLag = Number($("f-lag").value);
    const status = $("status");
    status.hidden = false;
    status.dataset.tone = "";
    status.textContent = "Running simulation…";
    const url = new URL("/api/broker-sim", window.location.origin);
    url.searchParams.set("days", String(days));
    url.searchParams.set("seed", String(seed));
    url.searchParams.set("live", live ? "true" : "false");
    url.searchParams.set("starting_cash", "250000");
    url.searchParams.set("include_scored", includeScored ? "true" : "false");
    url.searchParams.set(
      "score_lag",
      String(Number.isFinite(scoreLag) ? scoreLag : 1)
    );
    try {
      const resp = await fetch(url, { headers: apiHeaders() });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try {
          const body = await resp.json();
          if (body?.detail) detail = body.detail;
        } catch (_) {
          /* ignore */
        }
        throw new Error(detail);
      }
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
    renderPriceMeta(data);
    renderPairs(data.pair_comparisons || []);
    renderLeaderboard(data.brokers || []);
    renderQuotes(data.prices);
    drawChart();
  }

  function renderPriceMeta(data) {
    const el = $("price-meta");
    const prices = data.prices;
    if (!prices) {
      el.hidden = true;
      return;
    }
    const bits = [
      `prices=${prices.source}`,
      prices.session_utc ? `session=${prices.session_utc}` : null,
      `matched=${prices.matched_skus}`,
      data.include_scored
        ? `scored twins lag=${data.score_lag_days}d`
        : "baseline only",
      prices.attribution || null,
    ].filter(Boolean);
    el.textContent = bits.join(" · ");
    el.hidden = false;
    el.title = [prices.detail, prices.license_note, data.score_model && data.score_model.description]
      .filter(Boolean)
      .join("\n");
  }

  function renderPairs(pairs) {
    const root = $("pair-compare");
    root.innerHTML = "";
    if (!pairs.length) {
      root.hidden = true;
      return;
    }
    root.hidden = false;
    const title = document.createElement("h2");
    title.textContent = "Baseline vs scored twin";
    root.appendChild(title);
    const grid = document.createElement("div");
    grid.className = "pair-grid";
    pairs.forEach((p) => {
      const card = document.createElement("article");
      card.className = "pair-card";
      const color = SERIES_COLORS[p.baseline_id] || "var(--accent)";
      card.style.setProperty("--series", color);
      const verb = p.scored_wins ? "beats" : "trails";
      card.innerHTML = `
        <h3>${escapeHtml(p.baseline_id)} → scored</h3>
        <p class="meta">Scored twin ${verb} baseline</p>
        <div class="stats">
          <div><span>Δ Equity</span><br />${moneyExact(p.equity_delta)}</div>
          <div><span>Δ P&amp;L</span><br />${moneyExact(p.pnl_delta)}</div>
        </div>
      `;
      grid.appendChild(card);
    });
    root.appendChild(grid);
  }

  function renderLeaderboard(brokers) {
    const root = $("leaderboard");
    root.innerHTML = "";
    brokers.forEach((b, i) => {
      const card = document.createElement("article");
      card.className = "broker-card";
      if (b.cohort === "scored") card.classList.add("scored");
      card.style.setProperty("--series", SERIES_COLORS[b.broker_id] || "#0f5f63");
      const badge = b.cohort === "scored" ? "scored" : "baseline";
      card.innerHTML = `
        <h3>${escapeHtml(b.name)}${i === 0 ? " · lead" : ""}</h3>
        <p class="meta"><span class="badge">${badge}</span> ${escapeHtml(b.personality || "")}</p>
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

  function visibleIds(brokers) {
    const ids = brokers.map((b) => b.broker_id);
    if (state.cohort === "baseline") {
      return ids.filter((id) => !id.endsWith("_scored"));
    }
    if (state.cohort === "scored") {
      return ids.filter((id) => id.endsWith("_scored"));
    }
    return ids;
  }

  function seriesFromHistory(history, metric, ids) {
    const byId = {};
    ids.forEach((id) => {
      byId[id] = [];
    });
    (history || []).forEach((row) => {
      const day = row.day;
      const brokers = row.brokers || {};
      ids.forEach((id) => {
        if (!brokers[id]) return;
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

    const ids = visibleIds(data.brokers || []);
    const seriesMap = seriesFromHistory(data.history || [], state.metric, ids);
    if (!ids.length) return;

    const wrap = document.querySelector(".chart-wrap");
    const width = Math.max(640, wrap.clientWidth - 16);
    const height = 360;
    const margin = { top: 28, right: 24, bottom: 40, left: 72 };
    svg.attr("viewBox", `0 0 ${width} ${height}`);

    const allPoints = ids.flatMap((id) => seriesMap[id] || []);
    if (!allPoints.length) return;

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
      const scored = id.endsWith("_scored");
      g.append("path")
        .datum(seriesMap[id])
        .attr("fill", "none")
        .attr("stroke", SERIES_COLORS[id] || "#0f5f63")
        .attr("stroke-width", scored ? 2.5 : 2.0)
        .attr("stroke-dasharray", scored ? "6 4" : null)
        .attr("d", line);
    });

    // Compact legend in two rows if needed
    const legend = g.append("g").attr("transform", `translate(${margin.left + 4},${margin.top - 14})`);
    ids.forEach((id, i) => {
      const col = i % 3;
      const row = Math.floor(i / 3);
      const item = legend.append("g").attr("transform", `translate(${col * 200},${row * 14})`);
      item
        .append("line")
        .attr("x1", 0)
        .attr("x2", 16)
        .attr("y1", -3)
        .attr("y2", -3)
        .attr("stroke", SERIES_COLORS[id] || "#0f5f63")
        .attr("stroke-width", 2)
        .attr("stroke-dasharray", id.endsWith("_scored") ? "6 4" : null);
      item
        .append("text")
        .attr("class", "chart-legend")
        .attr("x", 20)
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

  function initTabs() {
    document.querySelectorAll(".metric-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.metric = btn.dataset.metric;
        document.querySelectorAll(".metric-tab").forEach((b) => {
          b.setAttribute("aria-selected", b === btn ? "true" : "false");
        });
        if (state.data) drawChart();
      });
    });
    document.querySelectorAll(".cohort-tab").forEach((btn) => {
      btn.addEventListener("click", () => {
        state.cohort = btn.dataset.cohort;
        document.querySelectorAll(".cohort-tab").forEach((b) => {
          b.setAttribute("aria-selected", b === btn ? "true" : "false");
        });
        if (state.data) drawChart();
      });
    });
  }

  function authReady() {
    if (window.msClerk?.isAuthenticated) {
      return window.msClerk.isAuthenticated();
    }
    return !window.__msAuth?.requiresAuth;
  }

  async function boot() {
    initTheme();
    initTabs();
    $("sim-form").addEventListener("submit", runSim);
    window.addEventListener("resize", () => {
      if (state.data) drawChart();
    });
    if (window.msClerk?.init) {
      try {
        await window.msClerk.init();
      } catch (err) {
        console.warn("clerk init", err);
      }
    }
    window.__msOnAuthChange = () => {
      if (!authReady()) return;
      runSim();
    };
    if (authReady()) runSim();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      boot();
    });
  } else {
    boot();
  }
})();
