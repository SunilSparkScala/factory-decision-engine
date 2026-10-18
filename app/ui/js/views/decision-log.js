// Decision Log: audit trail of human-approved decisions.

import { $, h, clear, toast } from "../dom.js";
import { api } from "../api.js";
import { usd, dateTime } from "../format.js";
import { documentTabs } from "./workspace.js";

export function init() {}

/** "[ALT-F001] Unscheduled downtime on M17 for 8.0 hours" -> "M17 down for 8 h" (older entries included). */
function situation(d) {
  const m = /downtime on (M\d+) for ([\d.]+) hours/i.exec(d.scenario || "");
  if (m) return `${m[1]} down for ${Number(m[2])} h`;
  return (d.scenario || "").replace(/^\[[^\]]+\]\s*/, "");
}

export async function onShow() {
  const box = $("#decision-log");
  clear(box, h("div", { class: "skeleton-block" }));
  try {
    const { decisions } = await api.decisions();
    if (!decisions.length) {
      clear(box, h("div", { class: "card empty-state" },
        h("div", { class: "empty-illustration", "aria-hidden": "true" }, "✓"),
        h("h3", {}, "No approved decisions yet"),
        h("p", {}, "Approve a recommendation in the Decide view and it will appear here with its documents.")));
      return;
    }
    clear(box, decisions.map((d, i) => h("details", { class: "log-item", open: i === 0 },
      h("summary", {},
        h("span", { class: "log-id" }, d.decision_id),
        h("div", {},
          h("div", { class: "log-title" }, d.plan_description),
          h("div", { class: "log-sub" }, `${situation(d)} · approved by ${d.approved_by} · ${dateTime(d.approved_at)}`)),
        h("span", { class: "badge badge-ok" }, `Approved · ${usd(d.cost_impact_usd)}`)),
      h("div", { class: "log-body" },
        d.conditions?.length
          ? h("div", { class: "approve-conditions" },
              h("div", { class: "approve-conditions-title" }, "Approved with these conditions:"),
              h("ul", {}, d.conditions.map((c) => h("li", {}, h("strong", {}, `${c.specialist}: `), c.condition))))
          : null,
        d.note ? h("p", { class: "fine-print" }, `Approver's note: ${d.note}`) : null,
        d.grounding_check
          ? h("span", { class: `badge ${d.grounding_check.passed ? "badge-grounding" : "badge-warn"}` },
              d.grounding_check.passed ? "✓ Numbers in these documents traced to engine data" : "⚠ Check the numbers in these documents")
          : null,
        documentTabs(d.documents)))));
  } catch (e) {
    toast(`Could not load the decision log: ${e.message}`, { error: true });
    clear(box);
  }
}
