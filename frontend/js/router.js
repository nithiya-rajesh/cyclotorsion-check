/**
 * Router + app shell (auth-gated, hash-based).
 *
 * Responsibilities:
 *   - Subscribe to Firebase auth state.
 *   - Show the login screen when signed out; the app shell (nav + views) when
 *     signed in.
 *   - Route between Analyze / History / Insights via the URL hash.
 *   - Manage the non-dismissible decision-support disclaimer banner (US-3.2).
 */

import { initFirebaseAuth, isFirebaseReady, onAuthStateChange, signOut } from "./auth.js";
import { isFirebaseConfigured } from "./config.js";
import { renderLogin } from "./pages/login.js";
import { renderAnalyze } from "./pages/analyze.js";
import { renderHistory } from "./pages/history.js";
import { renderInsights } from "./pages/insights.js";
import { renderPatients } from "./pages/patients.js";
import { clearSession } from "./session.js";
import { el } from "./common.js";

const ROUTES = {
  analyze: renderAnalyze,
  history: renderHistory,
  insights: renderInsights,
  patients: renderPatients,
};

let signedIn = false;
let currentView = null;

const view = document.getElementById("view");
const nav = document.getElementById("main-nav");
const navToggle = document.getElementById("nav-toggle");
const banner = document.getElementById("disclaimer-banner");
const logoutBtn = document.getElementById("logout-btn");

function closeMobileNav() {
  nav.classList.remove("open");
  navToggle.setAttribute("aria-expanded", "false");
}

function render() {
  if (!signedIn) {
    // Signed out: show login, hide nav + disclaimer.
    nav.hidden = true;
    navToggle.hidden = true;
    banner.hidden = true;
    closeMobileNav();
    renderLogin(view);
    return;
  }

  // Signed in: show nav + disclaimer.
  nav.hidden = false;
  navToggle.hidden = false;
  banner.hidden = false;
  closeMobileNav();

  const route = hashRoute();
  const renderFn = ROUTES[route] || ROUTES.analyze;
  if (currentView !== route) {
    renderFn(view, hashSubpath());
    currentView = route;
  }

  // Highlight the active nav link.
  document.querySelectorAll(".nav-link[data-route]").forEach((link) => {
    const active = link.getAttribute("data-route") === route;
    link.classList.toggle("active", active);
  });
}

function hashRoute() {
  const h = window.location.hash.replace(/^#\/?/, "") || "analyze";
  return ROUTES[h] ? h : "analyze";
}

/** Sub-state after the route segment, e.g. a selected patient id in
    "#/patients/<id>". Keeps deep links shareable/refreshable (guideline
    "URL as state"). */
function hashSubpath() {
  const h = window.location.hash.replace(/^#\/?/, "");
  if (!h) return "";
  const slash = h.indexOf("/");
  if (slash === -1) return "";
  return h.slice(slash + 1);
}

/** Navigate to a route, optionally with a subpath ("#/route/<sub>"). */
function navigate(route, sub = "") {
  window.location.hash = sub ? `/${route}/${sub}` : `/${route}`;
  if (hashRoute() === route) {
    // If already on that hash, re-render directly.
    currentView = null;
    render();
  }
}

async function start() {
  // News nav click routing.
  nav.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-route]");
    if (btn) {
      navigate(btn.getAttribute("data-route"));
      closeMobileNav();
    }
  });

  navToggle.addEventListener("click", () => {
    const open = nav.classList.toggle("open");
    navToggle.setAttribute("aria-expanded", String(open));
  });

  logoutBtn.addEventListener("click", async () => {
    try {
      await signOut();
    } catch (err) {
      console.error("Logout failed:", err);
    }
    clearSession();
    signedIn = false;
    currentView = null;
    render();
  });

  window.addEventListener("hashchange", () => {
    if (signedIn) {
      currentView = null;
      render();
    }
  });

  // Handle unconfigured Firebase gracefully.
  await initFirebaseAuth();
  if (!isFirebaseConfigured()) {
    renderSetupBanner();
    return;
  }
  if (!isFirebaseReady()) {
    renderErrorBanner();
    return;
  }

  await onAuthStateChange((isSignedIn) => {
    if (!isSignedIn) {
      if (signedIn) {
        // Signed out at some point after being signed in.
        clearSession();
      }
      signedIn = false;
    } else {
      signedIn = true;
    }
    currentView = null;
    render();
  });
}

function renderSetupBanner() {
  view.innerHTML = "";
  view.appendChild(
    el("div", { class: "setup-banner" }, [
      el("h2", { text: "CyclotorsionCheck is not configured yet." }),
      el("p", {
        text: "Fill in your Firebase web app configuration in frontend/js/config.js to enable sign-in.",
      }),
    ]),
  );
}

function renderErrorBanner() {
  view.innerHTML = "";
  view.appendChild(
    el("div", { class: "setup-banner error" }, [
      el("h2", { text: "Could not initialise Firebase Authentication." }),
      el("p", {
        text: "Check your network connection and that the Firebase SDK is reachable, then reload.",
      }),
    ]),
  );
}

start();
