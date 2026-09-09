/**
 * In-memory session store for the current browser session only (PRD US-4.1).
 *
 * Per US-4.1, the History/Insights "this session" views are strictly scoped to
 * the current browser session — no cross-session or cross-user history. Data
 * is kept in JS memory for the lifetime of the tab and is never persisted
 * client-side (deliberately NOT localStorage).
 */

const records = [];

/** Record one completed detection result in this session. */
export function recordDetection(result) {
  records.push({
    testId: result.test_id,
    angle: result.angle_deg,
    passed: result.passed_sanity_check,
    flags: result.sanity_flags || [],
    warning: result.warning || null,
    at: new Date(),
  });
}

/** All records for this session, newest first. */
export function sessionRecords() {
  return [...records].reverse();
}

/**
 * Session-level summary used by US-4.1: count, average absolute angle, and
 * sanity-check pass rate.
 */
export function sessionSummary() {
  if (records.length === 0) {
    return { count: 0, avgAbsAngle: null, passRate: null };
  }
  const count = records.length;
  const avgAbs = records.reduce((sum, r) => sum + Math.abs(r.angle), 0) / count;
  const passed = records.filter((r) => r.passed).length;
  return {
    count,
    avgAbsAngle: Number(avgAbs.toFixed(2)),
    passRate: passed / count,
  };
}

/** Clear the in-memory session store (e.g. on logout). */
export function clearSession() {
  records.length = 0;
}
