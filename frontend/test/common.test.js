/**
 * Unit tests for the pure helpers in js/common.js, specifically the US-6.4
 * `looksLikePII` case-reference safety-nudge check (PRD Epic 6). The DOM-based
 * helpers (el/checkImageQuality/validateImageFile) are covered by the live
 * serve smoke test; this isolates the pure pattern-checking logic.
 */

import { test } from "node:test";
import assert from "node:assert/strict";
import { GlobalRegistrator } from "@happy-dom/global-registrator";

await GlobalRegistrator.register();

const { looksLikePII } = await import("../js/common.js");

test("looksLikePII flags a DOB-like value", () => {
  assert.equal(looksLikePII("12/03/1990"), true);
  assert.equal(looksLikePII("1985-07-11"), true);
  assert.equal(looksLikePII("CASE 07/11/1985"), true);
});

test("looksLikePII flags a two-capitalised-word name", () => {
  assert.equal(looksLikePII("Anjali Rao"), true);
  assert.equal(looksLikePII("Anjali Devi Rao"), true);
  assert.equal(looksLikePII("Samuel John"), true);
});

test("looksLikePII leaves pseudonymous facility codes alone", () => {
  assert.equal(looksLikePII("CASE-2026-001"), false);
  assert.equal(looksLikePII("SX-4317"), false);
  assert.equal(looksLikePII("cs45"), false);
  assert.equal(looksLikePII(""), false);
  assert.equal(looksLikePII(null), false);
  assert.equal(looksLikePII(undefined), false);
});
