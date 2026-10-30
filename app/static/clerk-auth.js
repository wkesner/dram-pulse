/**
 * Clerk / app-token browser helpers.
 * AUTH_MODE=clerk → session JWT + org required for the whole UI.
 * AUTH_MODE=single + APP_ACCESS_TOKEN → shared secret (Bearer / X-App-Token).
 *
 * Signup invite: SIGNUP_INVITE_KEY verified once. Critical UX rule:
 * once SignUp is mounted, NEVER tear it down until Clerk session is active
 * (clerk.user alone is NOT enough — it appears mid-flow after password/confirm
 * and was remounting users back into Create account).
 */
(() => {
  // Bump when shipping signup UX fixes — verify on Fly via:
  //   curl -s https://ec-signals.fly.dev/clerk-auth.js | grep __msClerkBuild
  window.__msClerkBuild = "signup-virtual-v3-20261030";

  /** Clerk path segments that must stay on-origin (never 404 → Account Portal). */
  const CLERK_AUTH_PATH_RE =
    /^\/(verify-email-address|verify-phone-number|continue|factor-one|factor-two|reset-password|sso-callback)\/?$/;

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
    _gateKey: null,
    /** True after mountSignUp succeeds — blocks remount until session active. */
    _signUpMounted: false,
    _signInMounted: false,
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
      // Keep SignIn/SignUp on this origin — never bounce to *.accounts.dev mid-flow.
      signInUrl: "/",
      signUpUrl: "/",
    });
    return clerk;
  }

  /** Path or hash that means we are mid Clerk email/phone verify. */
  function clerkAuthRouteActive() {
    try {
      const path = (location.pathname || "/").replace(/\/+$/, "") || "/";
      if (CLERK_AUTH_PATH_RE.test(path)) return true;
      const hash = String(location.hash || "");
      if (/verify-email-address|verify-phone-number|factor-one|factor-two/i.test(hash)) {
        return true;
      }
    } catch {
      /* ignore */
    }
    return false;
  }

  /**
   * Video bug: SignUp used path routing → /verify-email-address → Fly 404 →
   * Clerk Account Portal (accounts.dev). Collapse that path back to / and
   * keep the signup gate open so OTP stays embedded.
   */
  function recoverClerkAuthPathIntoSignup() {
    if (!clerkAuthRouteActive()) return false;
    try {
      const path = (location.pathname || "/").replace(/\/+$/, "") || "/";
      if (CLERK_AUTH_PATH_RE.test(path)) {
        history.replaceState(null, "", "/" + (location.search || "") + (location.hash || ""));
      }
    } catch {
      /* ignore */
    }
    writeAuthView("signup");
    return true;
  }

  function sessionIsActive(clerk) {
    const session = clerk?.session;
    if (!session) return false;
    const st = String(session.status || "").toLowerCase();
    // Clerk: "active" means usable; pending/expired must not eject SignUp.
    return st === "active";
  }

  function signUpStillOpen(clerk) {
    try {
      const su = clerk?.client?.signUp;
      if (!su) return false;
      const st = String(su.status || "").toLowerCase();
      if (!st || st === "complete" || st === "abandoned") return false;
      return true; // missing_requirements | etc.
    } catch {
      return false;
    }
  }

  /**
   * Leave the Create-account / SignUp UI only when signup is truly done:
   * active session (not merely clerk.user, which appears mid password→confirm).
   */
  function signupFlowFinished(clerk) {
    if (!clerk) return false;
    if (signUpStillOpen(clerk)) return false;
    if (!sessionIsActive(clerk)) return false;
    if (!clerk.user) return false;
    return true;
  }

  async function refreshSession(clerk) {
    if (!sessionIsActive(clerk)) {
      // Keep prior userId during incomplete signup so we don't thrash chrome;
      // gate phase uses signupFlowFinished, not userId alone.
      if (!state._signUpMounted) {
        state.sessionToken = null;
        state.orgId = null;
        state.userId = null;
        state.displayName = "";
      } else {
        state.sessionToken = null;
        state.orgId = null;
      }
      return null;
    }
    const token = await clerk.session.getToken();
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
    state._signUpMounted = false;
    state._signInMounted = false;
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
      return Boolean(state.sessionToken && state.orgId && signupFlowFinished(state.clerk));
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

  /** Phase key — must NOT flip when clerk.user appears mid-SignUp. */
  function gateRenderKey() {
    if (!state.requiresAuth) return "open";
    if (state.mode === "clerk") {
      const finished = signupFlowFinished(state.clerk);
      if (finished && state.orgId) return "clerk:ready";
      if (finished) return "clerk:need-org";
      // Stick to signup only when we intentionally opened it (or mounted).
      // Do NOT key off signUpStillOpen alone — that trapped Sign-in tab (Bugbot).
      if (state.authView === "signup" || state._signUpMounted) {
        return "clerk:anon:signup";
      }
      return `clerk:anon:${state.authView}`;
    }
    if (state.requiresAppToken && !state.appToken) return "single:token";
    return "open";
  }

  function clerkComponentOpts(extra) {
    // Virtual routing keeps verify-email OTP in-memory — no /verify-email-address
    // path (Fly 404) and no #hash that Account Portal can intercept.
    // Do NOT set forceRedirectUrl / *ForceRedirectUrl (hard-nav mid-flow).
    return Object.assign(
      {
        routing: "virtual",
        signInUrl: "/",
        signUpUrl: "/",
      },
      extra || {}
    );
  }

  function ensureGateShell(title, body) {
    const gate = document.getElementById("auth-gate");
    if (!gate) return null;
    gate.hidden = false;

    let panel = gate.querySelector(".auth-gate-panel");
    if (!panel) {
      gate.replaceChildren();
      panel = document.createElement("div");
      panel.className = "auth-gate-panel auth-gate-panel-wide";
      gate.append(panel);
    }

    let h = panel.querySelector(":scope > h2.auth-gate-title");
    if (!h) {
      h = document.createElement("h2");
      h.className = "auth-gate-title";
      panel.prepend(h);
    }
    h.textContent = title;

    let p = panel.querySelector(":scope > p.auth-gate-lede-main");
    if (!p) {
      p = document.createElement("p");
      p.className = "auth-gate-lede auth-gate-lede-main";
      h.insertAdjacentElement("afterend", p);
    }
    p.textContent = body;

    let wrap = panel.querySelector(".auth-clerk-panel");
    if (!wrap) {
      wrap = document.createElement("div");
      wrap.className = "auth-clerk-panel";
      panel.append(wrap);
    }
    return { gate, panel, wrap };
  }

  function ensureTabs(wrap) {
    let tabs = wrap.querySelector(".auth-mode-tabs");
    if (tabs) {
      // Update aria-selected only — do not rebuild (keeps listeners).
      const buttons = tabs.querySelectorAll(".auth-mode-tab");
      if (buttons[0]) {
        buttons[0].setAttribute(
          "aria-selected",
          state.authView === "signin" ? "true" : "false"
        );
      }
      if (buttons[1]) {
        buttons[1].setAttribute(
          "aria-selected",
          state.authView === "signin" ? "false" : "true"
        );
      }
      return tabs;
    }

    tabs = document.createElement("div");
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
      if (state._signUpMounted && !signupFlowFinished(state.clerk)) {
        // Allow abandoning signup explicitly.
        try {
          state.clerk?.unmountSignUp?.();
        } catch {
          /* ignore */
        }
        state._signUpMounted = false;
      }
      state._signInMounted = false;
      writeAuthView("signin");
      state._gateKey = null;
      applyAuthGate({ force: true });
    });

    createTab.addEventListener("click", async () => {
      if (!state.requiresSignupInvite) {
        writeAuthView("signup");
        state._gateKey = null;
        applyAuthGate({ force: true });
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
      applyAuthGate({ force: true });
    });

    tabs.append(signInTab, createTab);
    wrap.prepend(tabs);
    return tabs;
  }

  function ensureClerkMount(wrap) {
    let mount = wrap.querySelector("#auth-gate-clerk");
    if (!mount) {
      mount = document.createElement("div");
      mount.id = "auth-gate-clerk";
      wrap.append(mount);
    }
    return mount;
  }

  function renderInviteForm(mount) {
    // Only rebuild invite form if not already present.
    if (mount.querySelector("form.auth-token-form[data-invite]")) return;

    mount.replaceChildren();
    state._signUpMounted = false;
    state._signInMounted = false;

    const form = document.createElement("form");
    form.className = "auth-token-form";
    form.dataset.invite = "1";
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
        applyAuthGate({ force: true });
      } catch (e) {
        err.hidden = false;
        err.textContent = e?.message || "Invalid invite key";
      } finally {
        btn.disabled = false;
      }
    });
    mount.append(form);
  }

  function mountSignInOnce(mount) {
    if (state._signInMounted && mount.querySelector("[data-clerk-signin]")) {
      return;
    }
    mount.replaceChildren();
    state._signUpMounted = false;
    const host = document.createElement("div");
    host.dataset.clerkSignin = "1";
    mount.append(host);
    const hideSignupFooter = state.requiresSignupInvite
      ? { elements: { footerAction: { display: "none" } } }
      : undefined;
    if (state.clerk) {
      state.clerk.mountSignIn(
        host,
        clerkComponentOpts({ appearance: hideSignupFooter })
      );
      state._signInMounted = true;
    }
  }

  async function mountSignUpOnce(mount) {
    if (state._signUpMounted && mount.querySelector("[data-clerk-signup]")) {
      return; // NEVER remount — this was the live Fly bug after password/confirm.
    }

    if (state.requiresSignupInvite) {
      const ok = await ensureInviteTicketForSignup();
      if (!ok) {
        writeAuthView("invite");
        state._signUpMounted = false;
        state._gateKey = null;
        applyAuthGate({ force: true });
        return;
      }
    }

    mount.replaceChildren();
    state._signInMounted = false;

    const note = document.createElement("p");
    note.className = "auth-gate-lede";
    note.textContent = state.requiresSignupInvite
      ? "Invite verified. Create your account with email and password — stay on this page through confirm."
      : "Create your account with email and password.";
    mount.append(note);

    const host = document.createElement("div");
    host.dataset.clerkSignup = "1";
    host.id = "auth-gate-clerk-signup";
    mount.append(host);

    if (state.clerk) {
      state.clerk.mountSignUp(host, clerkComponentOpts({}));
      state._signUpMounted = true;
      writeAuthView("signup");
    }
  }

  function renderTokenGate() {
    const gate = document.getElementById("auth-gate");
    if (!gate) return;
    gate.hidden = false;
    gate.replaceChildren();
    const panel = document.createElement("div");
    panel.className = "auth-gate-panel";
    const h = document.createElement("h2");
    h.textContent = "Enter access token";
    const p = document.createElement("p");
    p.className = "auth-gate-lede";
    p.textContent =
      "This deployment requires APP_ACCESS_TOKEN. Paste the shared secret to continue.";
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
      applyAuthGate({ force: true });
      if (typeof window.__msOnAuthChange === "function") {
        window.__msOnAuthChange();
      }
    });
    panel.append(h, p, form);
    gate.append(panel);
  }

  function renderOrgPickerOnly() {
    const gate = document.getElementById("auth-gate");
    if (!gate) return;
    gate.hidden = false;
    // Soft update: don't destroy chrome if already showing org message.
    if (gate.querySelector("[data-org-picker]")) return;
    gate.replaceChildren();
    state._signUpMounted = false;
    state._signInMounted = false;
    const panel = document.createElement("div");
    panel.className = "auth-gate-panel";
    panel.dataset.orgPicker = "1";
    const h = document.createElement("h2");
    h.textContent = "Select an organization";
    const p = document.createElement("p");
    p.className = "auth-gate-lede";
    p.textContent =
      "Create or switch to an organization in the header to load your private feed.";
    panel.append(h, p);
    gate.append(panel);
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

    if (!force && key === state._gateKey) {
      if (key === "clerk:ready" || key === "open") {
        setShellVisible(true);
        if (gate) gate.hidden = true;
      } else {
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
      if (key === "clerk:ready") {
        clearSignupProgress();
        if (gate) gate.hidden = true;
        setShellVisible(true);
        return true;
      }
      if (key === "clerk:need-org") {
        setShellVisible(false);
        renderOrgPickerOnly();
        return false;
      }

      setShellVisible(false);
      const title = "Sign in to EC Signals";
      const body = state.requiresSignupInvite
        ? "Sign in with an existing account, or create one with an invite key plus email and password."
        : "A Clerk account and organization are required to view signals, votes, and broker sims.";

      // Resolve effective anon view from key
      let view = state.authView;
      if (key === "clerk:anon:signup") view = "signup";
      else if (key.endsWith(":invite")) view = "invite";
      else if (key.endsWith(":signin")) view = "signin";

      // If signup claimed but no ticket, fall back to invite once.
      if (
        view === "signup" &&
        state.requiresSignupInvite &&
        !state._signUpMounted &&
        !(state.inviteTicket || readInviteTicket())
      ) {
        view = "invite";
        writeAuthView("invite");
      }

      const shell = ensureGateShell(title, body);
      if (!shell) return false;
      ensureTabs(shell.wrap);
      const mount = ensureClerkMount(shell.wrap);

      if (view === "signin") {
        mountSignInOnce(mount);
      } else if (view === "invite") {
        renderInviteForm(mount);
      } else {
        // Fire-and-forget; mountSignUpOnce is idempotent.
        mountSignUpOnce(mount);
      }
      return false;
    }

    if (state.requiresAppToken && !state.appToken) {
      setShellVisible(false);
      renderTokenGate();
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

    // Avoid thrashing UserButton during signup.
    if (!signupFlowFinished(clerk)) {
      if (!clerk.user) {
        host.replaceChildren();
        host.append(document.createElement("div")).className = "clerk-row";
      }
      return;
    }

    host.replaceChildren();
    const row = document.createElement("div");
    row.className = "clerk-row";
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
    if (recoverClerkAuthPathIntoSignup()) {
      const ticket = readInviteTicket();
      if (ticket) state.inviteTicket = ticket;
      return;
    }
    const saved = readAuthView();
    const ticket = readInviteTicket();
    if (ticket && saved !== "signin") {
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
    state._signUpMounted = false;
    state._signInMounted = false;
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
      const gate = document.getElementById("auth-gate");
      if (gate) {
        gate.hidden = false;
        gate.innerHTML =
          '<div class="auth-gate-panel"><h2>Clerk not configured</h2><p class="auth-gate-lede">Set CLERK_PUBLISHABLE_KEY / CLERK_SECRET_KEY and restart the server.</p></div>';
      }
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

    let prevFinished = signupFlowFinished(clerk);
    let prevOrgId = state.orgId;
    clerk.addListener(async () => {
      // If Clerk slipped onto /verify-email-address, pull back before gate runs.
      if (clerkAuthRouteActive() && !signupFlowFinished(clerk)) {
        recoverClerkAuthPathIntoSignup();
      }

      await refreshSession(clerk);
      const nowFinished = signupFlowFinished(clerk);
      const nowOrgId = state.orgId;

      // Remount chrome only when signup completes or org changes — not every
      // mid-SignUp clerk.user flicker (that remounted Create-account on Fly).
      if (
        nowFinished &&
        (!prevFinished || nowOrgId !== prevOrgId)
      ) {
        mountChrome(clerk);
      }

      applyAuthGate();

      if (
        nowFinished &&
        !prevFinished &&
        typeof window.__msOnAuthChange === "function"
      ) {
        window.__msOnAuthChange();
      } else if (
        nowFinished &&
        nowOrgId &&
        nowOrgId !== prevOrgId &&
        typeof window.__msOnAuthChange === "function"
      ) {
        window.__msOnAuthChange();
      }

      prevFinished = nowFinished;
      prevOrgId = nowOrgId;
    });

    if (signupFlowFinished(clerk) && clerk.organization && state.sessionToken) {
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
    signupFlowFinished,
  };
})();
