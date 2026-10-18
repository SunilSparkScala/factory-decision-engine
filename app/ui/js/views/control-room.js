// Factory Overview (Control Room): report a disruption, machine health, delivery pressure,
// what needs action now, machine health trends, and the plant map ("where's the trouble?").
// Every number shown comes from the API; the alert numbers come from the deterministic engine.

import { $, h, clear, toast, setLoading } from "../dom.js";
import { api } from "../api.js";
import { state, atRiskIds } from "../state.js";
import { num, usd, lineName, riskBadgeClass, plantTime, machineTypeLabel } from "../format.js";
import { renderPlantMap } from "../components/plant-map.js";
import * as workspace from "./workspace.js";
import { attachMic } from "../components/voice.js";

let ctx = { showView: () => {}, setPlantClock: () => {} };
let loaded = false;
let highlighted = new Set();
let modalMachine = null;

export function init(context) {
  ctx = context;
  $("#btn-simulate-fault").addEventListener("click", simulateFault);
  $("#btn-clear-faults").addEventListener("click", clearFaults);
  // An approved plan resolves its fault elsewhere; refresh the inbox next time it is shown
  window.addEventListener("fde:alerts-changed", () => { loaded = false; });
  $("#quick-report").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = $("#quick-report-input").value.trim();
    if (!text) { $("#quick-report-input").focus(); return; }
    $("#quick-report-input").value = "";
    ctx.showView("decide");
    workspace.detachIncident();
    workspace.analyze(text, { fresh: true });
  });
  attachMic($("#quick-report-input"), { label: "Speak the report instead of typing" });
  $("#btn-about-data").addEventListener("click", openAbout);
  $("#btn-modal-simulate-machine").addEventListener("click", () => {
    if (!modalMachine) return;
    const m = modalMachine;
    $("#machine-modal").hidden = true;
    ctx.showView("decide");
    workspace.prefill(`${m.machine_id} (${operationNoun(m.type)}, ${lineName(m.line_id)}) will be down for `);
  });
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

// ------------------------------------------------------------------ machine health & delivery

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
  const machines = liveLines().flatMap((l) => l.machines);
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
  const ids = (list) => list.map((m) => m.machine_id).join(", ");

  $("#fleet-total-count").textContent = `${total} machines`;
  clear($("#fleet-health-bar"),
    [["normal", normal], ["risk", atRisk], ["stopped", stopped]].map(([cls, list]) =>
      list.length ? h("span", { class: `hb-${cls}`, style: `flex:${list.length}` }) : null));
  $("#fleet-health-bar").setAttribute("aria-label", `${normal.length} running normally, ${atRisk.length} at risk, ${stopped.length} stopped, of ${total}`);

  const group = (key, label, list, describe) => h("button", {
    class: `health-group hg-${key}${list.length && highlighted.size && list.every((m) => highlighted.has(m.machine_id)) ? " active" : ""}`,
    title: "Click to show these machines on the plant map",
    onclick: () => showMachines(list),
  },
    h("span", { class: "hg-value" }, String(list.length)),
    h("span", { class: "hg-label" }, label),
    h("span", { class: "hg-desc" }, describe(list)));

  clear($("#health-groups"),
    group("normal", "Running normally", normal, () => "Working, no warning signs"),
    group("risk", "At risk", atRisk, (list) => (list.length ? `Working now, but showing warning signs: ${ids(list)}` : "No warning signs")),
    group("stopped", "Stopped", stopped, (list) => (list.length
      ? list.map((m) => `${m.machine_id} ${m.reported ? "fault reported" : m.status === "DOWN" ? "breakdown · repair under way" : "planned maintenance"}`).join(" · ")
      : "None")));

  const predicted = atRisk.filter((m) => m.status !== "DEGRADED");
  const degraded = atRisk.filter((m) => m.status === "DEGRADED");
  $("#fleet-note-text").textContent = atRisk.length
    ? [degraded.length ? `${ids(degraded)} already degraded` : null,
       predicted.length ? `${ids(predicted)} still reports healthy but its sensor trend points to an alarm` : null].filter(Boolean).join("; ") + "."
    : "No machine is degraded or trending toward an alarm.";

  $("#orders-total-badge").textContent = `${k.orders_active} active orders`;
  $("#cnt-due-24h").textContent = String(k.orders_due_24h);
  $("#cnt-high-pri-due").textContent = String(k.high_priority_due_24h);
  $("#cnt-high-pri-total").textContent = String(k.orders_high_priority);

  const urgent = state.alerts.filter((a) => ["HIGH", "CRITICAL"].includes(a.severity)).length;
  const pill = $("#cockpit-live-status-pill");
  pill.textContent = state.alerts.length
    ? `● ${state.alerts.length} need${state.alerts.length === 1 ? "s" : ""} action (${urgent} urgent)`
    : "● Nothing needs action";
  pill.className = `badge-status-dot${urgent ? " pulse" : ""}`;
}

function showMachines(list) {
  highlighted = new Set(list.map((m) => m.machine_id));
  renderKpis();
  renderMap();
  $("#sec-map").scrollIntoView({ behavior: "smooth", block: "start" });
}

// ------------------------------------------------------------------ needs action now

function etaText(hoursToAlarm, range) {
  if (hoursToAlarm === null || hoursToAlarm === undefined) return "—";
  const lo = range?.[0] != null ? Math.round(range[0]) : null;
  const hi = range?.[1] != null ? Math.round(range[1]) : null;
  const span = lo !== null && hi !== null && lo !== hi ? ` (${lo}–${hi} h)` : "";
  return `~${num(hoursToAlarm)} h${span}`;
}

function renderAlerts(newId) {
  const list = $("#alert-list");
  const urgent = state.alerts.filter((a) => ["HIGH", "CRITICAL"].includes(a.severity)).length;
  $("#btn-clear-faults").hidden = !state.alerts.some((a) => a.kind === "fault");
  const badge = $("#nav-alert-count");
  badge.hidden = urgent === 0;
  badge.textContent = String(urgent);

  if (!state.alerts.length) {
    clear(list, h("div", { class: "empty-note" }, "Nothing needs action right now. Use “🧪 Demo: simulate a machine fault” to see how a shop-floor event is handled."));
    return;
  }
  clear(list, state.alerts.map((a) => {
    const isNew = a.alert_id === newId;
    const isPred = a.kind === "predictive";
    const orders = a.affected_orders_count != null
      ? `${a.affected_orders_count} orders (${a.high_priority_orders_count ?? 0} high-priority)`
      : "—";
    return h("div", { class: `incident-row alert-item sev-${a.severity} kind-${a.kind}${isNew ? " alert-new" : ""}`, id: `alert-${a.alert_id}` },
      h("div", { class: "inc-col-rank" },
        h("span", { class: `badge ${isPred ? "badge-violet" : riskBadgeClass(a.severity)}` }, isPred ? "Predicted" : `Fault · ${a.severity}`)),
      h("div", { class: "inc-col-asset" },
        h("strong", { class: "inc-asset-id mono" }, a.machine_id),
        a.line_id ? h("span", { class: "inc-asset-line" }, lineName(a.line_id)) : null),
      h("div", { class: "inc-col-window" },
        h("span", { class: "inc-metric-lbl" }, isPred ? "Alarm expected in" : "Repair estimate"),
        h("span", { class: "inc-metric-val mono" }, isPred
          ? etaText(a.hours_to_alarm, a.hours_to_alarm_range)
          : `${a.estimated_repair_hours[0]}–${a.estimated_repair_hours[1]} h`)),
      h("div", { class: "inc-col-impact" },
        h("span", { class: "inc-metric-lbl" }, isPred ? "If it fails" : "Orders affected"),
        h("span", { class: "inc-metric-val highlight" }, orders)),
      h("div", { class: "inc-col-desc" },
        h("span", { class: "inc-desc-title" }, a.title),
        a.recommended_action ? h("span", { class: "inc-desc-sub" }, h("strong", {}, isPred ? "Backup plan ready: " : "Likely best response: "), a.recommended_action, a.prebuilt_plan ? ` · ${usd(a.prebuilt_plan.cost_impact_usd)}` : "") : null,
        h("span", { class: "alert-origin" }, isPred ? "Predicted from machine health trends" : `Reported automatically by the machine controller (simulated) · ${plantTime(a.raised_at)}`),
        isNew ? h("span", { class: "alert-hint" }, "New fault received. Click “Analyze impact” to respond.") : null),
      h("div", { class: "inc-col-actions" },
        h("button", {
          class: `btn btn-primary btn-sm btn-analyze${isNew ? " pulse" : ""}`,
          onclick: () => analyzeAlert(a),
          "aria-label": `Analyze impact of ${a.title}`,
        }, "Analyze impact →")));
  }));
  if (newId) document.getElementById(`alert-${newId}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

/** Open the top predictive alert (the tour's "Try it now"). */
export function analyzeTopPrediction() {
  const alert = state.alerts.find((a) => a.kind === "predictive");
  if (alert) analyzeAlert(alert);
}

function analyzeAlert(alert) {
  ctx.showView("decide");
  workspace.analyzeWithIncident(alert);
}

async function clearFaults() {
  try {
    const { removed } = await api.clearFaults();
    state.alerts = (await api.alerts()).alerts;
    renderAlerts();
    renderKpis();
    renderMap();
    toast(removed ? `Cleared ${removed} demo fault${removed === 1 ? "" : "s"}.` : "No demo faults to clear.");
  } catch (e) {
    toast(`Could not clear demo faults: ${e.message}`, { error: true });
  }
}

async function simulateFault() {
  const btn = $("#btn-simulate-fault");
  setLoading(btn, true);
  try {
    const alert = await api.simulateFault();
    state.alerts = (await api.alerts()).alerts;
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

// ------------------------------------------------------------------ machine health trends

function sparkline(values, threshold, label = "Sensor") {
  const w = 120, hgt = 32, pad = 2;
  // The alarm line can sit above (vibration rising) or below (health score falling) the readings
  const max = Math.max(threshold ?? -Infinity, ...values);
  const min = Math.min(threshold ?? Infinity, ...values);
  const sx = (i) => pad + (i / (values.length - 1)) * (w - pad * 2);
  const sy = (v) => hgt - pad - ((v - min) / (max - min || 1)) * (hgt - pad * 2);
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", `0 0 ${w} ${hgt}`);
  svg.setAttribute("class", "spark");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", `${label} trend, last 48 hours; dashed line is the alarm level`);
  if (threshold !== undefined) {
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

/** "16–18 h" under the estimate; nothing when the range rounds to the estimate itself. */
function etaRange(range) {
  if (!range || range[0] === null) return null;
  const [lo, hi] = range.map((v) => (v === null ? null : Math.round(v)));
  if (hi === null) return h("span", { class: "eta-range" }, `≥ ${lo} h`);
  return lo === hi ? null : h("span", { class: "eta-range" }, `${lo}–${hi} h`);
}

function renderWatchList() {
  const list = $("#watch-list");
  const items = state.watch.filter((w) => w.risk_level !== "OFFLINE");
  if (!items.length) {
    clear(list, h("div", { class: "empty-note" }, "All running machines are stable. No alarms forecast within 7 days."));
    return;
  }
  const alerted = new Set(state.alerts.filter((a) => a.kind === "predictive").map((a) => a.machine_id));
  clear(list, items.map((w) => {
    // Rows open only on click; a machine already in "Needs action now" keeps its backup plan there
    const promoted = alerted.has(w.machine_id);
    const body = h("div", { class: "watch-body", hidden: true });
    const vib = w.signals.vibration_mm_s;
    const toggle = h("button", {
      class: "watch-row",
      "aria-expanded": "false",
      onclick: () => {
        body.hidden = !body.hidden;
        toggle.setAttribute("aria-expanded", String(!body.hidden));
      },
    },
      h("div", {},
        h("div", { class: "watch-name" },
          h("span", { class: "mono" }, w.machine_id),
          h("span", { class: `badge ${riskBadgeClass(w.risk_level)}` }, w.risk_level),
          w.status !== "HEALTHY" ? h("span", { class: "badge" }, w.status.toLowerCase()) : h("span", { class: "badge badge-ok" }, `status healthy · health ${num(w.health_score, 1)}%`),
          promoted
            ? h("span", { class: "badge badge-danger alert-link", role: "link", tabindex: "0", title: "Predicted to hit its alarm within 36 hours: go to its alert",
                onclick: (e) => jumpToAlert(w.machine_id, e) }, "↑ In Needs action now")
            : null),
        h("div", { class: "watch-sub" }, `${machineTypeLabel(w.machine_type)} · ${lineName(w.line_id)} · ${w.driver_signal || ""} trend`)),
      sparkline(w.sparkline, w.sparkline_threshold ?? vib.threshold, w.sparkline_signal),
      h("div", {
        class: "watch-eta",
        title: w.trend_model ? `${w.trend_model === "quadratic" ? "Accelerating-curve" : "Straight-line"} trend on the last 24 h; range from sensor noise` : null,
      },
        h("strong", {}, w.hours_to_alarm !== null ? `~${num(w.hours_to_alarm)} h` : "—"),
        etaRange(w.hours_to_alarm_range),
        "to alarm"));

    body.append(...[
      h("p", {}, w.explanation),
      promoted ? h("button", { class: "link-btn watch-jump", onclick: (e) => jumpToAlert(w.machine_id, e) },
        "Its backup plan is in “Needs action now” ↑") : null,
      !promoted && w.prebuilt_plan ? h("div", { class: "prebuilt" },
        h("div", { class: "prebuilt-title" }, "Pre-built backup plan"),
        h("div", {}, h("strong", {}, w.prebuilt_plan.description)),
        h("div", { class: "watch-sub" },
          `If it fails (${w.prebuilt_plan.repair_range_hours[0]}–${w.prebuilt_plan.repair_range_hours[1]} h repair): `,
          `${w.prebuilt_plan.orders_at_risk} orders exposed (${w.prebuilt_plan.high_priority_at_risk} high-priority) · extra cost ${usd(w.prebuilt_plan.cost_impact_usd)}`)) : null,
      !promoted && w.prebuilt_plan ? h("div", {},
        h("button", { class: "btn btn-secondary btn-sm", onclick: () => { ctx.showView("decide"); workspace.detachIncident(); workspace.analyze(w.prebuilt_plan.analysis_prompt, { fresh: true }); } }, "Open full analysis →")) : null,
    ].filter(Boolean));
    return h("div", { class: "watch-item", id: `watch-${w.machine_id}` }, toggle, body);
  }));
}

// ------------------------------------------------------------------ plant map & machine details

function operationNoun(type) {
  const code = (type || "").split("_Unit_")[0];
  return state.pack?.operations?.find((o) => o.code === code)?.noun || machineTypeLabel(type);
}

function renderMap() {
  renderPlantMap($("#line-map"), {
    lines: liveLines(),
    atRisk: atRiskIds(),
    highlight: highlighted,
    onMachine: (m, line) => openMachineModal({ ...m, line_id: line.line_id }),
  });
}

async function openMachineModal(machine) {
  modalMachine = machine;
  const body = $("#machine-modal-body");
  $("#mm-badge-id").textContent = machine.machine_id;
  $("#machine-modal").hidden = false;
  clear(body, h("div", { class: "skeleton-block", style: "height:200px;" }));
  const attr = (label, value) => h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, label), h("span", { class: "attr-val" }, value));
  try {
    const d = await api.machineDetail(machine.machine_id);
    const t = d.telemetry || {};
    clear(body,
      h("div", { class: "modal-grid-2" },
        h("div", {},
          h("div", { class: "modal-section-title" }, "Machine"),
          h("div", { class: "detail-attr-grid" },
            attr("Type", machineTypeLabel(d.machine_type)),
            attr("Status", humanStatus(machine)),
            attr("Line", d.line.name),
            attr("Station", d.station.name),
            attr("Rated capacity", `${num(d.capacity_per_hour)} units/h`),
            attr("Health score", `${num(d.health_score, 1)}%`))),
        h("div", {},
          h("div", { class: "modal-section-title" }, "Sensor trend"),
          h("div", { class: "detail-attr-grid" },
            attr("Risk level", t.risk_level || "—"),
            attr("Alarm expected in", t.hours_to_alarm != null ? etaText(t.hours_to_alarm, t.hours_to_alarm_range) : "No alarm forecast")),
          h("p", { class: "modal-note" }, t.explanation || ""))),
      h("div", { class: "modal-section-title", style: "margin-top:16px" }, "Orders on this machine's line"),
      d.scheduled_orders.length
        ? h("table", { class: "data-table" },
            h("thead", {}, h("tr", {}, ["Order", "Model", "Qty", "Priority", "Due"].map((x) => h("th", {}, x)))),
            h("tbody", {}, d.scheduled_orders.map((o) => h("tr", {},
              h("td", { class: "mono" }, o.order_id),
              h("td", {}, o.product_model),
              h("td", { class: "num" }, num(o.quantity)),
              h("td", {}, h("span", { class: `badge ${o.priority === "HIGH" ? "badge-danger" : ""}` }, o.priority.toLowerCase())),
              h("td", {}, plantTime(o.due_time))))))
        : h("div", { class: "empty-note" }, "No orders queued."),
      d.maintenance_events.length
        ? h("p", { class: "modal-note" }, `Maintenance: ${d.maintenance_events.map((m) => `${m.maintenance_type.toLowerCase()} (${m.status.toLowerCase()}, ${m.duration_hours} h)`).join("; ")}`)
        : null);
  } catch (e) {
    clear(body, h("p", { class: "callout callout-error" }, `Could not load machine details: ${e.message}`));
  }
}

function humanStatus(m) {
  if (m.reported) return "Down (fault reported)";
  return { HEALTHY: "Running", DEGRADED: "Degraded", MAINTENANCE: "Planned maintenance", DOWN: "Down" }[m.status] || m.status;
}

// ------------------------------------------------------------------ about the demo data

function openAbout() {
  const o = state.overview;
  clear($("#about-body"),
    h("p", {}, h("strong", {}, `${o?.plant.name || "The plant"} is synthetic.`),
      " Real factory data is confidential, so this demo uses a realistic, made-up plant: the same plant every time you open it."),
    h("ul", { class: "plain-list about-list" },
      h("li", {}, `3 production lines, 12 stations, ${o ? o.kpis.machines_total : "36"} machines`),
      h("li", {}, `${o ? o.kpis.orders_active : "55"} customer orders with priorities and due dates, and 220 schedule entries`),
      h("li", {}, "25 materials, 15 suppliers and 5 maintenance events"),
      h("li", {}, "18 engineering documents (manuals, SOPs, quality rules, incident reports) written for this plant"),
      h("li", {}, "72 hours of hourly sensor history per machine (vibration, temperature, current, health)")),
    h("p", {}, h("strong", {}, "Not tied to one industry. "),
      "The engine works for any plant built from production lines, stations and machines (electronics, automotive, appliances…). A plant is described by a configuration (its lines, machines, products and engineering documents), so another factory means a new configuration, not new code; the code includes an automotive configuration to prove it. Every number is calculated by the engine; the AI explains and reviews them."),
    h("p", { class: "fine-print" }, "In a real deployment the same structure would come from the plant's ERP, MES and maintenance systems."));
  $("#about-modal").hidden = false;
  $("#about-modal [data-close]").focus();
}
