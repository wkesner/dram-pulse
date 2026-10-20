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
        /* ignore quota / private mode */
      }
    },
  };

  // --- Voting as (localStorage) ---
  const userIn = $("#user-name");
  if (userIn) {
    const saved = store.get(USER_KEY, "");
    if (typeof saved === "string") userIn.value = saved;
    userIn.addEventListener("input", () => {
      store.set(USER_KEY, userIn.value.trim());
    });
  }

  // --- Theme: light / dark override (also follows prefers-color-scheme when unset) ---
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
      themeBtn.setAttribute("aria-label", `Switch to ${themeBtn.textContent} theme`);
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
  const tabs = [
    { tab: $("#tab-feed"), panel: $("#view-feed") },
    { tab: $("#tab-map"), panel: $("#view-map") },
    { tab: $("#tab-families"), panel: $("#view-families") },
  ];

  function selectTab(selected) {
    for (const { tab, panel } of tabs) {
      if (!tab || !panel) continue;
      const on = tab === selected;
      tab.setAttribute("aria-selected", on ? "true" : "false");
      panel.hidden = !on;
    }
  }

  for (const { tab } of tabs) {
    if (!tab) continue;
    tab.addEventListener("click", () => selectTab(tab));
  }

  // --- Filters (shell only: clear resets selects; options filled in later prompts) ---
  const FILTER_IDS = [
    "f-category",
    "f-family",
    "f-direction",
    "f-company",
    "f-role",
    "f-section",
  ];
  const clearBtn = $("#clear-filters");
  if (clearBtn) {
    clearBtn.addEventListener("click", () => {
      for (const id of FILTER_IDS) {
        const sel = document.getElementById(id);
        if (sel) sel.value = "";
      }
    });
  }
})();
