// Control Room: report a disruption, plant health, what needs action now, health trends,
// and the plant map ("where's the trouble?").

import { $, h, clear, toast, setLoading } from "../dom.js";
import { api } from "../api.js";
import { state, atRiskIds } from "../state.js";
import { num, usd, lineName, riskBadgeClass, plantTime, machineTypeLabel } from "../format.js";
import { renderPlantMap } from "../components/plant-map.js";
import * as workspace from "./workspace.js";

let ctx = { showView: () => {}, setPlantClock: () => {} };
let loaded = false;
let highlighted = new Set();

export function init(context) {
  ctx = context;
  $("#btn-simulate-fault").addEventListener("click", simulateFault);
  $("#quick-report").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#quick-report-input").value.trim();
    if (!text) { $("#quick-report-input").focus(); return; }
    $("#quick-report-input").value = "";
    ctx.showView("decide");
    workspace.analyze(text, { fresh: true });
  });
  $("#btn-about-data").addEventListener("click", openAbout);
  for (const el of document.querySelectorAll("#about-modal [data-close]")) el.addEventListener("click", closeAbout);
  $("#about-modal").addEventListener("click", (e) => { if (e.target.id === "about-modal") closeAbout(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#about-modal").hidden) closeAbout(); });
}

/** Re-fetch everything (e.g. after the viewer switches industry). */
export async function reload() {
  loaded = false;
  state.alerts = [];
  state.watch = [];
  highlighted = new Set();
  await load();
}

export async function onShow() {
  if (!loaded) await load();
}

async function load() {
  try {
    const [overview, watch, alerts] = await Promise.all([api.overview(), api.watchList(), api.alerts()]);
    state.overview = overview;
    state.watch = watch.machines;
    state.alerts = alerts.alerts;
    loaded = true;
    ctx.setPlantClock(overview.plant_clock);
    $("#plant-name").textContent = `${overview.plant.name} · ${overview.plant.location}`;
    renderKpis();
    renderAlerts();
    renderWatchList();
    renderMap();
  } catch (e) {
    toast(`Could not load plant data: ${e.message}`, { error: true });
  }
}

// ------------------------------------------------------------------ health & delivery tiles

/** Lines with reported faults applied: a machine with a fault alert is down until repaired. */
function liveLines() {
  const faulted = new Set(state.alerts.filter((a) => a.kind === "fault").map((a) => a.machine_id));
  return state.overview.lines.map((l) => ({
    ...l,
    machines: l.machines.map((m) => (faulted.has(m.machine_id) ? { ...m, status: "DOWN", reported: true } : m)),
  }));
}

/** Every machine in exactly one group, so the groups always add up to the total. */
function machineGroups() {
  const machines = liveLines().flatMap((l) => l.machines.map((m) => ({ ...m, line_name: l.name })));
  const risky = atRiskIds();
  const stopped = machines.filter((m) => ["MAINTENANCE", "DOWN"].includes(m.status));
  const atRisk = machines.filter((m) => !stopped.includes(m) && (m.status === "DEGRADED" || risky.has(m.machine_id)));
  const normal = machines.filter((m) => !stopped.includes(m) && !atRisk.includes(m));
  return { machines, normal, atRisk, stopped };
}

function renderKpis() {
  const k = state.overview.kpis;
  const { machines, normal, atRisk, stopped } = machineGroups();
  const total = machines.length;

  const group = (key, label, list, definition, describe) => h("button", {
    class: `health-group hg-${key}${list.length && highlighted.size && list.every((m) => highlighted.has(m.machine_id)) ? " active" : ""}`,
    title: `${definition} Click to show them on the plant map.`,
    onclick: () => showMachines(list),
  },
    h("span", { class: "hg-value" }, String(list.length)),
    h("span", { class: "hg-label" }, label),
    h("span", { class: "hg-desc" }, describe(list)));

  const bar = h("div", { class: "health-bar", role: "img", "aria-label": `${normal.length} running normally, ${atRisk.length} at risk, ${stopped.length} stopped, of ${total}` },
    [["normal", normal], ["risk", atRisk], ["stopped", stopped]].map(([cls, list]) =>
      list.length ? h("span", { class: `hb-${cls}`, style: `flex:${list.length}` }) : null));

  const ids = (list) => list.map((m) => m.machine_id).join(", ");
  const stoppedText = (list) => list.length
    ? list.map((m) => `${m.machine_id} ${m.reported ? "fault reported" : m.status === "DOWN" ? "breakdown" : "planned maintenance"}`).join(" · ")
    : "None";
  const riskText = (list) => list.length ? `Working now, but likely to fail soon: ${ids(list)}` : "No warning signs";

  clear($("#kpi-row"),
    h("div", { class: "kpi kpi-health" },
      h("div", { class: "kpi-label" }, `Machine health · ${total} machines`),
      bar,
      h("div", { class: "health-groups" },
        group("normal", "Running normally", normal, "Working, with no warning signs.", () => "Working, no warning signs"),
        group("risk", "At risk", atRisk, "Working now, but degraded or predicted to reach an alarm soon.", riskText),
        group("stopped", "Stopped", stopped, "Not producing: planned maintenance or a breakdown.", stoppedText))),
    h("div", { class: "kpi kpi-delivery" },
      h("div", { class: "kpi-label" }, "Delivery pressure"),
      h("div", { class: "kpi-value" }, String(k.orders_due_24h), h("small", {}, " orders due in 24 h")),
      h("div", { class: "kpi-foot" },
        `${k.high_priority_due_24h} of them high-priority · ${k.orders_high_priority} high-priority orders in total (of ${k.orders_active} active)`),
      h("div", { class: "kpi-hint" }, "These deadlines are what a breakdown puts at risk.")),
  );
}

function showMachines(list) {
  highlighted = new Set(list.map((m) => m.machine_id));
  renderKpis();
  renderMap();
  $("#line-map").scrollIntoView({ behavior: "smooth", block: "center" });
}

// ------------------------------------------------------------------ needs action now

function renderAlerts(newId) {
  const list = $("#alert-list");
  const badge = $("#nav-alert-count");
  const urgent = state.alerts.filter((a) => ["HIGH", "CRITICAL"].includes(a.severity)).length;
  badge.hidden = urgent === 0;
  badge.textContent = String(urgent);

  if (!state.alerts.length) {
    clear(list, h("div", { class: "empty-note" }, "Nothing needs action right now. Use “🧪 Demo: simulate a machine fault” to see how a shop-floor event is handled."));
    return;
  }
  clear(list, state.alerts.map((a) => {
    const plan = a.prebuilt_plan;
    const isNew = a.alert_id === newId;
    return h("article", { class: `alert-item sev-${a.severity} kind-${a.kind}${isNew ? " alert-new" : ""}`, id: `alert-${a.alert_id}` },
      h("span", { class: "stripe", "aria-hidden": "true" }),
      h("div", {},
        h("div", { class: "alert-title" }, a.title),
        h("div", { class: "alert-meta" },
          h("span", { class: `badge ${a.kind === "predictive" ? "badge-violet" : riskBadgeClass(a.severity)}` }, a.kind === "predictive" ? "Predicted" : `Fault · ${a.severity}`),
          h("span", {}, `Repair estimate ${a.estimated_repair_hours[0]}–${a.estimated_repair_hours[1]} h`),
          h("span", {}, plantTime(a.raised_at))),
        h("div", { class: "alert-detail" }, a.detail),
        plan && plan.plan_id ? h("div", { class: "alert-detail" }, h("strong", {}, "Backup plan ready: "), `${plan.description} · ${usd(plan.cost_impact_usd)}`) : null,
        h("div", { class: "alert-origin" }, a.kind === "predictive"
          ? "Predicted from machine health trends"
          : "Reported automatically by the machine controller (simulated)"),
        isNew ? h("div", { class: "alert-hint" }, "New fault received. Click “Analyze impact” to respond.") : null),
      h("button", {
        class: `btn btn-primary btn-sm${isNew ? " pulse" : ""}`,
        onclick: () => analyzeAlert(a),
        "aria-label": `Analyze impact of ${a.title}`,
      }, "Analyze impact →"));
  }));
  if (newId) document.getElementById(`alert-${newId}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function analyzeAlert(alert) {
  if (!alert.analysis_prompt) return;
  ctx.showView("decide");
  workspace.analyze(alert.analysis_prompt, { fresh: true });
}

async function simulateFault() {
  const btn = $("#btn-simulate-fault");
  setLoading(btn, true);
  try {
    const alert = await api.simulateFault();
    const res = await api.alerts();
    state.alerts = res.alerts;
    renderAlerts(alert.alert_id);
    renderKpis();
    renderMap();
    toast(`New fault received: ${alert.title}`);
  } catch (e) {
    toast(`Could not simulate fault: ${e.message}`, { error: true });
  } finally {
    setLoading(btn, false);
  }
}

// ------------------------------------------------------------------ health trends

function sparkline(values, threshold) {
  const w = 120, hgt = 32, pad = 2;
  const max = Math.max(threshold ?? -Infinity, ...values);
  const min = Math.min(...values);
  const sx = (i) => pad + (i / (values.length - 1)) * (w - pad * 2);
  const sy = (v) => hgt - pad - ((v - min) / (max - min || 1)) * (hgt - pad * 2);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${hgt}`);
  svg.setAttribute("class", "spark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", "Vibration trend, last 48 hours; dashed line is the alarm level");
  if (threshold !== undefined && threshold <= max) {
    const t = document.createElementNS(ns, "line");
    t.setAttribute("x1", pad); t.setAttribute("x2", w - pad);
    t.setAttribute("y1", sy(threshold)); t.setAttribute("y2", sy(threshold));
    t.setAttribute("class", "threshold");
    svg.appendChild(t);
  }
  const path = document.createElementNS(ns, "path");
  path.setAttribute("d", values.map((v, i) => `${i ? "L" : "M"}${sx(i).toFixed(1)},${sy(v).toFixed(1)}`).join(" "));
  svg.appendChild(path);
  return svg;
}

function jumpToAlert(machineId, e) {
  e.stopPropagation();
  const alert = state.alerts.find((a) => a.machine_id === machineId && a.kind === "predictive");
  const el = alert && document.getElementById(`alert-${alert.alert_id}`);
  if (!el) return;
  el.scrollIntoView({ behavior: "smooth", block: "center" });
  el.classList.remove("alert-new");
  void el.offsetWidth;
  el.classList.add("alert-new");
}

function renderWatchList() {
  const list = $("#watch-list");
  const items = state.watch.filter((w) => w.risk_level !== "OFFLINE");
  if (!items.length) {
    clear(list, h("div", { class: "empty-note" }, "All running machines are stable. No alarms forecast within 7 days."));
    return;
  }
  const alerted = new Set(state.alerts.filter((a) => a.kind === "predictive").map((a) => a.machine_id));
  clear(list, items.map((w, idx) => {
    const body = h("div", { class: "watch-body", hidden: idx !== 0 });
    const vib = w.signals.vibration_mm_s;
    const toggle = h("button", {
      class: "watch-row",
      "aria-expanded": idx === 0 ? "true" : "false",
      onclick: () => {
        body.hidden = !body.hidden;
        toggle.setAttribute("aria-expanded", String(!body.hidden));
      },
    },
      h("div", {},
        h("div", { class: "watch-name" },
          h("span", { class: "mono" }, w.machine_id),
          h("span", { class: `badge ${riskBadgeClass(w.risk_level)}` }, w.risk_level),
          w.status !== "HEALTHY" ? h("span", { class: "badge" }, w.status.toLowerCase()) : h("span", { class: "badge badge-ok" }, `status healthy · ${num(w.health_score, 1)}%`),
          alerted.has(w.machine_id)
            ? h("span", { class: "badge badge-danger alert-link", role: "link", tabindex: "0", title: "This machine has been promoted to Needs action now",
                onclick: (e) => jumpToAlert(w.machine_id, e) }, "🔔 Alert raised")
            : null),
        h("div", { class: "watch-sub" }, `${machineTypeLabel(w.machine_type)} · ${lineName(w.line_id)} · ${w.driver_signal || ""} trend`)),
      sparkline(w.sparkline, vib.threshold),
      h("div", { class: "watch-eta" }, h("strong", {}, w.hours_to_alarm !== null ? `~${num(w.hours_to_alarm)} h` : "—"), "to alarm"));

    body.append(
      h("p", {}, w.explanation),
      w.prebuilt_plan ? h("div", { class: "prebuilt" },
        h("div", { class: "prebuilt-title" }, "Pre-built backup plan"),
        h("div", {}, h("strong", {}, w.prebuilt_plan.description)),
        h("div", { class: "watch-sub" },
          `If it fails (${w.prebuilt_plan.repair_range_hours[0]}–${w.prebuilt_plan.repair_range_hours[1]} h repair): `,
          `${w.prebuilt_plan.orders_at_risk} orders exposed (${w.prebuilt_plan.high_priority_at_risk} high-priority) · extra cost ${usd(w.prebuilt_plan.cost_impact_usd)}`)) : null,
      w.prebuilt_plan ? h("div", {},
        h("button", { class: "btn btn-secondary btn-sm", onclick: () => { ctx.showView("decide"); workspace.analyze(w.prebuilt_plan.analysis_prompt, { fresh: true }); } }, "Open full analysis →")) : null,
    );
    return h("div", { class: "watch-item", id: `watch-${w.machine_id}` }, toggle, body);
  }));
}

// ------------------------------------------------------------------ plant map

function operationNoun(type) {
  const code = (type || "").split("_Unit_")[0];
  return state.pack?.operations?.find((o) => o.code === code)?.noun || machineTypeLabel(type);
}

function renderMap() {
  const risky = atRiskIds();
  renderPlantMap($("#line-map"), {
    lines: liveLines(),
    atRisk: risky,
    highlight: highlighted,
    onMachine: (m, line) => {
      ctx.showView("decide");
      workspace.prefill(`${m.machine_id} (${operationNoun(m.type)}, ${lineName(line.line_id)}) will be down for `);
    },
  });
}

// ------------------------------------------------------------------ about the demo data

function openAbout() {
  const o = state.overview;
  const pack = state.pack;
  const machines = o ? o.kpis.machines_total : 36;
  const orders = o ? o.kpis.orders_active : 55;
  clear($("#about-body"),
    h("p", {}, h("strong", {}, `${o?.plant.name || "The plant"} is synthetic.`),
      " Real factory data is confidential, so this demo uses a realistic, made-up plant generated by a seeded script: the same plant every time, so results are reproducible."),
    h("ul", { class: "plain-list about-list" },
      h("li", {}, `3 production lines, 12 stations, ${machines} machines`),
      h("li", {}, `${orders} customer orders with priorities and due dates, and 220 schedule entries`),
      h("li", {}, "25 materials, 15 suppliers and 5 maintenance events"),
      h("li", {}, "18 engineering documents (manuals, SOPs, quality rules, incident reports) written for this plant"),
      h("li", {}, "72 hours of hourly sensor history per machine (vibration, temperature, current, health)")),
    h("p", {}, h("strong", {}, "Same engine, any factory. "),
      `Switch the Industry selector in the header to see the same engine run a different plant${pack ? ` (you are viewing ${pack.label})` : ""}. All numbers are calculated by the deterministic engine; AI explains and reviews them.`),
    h("p", { class: "fine-print" }, "In a real deployment the same structure comes from the plant's ERP, MES and maintenance systems, with sensor data streamed into BigQuery."));
  $("#about-modal").hidden = false;
  $("#about-modal [data-close]").focus();
}

function closeAbout() {
  $("#about-modal").hidden = true;
  $("#btn-about-data").focus();
}
