/**
 * Unit tests for js/api.js (staff review Minor: this was the highest-value
 * untested frontend module — Bearer-header attachment, timeout handling, and
 * connection/server/validation error classification are exactly the kind of
 * logic that regresses silently without tests).
 *
 * Uses node:test's native ESM module mocking (Node 22+,
 * --experimental-test-module-mocks) to stub js/auth.js's
 * getCurrentUserToken() without touching the real Firebase SDK, and stubs
 * global.fetch directly.
 */

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { GlobalRegistrator } from "@happy-dom/global-registrator";

await GlobalRegistrator.register();

mock.module("../js/auth.js", {
  // namedExports, not exports: Node 22's mock.module() only recognizes
  // namedExports for ESM named exports (exports is a Node >=23 addition);
  // this file already targets "Node 22+" per the docstring above, so
  // namedExports is the option that's actually correct for that target.
  namedExports: {
    getCurrentUserToken: async () => "fake-id-token",
  },
});

const { detect, fetchStats, ApiError } = await import("../js/api.js");
const { searchPatients, createPatient, erasePatient, fetchPatientCases } =
  await import("../js/api.js");
const { CONFIG } = await import("../js/config.js");

function fakeFile(name = "u.png") {
  return new File([new Uint8Array([1, 2, 3])], name, { type: "image/png" });
}

test("detect() attaches the Bearer token and posts both images", async () => {
  let seenUrl, seenInit;
  global.fetch = mock.fn(async (url, init) => {
    seenUrl = url;
    seenInit = init;
    return new Response(JSON.stringify({ angle_deg: 1.23, test_id: "t1" }), { status: 200 });
  });

  const result = await detect(fakeFile("u.png"), fakeFile("r.png"));

  assert.equal(seenUrl, `${CONFIG.API_BASE}/detect`);
  assert.equal(seenInit.method, "POST");
  assert.equal(seenInit.headers.Authorization, "Bearer fake-id-token");
  assert.ok(seenInit.body instanceof FormData);
  assert.deepEqual(result, { angle_deg: 1.23, test_id: "t1" });
});

test("fetchStats() attaches the Bearer token on a GET", async () => {
  let seenUrl, seenInit;
  global.fetch = mock.fn(async (url, init) => {
    seenUrl = url;
    seenInit = init;
    return new Response(JSON.stringify({ total_tests: 0 }), { status: 200 });
  });

  const result = await fetchStats();

  assert.equal(seenUrl, `${CONFIG.API_BASE}/stats`);
  assert.equal(seenInit.headers.Authorization, "Bearer fake-id-token");
  assert.deepEqual(result, { total_tests: 0 });
});

test("detect() forwards optional Epic 6 case context fields", async () => {
  let seenBody;
  global.fetch = mock.fn(async (url, init) => {
    seenBody = init.body;
    return new Response(JSON.stringify({ angle_deg: 1.0, test_id: "t2" }), { status: 200 });
  });

  await detect(fakeFile("u.png"), fakeFile("r.png"), {
    caseRef: "CASE-2026-001",
    eyeLaterality: "OD",
    targetAxisDeg: 85,
  });

  assert.ok(seenBody instanceof FormData);
  assert.equal(seenBody.get("case_ref"), "CASE-2026-001");
  assert.equal(seenBody.get("eye_laterality"), "OD");
  assert.equal(seenBody.get("target_axis_deg"), "85");
});

test("detect() omits case fields when not supplied", async () => {
  let seenBody;
  global.fetch = mock.fn(async (url, init) => {
    seenBody = init.body;
    return new Response(JSON.stringify({ angle_deg: 1.0 }), { status: 200 });
  });

  await detect(fakeFile("u.png"), fakeFile("r.png"), {});

  assert.equal(seenBody.get("case_ref"), null);
  assert.equal(seenBody.get("eye_laterality"), null);
  assert.equal(seenBody.get("target_axis_deg"), null);
});

test("detect() classifies a network failure as a connection ApiError", async () => {
  global.fetch = mock.fn(async () => {
    throw new TypeError("fetch failed");
  });

  await assert.rejects(
    () => detect(fakeFile(), fakeFile()),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.kind, "connection");
      return true;
    },
  );
});

test("detect() classifies an AbortError (timeout) as a connection ApiError", async () => {
  global.fetch = mock.fn(async () => {
    const err = new Error("aborted");
    err.name = "AbortError";
    throw err;
  });

  await assert.rejects(
    () => detect(fakeFile(), fakeFile()),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.kind, "connection");
      assert.match(err.message, /timed out/i);
      return true;
    },
  );
});

test("detect() classifies a 401 as a validation ApiError carrying server detail", async () => {
  global.fetch = mock.fn(
    async () => new Response(JSON.stringify({ detail: "bad token" }), { status: 401 }),
  );

  await assert.rejects(
    () => detect(fakeFile(), fakeFile()),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.kind, "validation");
      assert.equal(err.status, 401);
      assert.equal(err.message, "bad token");
      return true;
    },
  );
});

test("detect() classifies a 500 as a server ApiError", async () => {
  global.fetch = mock.fn(async () => new Response("not json", { status: 500 }));

  await assert.rejects(
    () => detect(fakeFile(), fakeFile()),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.kind, "server");
      assert.equal(err.status, 500);
      return true;
    },
  );
});

test("fetchStats() classifies a network failure as a connection ApiError", async () => {
  global.fetch = mock.fn(async () => {
    throw new TypeError("offline");
  });

  await assert.rejects(
    () => fetchStats(),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.kind, "connection");
      return true;
    },
  );
});

test("detect() forwards an Epic 7 patient_id when supplied", async () => {
  let seenBody;
  global.fetch = mock.fn(async (url, init) => {
    seenBody = init.body;
    return new Response(JSON.stringify({ angle_deg: 1.0, test_id: "t3" }), { status: 200 });
  });

  await detect(fakeFile(), fakeFile(), { patientId: "pat-uuid-123" });

  assert.ok(seenBody instanceof FormData);
  assert.equal(seenBody.get("patient_id"), "pat-uuid-123");
});

test("searchPatients() queries the patients search endpoint with the Bearer token", async () => {
  let seenUrl, seenInit;
  global.fetch = mock.fn(async (url, init) => {
    seenUrl = url;
    seenInit = init;
    return new Response(
      JSON.stringify({ results: [{ patient_id: "p1", full_name: "Anjali Rao", mrn: "MRN-1" }] }),
      { status: 200 },
    );
  });

  const data = await searchPatients("Anj");

  assert.ok(seenUrl.startsWith(`${CONFIG.API_BASE}/patients/search`));
  assert.ok(seenUrl.includes("q=Anj"));
  assert.equal(seenInit.headers.Authorization, "Bearer fake-id-token");
  assert.equal(data.results.length, 1);
});

test("createPatient() posts consent with the profile (US-7.4)", async () => {
  let seenBody;
  global.fetch = mock.fn(async (url, init) => {
    seenBody = JSON.parse(init.body);
    return new Response(JSON.stringify({ patient_id: "p1" }), { status: 201 });
  });

  await createPatient({
    fullName: "Anjali Rao",
    dateOfBirth: "1985-04-12",
    mrn: "MRN-1",
    phone: "",
  });

  assert.equal(seenBody.full_name, "Anjali Rao");
  assert.equal(seenBody.consent, true);
  assert.equal(seenBody.phone, undefined);
});

test("createPatient() surfaces a 409 duplicate-MRN as a validation ApiError", async () => {
  global.fetch = mock.fn(
    async () => new Response(JSON.stringify({ detail: "MRN exists" }), { status: 409 }),
  );

  await assert.rejects(
    () =>
      createPatient({
        fullName: "A",
        dateOfBirth: "1990-01-01",
        mrn: "MRN-1",
      }),
    (err) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.kind, "validation");
      assert.equal(err.status, 409);
      assert.equal(err.message, "MRN exists");
      return true;
    },
  );
});

test("erasePatient() sends a DELETE with the Bearer token (US-7.5)", async () => {
  let seenUrl, seenMethod, seenHeaders;
  global.fetch = mock.fn(async (url, init) => {
    seenUrl = url;
    seenMethod = init.method;
    seenHeaders = init.headers;
    return new Response(
      JSON.stringify({ erased_patient_id: "p1", cases_deleted: 2, results_deleted: 3 }),
      { status: 200 },
    );
  });

  const result = await erasePatient("p1");

  assert.equal(seenUrl, `${CONFIG.API_BASE}/patients/p1`);
  assert.equal(seenMethod, "DELETE");
  assert.equal(seenHeaders.Authorization, "Bearer fake-id-token");
  assert.equal(result.cases_deleted, 2);
});

test("fetchPatientCases() loads a patient's history (US-7.3)", async () => {
  let seenUrl;
  global.fetch = mock.fn(async (url) => {
    seenUrl = url;
    return new Response(JSON.stringify({ case_refs: ["C1"], results: [] }), { status: 200 });
  });

  const data = await fetchPatientCases("p1");

  assert.equal(seenUrl, `${CONFIG.API_BASE}/patients/p1/cases`);
  assert.deepEqual(data.case_refs, ["C1"]);
});
