/**
 * Result rendering (PRD Epic 3, US-3.1 / US-2.2).
 *
 * Displays the server-computed angle and surfaces any sanity flags in a
 * visually distinct warning state (never the default success state). Contains
 * no business logic — it only formats what the backend returned.
 */

import { el, icon } from "../common.js";
import { renderAnalyze } from "./analyze.js";

export function renderResult(container, result) {
  container.innerHTML = "";

  const page = el("div", { class: "result-page" });
  const heading = el("h1", { class: "page-title", text: "Detection result" });

  const flagState = result.passed_sanity_check ? "pass" : "warn";
  const card = el("div", { class: `result-card ${flagState}` });

  const badge = el("div", { class: "result-badge" }, [
    icon(flagState === "pass" ? "check-circle" : "warning", {
      size: 24,
      decorative: false,
      label: flagState === "pass" ? "Passed sanity check" : "Flagged for manual review",
    }),
  ]);
  const angleLabel = el("div", { class: "result-angle-label", text: "Calculated cyclotorsion" });
  const angleValue = el("div", {
    class: "result-angle-value",
    text: `${formatAngle(result.angle_deg)}°`,
  });
  const head = el("div", { class: "result-head" }, [
    badge,
    el("div", {}, [angleLabel, angleValue]),
  ]);
  card.append(head);

  // US-6.2: when the surgeon supplied a real target axis, show the clinically
  // meaningful corrected axis (target + rotation) computed server-side.
  if (result.corrected_axis_deg !== undefined) {
    const corrected = el("div", { class: "result-corrected" });
    corrected.append(
      el("span", { class: "result-corrected-label", text: "Corrected axis" }),
      el("span", {
        class: "result-corrected-value",
        text: `${formatAngle(result.corrected_axis_deg)}°`,
      }),
    );
    card.appendChild(corrected);
  }

  if (result.warning) {
    const warn = el("div", { class: "result-warning" }, [
      icon("warning", { size: 16 }),
      el("span", { text: result.warning }),
    ]);
    card.appendChild(warn);
  }

  if (!result.passed_sanity_check) {
    const advise = el("p", {
      class: "result-advice",
      text: "This result failed the plausibility check. Verify manually before relying on it.",
    });
    card.appendChild(advise);
  }

  // Supporting detail (auditable, non-identifiable).
  const detail = el("dl", { class: "result-detail" });
  detail.append(
    el("dt", { text: "Passed sanity check" }),
    el("dd", { text: result.passed_sanity_check ? "Yes" : "No" }),
    el("dt", { text: "Sanity flags" }),
    el("dd", { text: (result.sanity_flags || []).join(", ") || "None" }),
    el("dt", { text: "Landmark (upright)" }),
    el("dd", { text: result.upright_landmark || "—" }),
    el("dt", { text: "Landmark (supine)" }),
    el("dd", { text: result.rotated_landmark || "—" }),
  );
  if (result.case_ref) {
    detail.append(
      el("dt", { text: "Case reference" }),
      el("dd", { text: result.case_ref }),
      el("dt", { text: "Eye" }),
      el("dd", { text: result.eye_laterality || "—" }),
    );
  }
  detail.append(el("dt", { text: "Reference" }), el("dd", { text: result.test_id || "—" }));

  const actions = el("div", { class: "result-actions" });
  const newBtn = el("button", { class: "btn btn-secondary", text: "New detection" });
  newBtn.addEventListener("click", () => renderAnalyze(container));
  actions.appendChild(newBtn);

  page.append(heading, card, detail, actions);
  container.appendChild(page);
}

function formatAngle(value) {
  // Two decimal places, matching the backend (PRD US-2.2).
  if (typeof value !== "number") return String(value);
  return value.toFixed(2);
}
