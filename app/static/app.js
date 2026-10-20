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
    if (selected === $("#tab-feed")) renderCard();
    if (selected === $("#tab-families")) renderFamilies();
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
      renderCard();
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
        renderCard();
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
      renderCard();
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

  async function castVote(s, v) {
    const user = (userIn?.value || "").trim();
    if (!user) {
      userIn?.focus();
      return;
    }
    const resp = await fetch("/api/votes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
    renderCard();
  }

  function renderCard() {
    const card = $("#signal-card");
    const qLeft = $(".q-left");
    if (!card) return;

    card.replaceChildren();
    const q = queue();
    const user = (userIn?.value || "").trim();
    const unvoted = q.filter((s) => userVotes[s.signal_id] == null).length;
    if (qLeft) {
      qLeft.textContent = `${unvoted} of ${q.length} signals in this filter still need your vote`;
    }
    if (!q.length) {
      card.append(
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
    const s = q[0];
    const sightings = s.sightings || [];
    const firstLink = primarySighting(s);

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
    open.href = (firstLink.source_url || "") + fragment(firstLink.excerpt || s.excerpt);
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
    const mine = userVotes[s.signal_id];
    const t = s.votes || { up: 0, down: 0 };
    const mk = (cls, sym, text, count, v) => {
      const b = el("button", `vote ${cls}`);
      b.type = "button";
      b.append(sym + " " + text);
      if (count != null) b.append(el("span", "n", String(count)));
      if (v != null) {
        b.setAttribute("aria-pressed", String(mine === v));
        b.disabled = !user;
        b.addEventListener("click", () => castVote(s, v));
      }
      return b;
    };
    const skip = mk("skip", "→", "Skip", null, null);
    skip.addEventListener("click", () => {
      skipped.add(s.signal_id);
      renderCard();
    });
    actions.append(
      mk("up", "👍", "Useful", t.up, 1),
      mk("down", "👎", "Not useful", t.down, -1),
      skip
    );
    if (!user) {
      actions.append(el("span", "hint", "Type your name above to vote."));
    }

    card.append(meta, chips, ex, find, det, actions);
  }

  async function loadUserVotes() {
    const user = (userIn?.value || "").trim();
    userVotes = {};
    if (!user) return;
    const resp = await fetch(`/api/votes?user=${encodeURIComponent(user)}`);
    if (!resp.ok) return;
    userVotes = await resp.json();
  }

  async function loadSignals({ keepFilters = false } = {}) {
    const resp = await fetch("/api/signals");
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
          renderCard();
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

  // Expose helpers for checks / debugging
  window.__ms = { fragment, latest, highlight, label, renderFamilies, showTab };

  (async () => {
    try {
      await loadSignals();
      await loadUserVotes();
      renderCard();
      renderFamilies();
      if (location.hash === "#families") showTab("families");
    } catch (err) {
      console.error(err);
      const card = $("#signal-card");
      if (card) {
        card.replaceChildren(
          el("div", "empty", "Failed to load /api/signals. Is the server running?")
        );
      }
    }
  })();
})();

