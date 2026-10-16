// Factory Decision Engine — UI entry point.
// Orchestrates Experience A (Cinematic Landing) & Experience B (Fixed Workspace Shell)

import { $, $$, h, clear, toast } from "./js/dom.js";
import { api, getDomain, setDomain } from "./js/api.js";
import { state } from "./js/state.js";
import { plantTime, setOperationNames } from "./js/format.js";
import * as controlRoom from "./js/views/control-room.js";
import * as workspace from "./js/views/workspace.js";
import * as decisionLog from "./js/views/decision-log.js";
import { startTour, tourDone } from "./js/components/tour.js";

const VIEW_TITLES = {
  landing: "Home",
  decide: "Decision Studio",
  control: "Factory Overview",
  evidence: "Engineering Evidence",
  log: "Decision Log",
};

/** Landing panel: the demo plant's real size, from the API. */
async function loadHeroPlant() {
  try {
    const o = state.overview || await api.overview();
    $("#hero-plant-name").textContent = `${o.plant.name}, ${o.plant.location}`;
    $("#hero-plant-machines").textContent = String(o.kpis.machines_total);
    $("#hero-plant-orders").textContent = String(o.kpis.orders_active);
  } catch { /* the panel keeps its placeholders */ }
}

const VIEWS = {
  landing: { isLanding: true, onShow: loadHeroPlant },
  decide: { el: "#view-decide", onShow: workspace.onShow },
  control: { el: "#view-control", onShow: controlRoom.onShow },
  evidence: { el: "#view-evidence", onShow: showEvidenceView },
  log: { el: "#view-log", onShow: decisionLog.onShow },
};

export function showView(name) {
  if (!VIEWS[name]) name = "control";

  const landingSection = $("#view-landing");
  const appShell = $("#app-shell");

  if (name === "landing") {
    if (landingSection) landingSection.hidden = false;
    if (appShell) appShell.hidden = true;
    document.body.classList.add("is-landing");
  } else {
    if (landingSection) landingSection.hidden = true;
    if (appShell) appShell.hidden = false;
    document.body.classList.remove("is-landing");

    // Hide all view sections in app-shell except current
    for (const [key, view] of Object.entries(VIEWS)) {
      if (view.el) {
        const el = $(view.el);
        if (el) el.hidden = key !== name;
      }
    }

    // Update sidebar navigation active state
    for (const tab of $$(".sidebar-tab")) {
      const isMatch = tab.dataset.view === name;
      tab.classList.toggle("active", isMatch);
      if (isMatch) tab.setAttribute("aria-current", "page");
      else tab.removeAttribute("aria-current");
    }

    // Update header breadcrumb
    const bc = $("#bc-view-name");
    if (bc) bc.textContent = VIEW_TITLES[name] || name;

  }

  if (location.hash.split("?")[0] !== `#${name}`) {
    history.replaceState(null, "", `#${name}`);
  }

  VIEWS[name].onShow?.();
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

function initTheme() {
  const toggleBtn = $("#theme-toggle");
  if (!toggleBtn) return;
  toggleBtn.addEventListener("click", () => {
    const root = document.documentElement;
    const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("fde-theme", next); } catch { /* storage unavailable */ }
  });
}

async function initHealth() {
  const pills = [$("#ai-status"), $("#ai-status-landing")].filter(Boolean);
  const textNodes = [$("#ai-status-text"), $(".ai-status-text")].filter(Boolean);

  try {
    state.health = await api.health();
    const live = state.health.live_gemini_available;
    const provider = state.health.gemini_provider === "vertex_ai" ? " · Vertex AI" : "";
    const label = live ? `Gemini Live${provider}` : "AI Offline · Rule-based";

    pills.forEach((p) => {
      p.classList.add(live ? "live" : "offline");
      p.title = live
        ? `Decision agent: ${state.health.models?.decision_agent}\nUnderstanding & assistant: ${state.health.models?.understanding_and_assistant}\nSpecialist war room: ${state.health.models?.specialist_war_room}`
        : "Deterministic optimization and simulation active. Gemini API key required for live generative specialists.";
    });

    textNodes.forEach((t) => { t.textContent = label; });
  } catch {
    pills.forEach((p) => p.classList.add("offline"));
    textNodes.forEach((t) => { t.textContent = "Server unreachable"; });
    toast("Cannot reach the Factory Decision Engine server.", { error: true });
  }
}

function initNav() {
  // Sidebar tabs
  for (const tab of $$(".sidebar-tab")) {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  }

  // Header return to landing
  $("#brand-home")?.addEventListener("click", () => showView("landing"));
  $("#btn-back-landing")?.addEventListener("click", () => showView("landing"));

  // Landing CTAs lead directly to Factory Overview as default operational hub
  $("#btn-hero-enter")?.addEventListener("click", () => showView("control"));
  $("#btn-enter-workspace")?.addEventListener("click", () => showView("control"));
  $("#btn-how-enter")?.addEventListener("click", () => {
    $("#how-modal").hidden = true;
    showView("control");
  });

  // Modal triggers
  const howModal = $("#how-modal");
  $("#btn-how-it-works")?.addEventListener("click", () => { if (howModal) howModal.hidden = false; });
  $("#btn-architecture")?.addEventListener("click", () => { if (howModal) howModal.hidden = false; });
  $("#btn-explore-modal")?.addEventListener("click", () => { if (howModal) howModal.hidden = false; });

  // Quick launch canonical scenario from landing hero
  $("#btn-launch-m17")?.addEventListener("click", () => {
    showView("decide");
    workspace.analyze("M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.", { fresh: true });
  });

  $("#btn-empty-canonical")?.addEventListener("click", () => {
    workspace.analyze("M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.", { fresh: true });
  });

  // Subtab switching in Decision Studio (Routing / Candidates / Robustness)
  for (const btn of $$(".subtab-btn")) {
    btn.addEventListener("click", () => {
      const target = btn.dataset.subtab;
      for (const b of $$(".subtab-btn")) {
        const isMatch = b === btn;
        b.classList.toggle("active", isMatch);
        b.setAttribute("aria-selected", String(isMatch));
      }
      $("#panel-map").hidden = target !== "map";
      $("#panel-candidates").hidden = target !== "candidates";
      $("#panel-robustness").hidden = target !== "robustness";
      if (target === "map") {
        workspace.redrawMap();
      }
    });
  }

  // Modal backdrop click-to-close
  for (const modal of $$(".modal-backdrop")) {
    modal.addEventListener("click", (e) => {
      if (e.target === modal) modal.hidden = true;
    });
  }
  for (const closeBtn of $$("[data-close]")) {
    closeBtn.addEventListener("click", (e) => {
      const modal = e.target.closest(".modal-backdrop");
      if (modal) modal.hidden = true;
    });
  }

  window.addEventListener("hashchange", () => {
    const raw = location.hash.slice(1).split("?")[0];
    showView(raw || "landing");
  });
}

export function setPlantClock(iso) {
  const el = $("#plant-clock");
  if (el) el.textContent = `🕗 ${plantTime(iso)}`;
}

async function initDomains() {
  const selects = [$("#domain-select"), $("#domain-select-landing")].filter(Boolean);
  try {
    const { domains, default: fallback } = await api.domains();
    state.domains = domains;
    const linked = new URLSearchParams(location.hash.split("?")[1] || "").get("domain");
    const wanted = linked || getDomain();
    const chosen = domains.some((d) => d.key === wanted) ? wanted : fallback;
    setDomain(chosen);

    selects.forEach((sel) => {
      sel.replaceChildren(...domains.map((d) => {
        const opt = document.createElement("option");
        opt.value = d.key;
        opt.textContent = d.label;
        return opt;
      }));
      sel.value = chosen;
      sel.addEventListener("change", async (ev) => {
        const newKey = ev.target.value;
        selects.forEach((s) => { s.value = newKey; });
        setDomain(newKey);
        const pack = state.domains.find((d) => d.key === newKey);
        state.pack = pack;
        setOperationNames(pack);
        clear($("#evidence-catalog"));
        await controlRoom.reload();
        workspace.applyPack(pack);
        if (!$("#view-landing").hidden) loadHeroPlant();
        toast(`Switched synthetic pack to ${pack.plant.name} (${pack.label})`);
      });
    });

    state.pack = domains.find((d) => d.key === chosen);
    setOperationNames(state.pack);
    workspace.applyPack(state.pack);
  } catch {
    selects.forEach((s) => {
      const wrap = s.closest(".domain-select");
      if (wrap) wrap.hidden = true;
    });
  }
}

/** Render full browsable Engineering Evidence catalog */
async function showEvidenceView() {
  const catalog = $("#evidence-catalog");
  if (!catalog) return;
  if (catalog.children.length > 0) return; // already loaded

  try {
    const res = await api.knowledge();
    const docs = res.documents || [];
    if (!docs.length) { clear(catalog, h("p", { class: "empty-note" }, "No documents found.")); return; }
    clear(catalog, docs.map((doc) => h("article", { class: "catalog-card" },
      h("div", { class: "catalog-card-header" },
        h("span", { class: "badge badge-mono" }, doc.document_id),
        h("span", { class: "badge badge-category" }, doc.document_type),
        doc.machine_ids?.length ? h("span", { class: "badge badge-subtle" }, `Machines: ${doc.machine_ids.join(", ")}`) : null,
      ),
      h("h3", { class: "catalog-title" }, doc.title),
      h("p", { class: "catalog-excerpt" }, doc.excerpt),
      h("details", { class: "catalog-full" },
        h("summary", {}, "Read the full document"),
        h("pre", { class: "catalog-pre" }, doc.content),
      ),
    )));
  } catch {
    clear(catalog, h("p", { class: "empty-note" }, "Could not load the engineering documents. Try again in a moment."));
  }
}

async function controlRoomReady(timeout = 15000) {
  const start = Date.now();
  while (!document.querySelector("#health-groups .health-group") || !document.querySelector("#alert-list .alert-item, #alert-list .empty-note")) {
    if (Date.now() - start > timeout) return false;
    await new Promise((r) => setTimeout(r, 150));
  }
  return true;
}

async function openTour() {
  showView("control");
  await controlRoomReady();
  startTour({
    plantName: state.overview?.plant?.name || "your plant",
    onTryIt: controlRoom.analyzeTopPrediction,
  });
}

async function boot() {
  initTheme();
  initNav();
  $("#btn-tour")?.addEventListener("click", openTour);
  controlRoom.init({ showView, setPlantClock });
  workspace.init({ showView });
  decisionLog.init();
  await initDomains();
  await initHealth();
  api.overview().then((o) => setPlantClock(o.plant_clock)).catch(() => {});

  const [view, query] = location.hash.slice(1).split("?");
  // Default to Factory Overview for root / unless specific view is in hash (e.g. #landing)
  const initialView = view || "control";
  showView(initialView);

  const q = new URLSearchParams(query || "").get("q");
  if (view === "decide" && q) {
    workspace.analyze(q, { fresh: true });
  } else if (initialView === "control" && !tourDone()) {
    openTour();
  }
}

boot();
