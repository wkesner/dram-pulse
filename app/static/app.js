(() => {
  const $ = (s) => document.querySelector(s);
  const USER_KEY = "ms-user";
  const THEME_KEY = "ms-theme";

  const store = {
    get(k, d) {
      try {
        const v = localStorage.getItem(k);
        return v == null ? d : JSON.parse(v);
      } catch {
        return d;
      }
    },
    set(k, v) {
      try {
        localStorage.setItem(k, JSON.stringify(v));
      } catch {
        /* ignore */
      }
    },
  };

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const label = (f) => String(f || "").replace(/_/g, " ");

  // Text-fragment link: first five words, last five words (Chrome #:~:text=).
  const fragment = (text) => {
    const enc = (t) => encodeURIComponent(t).replace(/-/g, "%2D");
    const w = String(text || "")
      .replace(/\s+/g, " ")
      .trim()
      .split(" ")
      .filter(Boolean);
    if (!w.length) return "";
    return (
      "#:~:text=" +
      (w.length <= 10
        ? enc(w.join(" "))
        : enc(w.slice(0, 5).join(" ")) + "," + enc(w.slice(-5).join(" "))
      )
    );
  };

  const latest = (s) => {
    if (s.last_seen) return s.last_seen;
    const dates = (s.sightings || []).map((x) => x.filing_date || "");
    return dates.reduce((m, d) => (d > m ? d : m), s.filing_date || "");
  };

  const primarySighting = (s) => {
    const list = s.sightings || [];
    if (!list.length) {
      return {
        filing_date: s.filing_date || "",
        form: s.form || "",
        section: s.section || "",
        match: "first",
        source_url: s.source_url || "",
        excerpt: s.excerpt || "",
      };
    }
    return [...list].sort((a, b) =>
      (b.filing_date || "").localeCompare(a.filing_date || "")
    )[0];
  };

  // --- Theme ---
  const root = document.documentElement;
  const themeBtn = $("#theme-toggle");
  function applyTheme(theme) {
    if (theme === "light" || theme === "dark") {
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
    if (themeBtn) {
      const effective =
        theme ||
        (window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light");
      themeBtn.textContent = effective === "dark" ? "Light" : "Dark";
      themeBtn.setAttribute(
        "aria-label",
        `Switch to ${themeBtn.textContent} theme`
      );
    }
  }
  applyTheme(store.get(THEME_KEY, null));
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const current = root.getAttribute("data-theme");
      const next =
        current === "dark"
          ? "light"
          : current === "light"
            ? "dark"
            : window.matchMedia("(prefers-color-scheme: dark)").matches
              ? "light"
              : "dark";
      store.set(THEME_KEY, next);
      applyTheme(next);
    });
  }

  // --- Tabs ---
  const tabDefs = [
    { tab: $("#tab-feed"), panel: $("#view-feed") },
    { tab: $("#tab-map"), panel: $("#view-map") },
    { tab: $("#tab-families"), panel: $("#view-families") },
  ];
  function selectTab(selected) {
    for (const { tab, panel } of tabDefs) {
      if (!tab || !panel) continue;
      const on = tab === selected;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      panel.hidden = !on;
    }
    if (selected === $("#tab-feed")) renderFeed();
    if (selected === $("#tab-families")) renderFamilies();
    if (selected === $("#tab-map")) {
      // Charts need a visible width; draw after layout.
      requestAnimationFrame(() => {
        renderMap();
        renderDataNotes();
      });
    }
  }
  function showTab(name) {
    const map = {
      feed: $("#tab-feed"),
      map: $("#tab-map"),
      families: $("#tab-families"),
    };
    if (map[name]) selectTab(map[name]);
  }
  for (const { tab } of tabDefs) {
    if (tab) tab.addEventListener("click", () => selectTab(tab));
  }

  // --- Feed state ---
  let SIGNALS = [];
  /** @type {Record<string, number>} signal_id → 1|-1 for current user */
  let userVotes = {};
  let skipped = new Set();
  const FEED_MODE_KEY = "ms-feed-mode";
  /** @type {"stack"|"one"} */
  let feedMode = store.get(FEED_MODE_KEY, "stack") === "one" ? "one" : "stack";

  const FILTERS = [
    ["category", "product_category", "All categories"],
    ["family", "product_family", "All families"],
    ["direction", "supply_direction", "Any direction"],
    ["company", "company", "All companies"],
    ["role", "role", "Suppliers and buyers"],
    ["section", "section", "All sections"],
  ];

  const userIn = $("#user-name");
  if (userIn) {
    const saved = store.get(USER_KEY, "");
    if (typeof saved === "string") userIn.value = saved;
    userIn.addEventListener("input", async () => {
      store.set(USER_KEY, userIn.value.trim());
      skipped = new Set();
      await loadUserVotes();
      renderFeed();
    });
  }

  const feedModeSel = $("#feed-mode");
  if (feedModeSel) {
    feedModeSel.value = feedMode;
    feedModeSel.addEventListener("change", () => {
      feedMode = feedModeSel.value === "one" ? "one" : "stack";
      store.set(FEED_MODE_KEY, feedMode);
      renderFeed();
    });
  }

  function fillFilters() {
    for (const [id, field, all] of FILTERS) {
      const sel = $("#f-" + id);
      if (!sel) continue;
      const prev = sel.value;
      sel.replaceChildren();
      sel.append(new Option(all, ""));
      const values = [
        ...new Set(SIGNALS.map((s) => s[field]).filter(Boolean)),
      ].sort((a, b) => String(a).localeCompare(String(b)));
      for (const v of values) {
        sel.append(
          new Option(field === "product_family" ? label(v) : v, v)
        );
      }
      if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
      sel.onchange = () => {
        skipped = new Set();
        renderFeed();
      };
    }
  }

  const clearBtn = $("#clear-filters");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      for (const [id] of FILTERS) {
        const sel = $("#f-" + id);
        if (sel) sel.value = "";
      }
      skipped = new Set();
      renderFeed();
    });
  }

  function highlight(text, terms) {
    const frag = document.createDocumentFragment();
    const ts = terms
      .filter((t) => t.length > 2)
      .sort((a, b) => b.length - a.length);
    if (!ts.length) {
      frag.append(text);
      return frag;
    }
    const esc = ts.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    const re = new RegExp("(" + esc.join("|") + ")", "gi");
    let last = 0;
    for (const m of text.matchAll(re)) {
      frag.append(text.slice(last, m.index));
      frag.append(el("mark", null, m[0]));
      last = m.index + m[0].length;
    }
    frag.append(text.slice(last));
    return frag;
  }

  function queue() {
    const user = (userIn?.value || "").trim();
    const pass = (s) =>
      FILTERS.every(
        ([id, field]) => !$("#f-" + id)?.value || s[field] === $("#f-" + id).value
      );
    return SIGNALS.filter(pass)
      .filter((s) => !skipped.has(s.signal_id))
      .sort(
        (a, b) =>
          (userVotes[a.signal_id] != null) - (userVotes[b.signal_id] != null) ||
          latest(b).localeCompare(latest(a)) ||
          a.signal_id.localeCompare(b.signal_id)
      );
  }

  function isClerkMode() {
    return window.__msAuth?.mode === "clerk";
  }

  function currentUserLabel() {
    if (isClerkMode()) {
      return window.__msAuth?.displayName || window.__msAuth?.userId || "";
    }
    return (userIn?.value || "").trim();
  }

  function apiHeaders(extra) {
    const h = { ...(extra || {}) };
    if (window.msClerk?.authHeaders) {
      Object.assign(h, window.msClerk.authHeaders());
    }
    return h;
  }

  async function castVote(s, v) {
    const user = currentUserLabel();
    if (!isClerkMode() && !user) {
      userIn?.focus();
      return;
    }
    if (isClerkMode() && !window.__msAuth?.orgId) {
      console.warn("select an organization before voting");
      return;
    }
    const resp = await fetch("/api/votes", {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ signal_id: s.signal_id, user, vote: v }),
    });
    if (!resp.ok) {
      const detail = await resp.text();
      console.error("vote failed", resp.status, detail);
      return;
    }
    userVotes[s.signal_id] = v;
    // Refresh tallies from server
    await loadSignals({ keepFilters: true });
    renderFeed();
  }

  function buildCard(s) {
    const user = currentUserLabel();
    const canVote = isClerkMode()
      ? Boolean(window.__msAuth?.orgId && window.__msAuth?.sessionToken)
      : Boolean(user);
    const sightings = s.sightings || [];
    const firstLink = primarySighting(s);
    const mine = userVotes[s.signal_id];

    const card = el("article", mine != null ? "card has-vote" : "card");
    card.dataset.signalId = s.signal_id;

    const meta = el("div", "meta");
    meta.append(
      el("span", "co", s.company),
      el("span", "role", s.role),
      el(
        "span",
        "filing",
        `${s.form} · ${latest(s)} · ${label(s.section)}`
      )
    );
    if (s.source && s.source !== "EDGAR") {
      meta.append(el("span", "filing", s.source));
    }

    const chips = el("div", "chips");
    chips.append(
      el("span", `chip ${s.supply_direction}`, s.supply_direction),
      el("span", "chip", label(s.product_family)),
      el("span", "chip", `tone: ${s.sentiment}`)
    );

    const terms = String(s.matched_terms || "")
      .split(";")
      .map((t) => t.split(":").slice(1).join(":").trim())
      .filter(Boolean);
    const ex = el("p", "excerpt");
    ex.append(highlight(s.excerpt || "", terms));

    const find = el("div", "find");
    const open = el("a", null, "Open filing at this passage");
    open.href =
      (firstLink.source_url || "") + fragment(firstLink.excerpt || s.excerpt);
    open.target = "_blank";
    open.rel = "noopener";
    find.append(open);

    const det = el("details");
    const sum = el("summary");
    const n = sightings.length || 1;
    const latestDate = latest(s);
    if (n > 1) {
      sum.append(
        "Seen in ",
        el("b", null, `${n} filings`),
        `, latest ${latestDate}`
      );
    } else {
      sum.append("First seen in ", el("b", null, "1 filing"));
    }
    const ul = el("ul", "sightings");
    const list = sightings.length
      ? [...sightings].sort((a, b) =>
          (b.filing_date || "").localeCompare(a.filing_date || "")
        )
      : [firstLink];
    for (const x of list) {
      const li = el("li");
      const a = el(
        "a",
        "d",
        `${x.filing_date || ""} ${x.form || ""}`.trim()
      );
      a.href = (x.source_url || "") + fragment(x.excerpt || "");
      a.target = "_blank";
      a.rel = "noopener";
      const match = x.match || "first";
      li.append(
        a,
        el(
          "span",
          "m",
          match === "first"
            ? "first appearance"
            : match === "exact"
              ? "same text"
              : "lightly reworded"
        )
      );
      ul.append(li);
    }
    det.append(sum, ul);

    const actions = el("div", "actions");
    const t = s.votes || { up: 0, down: 0 };
    const mk = (cls, sym, text, count, v) => {
      const b = el("button", `vote ${cls}`);
      b.type = "button";
      b.append(sym + " " + text);
      if (count != null) b.append(el("span", "n", String(count)));
      if (v != null) {
        b.setAttribute("aria-pressed", String(mine === v));
        b.disabled = !canVote;
        b.addEventListener("click", () => castVote(s, v));
      }
      return b;
    };
    const skip = mk("skip", "→", "Skip", null, null);
    skip.addEventListener("click", () => {
      skipped.add(s.signal_id);
      renderFeed();
    });
    actions.append(
      mk("up", "👍", "Useful", t.up, 1),
      mk("down", "👎", "Not useful", t.down, -1),
      skip
    );
    if (!canVote) {
      actions.append(
        el(
          "span",
          "hint",
          isClerkMode()
            ? "Sign in and select an organization to vote."
            : "Type your name above to vote."
        )
      );
    }

    card.append(meta, chips, ex, find, det, actions);
    return card;
  }

  function renderFeed() {
    const stack = $("#feed-stack");
    const qLeft = $(".q-left");
    if (!stack) return;

    stack.replaceChildren();
    const q = queue();
    const unvoted = q.filter((s) => userVotes[s.signal_id] == null).length;
    if (qLeft) {
      qLeft.textContent = `${unvoted} of ${q.length} signals in this filter still need your vote`;
    }
    if (!q.length) {
      stack.append(
        el(
          "div",
          "empty",
          SIGNALS.length
            ? "No signals match these filters. Clear a filter to see more."
            : "No signals loaded."
        )
      );
      return;
    }
    const toShow = feedMode === "one" ? q.slice(0, 1) : q;
    for (const s of toShow) {
      stack.append(buildCard(s));
    }
  }

  /** @deprecated alias kept for any leftover callers / debugging */
  function renderCard() {
    renderFeed();
  }

  async function loadUserVotes() {
    userVotes = {};
    if (isClerkMode()) {
      if (!window.__msAuth?.orgId || !window.__msAuth?.sessionToken) return;
      const resp = await fetch("/api/votes", { headers: apiHeaders() });
      if (!resp.ok) return;
      userVotes = await resp.json();
      return;
    }
    const user = (userIn?.value || "").trim();
    if (!user) return;
    const resp = await fetch(`/api/votes?user=${encodeURIComponent(user)}`);
    if (!resp.ok) return;
    userVotes = await resp.json();
  }

  async function loadSignals({ keepFilters = false } = {}) {
    if (isClerkMode() && !window.__msAuth?.orgId) {
      SIGNALS = [];
      fillFilters();
      return;
    }
    const resp = await fetch("/api/signals", { headers: apiHeaders() });
    if (!resp.ok) throw new Error(`signals ${resp.status}`);
    SIGNALS = await resp.json();
    if (!keepFilters) fillFilters();
    else fillFilters();
  }

  function lookbackWindow() {
    const starts = SIGNALS.map((s) => s.window_start).filter(Boolean).sort();
    const ends = SIGNALS.map((s) => s.window_end).filter(Boolean).sort();
    if (!starts.length || !ends.length) return "n/a";
    return `${starts[0]} to ${ends[ends.length - 1]}`;
  }

  function renderFamilies() {
    const body = $("#fam-rows");
    const win = $(".win");
    if (win) win.textContent = lookbackWindow();
    if (!body) return;
    body.replaceChildren();
    if (!SIGNALS.length) {
      const tr = el("tr");
      const td = el("td", null, "No signals loaded.");
      td.colSpan = 7;
      tr.append(td);
      body.append(tr);
      return;
    }

    const fams = new Map();
    for (const s of SIGNALS) {
      const key = s.product_family || "(unknown)";
      const f =
        fams.get(key) || {
          family: key,
          category: s.product_category || "",
          tightening: 0,
          easing: 0,
          stable: 0,
          unclear: 0,
          supplier: 0,
          buyer: 0,
          latest: "",
        };
      const dir = s.supply_direction || "unclear";
      f[dir] = (f[dir] || 0) + 1;
      const role = (s.role || "").toLowerCase();
      if (role === "supplier") f.supplier += 1;
      else if (role === "buyer") f.buyer += 1;
      const ls = latest(s);
      if (ls > f.latest) f.latest = ls;
      fams.set(key, f);
    }

    const values = [...fams.values()];
    const max = Math.max(
      1,
      ...values.map((f) => Math.max(f.tightening || 0, f.easing || 0))
    );
    const CAT = { DRAM: 0, NAND: 1, Other: 2 };
    values
      .sort(
        (a, b) =>
          (CAT[a.category] ?? 9) - (CAT[b.category] ?? 9) ||
          b.tightening + b.easing - (a.tightening + a.easing) ||
          a.family.localeCompare(b.family)
      )
      .forEach((f) => {
        const tr = el("tr");
        tr.tabIndex = 0;
        tr.dataset.family = f.family;
        const bar = (n, color) => {
          const td = el("td", "num");
          const b = el("span", "bar");
          b.style.width = `${Math.round((n / max) * 60)}px`;
          b.style.background = color;
          if (n) td.append(b);
          td.append(String(n));
          return td;
        };
        tr.append(
          el("td", null, label(f.family)),
          el("td", null, f.category),
          bar(f.tightening || 0, "var(--tight)"),
          bar(f.easing || 0, "var(--ease)"),
          el("td", "num", String(f.supplier)),
          el("td", "num", String(f.buyer)),
          el("td", "mono", f.latest)
        );
        const go = () => {
          FILTERS.forEach(([id]) => {
            const sel = $("#f-" + id);
            if (sel) sel.value = "";
          });
          const famSel = $("#f-family");
          if (famSel) famSel.value = f.family;
          skipped = new Set();
          showTab("feed");
          renderFeed();
        };
        tr.addEventListener("click", go);
        tr.addEventListener("keydown", (e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            go();
          }
        });
        body.append(tr);
      });
  }

  // --- Signal map (Prompt 5–6) ---
  let leaveOutRisk = false;
  let mapFamily = null; // product_family key or null
  let META_NOTES = null;

  async function loadMeta() {
    if (isClerkMode() && !window.__msAuth?.orgId) {
      META_NOTES = null;
      return null;
    }
    const resp = await fetch("/api/meta", { headers: apiHeaders() });
    if (!resp.ok) throw new Error(`meta ${resp.status}`);
    const data = await resp.json();
    META_NOTES = data.notes || null;
    return META_NOTES;
  }

  function renderDataNotes() {
    const n = META_NOTES;
    const set = (id, text) => {
      const el = document.getElementById(id);
      if (el) el.textContent = text == null ? "—" : String(text);
    };
    if (!n) {
      ["note-missing", "note-sources", "note-general", "note-total", "note-buyer-specific", "note-buyer-rows", "note-conflict"].forEach(
        (id) => set(id, "—")
      );
      return;
    }
    const fmt = window.ms?.fmt || ((x) => String(x));
    set("note-missing", n.missing);
    set("note-sources", n.sources);
    set("note-general", fmt(n.general_rows));
    set("note-total", fmt(n.total));
    set("note-buyer-specific", fmt(n.buyer_specific));
    set("note-buyer-rows", fmt(n.buyer_rows));
    set("note-conflict", fmt(n.conflict_rows));
  }

  const zeroDirs = () => ({
    tightening: { supplier: 0, buyer: 0 },
    easing: { supplier: 0, buyer: 0 },
  });

  function signalsInMapView() {
    return SIGNALS.filter((s) => {
      if (leaveOutRisk && (s.section || "") === "risk_factors") return false;
      if (mapFamily && s.product_family !== mapFamily) return false;
      return true;
    });
  }

  /** Flatten sightings for month chart; apply risk + family filters. */
  function sightingsInMapView() {
    const out = [];
    for (const s of SIGNALS) {
      if (mapFamily && s.product_family !== mapFamily) continue;
      const list = s.sightings || [];
      if (!list.length) {
        // Fall back to signal row as one sighting
        if (leaveOutRisk && (s.section || "") === "risk_factors") continue;
        out.push({
          filing_date: s.filing_date || s.last_seen || "",
          section: s.section || "",
          supply_direction: s.supply_direction || "",
          company: s.company,
          product_family: s.product_family,
          role: s.role,
        });
        continue;
      }
      for (const x of list) {
        if (leaveOutRisk && (x.section || "") === "risk_factors") continue;
        out.push({
          filing_date: x.filing_date || "",
          section: x.section || "",
          supply_direction: x.supply_direction || s.supply_direction || "",
          company: s.company,
          product_family: s.product_family,
          role: s.role,
        });
      }
    }
    return out;
  }

  function lookbackMonths() {
    const starts = SIGNALS.map((s) => s.window_start).filter(Boolean).sort();
    const ends = SIGNALS.map((s) => s.window_end).filter(Boolean).sort();
    if (!starts.length || !ends.length || !window.d3) return [];
    const start = starts[0].slice(0, 7) + "-01T00:00:00Z";
    const end = ends[ends.length - 1].slice(0, 7) + "-01T00:00:00Z";
    return d3.utcMonth
      .range(new Date(start), d3.utcMonth.offset(new Date(end), 1))
      .map(d3.utcFormat("%Y-%m"));
  }

  function pickMapFamily(f) {
    mapFamily = mapFamily === f ? null : f;
    renderMap();
  }

  function renderMap() {
    const ms = window.ms;
    if (!ms || !window.d3) return;
    const root = $("#signal-map");
    if (!root || root.closest("[hidden]")) return;

    const view = signalsInMapView();
    const tightening = view.filter((s) => s.supply_direction === "tightening").length;
    const easing = view.filter((s) => s.supply_direction === "easing").length;
    const companies = new Set(view.map((s) => s.company)).size;
    const tightPct = view.length ? Math.round((100 * tightening) / view.length) : 0;

    const setText = (id, text) => {
      const n = document.getElementById(id);
      if (n) n.textContent = text;
    };
    setText("ms-v-signals", ms.fmt(view.length));
    setText("ms-v-tightening", ms.fmt(tightening));
    setText("ms-v-easing", ms.fmt(easing));
    setText("ms-v-companies", ms.fmt(companies));
    setText("ms-v-tight-pct", String(tightPct));
    setText("ms-t-tight", ms.fmt(tightening));
    setText("ms-t-all", ms.fmt(view.length));
    setText(
      "ms-t-fam",
      mapFamily ? ` for ${ms.label(mapFamily)}` : ""
    );
    setText(
      "ms-scope",
      mapFamily ? ms.label(mapFamily) : "All families"
    );

    const repBtn = $("#toggle-reported");
    if (repBtn) repBtn.setAttribute("aria-pressed", String(leaveOutRisk));
    const picked = $("#picked-family");
    if (picked) {
      picked.hidden = !mapFamily;
      picked.textContent = mapFamily ? `${ms.label(mapFamily)} ×` : "";
      picked.setAttribute(
        "aria-label",
        mapFamily
          ? `Clear family filter ${ms.label(mapFamily)}`
          : "Clear family filter"
      );
    }

    // Family chart — always show all families in current risk-filter (dim non-selected)
    const famSource = SIGNALS.filter(
      (s) => !(leaveOutRisk && (s.section || "") === "risk_factors")
    );
    const fams = new Map();
    for (const s of famSource) {
      const key = s.product_family || "(unknown)";
      if (!fams.has(key)) {
        fams.set(key, {
          key,
          category: s.product_category || "",
          ...zeroDirs(),
        });
      }
      const dir = s.supply_direction;
      const role = (s.role || "").toLowerCase();
      if ((dir === "tightening" || dir === "easing") && (role === "supplier" || role === "buyer")) {
        fams.get(key)[dir][role] += 1;
      }
    }
    const famTotal = (d) =>
      d.tightening.supplier +
      d.tightening.buyer +
      d.easing.supplier +
      d.easing.buyer;
    const CAT = { DRAM: 0, NAND: 1, Other: 2 };
    const famRows = [...fams.values()]
      .map((d) => ({
        ...d,
        label: ms.label(d.key),
        sub: d.category === "Other" ? "No family named" : d.category,
        tipHead: `${ms.label(d.key)} (${d.category})`,
      }))
      .sort(
        (a, b) =>
          (CAT[a.category] ?? 9) - (CAT[b.category] ?? 9) ||
          Number(a.key.endsWith("_general")) - Number(b.key.endsWith("_general")) ||
          famTotal(b) - famTotal(a)
      );
    ms.bars($("#family-chart"), famRows, {
      selected: mapFamily,
      onClick: pickMapFamily,
    });

    // Month chart — sightings
    const months = lookbackMonths();
    const counts = new Map(months.map((m) => [m, { tightening: 0, easing: 0 }]));
    let monthTotal = 0;
    for (const x of sightingsInMapView()) {
      const m = (x.filing_date || "").slice(0, 7);
      if (!counts.has(m)) continue;
      const dir = x.supply_direction;
      if (dir !== "tightening" && dir !== "easing") continue;
      counts.get(m)[dir] += 1;
      monthTotal += 1;
    }
    ms.months($("#month-chart"), months, counts);
    // Stash for Prompt 5 checks
    root.dataset.monthTotal = String(monthTotal);
    root.dataset.signals = String(view.length);
    root.dataset.tightening = String(tightening);
    root.dataset.easing = String(easing);
    root.dataset.companies = String(companies);

    // Company chart
    const cos = new Map();
    for (const s of view) {
      const key = s.company || "(unknown)";
      if (!cos.has(key)) {
        cos.set(key, {
          key,
          role: (s.role || "").toLowerCase(),
          ...zeroDirs(),
        });
      }
      const dir = s.supply_direction;
      const role = (s.role || "").toLowerCase();
      if ((dir === "tightening" || dir === "easing") && (role === "supplier" || role === "buyer")) {
        cos.get(key)[dir][role] += 1;
      }
    }
    const coTotal = (d) =>
      d.tightening.supplier +
      d.tightening.buyer +
      d.easing.supplier +
      d.easing.buyer;
    const coRows = [...cos.values()]
      .map((d) => ({
        ...d,
        label: d.key,
        sub: d.role === "supplier" ? "Supplier" : "Buyer",
      }))
      .sort(
        (a, b) =>
          (a.role === "supplier" ? 0 : 1) - (b.role === "supplier" ? 0 : 1) ||
          coTotal(b) - coTotal(a)
      );
    const companyEl = $("#company-chart");
    if (!coRows.length) {
      companyEl.replaceChildren();
      companyEl.append(el("div", "ms-empty", "No signals in this view."));
    } else {
      ms.bars(companyEl, coRows, {});
    }
  }

  const repBtn = $("#toggle-reported");
  if (repBtn) {
    repBtn.addEventListener("click", () => {
      leaveOutRisk = !leaveOutRisk;
      renderMap();
    });
  }
  const pickedBtn = $("#picked-family");
  if (pickedBtn) {
    pickedBtn.addEventListener("click", () => {
      mapFamily = null;
      renderMap();
    });
  }
  window.addEventListener("resize", () => {
    if (!$("#view-map")?.hidden) renderMap();
  });

  // Expose helpers for checks / debugging
  window.__ms = {
    fragment,
    latest,
    highlight,
    label,
    renderFeed,
    renderCard,
    renderFamilies,
    renderMap,
    renderDataNotes,
    showTab,
    getFeedMode: () => feedMode,
    getNotes: () => META_NOTES,
    getMapState: () => ({
      leaveOutRisk,
      mapFamily,
      monthTotal: Number($("#signal-map")?.dataset.monthTotal || 0),
      signals: Number($("#signal-map")?.dataset.signals || 0),
      tightening: Number($("#signal-map")?.dataset.tightening || 0),
      easing: Number($("#signal-map")?.dataset.easing || 0),
      companies: Number($("#signal-map")?.dataset.companies || 0),
    }),
  };

  async function bootApp() {
    await loadSignals();
    await loadUserVotes();
    await loadMeta();
    renderFeed();
    renderFamilies();
    renderDataNotes();
    if (location.hash === "#families") showTab("families");
    else if (location.hash === "#map") showTab("map");
  }

  window.__msOnAuthChange = async () => {
    skipped = new Set();
    try {
      if (isClerkMode() && window.__msAuth?.orgId) {
        await fetch("/api/orgs/bootstrap", {
          method: "POST",
          headers: apiHeaders(),
        });
      }
      await bootApp();
    } catch (err) {
      console.error(err);
    }
  };

  (async () => {
    try {
      if (window.msClerk?.init) {
        await window.msClerk.init();
      }
      await bootApp();
    } catch (err) {
      console.error(err);
      const stack = $("#feed-stack");
      if (stack) {
        stack.replaceChildren(
          el("div", "empty", "Failed to load /api/signals. Is the server running?")
        );
      }
    }
  })();
})();


