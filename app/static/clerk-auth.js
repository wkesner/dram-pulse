/**
 * Clerk / app-token browser helpers.
 * AUTH_MODE=clerk → session JWT + org required for the whole UI.
 * AUTH_MODE=single + APP_ACCESS_TOKEN → shared secret (Bearer / X-App-Token).
 * Aligns with Clerk JS CDN quickstart (clerk-js@6 + @clerk/ui):
 * https://clerk.com/docs/js-frontend/getting-started/quickstart
 *
 * Signup invite: SIGNUP_INVITE_KEY is verified once; the ticket + authView are
 * persisted so Clerk SignUp can finish without a remount that wipes the form.
 */
(() => {
  const APP_TOKEN_KEY = "ms-app-access-token";
  const INVITE_TICKET_KEY = "ms-signup-invite-ticket";
  const AUTH_VIEW_KEY = "ms-auth-view";

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
    requiresSignupInvite: false,
    appToken: null,
    inviteTicket: null,
    authView: "signin", // signin | invite | signup
    authenticated: false,
    /** Last gate render key — skip DOM tear-down when unchanged. */
    _gateKey: null,
  };

  function storageGet(key) {
    try {
      return (sessionStorage.getItem(key) || localStorage.getItem(key) || "").trim();
    } catch {
      return "";
    }
  }

  function storageSet(key, value) {
    try {
      if (value) {
        sessionStorage.setItem(key, value);
        localStorage.setItem(key, value);
      } else {
        sessionStorage.removeItem(key);
        localStorage.removeItem(key);
      }
    } catch {
      /* ignore */
    }
  }

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
    return storageGet(APP_TOKEN_KEY);
  }

  function writeStoredAppToken(token) {
    storageSet(APP_TOKEN_KEY, token || "");
  }

  function readInviteTicket() {
    return storageGet(INVITE_TICKET_KEY);
  }

  function writeInviteTicket(ticket) {
    storageSet(INVITE_TICKET_KEY, ticket || "");
  }

  function readAuthView() {
    const v = storageGet(AUTH_VIEW_KEY);
    return v === "invite" || v === "signup" || v === "signin" ? v : "";
  }

  function writeAuthView(view) {
    state.authView = view;
    storageSet(AUTH_VIEW_KEY, view || "");
  }

  function clearSignupProgress() {
    writeInviteTicket("");
    writeAuthView("signin");
    state.inviteTicket = null;
  }

  async function verifyInviteKey(key) {
    const resp = await fetch("/api/signup/verify-invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const detail = body?.detail || `HTTP ${resp.status}`;
      throw new Error(typeof detail === "string" ? detail : "invalid invite key");
    }
    return body.ticket || "";
  }

  async function refreshInviteTicket(ticket) {
    if (!ticket) return "";
    const resp = await fetch("/api/signup/verify-invite", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ticket }),
    });
    if (!resp.ok) {
      writeInviteTicket("");
      state.inviteTicket = null;
      return "";
    }
    const body = await resp.json().catch(() => ({}));
    return body.ticket || ticket;
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

  /** Stable key for the auth gate — remount only when this changes. */
  function gateRenderKey() {
    if (!state.requiresAuth) return "open";
    if (state.mode === "clerk") {
      if (state.clerk?.user && state.orgId) return "clerk:ready";
      if (state.clerk?.user) return "clerk:need-org";
      // Keep SignUp mounted across Clerk client events during multi-step signup.
      return `clerk:anon:${state.authView}`;
    }
    if (state.requiresAppToken && !state.appToken) return "single:token";
    return "open";
  }

  function clerkComponentOpts(extra) {
    // Hash routing keeps multi-step SignUp/SignIn in-page (no full reload that
    // would wipe invite ticket / remount progress).
    return Object.assign(
      {
        routing: "hash",
        // Stay on this origin after completion; listener advances the gate.
        forceRedirectUrl: window.location.origin + "/",
        fallbackRedirectUrl: window.location.origin + "/",
      },
      extra || {}
    );
  }

  function renderAuthGate({ title, body, showTokenForm, clerkPanel }) {
    const gate = document.getElementById("auth-gate");
    if (!gate) return;
    gate.hidden = false;
    gate.replaceChildren();

    const panel = document.createElement("div");
    panel.className = "auth-gate-panel";
    if (clerkPanel) panel.classList.add("auth-gate-panel-wide");
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
        state._gateKey = null;
        applyAuthGate();
        if (typeof window.__msOnAuthChange === "function") {
          window.__msOnAuthChange();
        }
      });
      panel.append(form);
    }

    if (clerkPanel) {
      panel.append(buildClerkAuthPanel());
    }

    gate.append(panel);
  }

  function buildClerkAuthPanel() {
    const wrap = document.createElement("div");
    wrap.className = "auth-clerk-panel";

    const tabs = document.createElement("div");
    tabs.className = "auth-mode-tabs";
    tabs.setAttribute("role", "tablist");

    const signInTab = document.createElement("button");
    signInTab.type = "button";
    signInTab.className = "auth-mode-tab";
    signInTab.textContent = "Sign in";
    signInTab.setAttribute(
      "aria-selected",
      state.authView === "signin" ? "true" : "false"
    );

    const createTab = document.createElement("button");
    createTab.type = "button";
    createTab.className = "auth-mode-tab";
    createTab.textContent = "Create account";
    createTab.setAttribute(
      "aria-selected",
      state.authView === "signin" ? "false" : "true"
    );

    signInTab.addEventListener("click", () => {
      writeAuthView("signin");
      state._gateKey = null;
      applyAuthGate();
    });
    createTab.addEventListener("click", async () => {
      if (!state.requiresSignupInvite) {
        writeAuthView("signup");
        state._gateKey = null;
        applyAuthGate();
        return;
      }
      createTab.disabled = true;
      try {
        const ok = await ensureInviteTicketForSignup();
        writeAuthView(ok ? "signup" : "invite");
      } finally {
        createTab.disabled = false;
      }
      state._gateKey = null;
      applyAuthGate();
    });
    tabs.append(signInTab, createTab);
    wrap.append(tabs);

    const mount = document.createElement("div");
    mount.id = "auth-gate-clerk";
    wrap.append(mount);

    if (state.authView === "signin") {
      const hideSignupFooter = state.requiresSignupInvite
        ? { elements: { footerAction: { display: "none" } } }
        : undefined;
      queueMicrotask(() => {
        if (!state.clerk) return;
        state.clerk.mountSignIn(
          mount,
          clerkComponentOpts({ appearance: hideSignupFooter })
        );
      });
      return wrap;
    }

    if (state.authView === "invite") {
      const form = document.createElement("form");
      form.className = "auth-token-form";
      form.setAttribute("autocomplete", "off");
      const label = document.createElement("label");
      label.textContent = "Invite / access key";
      const input = document.createElement("input");
      input.type = "password";
      input.name = "signup_invite";
      input.placeholder = "SIGNUP_INVITE_KEY";
      input.required = true;
      label.append(input);
      const btn = document.createElement("button");
      btn.type = "submit";
      btn.textContent = "Continue to create account";
      const err = document.createElement("p");
      err.className = "auth-gate-error";
      err.hidden = true;
      const hint = document.createElement("p");
      hint.className = "auth-gate-lede";
      hint.textContent =
        "Ask William for the invite key, then create your account with email + password.";
      form.append(hint, label, btn, err);
      form.addEventListener("submit", async (evt) => {
        evt.preventDefault();
        err.hidden = true;
        const key = (input.value || "").trim();
        if (!key) return;
        btn.disabled = true;
        try {
          const ticket = await verifyInviteKey(key);
          state.inviteTicket = ticket;
          writeInviteTicket(ticket);
          writeAuthView("signup");
          state._gateKey = null;
          applyAuthGate();
        } catch (e) {
          err.hidden = false;
          err.textContent = e?.message || "Invalid invite key";
        } finally {
          btn.disabled = false;
        }
      });
      mount.append(form);
      return wrap;
    }

    // signup — invite already verified (or not required)
    const note = document.createElement("p");
    note.className = "auth-gate-lede";
    note.textContent = state.requiresSignupInvite
      ? "Invite verified. Create your account with email and password — stay on this page through confirm."
      : "Create your account with email and password.";
    mount.append(note);

    const mountSignUpNow = () => {
      if (!state.clerk) return;
      // Empty the note-only mount node before Clerk owns it? Keep note as sibling.
      const host = document.createElement("div");
      host.id = "auth-gate-clerk-signup";
      mount.append(host);
      state.clerk.mountSignUp(host, clerkComponentOpts({}));
    };

    if (!state.requiresSignupInvite) {
      queueMicrotask(mountSignUpNow);
      return wrap;
    }

    queueMicrotask(async () => {
      const ok = await ensureInviteTicketForSignup();
      if (!ok) {
        writeAuthView("invite");
        state._gateKey = null;
        applyAuthGate();
        return;
      }
      mountSignUpNow();
    });
    return wrap;
  }

  async function ensureInviteTicketForSignup() {
    if (!state.requiresSignupInvite) return true;
    let ticket = state.inviteTicket || readInviteTicket();
    if (!ticket) return false;
    ticket = await refreshInviteTicket(ticket);
    if (!ticket) return false;
    state.inviteTicket = ticket;
    writeInviteTicket(ticket);
    return true;
  }

  function applyAuthGate(opts) {
    const force = Boolean(opts && opts.force);
    state.authenticated = isAuthenticated();
    const gate = document.getElementById("auth-gate");
    const key = gateRenderKey();

    // Skip tear-down/remount when the gate state is unchanged (critical during
    // Clerk SignUp password → confirm steps — listener fires often).
    if (!force && key === state._gateKey) {
      if (key === "clerk:ready" || key === "open") {
        setShellVisible(true);
        if (gate) gate.hidden = true;
      } else if (key.startsWith("clerk:") || key === "single:token") {
        setShellVisible(false);
        if (gate) gate.hidden = false;
      }
      return state.authenticated;
    }
    state._gateKey = key;

    if (!state.requiresAuth) {
      if (gate) gate.hidden = true;
      setShellVisible(true);
      return state.authenticated;
    }

    if (state.mode === "clerk") {
      if (state.clerk?.user && state.orgId) {
        clearSignupProgress();
        if (gate) gate.hidden = true;
        setShellVisible(true);
        return true;
      }
      if (state.clerk?.user) {
        // Signed in but no org — invite no longer needed.
        setShellVisible(false);
        renderAuthGate({
          title: "Select an organization",
          body: "Create or switch to an organization in the header to load your private feed.",
        });
        return false;
      }

      setShellVisible(false);
      if (
        state.authView === "signup" &&
        state.requiresSignupInvite &&
        !(state.inviteTicket || readInviteTicket())
      ) {
        writeAuthView("invite");
        state._gateKey = gateRenderKey();
      }
      renderAuthGate({
        title: "Sign in to EC Signals",
        body: state.requiresSignupInvite
          ? "Sign in with an existing account, or create one with an invite key plus email and password."
          : "A Clerk account and organization are required to view signals, votes, and broker sims.",
        clerkPanel: true,
      });
      return false;
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

  function restoreAuthViewFromStorage() {
    const saved = readAuthView();
    const ticket = readInviteTicket();
    if (ticket && saved !== "signin") {
      // Resume Create account after reload mid SignUp (password → confirm).
      state.inviteTicket = ticket;
      writeAuthView("signup");
      return;
    }
    if (saved) {
      writeAuthView(saved);
      return;
    }
    writeAuthView("signin");
  }

  async function init() {
    const cfg = await loadConfig();
    state.mode = cfg.auth_mode || "single";
    state.requiresAuth = Boolean(cfg.requires_auth);
    state.requiresAppToken = Boolean(cfg.requires_app_token);
    state.requiresSignupInvite = Boolean(cfg.requires_signup_invite);
    state.appToken = state.requiresAppToken ? readStoredAppToken() : null;
    state.inviteTicket = state.requiresSignupInvite ? readInviteTicket() : null;
    restoreAuthViewFromStorage();
    state._gateKey = null;
    window.__msAuth = state;

    const singleUser = document.querySelector("label.user");
    if (state.mode !== "clerk") {
      if (singleUser) singleUser.hidden = false;
      applyAuthGate({ force: true });
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
    applyAuthGate({ force: true });
    state.ready = true;

    clerk.addListener(async () => {
      const prevUser = state.userId;
      const prevOrg = state.orgId;
      await refreshSession(clerk);
      // Only remount chrome when identity changes — not on every SignUp tick.
      if (state.userId !== prevUser || state.orgId !== prevOrg) {
        mountChrome(clerk);
      }
      applyAuthGate();
      if (
        (state.userId !== prevUser || state.orgId !== prevOrg) &&
        typeof window.__msOnAuthChange === "function"
      ) {
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
