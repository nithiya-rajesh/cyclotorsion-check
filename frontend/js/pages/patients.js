/**
 * Patients screen (PRD Epic 7: US-7.1 create, US-7.2 search, US-7.3 history,
 * US-7.4 consent, US-7.5 right-to-erasure).
 *
 * Lets a clinic search for a patient by name/MRN (so a surgeon can attach the
 * right profile to a case from the Analyze screen), create a new profile with
 * explicit consent, view a patient's detection history, and (facility-admin)
 * perform a cascading right-to-erasure. All data is facility-scoped by the
 * backend; this page never stores patient data locally.
 */

import { el, icon, dash } from "../common.js";
import {
  createPatient,
  erasePatient,
  fetchPatient,
  fetchPatientCases,
  searchPatients,
  suggestMrn,
} from "../api.js";

export function renderPatients(container, subpath = "") {
  container.innerHTML = "";

  const page = el("div", { class: "patients-page" });
  const heading = el("h1", { class: "page-title", text: "Patients" });
  const sub = el("p", {
    class: "page-sub",
    text: "Find your facility's patient profiles to attach cases, or create one with the patient's consent.",
  });
  const status = el("div", { class: "status-line", "aria-live": "polite" });

  const layout = el("div", { class: "patients-layout" });
  const searchCard = buildSearchCard(onSelected);
  const createCard = buildCreateCard(onCreated);
  const detailCard = buildDetailCard();
  layout.append(searchCard.root, createCard.root);

  page.append(heading, sub, layout, detailCard.root, status);
  container.appendChild(page);

  function onCreated() {
    status.textContent = "Patient profile created with recorded consent.";
    searchCard.refresh();
  }

  function onSelected(patient) {
    status.textContent = "";
    setHash(patient.patient_id);
    renderDetail(patient);
  }

  // Restore a deep-linked patient on first render (URL as state), e.g. a
  // refreshed "#/patients/<id>" tab.
  if (subpath) {
    detailCard
      .load(subpath)
      .then((patient) => {
        setHash(patient.patient_id);
        searchCard.refresh();
      })
      .catch(() => {
        /* keep the page empty; hint points the user to search */
      });
  }

  function setHash(patientId) {
    const target = `#/patients/${patientId}`;
    if (window.location.hash !== target) {
      try {
        window.history.replaceState(null, "", target);
      } catch {
        window.location.hash = target.replace(/^#/, "");
      }
    }
  }

  async function renderDetail(patient) {
    detailCard.setBusy(true);
    try {
      const [profile, history] = await Promise.all([
        fetchPatient(patient.patient_id),
        fetchPatientCases(patient.patient_id),
      ]);
      detailCard.render(profile, history);
    } catch (err) {
      detailCard.showError(err.message);
    } finally {
      detailCard.setBusy(false);
    }
  }
}

/** Search + select card (US-7.2). */
function buildSearchCard(onSelected) {
  const root = el("section", { class: "card patients-card" });
  const title = el("h2", { class: "card-title", text: "Find a patient" });
  const input = el("input", {
    type: "search",
    class: "patient-search",
    placeholder: "Search by name or MRN…",
    "aria-label": "Search patients by name or medical record number",
  });
  const results = el("ul", { class: "patient-results" });
  const hint = el("p", {
    class: "card-hint",
    text: "Type at least 1 character to search your facility's patients.",
  });

  root.append(title, input, hint, results);

  let timer = null;
  input.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = input.value.trim();
      if (!q) {
        results.innerHTML = "";
        return;
      }
      results.innerHTML = "";
      hint.textContent = "Searching…";
      let data;
      try {
        data = await searchPatients(q);
      } catch (err) {
        hint.textContent = err.message;
        return;
      }
      hint.textContent = data.results.length
        ? `${data.results.length} result(s).`
        : "No patients found. You can create one on the right.";
      for (const p of data.results) {
        const li = el("li", { class: "patient-result" });
        const btn = el("button", {
          type: "button",
          class: "patient-result-btn",
          text: `${p.full_name} — ${p.mrn}`,
        });
        btn.addEventListener("click", () => onSelected(p));
        li.appendChild(btn);
        results.appendChild(li);
      }
    }, 250);
  });

  return {
    root,
    refresh: () => {
      results.innerHTML = "";
      hint.textContent = "Type at least 1 character to search your facility's patients.";
    },
  };
}

/** Create-with-consent card (US-7.1 + US-7.4). */
function buildCreateCard(onCreated) {
  const root = el("section", { class: "card patients-card" });
  const title = el("h2", { class: "card-title", text: "Create a patient profile" });

  const form = el("form", { class: "patients-form", novalidate: false });

  const nameField = el("label", { class: "field-row" });
  nameField.append(
    el("span", { class: "field-label", text: "Full name" }),
    el("input", {
      type: "text",
      name: "fullName",
      autocomplete: "name",
      class: "field",
      required: "required",
      placeholder: "e.g. Anjali Rao",
    }),
  );

  const dobField = el("label", { class: "field-row" });
  dobField.append(
    el("span", { class: "field-label", text: "Date of birth" }),
    el("input", {
      type: "date",
      name: "dateOfBirth",
      class: "field",
      required: "required",
    }),
  );

  // US-7.1: pre-fill a suggested MRN so the form isn't blocked when the
  // surgeon doesn't have the patient's real hospital MRN in front of them
  // yet. Always editable/overwritable — never a substitute for the real one.
  const mrnInput = el("input", {
    type: "text",
    name: "mrn",
    class: "field",
    required: "required",
    autocomplete: "off",
    spellcheck: "false",
    placeholder: "e.g. MRN-2045",
  });
  const mrnRegenBtn = el(
    "button",
    {
      type: "button",
      class: "mrn-regen-btn",
      "aria-label": "Generate a new suggested MRN",
      title: "Generate a new suggested MRN",
    },
    [icon("refresh", { size: 16 })],
  );
  const mrnRow = el("div", { class: "mrn-row" }, [mrnInput, mrnRegenBtn]);
  const mrnHint = el("p", {
    class: "field-hint",
    text: "Auto-suggested — replace with the patient's real hospital MRN if you have it.",
  });
  const mrnField = el("label", { class: "field-row" });
  mrnField.append(
    el("span", { class: "field-label", text: "Medical record number (MRN)" }),
    mrnRow,
    mrnHint,
  );

  async function loadMrnSuggestion({ force = false } = {}) {
    if (!force && mrnInput.value.trim()) return;
    try {
      const data = await suggestMrn();
      mrnInput.value = data.mrn;
    } catch {
      // Non-critical convenience — leave the field as-is so the surgeon can
      // still type the real MRN manually.
    }
  }
  mrnRegenBtn.addEventListener("click", () => loadMrnSuggestion({ force: true }));
  loadMrnSuggestion();

  const phoneField = el("label", { class: "field-row" });
  phoneField.append(
    el("span", { class: "field-label", text: "Phone (optional)" }),
    el("input", {
      type: "tel",
      name: "phone",
      class: "field",
      autocomplete: "tel",
      placeholder: "+91 12345 67890",
    }),
  );

  const consentLabel = el("label", { class: "consent-row" });
  const consent = el("input", {
    type: "checkbox",
    name: "consent",
    class: "consent-check",
    required: "required",
  });
  consentLabel.append(
    consent,
    el("span", {
      text: "I confirm the patient (or guardian) consents to storing their name, date of birth, and MRN solely to link this facility's toric-IOL alignment tests to their own patient record.",
    }),
  );

  const submit = el("button", { type: "submit", class: "btn btn-primary", text: "Create profile" });
  const formStatus = el("p", { class: "form-status", "aria-live": "polite" });

  form.append(title, nameField, dobField, mrnField, phoneField, consentLabel, submit, formStatus);
  root.appendChild(form);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    formStatus.textContent = "";
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }
    submit.disabled = true;
    try {
      await createPatient({
        fullName: form.elements.fullName.value.trim(),
        dateOfBirth: form.elements.dateOfBirth.value,
        mrn: form.elements.mrn.value.trim(),
        phone: form.elements.phone.value.trim() || undefined,
      });
      form.reset();
      formStatus.textContent = "Created. The recorded consent is stored securely.";
      loadMrnSuggestion({ force: true });
      onCreated();
    } catch (err) {
      formStatus.textContent = err.message;
    } finally {
      submit.disabled = false;
    }
  });

  return { root };
}

/** Detail + history + erasure card (US-7.3 / US-7.5). */
function buildDetailCard() {
  const root = el("section", { class: "card patients-card patient-detail", hidden: true });
  const title = el("h2", { class: "card-title", text: "Patient detail" });
  const body = el("div", { class: "patient-detail-body" });
  root.append(title, body);

  function setBusy(b) {
    root.classList.toggle("busy", b);
  }
  function showError(msg) {
    body.innerHTML = "";
    body.appendChild(el("p", { class: "empty-state", text: msg }));
  }

  return {
    root,
    setBusy,
    showError,
    async load(patientId) {
      root.hidden = false;
      this.setBusy(true);
      try {
        const [profile, history] = await Promise.all([
          fetchPatient(patientId),
          fetchPatientCases(patientId),
        ]);
        this.render(profile, history);
        return profile;
      } catch (err) {
        this.showError(err.message);
        throw err;
      } finally {
        this.setBusy(false);
      }
    },
    render(profile, history) {
      root.hidden = false;
      root.classList.add("busy");
      body.innerHTML = "";

      const meta = el("div", { class: "patient-meta" });
      meta.append(
        el("p", { text: `${profile.full_name} — MRN ${profile.mrn}` }),
        el("p", { text: `DOB: ${dash(profile.date_of_birth)}` }),
        el("p", { text: `Created: ${dash(profile.created_at)}` }),
      );
      const records = el("div", { class: "patient-records" });
      if (!history.results || history.results.length === 0) {
        records.appendChild(
          el("p", { class: "empty-state", text: "No detections linked to this patient yet." }),
        );
      } else {
        const table = el("table", { class: "data-table" });
        const thead = el("thead");
        const headRow = el("tr");
        for (const c of ["Time", "Angle", "Case", "Sanity"])
          headRow.appendChild(el("th", { text: c }));
        thead.appendChild(headRow);
        const tbody = el("tbody");
        for (const r of history.results) {
          const tr = el("tr", { class: r.passed_sanity_check ? "" : "danger-row" });
          tr.append(
            el("td", { text: dash(r.timestamp) }),
            el("td", { text: `${r.angle_deg.toFixed(2)}°` }),
            el("td", { text: dash(r.case_ref) }),
            el("td", { text: r.passed_sanity_check ? "Pass" : "Flagged" }),
          );
          tbody.appendChild(tr);
        }
        table.appendChild(tbody);
        records.appendChild(table);
      }

      const eraseBtn = el("button", { type: "button", class: "btn btn-danger" }, [
        icon("trash", { size: 16 }),
        el("span", { text: "Erase this patient's data" }),
      ]);
      eraseBtn.addEventListener("click", async () => {
        const ok = window.confirm(
          "This permanently deletes the patient profile AND all linked cases/results from this facility. This cannot be undone. Continue?",
        );
        if (!ok) return;
        eraseBtn.disabled = true;
        try {
          const result = await erasePatient(profile.patient_id);
          showError(
            `Erasure complete — ${result.cases_deleted} case(s) and ${result.results_deleted} result(s) removed.`,
          );
        } catch (err) {
          showError(err.message);
          eraseBtn.disabled = false;
        }
      });

      body.append(meta, records, eraseBtn);
      root.classList.remove("busy");
    },
  };
}
