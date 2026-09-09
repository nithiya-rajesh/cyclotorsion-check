# CyclotorsionCheck SPA (frontend)

A framework-free, build-free single-page application hosted on **Firebase
Hosting** (see `firebase.json`). It contains **zero clinical business logic** —
all angle calculation and safety validation happens server-side in the FastAPI
backend (TDD Section 2.2). The SPA only handles: authentication, image
capture/upload and preview, a non-blocking client-side quality heuristic, and
rendering the server's result.

## Screens

| Route | Purpose | PRD refs |
|---|---|---|
| Login/Register | Email/Password Firebase Authentication | §5.4 |
| Analyze | Capture/upload upright + supine photos, preview, quality warning, calculate | Epics 1–3 |
| History | Current-session test history (session-scoped only) | US-4.1 |
| Insights | Session summary + all-time aggregate from `/stats` | US-4.1, 4.2, 4.3 |

## Setup (one-time)

1. Open `frontend/js/config.js` and replace the `FIREBASE_CONFIG` placeholders
   with your Firebase web app config (Project settings → General → Your apps →
   Web app). This config is public — it is not a secret.
2. If the backend enforces auth (`CC_AUTH_ENABLED=true`), ensure the SPA sends
   the Firebase ID token. It does this automatically via the
   `Authorization: Bearer <token>` header (see `js/api.js`).
3. Set `window.CYCLOTORSION_API_BASE` before the app loads, or edit
   `CONFIG.API_BASE` in `js/config.js`, to point at your backend (local
   `http://127.0.0.1:8080` by default).

## Run locally

Serve the static files (no build step). From inside `frontend/`:

```sh
npm run serve        # or: python -m http.server 8090
```

Or from the repository root:

```sh
python -m http.server 8090 --directory frontend
```

Point a browser at `http://localhost:8090`. ES modules are loaded directly, so
communication with the backend must be CORS-enabled (the backend's dev CORS
allow-list includes `localhost` ports).

> Note for local dev: if Firebase is not configured, the app shows a setup
> banner instead of the login form, which is fine for UI inspection of the
> other screens' scaffolding.

## Deploy to Firebase Hosting

```sh
firebase login
firebase deploy --only hosting
```

`firebase.json` serves `frontend/` and rewrites all routes to `index.html`. It
also sets security headers (AppSec review Medium: CWE-1021), including a CSP
whose `connect-src` allows `https://*.run.app` plus local dev origins — once
your backend's origin is fixed for an environment (see `CONFIG.API_BASE`
above), narrow `connect-src` in `firebase.json` to that exact origin instead
of the `*.run.app` wildcard.

## Tests

Pure-logic modules are tested with Node's built-in runner (no deps):

```sh
npm test
```

Browser/DOM behavior (quality heuristic, page rendering, auth flow) is
validated via the live serve smoke test rather than a unit harness.
