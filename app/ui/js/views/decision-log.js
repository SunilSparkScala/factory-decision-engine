// Decision Log: audit trail of human-approved decisions.

import { $, h, clear, toast } from "../dom.js";
import { api } from "../api.js";
import { usd, dateTime } from "../format.js";
import { documentTabs } from "./workspace.js";

export function init() {}

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
          h("div", { class: "log-sub" }, `${d.scenario} · approved by ${d.approved_by} · ${dateTime(d.approved_at)}`)),
        h("span", { class: "badge badge-ok" }, `Approved · ${usd(d.cost_impact_usd)}`)),
      h("div", { class: "log-body" },
        d.note ? h("p", { class: "fine-print" }, `Note: ${d.note}`) : null,
        documentTabs(d.documents)))));
  } catch (e) {
    toast(`Could not load the decision log: ${e.message}`, { error: true });
    clear(box);
  }
}
