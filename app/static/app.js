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
  // Corpus stores easing; UI may say "loosening" — same value.
  const DIRECTION_LABELS = {
    tightening: "Tightening",
    easing: "Easing (loosening)",
    stable: "Stable",
    unclear: "Unclear",
  };
  const DIRECTION_ORDER = ["tightening", "easing", "stable", "unclear"];
  const directionLabel = (d) =>
    DIRECTION_LABELS[String(d || "").toLowerCase()] || label(d);

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

  // TWSE OpenAPI feeds return JSON — never use them as “Open filing” hrefs.
  const isTwseOpenApi = (url) =>
    /openapi\.twse\.com\.tw/i.test(String(url || "")) &&
    /\/opendata\//i.test(String(url || ""));

  const mopsPortalUrl = (ticker, filingDate, feed) => {
    let code = String(ticker || "").trim();
    if (/^\d+$/.test(code)) code = code.padStart(4, "0");
    const iso = String(filingDate || "").trim().slice(0, 10);
    let year = "";
    let month = "";
    if (iso.length >= 7 && iso[4] === "-") {
      const y = parseInt(iso.slice(0, 4), 10);
      const m = parseInt(iso.slice(5, 7), 10);
      if (!Number.isNaN(y) && !Number.isNaN(m)) {
        year = String(y - 1911);
        month = String(m);
      }
    }
    const rev = /t187ap05|revenue|月營收/i.test(String(feed || ""));
    if (rev) {
      const parts = [
        "encodeURIComponent=1",
        "step=1",
        "firstin=1",
        "off=1",
        "TYPEK=all",
        `co_id=${code}`,
      ];
      if (year) parts.push(`year=${year}`);
      if (month) parts.push(`month=${month}`);
      return `https://mopsov.twse.com.tw/mops/web/t05st10_if?${parts.join("&")}`;
    }
    const parts = ["TYPEK=all", "step=1", "firstin=true", `co_id=${code}`];
    if (year) parts.push(`year=${year}`);
    if (month) parts.push(`month=${month}`);
    return `https://mopsov.twse.com.tw/mops/web/t05st01?${parts.join("&")}`;
  };

  const filingOpenHref = (url, s, excerptText) => {
    let href = String(url || "").trim();
    const src = String(s?.source || "").toUpperCase();
    if (isTwseOpenApi(href) || (src === "MOPS" && isTwseOpenApi(href))) {
      return mopsPortalUrl(
        s?.ticker_or_code || s?.ticker || "",
        s?.filing_date || latest(s) || "",
        href
      );
    }
    if (!href) return "";
    return href + fragment(excerptText || "");
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
  /** @type {Record<string, number>} signal_id → 1|-1|0 (skip) for current user */
  let userVotes = {};
  const FEED_MODE_KEY = "ms-feed-mode";
  /** @type {"stack"|"one"} */
  let feedMode = store.get(FEED_MODE_KEY, "stack") === "one" ? "one" : "stack";

  function hasRated(signalId) {
    return Object.prototype.hasOwnProperty.call(userVotes, signalId);
  }

  const FILTERS = [
    ["tier", "tier", "All tiers"],
    ["category", "product_category", "All categories"],
    ["family", "product_family", "All families"],
    ["direction", "supply_direction", "Any direction"],
    ["company", "company", "All companies"],
    ["role", "role", "Suppliers and buyers"],
    ["section", "section", "All sections"],
  ];

  const MAP_TIER_KEY = "ms-map-tier";
  /** @type {string} "" = all tiers; default Tier 1 for the primary graph */
  let mapTier = store.get(MAP_TIER_KEY, "1");
  if (mapTier !== "" && mapTier !== "1" && mapTier !== "2" && mapTier !== "3") {
    mapTier = "1";
  }

  /** @type {Array<{id:number,label:string,summary:string}>} */
  let TIER_META = [
    { id: 1, label: "Tier 1", summary: "Core graph" },
    { id: 2, label: "Tier 2", summary: "F500–F1000 electronics / DC" },
    { id: 3, label: "Tier 3", summary: "Smaller listed" },
  ];
  /** @type {Array<{name:string,ticker:string,role:string,tier:number}>} */
  let COMPANY_ROSTER = [];

  const userIn = $("#user-name");
  if (userIn) {
    const saved = store.get(USER_KEY, "");
    if (typeof saved === "string") userIn.value = saved;
    userIn.addEventListener("input", async () => {
      store.set(USER_KEY, userIn.value.trim());
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

  const FAMILY_LABELS = {
    Power_IC: "Power IC",
    AI_accelerators: "AI accelerators",
    Memory_general: "Memory general",
    DRAM_general: "DRAM general",
    NAND_general: "NAND general",
    "Data center SSD": "Data center SSD",
    "Mobile/embedded": "Mobile/embedded",
  };
  const CATEGORY_ORDER = {
    DRAM: 0,
    NAND: 1,
    Other: 2,
    Compute: 3,
    Analog: 4,
    Passives: 5,
  };

  function optionLabel(field, value) {
    if (field === "supply_direction") return directionLabel(value);
    if (field === "product_family") {
      return FAMILY_LABELS[value] || label(value);
    }
    if (field === "product_category") return String(value);
    if (field === "tier") {
      const meta = TIER_META.find((t) => String(t.id) === String(value));
      return meta ? `${meta.label}` : `Tier ${value}`;
    }
    return String(value);
  }

  function filterValues(field) {
    if (field === "tier") {
      const fromRoster = new Set(
        COMPANY_ROSTER.map((c) => String(c.tier)).filter(Boolean)
      );
      const fromSignals = new Set(
        SIGNALS.map((s) => String(s.tier || "1")).filter(Boolean)
      );
      return ["1", "2", "3"].filter(
        (t) => fromRoster.has(t) || fromSignals.has(t) || t === "1"
      );
    }
    const present = [
      ...new Set(SIGNALS.map((s) => s[field]).filter(Boolean)),
    ];
    if (field !== "supply_direction") {
      return present.sort((a, b) => String(a).localeCompare(String(b)));
    }
    // Always expose Tightening + Easing (loosening) first, even if count is 0.
    const rest = present
      .filter((v) => !DIRECTION_ORDER.includes(v))
      .sort((a, b) => String(a).localeCompare(String(b)));
    const ordered = DIRECTION_ORDER.filter(
      (v) => present.includes(v) || v === "tightening" || v === "easing"
    );
    return [...ordered, ...rest];
  }

  function activePanel() {
    if (!$("#view-feed")?.hidden) return "feed";
    if (!$("#view-map")?.hidden) return "map";
    if (!$("#view-families")?.hidden) return "families";
    return "feed";
  }

  function refreshActiveView() {
    const view = activePanel();
    if (view === "map") renderMap();
    else if (view === "families") renderFamilies();
    else renderFeed();
  }

  function fillFilters() {
    for (const [id, field, all] of FILTERS) {
      const sel = $("#f-" + id);
      if (!sel) continue;
      const prev = sel.value;
      sel.replaceChildren();
      sel.append(new Option(all, ""));
      for (const v of filterValues(field)) {
        sel.append(new Option(optionLabel(field, v), v));
      }
      if ([...sel.options].some((o) => o.value === prev)) sel.value = prev;
      sel.onchange = () => {
        refreshActiveView();
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
      refreshActiveView();
    });
  }

  // Client EN supply glossary — paints yellow even if matched_terms_en missing.
  const EN_HIGHLIGHT_GLOSSARY = [
    "capacity utilization rate",
    "capacity utilization",
    "production capacity",
    "packaging capacity",
    "wafer capacity",
    "full capacity",
    "at full capacity",
    "fully loaded",
    "supply shortage",
    "component shortage",
    "price increases",
    "price increase",
    "rising prices",
    "wafer equivalents",
    "wafer equivalent",
    "equivalent wafers",
    "utilization",
    "undersupply",
    "oversupply",
    "allocation",
    "shortages",
    "shortage",
    "capacity",
    "crystals",
    "crystal",
    "wafers",
    "wafer",
    "foundry",
    "supply",
    "demand",
    "pricing",
    "prices",
    "price",
    "full",
  ];

  function highlight(text, terms) {
    const frag = document.createDocumentFragment();
    const raw = String(text || "");
    const lower = raw.toLowerCase();
    const ts = [...new Set(
      (terms || [])
        .map((t) => String(t || "").trim())
        .filter((t) => t.length > 2 && lower.includes(t.toLowerCase()))
    )]
      .sort((a, b) => b.length - a.length)
      .slice(0, 40);
    if (!ts.length) {
      frag.append(raw);
      return frag;
    }
    let re;
    try {
      const esc = ts.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
      re = new RegExp("(" + esc.join("|") + ")", "gi");
    } catch {
      frag.append(raw);
      return frag;
    }
    let last = 0;
    for (const m of raw.matchAll(re)) {
      if (m.index > last) frag.append(raw.slice(last, m.index));
      const mark = el("mark", "term-hl", m[0]);
      mark.style.background = "#fbe9a6";
      mark.style.color = "#172026";
      mark.style.borderRadius = "2px";
      mark.style.padding = "0 1px";
      frag.append(mark);
      last = m.index + m[0].length;
    }
    if (last < raw.length) frag.append(raw.slice(last));
    return frag;
  }

  function englishHighlightTerms(preferred) {
    return [...(preferred || []), ...EN_HIGHLIGHT_GLOSSARY];
  }

  function railFilteredSignals({ ignoreTier = false } = {}) {
    return SIGNALS.filter((s) =>
      FILTERS.every(([id, field]) => {
        if (ignoreTier && field === "tier") return true;
        const sel = $("#f-" + id);
        if (!sel?.value) return true;
        if (field === "tier") return String(s.tier || "1") === sel.value;
        return s[field] === sel.value;
      })
    );
  }

  function queue() {
    // Hide anything this profile already rated (👍/👎) or skipped.
    return railFilteredSignals()
      .filter((s) => !hasRated(s.signal_id))
      .sort(
        (a, b) =>
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
      el(
        "span",
        `chip ${s.supply_direction}`,
        directionLabel(s.supply_direction)
      ),
      el("span", "chip", FAMILY_LABELS[s.product_family] || label(s.product_family)),
      el("span", "chip", `tone: ${s.sentiment}`)
    );

    const terms = String(s.matched_terms || "")
      .split(";")
      .map((t) => t.split(":").slice(1).join(":").trim())
      .filter(Boolean);
    let termsEn = Array.isArray(s.matched_terms_en)
      ? s.matched_terms_en.map((t) => String(t || "").trim()).filter(Boolean)
      : [];
    const lang = String(s.language || "en").toLowerCase();
    const isEnglishLang = !lang || lang === "en" || lang === "english";
    // Any non-EN post gets a translate control; English view uses pipeline
    // excerpt_en when present, else on-demand POST /api/translate.
    let excerptEn = String(s.excerpt_en || "").trim();
    const canTranslate = !isEnglishLang;
    const langChip = (() => {
      const raw = lang.replace(/_/g, "-");
      if (raw === "zh-tw" || raw === "zh-hant" || raw === "zt") return "zh-TW";
      if (raw === "zh-cn" || raw === "zh-hans") return "zh";
      if (raw === "german" || raw === "deutsch") return "de";
      if (raw === "japanese" || raw === "jp") return "ja";
      if (raw === "korean") return "ko";
      return raw || "??";
    })();
    let showEnglish = false;
    let translating = false;
    const ex = el("p", "excerpt");
    const paintExcerpt = () => {
      ex.replaceChildren();
      const text = showEnglish && excerptEn ? excerptEn : s.excerpt || "";
      if (showEnglish && excerptEn) {
        ex.append(highlight(text, englishHighlightTerms(termsEn)));
      } else {
        ex.append(highlight(text, terms));
      }
    };
    paintExcerpt();

    const find = el("div", "find");
    if (canTranslate) {
      const tog = el("button", "lang-toggle", "Show English");
      tog.type = "button";
      tog.setAttribute("aria-pressed", "false");
      tog.title =
        "Machine translation (Argos); original excerpt is unchanged. " +
        "Uses stored excerpt_en or translates on demand.";
      const setToggleLabel = () => {
        if (translating) tog.textContent = "Translating…";
        else tog.textContent = showEnglish ? "Original" : "Show English";
        tog.setAttribute("aria-pressed", showEnglish ? "true" : "false");
        tog.disabled = translating;
      };
      tog.addEventListener("click", async () => {
        if (translating) return;
        if (showEnglish) {
          showEnglish = false;
          setToggleLabel();
          paintExcerpt();
          return;
        }
        if (!excerptEn) {
          translating = true;
          setToggleLabel();
          try {
            // Prefer main's /api/translate; fall back to #23 path still on Fly.
            let resp = await fetch("/api/translate", {
              method: "POST",
              headers: apiHeaders({ "Content-Type": "application/json" }),
              body: JSON.stringify({ signal_id: s.signal_id }),
            });
            if (resp.status === 404 || resp.status === 405) {
              resp = await fetch(
                `/api/signals/${encodeURIComponent(s.signal_id)}/translate`,
                { method: "POST", headers: apiHeaders() }
              );
            }
            if (!resp.ok) {
              const detail = await resp.text();
              console.error("translate failed", resp.status, detail);
              tog.title = `Translation failed (${resp.status}). Try again.`;
              translating = false;
              setToggleLabel();
              return;
            }
            const data = await resp.json();
            excerptEn = String(data.excerpt_en || "").trim();
            s.excerpt_en = excerptEn;
            if (Array.isArray(data.matched_terms_en)) {
              termsEn = data.matched_terms_en
                .map((t) => String(t || "").trim())
                .filter(Boolean);
              s.matched_terms_en = termsEn;
            }
            if (!excerptEn) {
              tog.title = "Translation returned empty English text.";
              translating = false;
              setToggleLabel();
              return;
            }
          } catch (err) {
            console.error("translate failed", err);
            tog.title = `Translation failed: ${err && err.message ? err.message : err}`;
            translating = false;
            setToggleLabel();
            return;
          }
          translating = false;
        }
        showEnglish = true;
        setToggleLabel();
        paintExcerpt();
      });
      find.append(tog);
      chips.append(el("span", "chip lang", langChip));
    }
    const open = el("a", null, "Open filing at this passage");
    open.href = filingOpenHref(
      firstLink.source_url || s.source_url,
      s,
      firstLink.excerpt || s.excerpt
    );
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
    skip.disabled = !canVote;
    skip.addEventListener("click", () => castVote(s, 0));
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
    const scoped = railFilteredSignals();
    const q = queue();
    const done = scoped.length - q.length;
    if (qLeft) {
      if (!scoped.length) {
        qLeft.textContent = "No signals in this filter";
      } else if (!q.length) {
        qLeft.textContent = `Caught up — ${done} already rated or skipped in this filter`;
      } else {
        qLeft.textContent = `${q.length} left to rate · ${done} already done in this filter`;
      }
    }
    if (!q.length) {
      let emptyMsg = "No signals loaded.";
      if (SIGNALS.length && !scoped.length) {
        emptyMsg = "No signals match these filters. Clear a filter to see more.";
      } else if (scoped.length && !q.length) {
        emptyMsg =
          "You're caught up — every signal in this filter was already rated or skipped.";
      }
      stack.append(el("div", "empty", emptyMsg));
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
    const scoped = railFilteredSignals();
    if (!scoped.length) {
      const tr = el("tr");
      const td = el(
        "td",
        null,
        SIGNALS.length
          ? "No signals match these filters. Clear a filter to see more."
          : "No signals loaded."
      );
      td.colSpan = 7;
      tr.append(td);
      body.append(tr);
      return;
    }

    const fams = new Map();
    for (const s of scoped) {
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
    const CAT = CATEGORY_ORDER;
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
    COMPANY_ROSTER = Array.isArray(data.company_roster) ? data.company_roster : [];
    if (Array.isArray(data.tiers) && data.tiers.length) {
      TIER_META = data.tiers.map((t) => ({
        id: t.id,
        label: t.label || `Tier ${t.id}`,
        summary: t.summary || "",
      }));
    }
    if (
      data.default_map_tier != null &&
      store.get(MAP_TIER_KEY, null) == null
    ) {
      mapTier = String(data.default_map_tier);
    }
    syncMapTierSwitch();
    return META_NOTES;
  }

  function renderTierRoster() {
    const list = $("#tier-roster-list");
    const note = $("#tier-roster-note");
    if (!list) return;
    list.replaceChildren();
    const scoped = COMPANY_ROSTER.filter((c) => {
      if (!mapTier) return true;
      return String(c.tier) === mapTier;
    }).sort(
      (a, b) =>
        String(a.role).localeCompare(String(b.role)) ||
        String(a.name).localeCompare(String(b.name))
    );
    const withSignals = new Set(
      SIGNALS.filter(
        (s) => !mapTier || String(s.tier || "1") === mapTier
      ).map((s) => s.company)
    );
    if (note) {
      const labeled =
        mapTier === ""
          ? "all tiers"
          : TIER_META.find((t) => String(t.id) === mapTier)?.label ||
            `Tier ${mapTier}`;
      note.textContent = `${scoped.length} companies configured for ${labeled} · ${withSignals.size} already have signals in this view`;
    }
    if (!scoped.length) {
      list.append(el("div", "tr-empty", "No companies configured for this tier."));
      return;
    }
    for (const c of scoped) {
      const chip = el("span", withSignals.has(c.name) ? "tr-chip has-signal" : "tr-chip");
      chip.append(c.name);
      if (c.ticker) chip.append(el("span", "tk", c.ticker));
      chip.title = `${c.role || "?"} · tier ${c.tier}${
        withSignals.has(c.name) ? " · has signals" : " · awaiting extract"
      }`;
      list.append(chip);
    }
  }

  function syncMapTierSwitch() {
    const root = $("#map-tier-switch");
    if (!root) return;
    for (const btn of root.querySelectorAll("button[data-tier]")) {
      const v = btn.getAttribute("data-tier") ?? "";
      btn.setAttribute("aria-pressed", String(v === mapTier));
    }
    const note = $("#ms-tier-note");
    if (note) {
      const meta = TIER_META.find((t) => String(t.id) === mapTier);
      if (mapTier === "") {
        note.textContent = "Map shows all tiers (Tier 1–3). Feed filter is separate.";
      } else if (meta) {
        note.textContent = `${meta.label} map — ${meta.summary || "scoped graph"}. Switch tiers for a different company set.`;
      }
    }
  }

  const mapTierRoot = $("#map-tier-switch");
  if (mapTierRoot) {
    mapTierRoot.addEventListener("click", (e) => {
      const btn = e.target.closest("button[data-tier]");
      if (!btn) return;
      const next = btn.getAttribute("data-tier") ?? "";
      // Second click on the active tier opens that set in the Feed.
      if (next === mapTier && next !== "") {
        const n = SIGNALS.filter((s) => String(s.tier || "1") === next).length;
        if (n > 0) {
          mapFamily = null;
          openFeedFromMap();
          return;
        }
      }
      mapTier = next;
      store.set(MAP_TIER_KEY, mapTier);
      syncMapTierSwitch();
      renderMap();
    });
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
    // Map has its own tier scope (default Tier 1) — separate from the feed filter.
    return railFilteredSignals({ ignoreTier: true }).filter((s) => {
      if (mapTier && String(s.tier || "1") !== mapTier) return false;
      if (leaveOutRisk && (s.section || "") === "risk_factors") return false;
      if (mapFamily && s.product_family !== mapFamily) return false;
      return true;
    });
  }

  /** Flatten sightings for month chart; apply rail + risk + family filters. */
  function sightingsInMapView() {
    const out = [];
    for (const s of railFilteredSignals({ ignoreTier: true })) {
      if (mapTier && String(s.tier || "1") !== mapTier) continue;
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

  /** Open the Feed with filters matching the current map scope (tier / family / direction). */
  function openFeedFromMap({ direction = "" } = {}) {
    for (const [id] of FILTERS) {
      const sel = $("#f-" + id);
      if (!sel) continue;
      if (id === "tier") sel.value = mapTier || "";
      else if (id === "family") sel.value = mapFamily || "";
      else if (id === "direction") sel.value = direction || "";
      else sel.value = "";
    }
    // Ensure tier options exist even before signals for that tier were loaded into filters.
    const tierSel = $("#f-tier");
    if (tierSel && mapTier && ![...tierSel.options].some((o) => o.value === mapTier)) {
      tierSel.append(new Option(optionLabel("tier", mapTier), mapTier));
      tierSel.value = mapTier;
    }
    const famSel = $("#f-family");
    if (famSel && mapFamily && ![...famSel.options].some((o) => o.value === mapFamily)) {
      famSel.append(new Option(optionLabel("product_family", mapFamily), mapFamily));
      famSel.value = mapFamily;
    }
    showTab("feed");
    renderFeed();
    const stack = $("#feed-stack");
    if (stack) stack.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function wireMapFeedTiles() {
    const root = $("#headline");
    if (!root || root.dataset.feedWired === "1") return;
    root.dataset.feedWired = "1";
    root.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-feed-open]");
      if (!btn || btn.disabled) return;
      const kind = btn.getAttribute("data-feed-open") || "signals";
      const direction =
        kind === "tightening" || kind === "easing" ? kind : "";
      openFeedFromMap({ direction });
    });
  }

  function updateTierSwitchCounts() {
    const root = $("#map-tier-switch");
    if (!root) return;
    const counts = { "1": 0, "2": 0, "3": 0 };
    for (const s of SIGNALS) {
      const t = String(s.tier || "1");
      if (counts[t] != null) counts[t] += 1;
    }
    for (const btn of root.querySelectorAll("button[data-tier]")) {
      const v = btn.getAttribute("data-tier") ?? "";
      if (v === "1" || v === "2" || v === "3") {
        const n = counts[v] || 0;
        btn.textContent = `Tier ${v} · ${n}`;
        btn.title =
          n > 0
            ? `${n} signals — click again (or the Signals tile) to open Feed`
            : `Tier ${v} — no signals yet`;
      } else {
        btn.textContent = "All";
        btn.title = "Show all tiers on the map";
      }
    }
  }

  function renderMap() {
    const ms = window.ms;
    if (!ms || !window.d3) return;
    const root = $("#signal-map");
    if (!root || root.closest("[hidden]")) return;

    wireMapFeedTiles();
    updateTierSwitchCounts();

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

    const armTile = (id, enabled, label) => {
      const btn = document.getElementById(id);
      if (!btn) return;
      btn.disabled = !enabled;
      if (enabled) {
        btn.setAttribute("aria-label", label);
      }
    };
    armTile(
      "tile-signals",
      view.length > 0,
      `Open ${view.length} signals in the Feed`
    );
    armTile(
      "tile-tightening",
      tightening > 0,
      `Open ${tightening} tightening signals in the Feed`
    );
    armTile(
      "tile-easing",
      easing > 0,
      `Open ${easing} easing signals in the Feed`
    );
    armTile(
      "tile-companies",
      view.length > 0,
      `Open ${view.length} signals in the Feed`
    );

    renderTierRoster();

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

    // Family chart — map tier + rail filters + optional risk leave-out
    const famSource = railFilteredSignals({ ignoreTier: true }).filter((s) => {
      if (mapTier && String(s.tier || "1") !== mapTier) return false;
      return !(leaveOutRisk && (s.section || "") === "risk_factors");
    });
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
    const CAT = CATEGORY_ORDER;
    const famRows = [...fams.values()]
      .map((d) => ({
        ...d,
        label: FAMILY_LABELS[d.key] || ms.label(d.key),
        sub:
          d.category === "Other" && d.key === "Memory_general"
            ? "No family named"
            : d.category,
        tipHead: `${FAMILY_LABELS[d.key] || ms.label(d.key)} (${d.category})`,
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
    directionLabel,
    railFilteredSignals,
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


