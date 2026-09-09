/**
 * DOM component tests for the login/registration flow (PRD 5.4).
 *
 * Uses happy-dom to provide a browser `document`. The Firebase auth backend is
 * not configured in this environment, so submitting the form exercises the
 * failure path: renderLogin shows a friendly error and re-enables the button.
 * This closes the gap flagged in review — the login screen was previously only
 * exercised via the live smoke test.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { GlobalRegistrator } from "@happy-dom/global-registrator";

// Provide a DOM globals before importing the page module.
await GlobalRegistrator.register();

const { renderLogin } = await import("../js/pages/login.js");

test("login card renders in sign-in mode by default", () => {
  const container = document.createElement("div");
  renderLogin(container);
  assert.equal(container.querySelector(".auth-title").textContent, "Sign in to CyclotorsionCheck");
  assert.equal(container.querySelector('button[type="submit"]').textContent, "Sign in");
});

test("toggle switches to register mode", () => {
  const container = document.createElement("div");
  renderLogin(container);
  container.querySelector(".link-btn").dispatchEvent(new window.Event("click", { bubbles: true }));

  assert.equal(
    container.querySelector(".auth-title").textContent,
    "Request a CyclotorsionCheck account",
  );
  assert.equal(container.querySelector('button[type="submit"]').textContent, "Register");
  assert.equal(container.querySelector('input[name="password"]').autocomplete, "new-password");
});

test("failed submit shows a friendly error and re-enables the button", async () => {
  const container = document.createElement("div");
  renderLogin(container);
  container.querySelector('input[name="email"]').value = "doc@hospital.org";
  container.querySelector('input[name="password"]').value = "secret123";
  // Firebase auth is not configured here, so signIn rejects -> error path.
  container
    .querySelector("form")
    .dispatchEvent(new window.Event("submit", { bubbles: true, cancelable: true }));
  // Wait for the async submit handler's catch block to run.
  await new Promise((r) => setImmediate(r));

  const errBox = container.querySelector(".form-error");
  assert.equal(errBox.hidden, false);
  assert.match(errBox.textContent, /Sign-in failed/i);
  const submit = container.querySelector('button[type="submit"]');
  assert.equal(submit.disabled, false);
});
