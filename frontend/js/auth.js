/**
 * Firebase Authentication integration for the SPA (PRD Section 5.4, TDD 5.1).
 *
 * Uses Email/Password auth via the Firebase JS SDK. Exposes:
 *   - initFirebaseAuth(): initialize the SDK (idempotent).
 *   - onAuthStateChange(cb): subscribe to sign-in state.
 *   - getCurrentUserToken(): the current ID token, or null when signed out.
 *   - signIn / createAccount / signOut helpers.
 *
 * If the Firebase config placeholders are not yet filled in, the app shows a
 * clear setup notice rather than failing cryptically.
 */

import { FIREBASE_CONFIG, isFirebaseConfigured } from "./config.js";

let app = null;
let auth = null;

/** Whether the Firebase SDK has been initialised successfully. */
let firebaseWorks = false;

export function isFirebaseReady() {
  return isFirebaseConfigured() && firebaseWorks;
}

export async function initFirebaseAuth() {
  if (app) return app;
  if (!isFirebaseConfigured()) {
    // Leave app null; router shows a setup banner.
    return null;
  }
  try {
    const { getApps, initializeApp } =
      await import("https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js");
    const { getAuth } = await import("https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js");
    const existing = getApps();
    if (existing.length > 0) {
      app = existing[0];
    } else {
      app = initializeApp(FIREBASE_CONFIG);
    }
    auth = getAuth(app);
    firebaseWorks = true;
    return app;
  } catch (err) {
    console.error("Firebase auth init failed:", err);
    firebaseWorks = false;
    return null;
  }
}

/** Wait for the auth SDK to be ready (for callers that run before init). */
async function ensureAuth() {
  await initFirebaseAuth();
  if (!auth) {
    throw new Error("Firebase Authentication is not configured for this deployment.");
  }
  return auth;
}

/**
 * Subscribe to auth-state changes. `cb(isSignedIn)` fires on every change.
 * Returns an unsubscribe function.
 */
export async function onAuthStateChange(cb) {
  const a = await ensureAuth();
  return a.onAuthStateChanged((user) => cb(Boolean(user)));
}

/** The current user's Firebase ID token, or null when signed out. */
export async function getCurrentUserToken() {
  const a = await ensureAuth();
  const user = a.currentUser;
  if (!user) return null;
  try {
    return await user.getIdToken(false);
  } catch {
    return null;
  }
}

/** The current user's display/email, or null. */
export async function getCurrentUserInfo() {
  const a = await ensureAuth();
  const user = a.currentUser;
  if (!user) return null;
  return { uid: user.uid, email: user.email, displayName: user.displayName };
}

export async function signIn(email, password) {
  const a = await ensureAuth();
  const { signInWithEmailAndPassword } =
    await import("https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js");
  return signInWithEmailAndPassword(a, email, password);
}

export async function createAccount(email, password) {
  const a = await ensureAuth();
  const { createUserWithEmailAndPassword } =
    await import("https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js");
  return createUserWithEmailAndPassword(a, email, password);
}

export async function signOut() {
  const a = await ensureAuth();
  const { signOut: fbSignOut } =
    await import("https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js");
  return fbSignOut(a);
}
