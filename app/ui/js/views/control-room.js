// Control Room & Factory Overview:
// Top-level status KPIs, prioritized incident queue, line performance & capacity utilization,
// interactive machine asset inventory, customer order delivery exposure, and modal investigations.

import { $, $$, h, clear, toast, setLoading } from "../dom.js";
import { api } from "../api.js";
import { state, atRiskIds } from "../state.js";
import { num, usd, lineName, riskBadgeClass, plantTime, machineTypeLabel } from "../format.js";
import { renderPlantMap } from "../components/plant-map.js";
import * as workspace from "./workspace.js";

let ctx = { showView: () => {}, setPlantClock: () => {} };
let loaded = false;
let highlighted = new Set();

let alertFilter = "all";
let machineFilter = { search: "", line: "ALL", status: "ALL" };
let ordersFilter = "ALL";
let currentModalMachineId = null;
let currentModalAlert = null;

export function init(context) {
  ctx = context;

  // Simulate fault button
  $("#btn-simulate-fault")?.addEventListener("click", simulateFault);

  // About demo data modal
  $("#btn-about-data")?.addEventListener("click", openAbout);
  for (const el of document.querySelectorAll("#about-modal [data-close]")) el.addEventListener("click", closeAbout);
  $("#about-modal")?.addEventListener("click", (e) => { if (e.target.id === "about-modal") closeAbout(); });

  // Incident filter chips
  for (const chip of $$("#incident-filter-chips .chip-filter")) {
    chip.addEventListener("click", () => {
      alertFilter = chip.dataset.filter;
      for (const c of $$("#incident-filter-chips .chip-filter")) {
        c.classList.toggle("active", c === chip);
      }
      renderAlerts();
    });
  }

  // Drill-down below-the-fold tabs (Inventory, Orders, Telemetry)
  const tabDefs = [
    { btn: $("#tab-btn-inventory"), panel: $("#panel-inventory") },
    { btn: $("#tab-btn-orders"), panel: $("#panel-orders") },
    { btn: $("#tab-btn-telemetry"), panel: $("#panel-telemetry") },
  ];
  tabDefs.forEach(({ btn, panel }) => {
    btn?.addEventListener("click", () => {
      tabDefs.forEach((t) => {
        const isMatch = t.btn === btn;
        t.btn?.classList.toggle("active", isMatch);
        t.btn?.setAttribute("aria-selected", String(isMatch));
        if (t.panel) t.panel.hidden = !isMatch;
      });
      if (btn?.id === "tab-btn-inventory") renderMachineTable();
      else if (btn?.id === "tab-btn-orders") renderOrdersTable();
      else if (btn?.id === "tab-btn-telemetry") renderWatchList();
    });
  });

  // Fleet health status chips (on Fleet Health cockpit card)
  for (const chip of $$("#fleet-status-chips .fleet-chip")) {
    chip.addEventListener("click", () => {
      const status = chip.dataset.status;
      machineFilter.status = status;
      const statSel = $("#machine-status-filter");
      if (statSel) statSel.value = status;
      for (const c of $$("#fleet-status-chips .fleet-chip")) {
        c.classList.toggle("active", c === chip);
      }
      // Switch to Inventory tab
      $("#tab-btn-inventory")?.click();
      renderMachineTable();
      $("#panel-inventory")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
  }

  // Machine table filters & search
  $("#machine-search-input")?.addEventListener("input", (e) => {
    machineFilter.search = e.target.value.trim().toLowerCase();
    renderMachineTable();
  });
  $("#machine-line-filter")?.addEventListener("change", (e) => {
    machineFilter.line = e.target.value;
    renderMachineTable();
  });
  $("#machine-status-filter")?.addEventListener("change", (e) => {
    machineFilter.status = e.target.value;
    renderMachineTable();
  });
  $("#btn-reset-machine-filters")?.addEventListener("click", () => {
    machineFilter = { search: "", line: "ALL", status: "ALL" };
    const searchInp = $("#machine-search-input");
    if (searchInp) searchInp.value = "";
    const lineSel = $("#machine-line-filter");
    if (lineSel) lineSel.value = "ALL";
    const statSel = $("#machine-status-filter");
    if (statSel) statSel.value = "ALL";
    for (const c of $$("#fleet-status-chips .fleet-chip")) c.classList.remove("active");
    highlighted.clear();
    renderKpis();
    renderMachineTable();
    renderMap();
  });

  // Table vs Map view toggle
  $("#btn-toggle-inventory-table")?.addEventListener("click", () => {
    $("#btn-toggle-inventory-table").classList.add("active");
    $("#btn-toggle-inventory-map")?.classList.remove("active");
    $("#machine-table-wrap").hidden = false;
    $("#machine-map-wrap").hidden = true;
  });
  $("#btn-toggle-inventory-map")?.addEventListener("click", () => {
    $("#btn-toggle-inventory-map").classList.add("active");
    $("#btn-toggle-inventory-table")?.classList.remove("active");
    $("#machine-table-wrap").hidden = true;
    $("#machine-map-wrap").hidden = false;
    renderMap();
  });

  // Orders priority filter
  $("#orders-priority-filter")?.addEventListener("change", (e) => {
    ordersFilter = e.target.value;
    renderOrdersTable();
  });

  // Machine Detail Modal listeners
  for (const el of document.querySelectorAll("#machine-modal [data-close]")) el.addEventListener("click", closeMachineModal);
  $("#machine-modal")?.addEventListener("click", (e) => { if (e.target.id === "machine-modal") closeMachineModal(); });
  $("#btn-modal-simulate-machine")?.addEventListener("click", () => {
    if (!currentModalMachineId) return;
    const mid = currentModalMachineId;
    closeMachineModal();
    ctx.showView("decide");
    workspace.analyze({ machine_id: mid, downtime_hours: 8.0 }, { fresh: true });
  });

  // Incident Investigation Modal listeners
  for (const el of document.querySelectorAll("#incident-modal [data-close]")) el.addEventListener("click", closeIncidentModal);
  $("#incident-modal")?.addEventListener("click", (e) => { if (e.target.id === "incident-modal") closeIncidentModal(); });
  $("#btn-modal-analyze-incident")?.addEventListener("click", () => {
    if (!currentModalAlert) return;
    const alt = currentModalAlert;
    closeIncidentModal();
    analyzeAlert(alt);
  });

  // Escape key closes modals
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      if (!$("#about-modal")?.hidden) closeAbout();
      if (!$("#machine-modal")?.hidden) closeMachineModal();
      if (!$("#incident-modal")?.hidden) closeIncidentModal();
    }
  });
}

export function openTopIncidentModal() {
  if (state.alerts && state.alerts.length) {
    openIncidentModal(state.alerts[0]);
  }
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
    const plantNameEl = $("#plant-name");
    if (plantNameEl) plantNameEl.textContent = `${overview.plant.name} · ${overview.plant.location}`;
    renderKpis();
    renderAlerts();
    renderWatchList();
    renderProductionLines();
    renderMachineTable();
    renderOrdersTable();
    renderMap();
  } catch (e) {
    toast(`Could not load plant data: ${e.message}`, { error: true });
  }
}

// ------------------------------------------------------------------ Health & Status Groups

/** Lines with reported faults applied: a machine with a fault alert is down until repaired. */
function liveLines() {
  const faulted = new Set(state.alerts.filter((a) => a.kind === "fault").map((a) => a.machine_id));
  return (state.overview?.lines || []).map((l) => ({
    ...l,
    machines: (l.machines || []).map((m) => (faulted.has(m.machine_id) ? { ...m, status: "DOWN", reported: true } : m)),
  }));
}

/** Every machine in exactly one operational state group; predictive risk is tracked separately. */
function machineGroups() {
  const machines = liveLines().flatMap((l) => (l.machines || []).map((m) => ({ ...m, line_name: l.name })));
  const risky = atRiskIds();
  const stopped = machines.filter((m) => ["MAINTENANCE", "DOWN"].includes(m.status));
  const degraded = machines.filter((m) => m.status === "DEGRADED");
  const running = machines.filter((m) => m.status === "HEALTHY");
  const unknown = machines.filter((m) => !["HEALTHY", "DEGRADED", "MAINTENANCE", "DOWN"].includes(m.status));
  const predictiveWatch = machines.filter((m) => risky.has(m.machine_id));
  return { machines, running, normal: running, degraded, atRisk: degraded, stopped, unknown, predictiveWatch };
}

function renderKpis() {
  const k = state.overview?.kpis || {};
  const { machines, running, degraded, stopped, unknown, predictiveWatch } = machineGroups();
  const total = machines.length || 36;

  // 1. Fleet Machine Health Distribution
  const fleetTotalEl = $("#fleet-total-count");
  if (fleetTotalEl) fleetTotalEl.textContent = `${total} Assets`;

  const bar = $("#fleet-health-bar");
  if (bar) {
    clear(bar,
      [["normal", running, "Running normally"], ["risk", degraded, "Degraded"], ["stopped", stopped, "Stopped / down"], ["unknown", unknown, "Unknown state"]].map(([cls, list, desc]) =>
        list.length ? h("span", {
          class: `hb-${cls}`,
          style: `flex:${list.length}`,
          title: `${list.length} ${desc} (${Math.round((list.length / total) * 100)}%)`,
        }) : null
      )
    );
  }

  const cntNormal = $("#cnt-fleet-normal");
  const cntRisk = $("#cnt-fleet-risk");
  const cntStopped = $("#cnt-fleet-stopped");
  const cntWatch = $("#cnt-fleet-watch");
  const cntUnknown = $("#cnt-fleet-unknown");
  if (cntNormal) cntNormal.textContent = String(running.length);
  if (cntRisk) cntRisk.textContent = String(degraded.length);
  if (cntStopped) cntStopped.textContent = String(stopped.length);
  if (cntWatch) cntWatch.textContent = String(predictiveWatch.length);
  if (cntUnknown) cntUnknown.textContent = String(unknown.length);

  const fleetNoteEl = $("#fleet-note-text");
  if (fleetNoteEl) {
    const stoppedInfo = stopped.length > 0 ? `${stopped.map((m) => m.machine_id).join(", ")} stopped` : "No stopped assets";
    const degradedInfo = degraded.length > 0 ? `${degraded.map((m) => m.machine_id).join(", ")} degraded` : "No degraded assets";
    const watchInfo = predictiveWatch.length > 0 ? `${predictiveWatch.map((m) => m.machine_id).join(", ")} under watch` : "No warnings";
    fleetNoteEl.textContent = `${stoppedInfo}; ${degradedInfo}. Predictive watch: ${predictiveWatch.length} flagged (${watchInfo}).`;
  }

  // 2. Production Lines & Utilization List
  renderProductionLines();

  const plantUtilBadge = $("#plant-util-badge");
  if (plantUtilBadge) {
    const pUtil = k.factory_utilization_pct;
    plantUtilBadge.textContent = pUtil !== null && pUtil !== undefined ? `${pUtil}% Plant Util` : "N/A (No Cap)";
  }

  // 3. Customer Orders & Delivery Risk Exposure
  const orders = state.overview?.orders || [];
  const totalOrders = orders.length || k.orders_active || 15;
  const lineExposureOrders = k.orders_line_exposure ?? orders.filter((o) => o.line_impacted).length;
  const elevatedOrders = k.orders_delivery_risk_elevated ?? orders.filter((o) => o.delivery_risk === "ELEVATED").length;
  const nominalOrders = Math.max(0, totalOrders - lineExposureOrders);

  const ordersTotalBadge = $("#orders-total-badge");
  if (ordersTotalBadge) ordersTotalBadge.textContent = `${totalOrders} Orders`;

  const drvNominal = $("#drv-bar-nominal");
  const drvElevated = $("#drv-bar-elevated");
  if (drvNominal) drvNominal.style.flex = String(Math.max(1, nominalOrders));
  if (drvElevated) drvElevated.style.flex = String(Math.max(0, elevatedOrders));

  const cntNominal = $("#cnt-orders-nominal");
  const cntElevated = $("#cnt-orders-elevated");
  const cntDue24 = $("#cnt-due-24h");
  const cntHighPriDue = $("#cnt-high-pri-due");
  const cntOrdersImpacted = $("#cnt-orders-impacted-line");
  if (cntNominal) cntNominal.textContent = String(nominalOrders);
  if (cntElevated) cntElevated.textContent = String(elevatedOrders);
  if (cntDue24) cntDue24.textContent = String(k.orders_due_24h || 0);
  if (cntHighPriDue) cntHighPriDue.textContent = String(k.high_priority_due_24h || 0);
  if (cntOrdersImpacted) cntOrdersImpacted.textContent = String(lineExposureOrders);

  // 4. Header live status pill
  const urgentAlertsCount = state.alerts.filter((a) => ["CRITICAL", "HIGH"].includes(a.severity)).length;
  const totalAlertsCount = state.alerts.length;
  const statusPill = $("#cockpit-live-status-pill");
  if (statusPill) {
    statusPill.textContent = `● ${totalAlertsCount} Active Disruptions (${urgentAlertsCount} Urgent)`;
    statusPill.className = `badge-status-dot ${urgentAlertsCount > 0 ? "pulse" : ""}`;
  }

  // 5. Update tab counts below the fold
  const tabMachCnt = $("#cnt-tab-machines");
  const tabOrdCnt = $("#cnt-tab-orders");
  const tabWatchCnt = $("#cnt-tab-watch");
  if (tabMachCnt) tabMachCnt.textContent = String(total);
  if (tabOrdCnt) tabOrdCnt.textContent = String(totalOrders);
  if (tabWatchCnt) tabWatchCnt.textContent = String(state.watch?.length || 4);
}

function showMachines(list) {
  highlighted = new Set(list.map((m) => m.machine_id));
  renderKpis();
  renderMachineTable();
  renderMap();
  $("#panel-inventory")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

// ------------------------------------------------------------------ Prioritized Incident Queue

function renderAlerts(newId) {
  const list = $("#alert-list");
  if (!list) return;

  const urgent = state.alerts.filter((a) => ["HIGH", "CRITICAL"].includes(a.severity)).length;
  const faults = state.alerts.filter((a) => a.kind === "fault").length;
  const preds = state.alerts.filter((a) => a.kind === "predictive").length;

  const cntAll = $("#cnt-incidents-all");
  const cntUrg = $("#cnt-incidents-urgent");
  const cntFlt = $("#cnt-incidents-fault");
  const cntPrd = $("#cnt-incidents-pred");
  if (cntAll) cntAll.textContent = String(state.alerts.length);
  if (cntUrg) cntUrg.textContent = String(urgent);
  if (cntFlt) cntFlt.textContent = String(faults);
  if (cntPrd) cntPrd.textContent = String(preds);

  const badge = $("#nav-alert-count");
  if (badge) {
    badge.hidden = urgent === 0;
    badge.textContent = String(urgent);
  }

  // Filter alerts
  let filtered = state.alerts;
  if (alertFilter === "urgent") filtered = state.alerts.filter((a) => ["HIGH", "CRITICAL"].includes(a.severity));
  else if (alertFilter === "fault") filtered = state.alerts.filter((a) => a.kind === "fault");
  else if (alertFilter === "predictive") filtered = state.alerts.filter((a) => a.kind === "predictive");

  if (!filtered.length) {
    clear(list, h("div", { class: "empty-note" }, "No disruptions match the selected filter. Use “🧪 Demo: simulate fault” to test incident response."));
    return;
  }

  clear(list, filtered.map((a, idx) => {
    const isNew = a.alert_id === newId;
    const rankNum = a.priority_rank || (idx + 1);
    const isPred = a.kind === "predictive";
    const sevBadge = isPred ? "badge-violet" : riskBadgeClass(a.severity);
    const sevLabel = isPred ? "PREDICTIVE ALARM" : `FAULT · ${a.severity}`;
    const windowText = a.estimated_repair_hours
      ? `${a.estimated_repair_hours[0]}–${a.estimated_repair_hours[1]}h`
      : "Immediate";
    const ordersText = a.affected_orders_count
      ? `${a.affected_orders_count} orders (${a.high_priority_orders_count || 0} High)`
      : "Nominal";
    const consequence = a.operational_consequence || a.title || "Shop-floor production disruption.";

    return h("div", { class: `incident-row alert-item sev-${a.severity} kind-${a.kind}${isNew ? " alert-new" : ""}`, id: `alert-${a.alert_id}` },
      h("div", { class: "inc-col-rank" },
        h("span", { class: "inc-rank-badge" }, `#${rankNum}`),
        h("span", { class: `badge ${sevBadge}` }, sevLabel)),
      h("div", { class: "inc-col-asset" },
        h("strong", { class: "inc-asset-id mono" }, a.machine_id),
        a.line_id ? h("span", { class: "inc-asset-line" }, lineName(a.line_id)) : null),
      h("div", { class: "inc-col-window" },
        h("span", { class: "inc-metric-lbl" }, isPred ? "Alarm ETA" : "Repair Window"),
        h("span", { class: "inc-metric-val mono" }, windowText)),
      h("div", { class: "inc-col-impact" },
        h("span", { class: "inc-metric-lbl" }, "Exposed Orders"),
        h("span", { class: "inc-metric-val highlight" }, ordersText)),
      h("div", { class: "inc-col-desc" },
        h("span", { class: "inc-desc-title" }, consequence),
        h("span", { class: "inc-desc-sub" }, a.detail ? (a.detail.slice(0, 95) + (a.detail.length > 95 ? "…" : "")) : "")),
      h("div", { class: "inc-col-actions" },
        h("button", {
          class: "btn btn-subtle btn-xs btn-investigate",
          onclick: () => openIncidentModal(a),
          "aria-label": `Investigate details of ${a.title}`,
        }, "🔍 Investigate"),
        h("button", {
          class: `btn btn-primary btn-sm btn-analyze${isNew ? " pulse" : ""}`,
          onclick: () => analyzeAlert(a),
          "aria-label": `Analyze impact of ${a.title}`,
        }, "Analyze in Studio →")),
    );
  }));

  if (newId) document.getElementById(`alert-${newId}`)?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

export function analyzeTopPrediction() {
  const alert = state.alerts.find((a) => a.kind === "predictive");
  if (alert) analyzeAlert(alert);
}

function analyzeAlert(alert) {
  ctx.showView("decide");
  workspace.analyzeWithIncident(alert);
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
    renderMachineTable();
    renderMap();
    toast(`New shop-floor fault recorded: ${alert.title}`);
  } catch (e) {
    toast(`Could not simulate fault: ${e.message}`, { error: true });
  } finally {
    setLoading(btn, false);
  }
}

// ------------------------------------------------------------------ Production Lines Performance

function renderProductionLines() {
  const container = $("#cockpit-lines-list") || $("#production-lines-grid");
  if (!container) return;
  const lines = state.overview?.lines || [];
  if (!lines.length) {
    clear(container, h("div", { class: "empty-note" }, "Production lines loading..."));
    return;
  }

  clear(container, lines.map((line) => {
    const util = line.utilization_pct;
    const hasCap = line.capacity_per_hour != null && line.capacity_per_hour > 0;
    const utilText = util !== null && util !== undefined ? `${util}% Util` : "N/A (No Cap)";
    const utilClass = util !== null && util > 90 ? "overload" : util !== null && util > 75 ? "high" : "";
    const isImpacted = line.status === "IMPACTED";

    return h("div", { class: `cockpit-line-row ${isImpacted ? "impacted" : ""}` },
      h("div", { class: "clr-head" },
        h("div", { class: "clr-name-group" },
          h("strong", { class: "clr-name" }, line.name),
          h("span", { class: `badge ${isImpacted ? "badge-danger" : "badge-ok"}` },
            isImpacted ? "DISRUPTED" : "NOMINAL")),
        h("div", { class: "clr-stats mono" },
          h("span", {}, hasCap ? `${line.capacity_per_hour} u/h cap` : "No Capacity"),
          h("span", { class: "clr-sep" }, "·"),
          h("strong", { class: "highlight" }, utilText))),
      h("div", { class: "clr-meter-wrap" },
        h("div", { class: `clr-meter-bar ${utilClass}`, style: `width: ${util !== null && util !== undefined ? Math.min(100, util) : 0}%` })),
      h("div", { class: "clr-foot" },
        h("span", { class: "clr-demand" }, `${line.scheduled_units_24h || 0} units scheduled (24h)`),
        line.bottleneck_station ? h("span", { class: "clr-bn" }, `Bottleneck: ${line.bottleneck_station.name}`) : null),
    );
  }));
}

// ------------------------------------------------------------------ Machine Inventory Table

function renderMachineTable() {
  const tbody = $("#machine-table-body");
  const countEl = $("#machine-records-count");
  if (!tbody) return;

  const allLines = liveLines();
  const allMachines = allLines.flatMap((l) => (l.machines || []).map((m) => ({ ...m, line_name: l.name, line_id: l.line_id })));
  const alertedIds = new Set(state.alerts.map((a) => a.machine_id));
  const risky = atRiskIds();

  const { running, degraded, stopped, predictiveWatch } = machineGroups();
  const runningIds = new Set(running.map((m) => m.machine_id));
  const degradedIds = new Set(degraded.map((m) => m.machine_id));
  const stoppedIdsSet = new Set(stopped.map((m) => m.machine_id));
  const watchIdsSet = new Set(predictiveWatch.map((m) => m.machine_id));

  // Filter
  const filtered = allMachines.filter((m) => {
    if (machineFilter.line !== "ALL" && m.line_id !== machineFilter.line) return false;
    if (machineFilter.status === "HEALTHY" || machineFilter.status === "NORMAL") {
      if (!runningIds.has(m.machine_id)) return false;
    } else if (machineFilter.status === "DEGRADED") {
      if (!degradedIds.has(m.machine_id)) return false;
    } else if (machineFilter.status === "STOPPED" || machineFilter.status === "DOWN" || machineFilter.status === "MAINTENANCE") {
      if (!stoppedIdsSet.has(m.machine_id)) return false;
    } else if (machineFilter.status === "WATCH" || machineFilter.status === "PREDICTIVE_WATCH") {
      if (!watchIdsSet.has(m.machine_id)) return false;
    } else if (machineFilter.status !== "ALL" && m.status !== machineFilter.status) {
      return false;
    }
    if (machineFilter.search) {
      const q = machineFilter.search;
      const match = m.machine_id.toLowerCase().includes(q) ||
        (m.type && m.type.toLowerCase().includes(q)) ||
        (m.station && m.station.toLowerCase().includes(q)) ||
        (m.line_name && m.line_name.toLowerCase().includes(q));
      if (!match) return false;
    }
    return true;
  });

  if (countEl) countEl.textContent = `Showing ${filtered.length} of ${allMachines.length} assets`;

  if (!filtered.length) {
    clear(tbody, h("tr", {}, h("td", { colspan: "9", class: "empty-note", style: "padding:24px; text-align:center;" },
      "No machines match the selected filter criteria.")));
    return;
  }

  clear(tbody, filtered.map((m) => {
    const isDegraded = m.status === "DEGRADED";
    const isDown = m.status === "DOWN";
    const isMaint = m.status === "MAINTENANCE";
    const hasAlert = alertedIds.has(m.machine_id);
    const hasWatch = risky.has(m.machine_id);

    const statusBadge = isDown
      ? h("span", { class: "badge badge-danger" }, "DOWN")
      : isDegraded
      ? h("span", { class: "badge badge-warn" }, "DEGRADED")
      : isMaint
      ? h("span", { class: "badge" }, "MAINTENANCE")
      : h("span", { class: "badge badge-ok" }, "RUNNING");

    const riskBadge = hasAlert
      ? h("span", { class: "badge badge-violet", title: "Active disruption alert" }, "🔔 Active Alert")
      : hasWatch
      ? h("span", { class: "badge badge-warn", title: "Predictive telemetry degradation warning" }, "⚠️ Telemetry Warning")
      : h("span", { style: "color:var(--text-3);" }, "—");

    return h("tr", { class: hasAlert ? "is-selected" : "" },
      h("td", { class: "mono font-semibold" },
        h("a", {
          href: "javascript:void(0)",
          onclick: () => openMachineModal(m.machine_id),
          style: "color:var(--primary); text-decoration:underline;",
        }, m.machine_id)),
      h("td", {}, operationNoun(m.type)),
      h("td", {}, `${lineName(m.line_id)} · ${m.station}`),
      h("td", { class: "num mono" }, m.cycle_time_seconds ? `${m.cycle_time_seconds}s` : "—"),
      h("td", { class: "num mono" }, m.capacity_per_hour ? `${m.capacity_per_hour}/h` : "—"),
      h("td", { class: "num mono font-semibold" }, `${(m.health || 0).toFixed(1)}%`),
      h("td", {}, statusBadge),
      h("td", {}, riskBadge),
      h("td", {},
        h("div", { style: "display:flex; gap:6px;" },
          h("button", {
            class: "btn btn-subtle btn-xs",
            onclick: () => openMachineModal(m.machine_id),
            title: "Inspect machine specifications, telemetry, and schedules",
          }, "Details"),
          h("button", {
            class: "btn btn-secondary btn-xs",
            onclick: () => {
              ctx.showView("decide");
              workspace.analyze({ machine_id: m.machine_id, downtime_hours: 8.0 }, { fresh: true });
            },
            title: `Simulate outage scenario on ${m.machine_id}`,
          }, "Simulate"))),
    );
  }));
}

// ------------------------------------------------------------------ Orders Table

function renderOrdersTable() {
  const tbody = $("#orders-table-body");
  const countEl = $("#orders-records-count");
  if (!tbody) return;
  const orders = state.overview?.orders || [];

  const filtered = orders.filter((o) => {
    if (ordersFilter === "ALL") return true;
    return o.priority === ordersFilter;
  });

  if (countEl) countEl.textContent = `Showing ${filtered.length} of ${orders.length} orders`;

  if (!filtered.length) {
    clear(tbody, h("tr", {}, h("td", { colspan: "8", class: "empty-note", style: "padding:24px; text-align:center;" },
      "No customer orders found matching this priority filter.")));
    return;
  }

  clear(tbody, filtered.map((o) => {
    const isElevated = o.delivery_risk === "ELEVATED";
    const isLineExposure = o.delivery_risk === "MONITORED_LINE_EXPOSURE";
    return h("tr", {},
      h("td", { class: "mono font-semibold" }, o.order_id),
      h("td", {}, o.product_model),
      h("td", { class: "num mono" }, String(o.quantity)),
      h("td", {}, h("span", { class: `badge ${o.priority === "HIGH" ? "badge-danger" : o.priority === "MEDIUM" ? "badge-warn" : "badge-subtle"}` }, o.priority)),
      h("td", {}, plantTime(o.due_time)),
      h("td", {}, lineName(o.assigned_line)),
      h("td", {}, o.status),
      h("td", {},
        h("span", { class: `risk-pill ${isElevated ? "elevated" : isLineExposure ? "warn" : "normal"}` },
          isElevated ? "⚠ Elevated (Rule-Based ≤24h)" : isLineExposure ? "Line Exposure (Monitored)" : "✓ Nominal")),
    );
  }));
}

// ------------------------------------------------------------------ Modals: Machine Detail & Incident Investigation

async function openMachineModal(machineId) {
  currentModalMachineId = machineId;
  const modal = $("#machine-modal");
  const body = $("#machine-modal-body");
  const badge = $("#mm-badge-id");
  if (!modal || !body) return;

  if (badge) badge.textContent = machineId;
  modal.hidden = false;
  clear(body, h("div", { class: "skeleton-block", style: "height:200px;" }));

  try {
    const detail = await api.machineDetail(machineId);
    clear(body,
      h("div", { class: "modal-grid-2" },
        // Column 1: Asset Specs
        h("div", {},
          h("div", { class: "modal-section-title" }, "Asset Specifications"),
          h("div", { class: "detail-attr-grid" },
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Machine ID"), h("span", { class: "attr-val" }, detail.machine_id)),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Operational State"), h("span", { class: "attr-val" }, detail.status)),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Operation Type"), h("span", { class: "attr-val" }, detail.station.operation_type)),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Health Score"), h("span", { class: "attr-val" }, `${detail.health_score.toFixed(1)}%`)),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Cycle Time"), h("span", { class: "attr-val" }, `${detail.cycle_time_seconds}s`)),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Rated Capacity"), h("span", { class: "attr-val" }, `${detail.capacity_per_hour} units/h`)),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Assigned Line"), h("span", { class: "attr-val" }, detail.line.name)),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Station"), h("span", { class: "attr-val" }, detail.station.name)))),
        // Column 2: Sensor Telemetry & Assessment
        h("div", {},
          h("div", { class: "modal-section-title" }, "Sensor Telemetry & Health Trend"),
          h("div", { class: "detail-attr-grid" },
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Risk Level"), h("span", { class: "attr-val" }, detail.telemetry.risk_level || "NOMINAL")),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Hours to Alarm"), h("span", { class: "attr-val" }, detail.telemetry.hours_to_alarm !== null ? `${detail.telemetry.hours_to_alarm}h` : "Stable")),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Driver Signal"), h("span", { class: "attr-val" }, detail.telemetry.driver_signal || "Nominal")),
            h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Trend Model"), h("span", { class: "attr-val" }, detail.telemetry.trend_model || "Linear"))),
          h("p", { style: "font-size:12px; color:var(--text-2); margin-top:10px; line-height:1.4;" },
            detail.telemetry.explanation || "All vibration, temperature, and current telemetry channels operating within nominal baselines."))),

      // Scheduled Production Orders Queue
      h("div", { style: "margin-top:16px;" },
        h("div", { class: "modal-section-title" }, "Scheduled Customer Production Orders"),
        detail.scheduled_orders.length
          ? h("table", { class: "data-table", style: "width:100%;" },
              h("thead", {}, h("tr", {}, ["Order ID", "Product Model", "Quantity", "Priority", "Due Time", "Status"].map((t) => h("th", {}, t)))),
              h("tbody", {}, detail.scheduled_orders.map((o) => h("tr", {},
                h("td", { class: "mono" }, o.order_id),
                h("td", {}, o.product_model),
                h("td", { class: "num mono" }, String(o.quantity)),
                h("td", {}, h("span", { class: `badge ${o.priority === "HIGH" ? "badge-danger" : "badge-subtle"}` }, o.priority)),
                h("td", {}, plantTime(o.due_time)),
                h("td", {}, o.status)))))
          : h("div", { class: "empty-note" }, "No production orders currently queued on this machine.")),

      // Maintenance History
      h("div", { style: "margin-top:16px;" },
        h("div", { class: "modal-section-title" }, "Maintenance Log & Upcoming Overhauls"),
        detail.maintenance_events.length
          ? h("table", { class: "data-table", style: "width:100%;" },
              h("thead", {}, h("tr", {}, ["Maintenance ID", "Type", "Status", "Start Time", "Duration"].map((t) => h("th", {}, t)))),
              h("tbody", {}, detail.maintenance_events.map((m) => h("tr", {},
                h("td", { class: "mono" }, m.maintenance_id),
                h("td", {}, m.maintenance_type),
                h("td", {}, m.status),
                h("td", {}, plantTime(m.start_time)),
                h("td", { class: "num mono" }, `${m.duration_hours}h`)))))
          : h("div", { class: "empty-note" }, "No active or scheduled maintenance events on record.")),
    );
  } catch (e) {
    clear(body, h("p", { class: "callout callout-error" }, `Could not load machine specification: ${e.message}`));
  }
}

function closeMachineModal() {
  const modal = $("#machine-modal");
  if (modal) modal.hidden = true;
  currentModalMachineId = null;
}

function openIncidentModal(alert) {
  currentModalAlert = alert;
  ctx.setJourneyStep?.(2);
  const modal = $("#incident-modal");
  const body = $("#incident-modal-body");
  const badge = $("#im-badge-sev");
  if (!modal || !body) return;

  if (badge) {
    badge.textContent = alert.severity;
    badge.className = `badge ${riskBadgeClass(alert.severity)}`;
  }
  modal.hidden = false;

  const plan = alert.prebuilt_plan;
  clear(body,
    h("div", { class: "modal-grid-2" },
      h("div", {},
        h("div", { class: "modal-section-title" }, "Incident Overview"),
        h("div", { class: "detail-attr-grid" },
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Incident ID"), h("span", { class: "attr-val" }, alert.alert_id)),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Disruption Kind"), h("span", { class: "attr-val" }, alert.kind.toUpperCase())),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Impacted Machine"), h("span", { class: "attr-val" }, alert.machine_id)),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Production Line"), h("span", { class: "attr-val" }, alert.line_id ? lineName(alert.line_id) : "—")),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Repair Estimate"), h("span", { class: "attr-val" }, `${alert.estimated_repair_hours[0]}–${alert.estimated_repair_hours[1]} h`)),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Raised Timestamp"), h("span", { class: "attr-val" }, plantTime(alert.raised_at))))),
      h("div", {},
        h("div", { class: "modal-section-title" }, "Downstream Operational Consequence"),
        h("div", { class: "detail-attr-grid" },
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Exposed Orders"), h("span", { class: "attr-val" }, String(alert.affected_orders_count || 0))),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "High-Priority Exposed"), h("span", { class: "attr-val" }, String(alert.high_priority_orders_count || 0))),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Potential Unit Loss"), h("span", { class: "attr-val" }, `${alert.loss_estimate_units || 0} units`)),
          h("div", { class: "attr-item" }, h("span", { class: "attr-lbl" }, "Next Protocol Step"), h("span", { class: "attr-val" }, "Run Recovery Optimization"))),
        h("p", { style: "font-size:12px; color:var(--text-2); margin-top:10px; line-height:1.4;" }, alert.detail))),

    h("div", { style: "margin-top:16px;" },
      h("div", { class: "modal-section-title" }, "Symptom & Investigation Context"),
      h("div", { class: "callout callout-warn" },
        h("div", { class: "callout-title" }, alert.title),
        h("div", {}, alert.detail))),

    plan && plan.plan_id
      ? h("div", { style: "margin-top:16px;" },
          h("div", { class: "modal-section-title" }, "Pre-Computed Physics Recovery Contingency"),
          h("div", { class: "card", style: "padding:12px;" },
            h("strong", {}, plan.description),
            h("div", { class: "watch-sub", style: "margin-top:4px;" },
              `Sized for ${plan.assumed_downtime_hours}h downtime · Extra cost ${usd(plan.cost_impact_usd)} · High-priority protected`)))
      : null,
  );
}

function closeIncidentModal() {
  const modal = $("#incident-modal");
  if (modal) modal.hidden = true;
  currentModalAlert = null;
  ctx.setJourneyStep?.(1);
}

// ------------------------------------------------------------------ Health Trends & Sparklines

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

function etaRange(range) {
  if (!range || range[0] === null) return null;
  const [lo, hi] = range.map((v) => (v === null ? null : Math.round(v)));
  if (hi === null) return h("span", { class: "eta-range" }, `≥ ${lo} h`);
  return lo === hi ? null : h("span", { class: "eta-range" }, `${lo}–${hi} h`);
}

function renderWatchList() {
  const list = $("#watch-list");
  if (!list) return;
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
      h("div", {
        class: "watch-eta",
        title: w.trend_model ? `${w.trend_model === "quadratic" ? "Accelerating-curve" : "Straight-line"} trend on the last 24 h; range from sensor noise` : null,
      },
        h("strong", {}, w.hours_to_alarm !== null ? `~${num(w.hours_to_alarm)} h` : "—"),
        etaRange(w.hours_to_alarm_range),
        "to alarm"));

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

// ------------------------------------------------------------------ Plant Map

function operationNoun(type) {
  const code = (type || "").split("_Unit_")[0];
  return state.pack?.operations?.find((o) => o.code === code)?.noun || machineTypeLabel(type);
}

function renderMap() {
  const mapEl = $("#line-map");
  if (!mapEl) return;
  const risky = atRiskIds();
  renderPlantMap(mapEl, {
    lines: liveLines(),
    atRisk: risky,
    highlight: highlighted,
    onMachine: (m, line) => {
      openMachineModal(m.machine_id);
    },
  });
}

// ------------------------------------------------------------------ About the Demo Data

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

