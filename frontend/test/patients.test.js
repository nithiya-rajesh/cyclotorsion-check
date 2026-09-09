/**
 * Unit tests for the Patients screen (PRD Epic 7), rendered with happy-dom.
 *
 * Covers the create-with-consent form (US-7.1/7.4) and the URL-as-state deep
 * link (subpath auto-load) added for the web-interface guidelines ("URL as
 * state"). The API layer is stubbed via node:test module mocking so this only
 * exercises the DOM/logic of the page itself.
 */

import { test, mock } from "node:test";
import assert from "node:assert/strict";
import { GlobalRegistrator } from "@happy-dom/global-registrator";

await GlobalRegistrator.register();

const calls = { created: [], erased: [], searched: [], loaded: [], suggested: 0 };

mock.module("../js/api.js", {
  // namedExports (not exports): Node 22's mock.module() only recognizes
  // namedExports for ESM named exports — exports silently produces a module
  // with none of these names, failing with "does not provide an export
  // named ..." at the importing module. Node >=23 accepts exports too (and
  // deprecates namedExports), but namedExports still works there — this is
  // the one option that's actually correct on the Node 22 this project's CI
  // pins (.github/workflows/ci.yml), confirmed by running this suite against
  // both a local Node 22 install and the newer Node already on this machine.
  namedExports: {
    createPatient: async (payload) => {
      calls.created.push(payload);
      return { patient_id: "new-1" };
    },
    erasePatient: async () => ({ cases_deleted: 0, results_deleted: 0 }),
    fetchPatient: async (id) => {
      calls.loaded.push(id);
      return {
        patient_id: id,
        full_name: "Anjali Rao",
        mrn: "MRN-1",
        date_of_birth: "1985-04-12",
        created_at: "2026-01-01T00:00:00Z",
      };
    },
    fetchPatientCases: async () => ({ results: [] }),
    searchPatients: async (q) => {
      calls.searched.push(q);
      return {
        results: [{ patient_id: "p1", full_name: "Anjali Rao", mrn: "MRN-1" }],
      };
    },
    suggestMrn: async () => {
      calls.suggested += 1;
      return { mrn: `MRN-SUGGESTED-${calls.suggested}` };
    },
  },
});

const { renderPatients } = await import("../js/pages/patients.js");

function container() {
  const c = document.createElement("div");
  document.body.appendChild(c);
  return c;
}

test("create form renders with labels and does not submit without consent", async () => {
  const c = container();
  renderPatients(c, "");

  const form = c.querySelector("form.patients-form");
  assert.ok(form, "create card should be a real <form>");

  // Labels are associated with controls (guideline "Labels everywhere").
  const nameLabel = c.querySelector('label input[name="fullName"]');
  assert.ok(nameLabel, "name input is wrapped by a label");

  // Fill required fields but leave consent unchecked.
  form.elements.fullName.value = "Anjali Rao";
  form.elements.dateOfBirth.value = "1985-04-12";
  form.elements.mrn.value = "MRN-1";
  form.dispatchEvent(new Event("submit", { cancelable: true }));

  assert.equal(calls.created.length, 0, "must not create without consent");
});

test("create form submits only when consent is checked (US-7.4)", async () => {
  const c = container();
  renderPatients(c, "");

  const form = c.querySelector("form.patients-form");
  form.elements.fullName.value = "Anjali Rao";
  form.elements.dateOfBirth.value = "1985-04-12";
  form.elements.mrn.value = "MRN-1";
  form.elements.consent.checked = true;
  form.dispatchEvent(new Event("submit", { cancelable: true }));

  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.created.length, 1);
  assert.equal(calls.created[0].fullName, "Anjali Rao");
  assert.equal(calls.created[0].phone, undefined);
  c.remove();
});

test("MRN field is pre-filled with a suggested value (US-7.1), still editable", async () => {
  const c = container();
  renderPatients(c, "");

  const mrnInput = c.querySelector('input[name="mrn"]');
  await new Promise((r) => setTimeout(r, 0));
  assert.match(mrnInput.value, /^MRN-SUGGESTED-/, "field should be pre-filled with a suggestion");

  // Still a normal editable input — the surgeon can overwrite it.
  mrnInput.value = "MRN-REAL-1234";
  assert.equal(mrnInput.value, "MRN-REAL-1234");
  c.remove();
});

test("a deep-link subpath auto-loads the selected patient (URL as state)", async () => {
  const c = container();
  renderPatients(c, "deep-1");

  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls.loaded, ["deep-1"], "detail should load from the subpath");
  const detail = c.querySelector(".patient-detail");
  assert.ok(detail, "detail card should be shown");
  assert.match(detail.textContent, /Anjali Rao/);
  c.remove();
});
