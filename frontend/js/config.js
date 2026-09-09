/**
 * Runtime configuration for the CyclotorsionCheck SPA.
 *
 * IMPORTANT: The Firebase web app config below must be replaced with the values
 * from your own Firebase project (Project settings -> General -> Your apps ->
 * Web app). It is NOT a secret — Firebase web config is intentionally public —
 * but it must point at *your* project for sign-in and hosting to work.
 *
 * The API base URL points at the Cloud Run / local FastAPI backend. When auth
 * is enforced (CC_AUTH_ENABLED=true), the SPA sends the Firebase ID token as a
 * Bearer token to /detect and /stats.
 */

// Local dev serves this same index.html/config.js from localhost/127.0.0.1
// (python -m http.server, per frontend/README.md); anything else (Firebase
// Hosting's *.web.app, or a custom domain) is production. Deriving this from
// the page's own origin — rather than hardcoding the Cloud Run URL here or in
// index.html — means the one shared file works correctly in both places
// without a build step, matching the project's framework-free, no-build design.
const IS_LOCAL_DEV = ["localhost", "127.0.0.1"].includes(window.location.hostname);
const PROD_API_BASE = "https://cyclotorsion-check-api-949330221093.us-central1.run.app";

export const CONFIG = {
  // Backend base URL. window.CYCLOTORSION_API_BASE (set before this module
  // loads) always wins; otherwise local dev talks to the local FastAPI
  // server and every other origin talks to the deployed Cloud Run service.
  API_BASE:
    window.CYCLOTORSION_API_BASE || (IS_LOCAL_DEV ? "http://127.0.0.1:8080" : PROD_API_BASE),

  // Timeout for a /detect request, in ms (PRD Section 6.3: 30 seconds).
  DETECT_TIMEOUT_MS: 30_000,

  // Max accepted upload size on the client (PRD US-1.1: 10 MB).
  MAX_IMAGE_BYTES: 10 * 1024 * 1024,

  // Allowed image MIME types (must match the backend allow-list).
  ALLOWED_MIME_TYPES: ["image/jpeg", "image/png", "image/webp"],
};

/**
 * Firebase web app configuration. Replace these fields with your project's
 * values. This is a placeholder used by js/auth.js to initialize the SDK.
 */
export const FIREBASE_CONFIG = {
  apiKey: "AIzaSyC397JZRrSMX3EPou90y_68N1bElHVKbBo",
  authDomain: "patchamomma-2026-bigquery-lab.firebaseapp.com",
  projectId: "patchamomma-2026-bigquery-lab",
  appId: "1:949330221093:web:eee17dfdab50b7ab6e87cc",
};

/** True once the placeholder config has been replaced. */
export function isFirebaseConfigured() {
  return FIREBASE_CONFIG.apiKey !== "YOUR_API_KEY" && !FIREBASE_CONFIG.apiKey.includes("YOUR_");
}
