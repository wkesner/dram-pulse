/**
 * Clerk browser helpers for AUTH_MODE=clerk.
 * Aligns with Clerk JS CDN quickstart (clerk-js@6 + @clerk/ui):
 * https://clerk.com/docs/js-frontend/getting-started/quickstart
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
    // Organizations: switcher next to UserButton (Clerk SKILL "After setup")
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
          "Clerk mode is on, but keys are missing. Run: npx clerk@latest auth login && npx clerk@latest link --app <id> && npx clerk@latest env pull --file .env — then set AUTH_MODE=clerk and restart uvicorn.";
      }
      return { mode: "clerk", configured: false };
    }
    if (banner) banner.hidden = true;

    const clerk = await ensureClerk(
      cfg.clerk_publishable_key,
      cfg.clerk_issuer || ""
    );
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
