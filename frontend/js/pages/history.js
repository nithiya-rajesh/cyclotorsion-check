/**
 * History screen (PRD US-4.1).
 *
 * Shows the current session's test history — strictly session-scoped, no
 * cross-session/cross-user data. Provides the surgeon visibility into their
 * own recent results to build trust over repeated use.
 */

import { el, dash } from "../common.js";
import { sessionRecords } from "../session.js";

export function renderHistory(container) {
  container.innerHTML = "";

  const records = sessionRecords();
  const page = el("div", { class: "history-page" });
  const heading = el("h1", { class: "page-title", text: "History" });
  const sub = el("p", {
    class: "page-sub",
    text: "Your tests from this browser session only — nothing is stored across sessions or users.",
  });

  page.append(heading, sub);

  if (records.length === 0) {
    page.appendChild(el("p", { class: "empty-state", text: "No detections in this session yet." }));
    container.appendChild(page);
    return;
  }

  const table = el("table", { class: "data-table" });
  const thead = el("thead");
  const headRow = el("tr");
  for (const c of ["Time", "Angle", "Sanity", "Flags", "Reference"]) {
    headRow.appendChild(el("th", { text: c }));
  }
  thead.appendChild(headRow);
  table.appendChild(thead);

  const tbody = el("tbody");
  for (const r of records) {
    const tr = el("tr", { class: r.passed ? "" : "danger-row" });
    tr.append(
      el("td", { text: r.at.toLocaleTimeString() }),
      el("td", { text: `${r.angle.toFixed(2)}°` }),
      el("td", { text: r.passed ? "Pass" : "Flagged" }),
      el("td", { text: (r.flags || []).join(", ") || "—" }),
      el("td", { text: dash(r.testId) }),
    );
    tbody.appendChild(tr);
  }
  table.appendChild(tbody);
  page.appendChild(table);
  container.appendChild(page);
}
