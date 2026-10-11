// Control Room: plant KPIs, alert inbox, early-warning watch list and line map.

import { $, h, clear, toast, setLoading } from "../dom.js";
import { api } from "../api.js";
import { state, atRiskIds } from "../state.js";
import { num, usd, lineName, riskBadgeClass, plantTime } from "../format.js";
import * as workspace from "./workspace.js";

let ctx = { showView: () => {}, setPlantClock: () => {} };
let loaded = false;

export function init(context) {
  ctx = context;
  $("#btn-simulate-fault").addEventListener("click", simulateFault);
  $("#btn-new-scenario").addEventListener("click", () => {
    ctx.showView("decide");
    workspace.focusInput();
  });
}

/** Re-fetch everything (e.g. after the viewer switches industry). */
export async function reload() {
  loaded = false;
  state.alerts = [];
  state.watch = [];
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
    state.alerts.forEach((a) => state.seenAlertIds.add(a.alert_id));
    loaded = true;
    ctx.setPlantClock(overview.plant_clock);
    $("#plant-name").textContent = `${overview.plant.name} · ${overview.plant.location} · plant status, incoming alerts and early warnings.`;
    renderKpis();
    renderAlerts();
    renderWatchList();
    renderLines();
  } catch (e) {
    toast(`Could not load plant data: ${e.message}`, { error: true });
  }
}

function renderKpis() {
  const k = state.overview.kpis;
  const by = k.machines_by_status;
  const running = (by.HEALTHY || 0) + (by.DEGRADED || 0);
  const offline = (by.MAINTENANCE || 0) + (by.DOWN || 0);
  const risky = state.watch.filter((w) => ["CRITICAL", "HIGH", "ELEVATED"].includes(w.risk_level));
  const soon = risky.filter((w) => w.hours_to_alarm !== null && w.hours_to_alarm <= 24);
  const offlineIds = state.overview.lines
    .flatMap((l) => l.machines)
    .filter((m) => ["MAINTENANCE", "DOWN"].includes(m.status))
    .map((m) => `${m.machine_id} ${m.status.toLowerCase()}`);

  const kpi = (label, value, foot, attention = false) =>
    h("div", { class: `kpi${attention ? " attention" : ""}` },
      h("div", { class: "kpi-label" }, label),
      h("div", { class: "kpi-value" }, value),
      h("div", { class: "kpi-foot" }, foot));

  clear($("#kpi-row"),
    kpi("Machines running", h("span", {}, String(running), h("small", {}, ` / ${k.machines_total}`)), `${by.DEGRADED || 0} running degraded`),
    kpi("Need attention", String(risky.length), soon.length ? `${soon.length} predicted to alarm within 24 h` : "No alarms forecast within 24 h", risky.length > 0),
    kpi("Offline now", String(offline), offlineIds.join(" · ") || "All machines available", offline > 0),
    kpi("Orders due in 24 h", String(k.orders_due_24h), `${k.high_priority_due_24h} high-priority`),
    kpi("High-priority orders", String(k.orders_high_priority), `of ${k.orders_active} active orders`),
  );
}

function renderAlerts(newId) {
  const list = $("#alert-list");
  const badge = $("#nav-alert-count");
  const urgent = state.alerts.filter((a) => ["HIGH", "CRITICAL"].includes(a.severity)).length;
  badge.hidden = urgent === 0;
  badge.textContent = String(urgent);

  if (!state.alerts.length) {
    clear(list, h("div", { class: "empty-note" }, "No active alerts. Use “Simulate incoming fault” to see how a shop-floor event is handled."));
    return;
  }
  clear(list, state.alerts.map((a) => {
    const plan = a.prebuilt_plan;
    return h("article", { class: `alert-item sev-${a.severity} kind-${a.kind}${a.alert_id === newId ? " alert-new" : ""}` },
      h("span", { class: "stripe", "aria-hidden": "true" }),
      h("div", {},
        h("div", { class: "alert-title" }, a.title),
        h("div", { class: "alert-meta" },
          h("span", { class: `badge ${a.kind === "predictive" ? "badge-violet" : riskBadgeClass(a.severity)}` }, a.kind === "predictive" ? "Predicted" : `Fault · ${a.severity}`),
          h("span", {}, `Repair estimate ${a.estimated_repair_hours[0]}–${a.estimated_repair_hours[1]} h`),
          h("span", {}, plantTime(a.raised_at))),
        h("div", { class: "alert-detail" }, a.detail),
        plan && plan.plan_id ? h("div", { class: "alert-detail" }, h("strong", {}, "Backup plan ready: "), `${plan.description} · ${usd(plan.cost_impact_usd)}`) : null),
      h("button", {
        class: "btn btn-primary btn-sm",
        onclick: () => analyzeAlert(a),
        "aria-label": `Analyze impact of ${a.title}`,
      }, "Analyze impact →"));
  }));
}

function analyzeAlert(alert) {
  if (!alert.analysis_prompt) return;
  ctx.showView("decide");
  workspace.analyze(alert.analysis_prompt);
}

async function simulateFault() {
  const btn = $("#btn-simulate-fault");
  setLoading(btn, true);
  try {
    const alert = await api.simulateFault();
    const res = await api.alerts();
    state.alerts = res.alerts;
    renderAlerts(alert.alert_id);
    toast(`New fault received: ${alert.title}`);
  } catch (e) {
    toast(`Could not simulate fault: ${e.message}`, { error: true });
  } finally {
    setLoading(btn, false);
  }
}

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
  svg.setAttribute("aria-label", "Vibration trend, last 48 hours");
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

function renderWatchList() {
  const list = $("#watch-list");
  const items = state.watch.filter((w) => w.risk_level !== "OFFLINE");
  if (!items.length) {
    clear(list, h("div", { class: "empty-note" }, "All running machines are stable. No alarms forecast within 7 days."));
    return;
  }
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
          w.status !== "HEALTHY" ? h("span", { class: "badge" }, w.status.toLowerCase()) : h("span", { class: "badge badge-ok" }, `status healthy · ${num(w.health_score, 1)}%`)),
        h("div", { class: "watch-sub" }, `${w.machine_type.replace(/_/g, " ")} · ${lineName(w.line_id)} · ${w.driver_signal || ""} trend`)),
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
        h("button", { class: "btn btn-secondary btn-sm", onclick: () => { ctx.showView("decide"); workspace.analyze(w.prebuilt_plan.analysis_prompt); } }, "Open full analysis →")) : null,
    );
    return h("div", { class: "watch-item" }, toggle, body);
  }));
}

function renderLines() {
  const risky = atRiskIds();
  clear($("#line-map"), state.overview.lines.map((line) => {
    const stations = new Map();
    for (const m of line.machines) {
      if (!stations.has(m.station)) stations.set(m.station, []);
      stations.get(m.station).push(m);
    }
    return h("div", { class: "line-card" },
      h("div", { class: "line-head" },
        h("span", { class: "line-name" }, line.name),
        h("span", { class: "line-sub" }, `${line.order_count} orders · ${line.high_priority_orders} high-priority`)),
      Array.from(stations.entries()).map(([station, machines]) =>
        h("div", { class: "station" },
          h("span", {}, station.replace(/^Station [A-Z]\d - /, "")),
          h("div", { class: "station-machines" }, machines.map((m) => h("button", {
            class: `machine-dot${risky.has(m.machine_id) ? " at-risk" : ""}`,
            title: `${m.machine_id} · ${m.type.replace(/_/g, " ")} · ${m.status.toLowerCase()} · health ${num(m.health, 1)}%${risky.has(m.machine_id) ? " · predicted risk" : ""}`,
            onclick: () => {
              ctx.showView("decide");
              workspace.prefill(`${m.machine_id} (${m.type.replace(/_/g, " ")}, ${station}) will be unavailable for `);
            },
          }, h("i", { class: `dot dot-${m.status.toLowerCase()}`, "aria-hidden": "true" }), m.machine_id))))));
  }));
}
