/**
 * Unit tests for the SPA's pure-logic modules.
 *
 * Uses Node's built-in test runner (no extra deps). The SPA's pure logic lives
 * in session.js (in-memory session store). Browser/DOM-dependent modules
 * (common.js quality heuristic, api.js, pages/*) are exercised via the live
 * serve smoke test instead.
 *
 * Run from repo root:
 *   node --test frontend/test/
 */

import { test } from "node:test";
import assert from "node:assert/strict";

import { recordDetection, sessionRecords, sessionSummary, clearSession } from "../js/session.js";

test("empty session summary is zeroed", () => {
  clearSession();
  const s = sessionSummary();
  assert.equal(s.count, 0);
  assert.equal(s.avgAbsAngle, null);
  assert.equal(s.passRate, null);
});

test("recordDetection appends and summary computes averages", () => {
  clearSession();
  recordDetection({
    test_id: "a",
    angle_deg: 5.0,
    passed_sanity_check: true,
    sanity_flags: [],
    warning: null,
  });
  recordDetection({
    test_id: "b",
    angle_deg: -3.0,
    passed_sanity_check: false,
    sanity_flags: ["angle_near_zero"],
    warning: "flag",
  });
  const s = sessionSummary();
  assert.equal(s.count, 2);
  // avg absolute = (5 + 3) / 2 = 4.0
  assert.equal(s.avgAbsAngle, 4.0);
  assert.equal(s.passRate, 0.5);
});

test("sessionRecords returns newest first", () => {
  clearSession();
  recordDetection({
    test_id: "first",
    angle_deg: 1,
    passed_sanity_check: true,
    sanity_flags: [],
    warning: null,
  });
  recordDetection({
    test_id: "second",
    angle_deg: 2,
    passed_sanity_check: true,
    sanity_flags: [],
    warning: null,
  });
  const r = sessionRecords();
  assert.equal(r[0].testId, "second");
  assert.equal(r[1].testId, "first");
});

test("clearSession empties the store", () => {
  clearSession();
  recordDetection({
    test_id: "x",
    angle_deg: 1,
    passed_sanity_check: true,
    sanity_flags: [],
    warning: null,
  });
  clearSession();
  assert.equal(sessionRecords().length, 0);
});
