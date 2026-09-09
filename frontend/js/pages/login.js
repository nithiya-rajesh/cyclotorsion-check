/**
 * Login / Registration screen (PRD US-... Section 5.4).
 *
 * Email/Password auth via Firebase. The UI is a single card with a
 * login/register toggle; only the backing calls differ.
 */

import { el } from "../common.js";
import { createAccount, signIn } from "../auth.js";

export function renderLogin(container) {
  container.innerHTML = "";

  const form = el("form", { class: "auth-card" });
  const title = el("h1", { class: "auth-title", text: "Sign in to CyclotorsionCheck" });
  const modeTxt = el("p", { class: "auth-sub", text: "Use your hospital-issued account." });

  const email = el("input", {
    type: "email",
    name: "email",
    autocomplete: "username",
    placeholder: "Email",
    required: "required",
    class: "input",
  });
  const password = el("input", {
    type: "password",
    name: "password",
    autocomplete: "current-password",
    placeholder: "Password",
    required: "required",
    class: "input",
  });
  const submit = el("button", { type: "submit", class: "btn btn-primary", text: "Sign in" });
  const errBox = el("div", { class: "form-error", hidden: true });
  const toggle = el("button", {
    type: "button",
    class: "link-btn",
    text: "Need an account? Register",
  });

  form.append(title, modeTxt, email, password, errBox, submit, toggle);

  let mode = "login";

  function setMode(m) {
    mode = m;
    title.textContent =
      m === "login" ? "Sign in to CyclotorsionCheck" : "Request a CyclotorsionCheck account";
    modeTxt.textContent =
      m === "login"
        ? "Use your hospital-issued account."
        : "Register with your hospital email. Access is allow-listed.";
    submit.textContent = m === "login" ? "Sign in" : "Register";
    password.autocomplete = m === "login" ? "current-password" : "new-password";
    toggle.textContent =
      m === "login" ? "Need an account? Register" : "Already registered? Sign in";
  }

  toggle.addEventListener("click", () => setMode(mode === "login" ? "register" : "login"));

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    errBox.hidden = true;
    submit.disabled = true;
    submit.textContent = "Please wait…";
    try {
      if (mode === "login") {
        await signIn(email.value, password.value);
      } else {
        await createAccount(email.value, password.value);
      }
      // On success the onAuthStateChanged listener in router.js navigates away.
    } catch (err) {
      errBox.hidden = false;
      errBox.textContent = friendlyAuthError(err);
      submit.disabled = false;
      submit.textContent = mode === "login" ? "Sign in" : "Register";
    }
  });

  container.appendChild(form);
}

function friendlyAuthError(err) {
  const code = err && err.code ? err.code : "";
  switch (code) {
    case "auth/invalid-email":
    case "auth/user-not-found":
    case "auth/wrong-password":
    case "auth/invalid-credential":
      return "Invalid email or password.";
    case "auth/email-already-in-use":
      return "An account with this email already exists. Try signing in.";
    case "auth/weak-password":
      return "Password is too weak (min 6 characters).";
    case "auth/network-request-failed":
      return "Connection issue — check your internet and try again.";
    default:
      return `Sign-in failed (${code || "unknown error"}).`;
  }
}
