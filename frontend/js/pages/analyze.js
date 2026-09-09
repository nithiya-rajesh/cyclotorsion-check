/**
 * Analyze screen (PRD Epics 1, 2, 3; US-1.1 / 1.2 / 1.3 / 2.1 / 3.1).
 *
 * Lets the surgeon provide the "upright" (pre-op) and "supine" (intra-op) eye
 * photos, previews each slot, runs a client-side quality heuristic, and calls
 * the backend /detect endpoint. All angle calculation and safety validation
 * happen server-side; this page only displays the result.
 */

import { el, icon, checkImageQuality, looksLikePII, validateImageFile } from "../common.js";
import { detect, searchPatients } from "../api.js";
import { recordDetection } from "../session.js";
import { renderResult } from "./result.js";

const SLOTS = {
  upright: { label: "Upright (pre-op)", hint: "Taken with patient seated upright" },
  rotated: { label: "Supine (intra-op)", hint: "Taken immediately before incision" },
};

const PII_NUDGE =
  "This looks like it might contain a name or date of birth — please use your " +
  "facility's case number instead. Continue anyway?";

export function renderAnalyze(container) {
  container.innerHTML = "";

  const state = { upright: null, rotated: null };

  const page = el("div", { class: "analyze-page" });
  const heading = el("h1", { class: "page-title", text: "Analyze toric IOL alignment" });
  const sub = el("p", {
    class: "page-sub",
    text: "Upload two photographs of the same eye to measure cyclotorsion.",
  });
  const slotsRow = el("div", { class: "slots-row" });

  // Case/patient context is fully optional (PRD Epic 6/7) — collapsed by
  // default so the fast, common path (just the two photos) stays uncluttered.
  const casePanel = buildCasePanel();
  const caseToggle = el("button", {
    type: "button",
    class: "panel-toggle",
    "aria-expanded": "false",
    "aria-controls": "case-panel",
  });
  caseToggle.append(
    icon("chevron", { size: 16 }),
    el("span", { text: "Add case & patient context (optional)" }),
  );
  caseToggle.addEventListener("click", () => {
    const open = casePanel.root.classList.toggle("open");
    caseToggle.setAttribute("aria-expanded", String(open));
  });

  const calcWrap = el("div", { class: "calc-wrap" });
  const calcBtn = el("button", {
    class: "btn btn-primary btn-calc",
    text: "Calculate rotation",
    disabled: "disabled",
  });
  const status = el("div", { class: "status-line", "aria-live": "polite" });
  calcWrap.append(calcBtn, status);

  const clickHint = el("p", {
    class: "page-sub",
    text: "You can upload a file or capture a photo directly if using a camera-enabled device.",
  });
  page.append(heading, sub, clickHint, slotsRow, caseToggle, casePanel.root, calcWrap);
  container.appendChild(page);

  // Build the two slots.
  const slotEls = {};
  for (const key of Object.keys(SLOTS)) {
    const slot = buildSlot(key, state, updateButtons, showStatus);
    slotEls[key] = slot;
    slotsRow.appendChild(slot.root);
  }

  function updateButtons() {
    const ready = Boolean(state.upright) && Boolean(state.rotated);
    calcBtn.disabled = !ready;
    if (ready) calcBtn.classList.add("ready");
    else calcBtn.classList.remove("ready");
  }

  function showStatus(text, { busy = false } = {}) {
    status.innerHTML = "";
    if (busy) status.appendChild(el("div", { class: "spinner" }));
    if (text) status.appendChild(el("span", { text }));
  }

  // Calculate handler.
  calcBtn.addEventListener("click", async () => {
    if (!state.upright || !state.rotated) return;

    // US-6.4 safety nudge: if the case reference looks like it might contain a
    // real name or DOB, ask for confirmation before proceeding. This is a
    // nudge, not a hard block — the facility's data policy remains primary.
    if (casePanel.caseRef() && looksLikePII(casePanel.caseRef())) {
      const proceed = window.confirm(PII_NUDGE);
      if (!proceed) {
        showStatus("Change the case reference to your facility's case number.");
        return;
      }
    }

    calcBtn.disabled = true;
    showStatus("Analyzing… (this can take up to 30 seconds)", { busy: true });
    slotEls.upright.setBusy(true);
    slotEls.rotated.setBusy(true);
    try {
      const result = await detect(state.upright, state.rotated, {
        caseRef: casePanel.caseRef() || undefined,
        eyeLaterality: casePanel.eyeLaterality() || undefined,
        targetAxisDeg: casePanel.targetAxisDeg(),
        patientId: casePanel.patientId() || undefined,
      });
      recordDetection(result);
      renderResult(container, result);
    } catch (err) {
      showStatus(err.message || "Something went wrong.");
    } finally {
      calcBtn.disabled = false;
      updateButtons();
      slotEls.upright.setBusy(false);
      slotEls.rotated.setBusy(false);
    }
  });
}

/**
 * Build the optional PRD Epic 6 "case context" panel (US-6.1 / US-6.2 / US-6.3).
 *
 * All fields are fully optional — the core detection workflow works identically
 * without them. Returns an object exposing the current values so the submit
 * handler can read them:
 *   - caseRef() -> trimmed string or ""
 *   - eyeLaterality() -> "OD" | "OS" | ""
 *   - targetAxisDeg() -> number | undefined
 */
function buildCasePanel() {
  const root = el("section", { id: "case-panel", class: "case-panel" });
  const title = el("h2", { class: "case-panel-title", text: "Case context (optional)" });
  const hint = el("p", {
    class: "case-panel-hint",
    text: "Attach this detection to a surgical case. Use your facility's case number — never a patient name or date of birth.",
  });

  const caseRefInput = el("input", {
    type: "text",
    class: "case-input",
    placeholder: "e.g. CASE-2026-001",
    maxlength: "80",
    "aria-label": "Case reference code",
  });
  const eyeSelect = el("select", { class: "case-select", "aria-label": "Eye" });
  eyeSelect.append(
    el("option", { value: "", text: "Eye (OD/OS) — optional" }),
    el("option", { value: "OD", text: "Right eye (OD)" }),
    el("option", { value: "OS", text: "Left eye (OS)" }),
  );
  const targetInput = el("input", {
    type: "number",
    class: "case-input",
    min: "0",
    max: "179.99",
    step: "0.01",
    placeholder: "Target axis (°) — optional",
    "aria-label": "Planned target axis in degrees",
  });

  const fields = el("div", { class: "case-fields" });
  fields.append(caseRefInput, eyeSelect, targetInput);

  // PRD Epic 7: attach this case to an existing patient profile (US-7.2). The
  // patient is found by live search, facility-scoped server-side; only the
  // patient_id is recorded with the case — patient data is never stored here.
  const patientWrap = el("div", { class: "patient-attach" });
  const patientLabel = el("label", {
    class: "patient-attach-label",
    text: "Patient (optional)",
  });
  const patientInput = el("input", {
    type: "search",
    class: "patient-attach-input",
    placeholder: "Search by name or MRN…",
    "aria-label": "Search patient to attach",
  });
  const patientResults = el("ul", { class: "patient-attach-results" });
  const chosen = el("span", { class: "patient-chosen", hidden: true });
  let selectedPatientId = null;
  let searchTimer = null;

  patientInput.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(async () => {
      const q = patientInput.value.trim();
      if (!q) {
        patientResults.innerHTML = "";
        return;
      }
      let data;
      try {
        data = await searchPatients(q);
      } catch {
        patientResults.innerHTML = "";
        return;
      }
      patientResults.innerHTML = "";
      for (const p of data.results) {
        const li = el("li");
        const btn = el("button", {
          type: "button",
          class: "patient-attach-option",
          text: `${p.full_name} — ${p.mrn}`,
        });
        btn.addEventListener("click", () => {
          selectedPatientId = p.patient_id;
          patientInput.value = "";
          patientResults.innerHTML = "";
          chosen.hidden = false;
          chosen.textContent = `Attached: ${p.full_name}`;
        });
        li.appendChild(btn);
        patientResults.appendChild(li);
      }
    }, 250);
  });
  patientWrap.append(patientLabel, patientInput, patientResults, chosen);

  root.append(title, hint, patientWrap, fields);

  return {
    root,
    caseRef: () => caseRefInput.value.trim(),
    eyeLaterality: () => eyeSelect.value,
    targetAxisDeg: () => {
      const v = targetInput.value;
      if (v === "" || v === null) return undefined;
      const n = Number(v);
      if (Number.isNaN(n)) return undefined;
      return n;
    },
    patientId: () => selectedPatientId,
  };
}

/** Build a single capture slot with preview, file/camera picker, and quality note. */
function buildSlot(key, state, updateButtons, showStatus) {
  const info = SLOTS[key];
  const root = el("div", { class: "slot" });
  const header = el("div", { class: "slot-header" });
  const checkIcon = icon("check-circle", { size: 16, decorative: false, label: "Photo added" });
  checkIcon.classList.add("slot-check");
  const title = el("div", { class: "slot-title" }, [el("span", { text: info.label }), checkIcon]);
  const hint = el("div", { class: "slot-hint", text: info.hint });
  header.append(title, hint);

  const preview = el("div", { class: "slot-preview empty" });
  const placeholder = el("div", { class: "slot-placeholder", text: "No photo" });
  const img = el("img", { class: "slot-img", alt: `${info.label} preview`, hidden: true });
  preview.append(placeholder, img);

  const qualityNote = el("div", { class: "quality-note", hidden: true });

  const fileInput = el("input", { type: "file", accept: "image/*", class: "slot-file" });
  const fileBtn = el("label", { class: "btn btn-secondary slot-file-btn" }, [
    icon("upload", { size: 16 }),
    el("span", { text: "Upload photo" }),
  ]);
  fileBtn.appendChild(fileInput);
  // No click handler needed: fileInput is a descendant of this <label>, so
  // the browser already opens the file picker natively on click. An earlier
  // version added a handler here that called fileInput.click() manually —
  // since that call's own click event bubbles back up through this same
  // label, it re-entered this handler, which then called preventDefault()
  // on that second (bubbled) event and silently cancelled the file picker's
  // default action. Native label/input association needs no JS at all.

  const captureInput = el("input", {
    type: "file",
    accept: "image/*",
    capture: "environment",
    class: "slot-capture",
    hidden: "",
  });
  const captureBtn = el("button", { type: "button", class: "btn btn-secondary" }, [
    icon("camera", { size: 16 }),
    el("span", { text: "Use camera" }),
  ]);
  captureInput.addEventListener("change", () => handleFile(captureInput.files[0]));
  captureBtn.addEventListener("click", () => captureInput.click());

  const controls = el("div", { class: "slot-controls" });
  controls.append(fileBtn, captureBtn);

  root.append(header, preview, qualityNote, controls, captureInput);
  root.__setFile = (file) => handleFile(file);
  root.setBusy = (busy) => {
    root.classList.toggle("busy", busy);
  };

  fileInput.addEventListener("change", () => handleFile(fileInput.files[0]));

  async function handleFile(file) {
    if (!file) return;
    const v = validateImageFile(file);
    qualityNote.hidden = true;
    if (!v.ok) {
      showStatus(v.reason);
      return;
    }
    state[key] = file;
    root.classList.add("filled");
    // Live preview.
    const url = URL.createObjectURL(file);
    img.src = url;
    img.hidden = false;
    placeholder.hidden = true;
    preview.classList.remove("empty");

    // Non-blocking quality heuristic (PRD US-1.3).
    const q = await checkImageQuality(file);
    if (!q.ok) {
      qualityNote.hidden = false;
      qualityNote.textContent = `Image quality may affect accuracy — consider retaking. (${q.reason})`;
    } else {
      qualityNote.hidden = true;
    }
    updateButtons();
    showStatus("");
  }

  return { root, setBusy: root.setBusy };
}
