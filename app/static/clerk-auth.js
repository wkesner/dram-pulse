/**
 * Clerk browser helpers for AUTH_MODE=clerk.
 * Loads @clerk/clerk-js when a publishable key is present.
 */
(() => {
  const state = {
    mode: "single",
    ready: false,
    clerk: null,
    sessionToken: null,
    orgId: null,
    userId: null,
    displayName: "",
  };

  async function loadConfig() {
    const resp = await fetch("/api/config");
    if (!resp.ok) throw new Error("config " + resp.status);
    return resp.json();
  }

  function loadScript(src) {
    return new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = src;
      s.async = true;
      s.onload = () => resolve();
      s.onerror = () => reject(new Error("failed to load " + src));
      document.head.appendChild(s);
    });
  }

  async function ensureClerk(publishableKey) {
    if (window.Clerk && window.Clerk.loaded) return window.Clerk;
    if (!window.Clerk) {
      await loadScript(
        "https://cdn.jsdelivr.net/npm/@clerk/clerk-js@5/dist/clerk.browser.js"
      );
    }
    const clerk = window.Clerk;
    if (!clerk) throw new Error("Clerk global missing");
    await clerk.load({ publishableKey });
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

  function authHeaders() {
    if (state.mode !== "clerk" || !state.sessionToken) return {};
    return { Authorization: "Bearer " + state.sessionToken };
  }

  function mountChrome(clerk) {
    const host = document.getElementById("clerk-chrome");
    if (!host) return;
    host.hidden = false;
    host.replaceChildren();

    const row = document.createElement("div");
    row.className = "clerk-row";

    if (!clerk.user) {
      const signIn = document.createElement("div");
      signIn.id = "clerk-sign-in";
      row.append(signIn);
      host.append(row);
      clerk.mountSignIn(signIn);
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
        : "Create or select an organization to load your private signal feed.";
    }
  }

  async function init() {
    const cfg = await loadConfig();
    state.mode = cfg.auth_mode || "single";
    window.__msAuth = state;

    const singleUser = document.querySelector("label.user");
    if (state.mode !== "clerk") {
      if (singleUser) singleUser.hidden = false;
      return { mode: "single" };
    }

    if (singleUser) singleUser.hidden = true;
    const banner = document.getElementById("clerk-setup-banner");

    if (!cfg.clerk_configured || !cfg.clerk_publishable_key) {
      if (banner) {
        banner.hidden = false;
        banner.textContent =
          "Clerk mode is on, but keys are missing. Add CLERK_PUBLISHABLE_KEY, CLERK_SECRET_KEY, and CLERK_ISSUER to .env (see .env.example), enable Organizations in the Clerk dashboard, then restart uvicorn.";
      }
      return { mode: "clerk", configured: false };
    }
    if (banner) banner.hidden = true;

    const clerk = await ensureClerk(cfg.clerk_publishable_key);
    state.clerk = clerk;
    await refreshSession(clerk);
    mountChrome(clerk);
    state.ready = true;

    clerk.addListener(async () => {
      await refreshSession(clerk);
      mountChrome(clerk);
      if (typeof window.__msOnAuthChange === "function") {
        window.__msOnAuthChange();
      }
    });

    if (clerk.organization) {
      try {
        await fetch("/api/orgs/bootstrap", {
          method: "POST",
          headers: { ...authHeaders() },
        });
      } catch (err) {
        console.warn("org bootstrap", err);
      }
    }

    return { mode: "clerk", configured: true, orgId: state.orgId };
  }

  window.msClerk = { init, authHeaders, state, refreshSession };
})();
