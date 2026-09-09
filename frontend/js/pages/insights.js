/**
 * Insights screen (PRD Epic 4, US-4.1 / 4.2 / 4.3).
 *
 * Two panels:
 *   - "This session": the surgeon's own in-tab summary (US-4.1).
 *   - "All-time": aggregate, de-identified statistics fetched from the backend
 *     /stats endpoint (US-4.2 / US-4.3). Every field is numeric aggregate only;
 *     no patient-identifiable data is shown or fetched.
 */

import { el, dash } from "../common.js";
import { sessionSummary } from "../session.js";
import { fetchStats } from "../api.js";

export function renderInsights(container) {
  container.innerHTML = "";

  const page = el("div", { class: "insights-page" });
  const heading = el("h1", { class: "page-title", text: "Insights" });
  page.append(heading);

  // ---- Session panel ----
  const s = sessionSummary();
  const sessionCard = statCard("This session", "Scoped to this browser session only.", [
    { label: "Tests run", value: s.count },
    { label: "Avg abs. angle", value: s.avgAbsAngle === null ? "—" : `${s.avgAbsAngle}°` },
    { label: "Sanity pass rate", value: fmtRate(s.passRate) },
  ]);

  // ---- All-time panel (backend aggregate) ----
  const alltimeCard = el("div", { class: "stat-card" });
  const loadingGrid = el("div", { class: "stat-grid stat-loading" });
  for (let i = 0; i < 3; i++) {
    loadingGrid.append(
      el("div", { class: "stat-item" }, [
        el("div", { class: "stat-value skeleton", text: "00" }),
        el("div", { class: "stat-label skeleton", text: "Loading" }),
      ]),
    );
  }
  alltimeCard.append(
    el("h2", { class: "stat-card-title", text: "All-time (aggregate)" }),
    el("p", {
      class: "stat-card-sub",
      text: "De-identified statistics across all logged detections.",
    }),
    loadingGrid,
  );

  page.append(sessionCard, alltimeCard);
  container.appendChild(page);

  // Avoid amplifying the parallel page's own fetch; fetch all-time here.
  loadAllTime(alltimeCard);
}

function statCard(title, sub, items) {
  const card = el("div", { class: "stat-card" });
  card.append(
    el("h2", { class: "stat-card-title", text: title }),
    el("p", { class: "stat-card-sub", text: sub }),
  );
  const grid = el("div", { class: "stat-grid" });
  for (const it of items) {
    grid.append(
      el("div", { class: "stat-item" }, [
        el("div", { class: "stat-value", text: String(it.value) }),
        el("div", { class: "stat-label", text: it.label }),
      ]),
    );
  }
  card.appendChild(grid);
  return card;
}

async function loadAllTime(host) {
  // Short client-side cache (TDD Section 4.2) could be layered here; for now
  // the aggregate panel re-fetches on each visit, which is negligible at pilot
  // volume.
  try {
    const stats = await fetchStats();
    host.querySelector(".stat-loading")?.remove();
    const grid = el("div", { class: "stat-grid" });
    grid.append(
      statItem("Total tests", stats.total_tests),
      statItem(
        "Avg abs. angle",
        stats.average_abs_angle_deg === null || stats.average_abs_angle_deg === undefined
          ? dash(stats.average_abs_angle_deg)
          : `${stats.average_abs_angle_deg}°`,
      ),
      statItem("Sanity pass rate", fmtRate(stats.sanity_check_pass_rate)),
    );
    host.appendChild(grid);
  } catch (err) {
    host.querySelector(".stat-loading")?.remove();
    host.appendChild(
      el("div", {
        class: "stat-error",
        text: err.message || "Could not load aggregate statistics.",
      }),
    );
  }
}

function statItem(label, value) {
  return el("div", { class: "stat-item" }, [
    el("div", { class: "stat-value", text: String(value) }),
    el("div", { class: "stat-label", text: label }),
  ]);
}

function fmtRate(rate) {
  if (rate === null || rate === undefined) return "—";
  return `${(rate * 100).toFixed(0)}%`;
}
