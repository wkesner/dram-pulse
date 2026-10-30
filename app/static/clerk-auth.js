/**
 * Clerk / app-token browser helpers.
 * AUTH_MODE=clerk → session JWT + org required for the whole UI.
 * AUTH_MODE=single + APP_ACCESS_TOKEN → shared secret (Bearer / X-App-Token).
 * Aligns with Clerk JS CDN quickstart (clerk-js@6 + @clerk/ui):
 * https://clerk.com/docs/js-frontend/getting-started/quickstart
 */
(() => {
  const APP_TOKEN_KEY = "ms-app-access-token";

  const state = {
    mode: "single",
    ready: false,
    clerk: null,
    sessionToken: null,
    orgId: null,
    userId: null,
    displayName: "",
    requiresAuth: false,
    requiresAppToken: false,
    appToken: null,
    authenticated: false,
  };

  async function loadConfig() {
    const resp = await fetch("/api/config");
    if (!resp.ok) throw new Error("config " + resp.status);
    return resp.json();
  }

  function loadScript(src, attrs) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.crossOrigin = "anonymous";
      if (attrs) {
        for (const [k, v] of Object.entries(attrs)) {
          s.setAttribute(k, v);
        }
      }
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("failed to load " + src));
      document.head.appendChild(s);
    });
  }

  /** Derive Frontend API host from pk_test_/pk_live_ (Clerk JS quickstart). */
  function frontendApiFromPublishableKey(pk) {
    try {
      const encoded = pk.split("_").slice(2).join("_");
      const decoded = atob(encoded);
      return decoded.replace(/\$$/, "").replace(/^https?:\/\//, "");
    } catch {
      return "";
    }
  }

  async function ensureClerk(publishableKey, issuer) {
    if (window.Clerk && state.clerk) return window.Clerk;

    let fapi = "";
    if (issuer) {
      try {
        fapi = new URL(issuer).host;
      } catch {
        fapi = String(issuer)
          .replace(/^https?:\/\//, "")
          .replace(/\/$/, "");
      }
    }
    if (!fapi) fapi = frontendApiFromPublishableKey(publishableKey);
    if (!fapi) throw new Error("cannot resolve Clerk Frontend API host");

    // Official CDN pattern: UI bundle then clerk-js@6 with data-clerk-publishable-key
    if (!window.__internal_ClerkUICtor) {
      await loadScript(
        `https://${fapi}/npm/@clerk/ui@1/dist/ui.browser.js`
      );
    }
    if (!window.Clerk) {
      await loadScript(
        `https://${fapi}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`,
        { "data-clerk-publishable-key": publishableKey }
      );
    }
    const clerk = window.Clerk;
    if (!clerk) throw new Error("Clerk global missing after CDN load");
    await clerk.load({
      publishableKey,
      ui: { ClerkUI: window.__internal_ClerkUICtor },
    });
    return clerk;
  }

  async function refreshSession(clerk) {
    const session = clerk.session;
    if (!session) {
      state.sessionToken = null;
      state.orgId = null;
      state.userId = null;
      state.displayName = "";
      return null;
    }
    const token = await session.getToken();
    state.sessionToken = token;
    state.userId = clerk.user?.id || null;
    state.displayName =
      clerk.user?.fullName ||
      clerk.user?.primaryEmailAddress?.emailAddress ||
      state.userId ||
      "";
    state.orgId = clerk.organization?.id || null;
    return token;
  }

  function readStoredAppToken() {
    try {
      return (sessionStorage.getItem(APP_TOKEN_KEY) || "").trim();
    } catch {
      return "";
    }
  }

  function writeStoredAppToken(token) {
    try {
      if (token) sessionStorage.setItem(APP_TOKEN_KEY, token);
      else sessionStorage.removeItem(APP_TOKEN_KEY);
    } catch {
      /* ignore */
    }
  }

  function authHeaders() {
    const h = {};
    if (state.mode === "clerk" && state.sessionToken) {
      h.Authorization = "Bearer " + state.sessionToken;
    } else if (state.requiresAppToken && state.appToken) {
      h.Authorization = "Bearer " + state.appToken;
      h["X-App-Token"] = state.appToken;
    }
    return h;
  }

  function isAuthenticated() {
    if (state.mode === "clerk") {
      return Boolean(state.sessionToken && state.orgId);
    }
    if (state.requiresAppToken) {
      return Boolean(state.appToken);
    }
    return true;
  }

  function setShellVisible(visible) {
    document.body.classList.toggle("auth-locked", !visible);
    const main = document.getElementById("app-main");
    if (main) main.hidden = !visible;
    document.querySelectorAll("[data-auth-hide]").forEach((el) => {
      el.hidden = !visible;
    });
  }

  function renderAuthGate({ title, body, mountSignIn, showTokenForm }) {
    const gate = document.getElementById("auth-gate");
    if (!gate) return;
    gate.hidden = false;
    gate.replaceChildren();

    const panel = document.createElement("div");
    panel.className = "auth-gate-panel";
    const h = document.createElement("h2");
    h.textContent = title;
    const p = document.createElement("p");
    p.className = "auth-gate-lede";
    p.textContent = body;
    panel.append(h, p);

    if (showTokenForm) {
      const form = document.createElement("form");
      form.className = "auth-token-form";
      form.setAttribute("autocomplete", "off");
      const label = document.createElement("label");
      label.textContent = "Access token";
      const input = document.createElement("input");
      input.type = "password";
      input.name = "app_token";
      input.placeholder = "APP_ACCESS_TOKEN";
      input.required = true;
      input.value = state.appToken || "";
      label.append(input);
      const btn = document.createElement("button");
      btn.type = "submit";
      btn.textContent = "Continue";
      const err = document.createElement("p");
      err.className = "auth-gate-error";
      err.hidden = true;
      form.append(label, btn, err);
      form.addEventListener("submit", async (evt) => {
        evt.preventDefault();
        const tok = (input.value || "").trim();
        err.hidden = true;
        if (!tok) return;
        // Probe a gated endpoint before unlocking the shell.
        const probe = await fetch("/api/meta", {
          headers: {
            Authorization: "Bearer " + tok,
            "X-App-Token": tok,
          },
        });
        if (!probe.ok) {
          err.hidden = false;
          err.textContent =
            probe.status === 401
              ? "That token was rejected. Check APP_ACCESS_TOKEN."
              : `Could not verify token (HTTP ${probe.status}).`;
          return;
        }
        state.appToken = tok;
        writeStoredAppToken(tok);
        state.authenticated = true;
        applyAuthGate();
        if (typeof window.__msOnAuthChange === "function") {
          window.__msOnAuthChange();
        }
      });
      panel.append(form);
    }

    if (mountSignIn) {
      const mount = document.createElement("div");
      mount.id = "auth-gate-clerk";
      panel.append(mount);
      gate.append(panel);
      if (state.clerk) state.clerk.mountSignIn(mount);
      return;
    }

    gate.append(panel);
  }

  function applyAuthGate() {
    state.authenticated = isAuthenticated();
    const gate = document.getElementById("auth-gate");

    if (!state.requiresAuth) {
      if (gate) gate.hidden = true;
      setShellVisible(true);
      return state.authenticated;
    }

    if (state.mode === "clerk") {
      if (!state.clerk?.user) {
        setShellVisible(false);
        renderAuthGate({
          title: "Sign in to EC Signals",
          body: "A Clerk account and organization are required to view signals, votes, and broker sims.",
          mountSignIn: true,
        });
        return false;
      }
      if (!state.orgId) {
        setShellVisible(false);
        renderAuthGate({
          title: "Select an organization",
          body: "Create or switch to an organization in the header to load your private feed.",
        });
        return false;
      }
      if (gate) gate.hidden = true;
      setShellVisible(true);
      return true;
    }

    if (state.requiresAppToken && !state.appToken) {
      setShellVisible(false);
      renderAuthGate({
        title: "Enter access token",
        body: "This deployment requires APP_ACCESS_TOKEN. Paste the shared secret to continue.",
        showTokenForm: true,
      });
      return false;
    }

    if (gate) gate.hidden = true;
    setShellVisible(true);
    return true;
  }

  function mountChrome(clerk) {
    const host = document.getElementById("clerk-chrome");
    if (!host) return;
    host.hidden = false;
    host.replaceChildren();

    const row = document.createElement("div");
    row.className = "clerk-row";

    if (!clerk.user) {
      // Full-page gate owns SignIn; keep chrome empty while signed out.
      host.append(row);
      return;
    }

    const userBtn = document.createElement("div");
    userBtn.id = "clerk-user-button";
    const orgBtn = document.createElement("div");
    orgBtn.id = "clerk-org-switcher";
    row.append(orgBtn, userBtn);
    host.append(row);
    clerk.mountOrganizationSwitcher(orgBtn, {
      createOrganizationMode: "modal",
      organizationProfileMode: "modal",
    });
    clerk.mountUserButton(userBtn);

    const hint = document.getElementById("clerk-hint");
    if (hint) {
      hint.hidden = Boolean(clerk.organization);
      hint.textContent = clerk.organization
        ? ""
        : "Create or select an organization to unlock the app.";
    }
  }

  async function init() {
    const cfg = await loadConfig();
    state.mode = cfg.auth_mode || "single";
    state.requiresAuth = Boolean(cfg.requires_auth);
    state.requiresAppToken = Boolean(cfg.requires_app_token);
    state.appToken = state.requiresAppToken ? readStoredAppToken() : null;
    window.__msAuth = state;

    const singleUser = document.querySelector("label.user");
    if (state.mode !== "clerk") {
      if (singleUser) singleUser.hidden = false;
      applyAuthGate();
      state.ready = true;
      return {
        mode: "single",
        requiresAuth: state.requiresAuth,
        authenticated: state.authenticated,
      };
    }

    if (singleUser) singleUser.hidden = true;
    const banner = document.getElementById("clerk-setup-banner");

    if (!cfg.clerk_configured || !cfg.clerk_publishable_key) {
      if (banner) {
        banner.hidden = false;
        banner.textContent =
          "Clerk mode is on, but keys are missing. Run: npx clerk@latest auth login && npx clerk@latest link --app <id> && npx clerk@latest env pull --file .env — then set AUTH_MODE=clerk and restart uvicorn.";
      }
      setShellVisible(false);
      renderAuthGate({
        title: "Clerk not configured",
        body: "Set CLERK_PUBLISHABLE_KEY / CLERK_SECRET_KEY and restart the server.",
      });
      return { mode: "clerk", configured: false, authenticated: false };
    }
    if (banner) banner.hidden = true;

    const clerk = await ensureClerk(
      cfg.clerk_publishable_key,
      cfg.clerk_issuer || ""
    );
    state.clerk = clerk;
    await refreshSession(clerk);
    mountChrome(clerk);
    applyAuthGate();
    state.ready = true;

    clerk.addListener(async () => {
      await refreshSession(clerk);
      mountChrome(clerk);
      applyAuthGate();
      if (typeof window.__msOnAuthChange === "function") {
        window.__msOnAuthChange();
      }
    });

    if (clerk.organization && state.sessionToken) {
      try {
        await fetch("/api/orgs/bootstrap", {
          method: "POST",
          headers: { ...authHeaders() },
        });
      } catch (err) {
        console.warn("org bootstrap", err);
      }
    }

    return {
      mode: "clerk",
      configured: true,
      orgId: state.orgId,
      authenticated: state.authenticated,
    };
  }

  window.msClerk = {
    init,
    authHeaders,
    state,
    refreshSession,
    isAuthenticated,
    applyAuthGate,
  };
})();
