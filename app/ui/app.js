// Factory Decision Engine — UI entry point.
// Wires the header, the left navigation and the four views.

import { $, $$, h, clear, toast, renderMarkdown } from "./js/dom.js";
import { api, getDomain, setDomain } from "./js/api.js";
import { state } from "./js/state.js";
import { plantTime, setOperationNames } from "./js/format.js";
import * as controlRoom from "./js/views/control-room.js";
import * as workspace from "./js/views/workspace.js";
import * as decisionLog from "./js/views/decision-log.js";
import { startTour, tourDone } from "./js/components/tour.js";

const VIEW_TITLES = {
  decide: "Decision Studio",
  control: "Factory Overview",
  evidence: "Engineering Evidence",
  log: "Decision Log",
};

const VIEWS = {
  decide: { el: "#view-decide", onShow: workspace.onShow },
  control: { el: "#view-control", onShow: controlRoom.onShow },
  evidence: { el: "#view-evidence", onShow: showEvidenceView },
  log: { el: "#view-log", onShow: decisionLog.onShow },
};

export function showView(name) {
  if (!VIEWS[name]) name = "control";

    // Hide all view sections in app-shell except current
    for (const [key, view] of Object.entries(VIEWS)) {
      if (view.el) {
        const el = $(view.el);
        if (el) el.hidden = key !== name;
      }
    }

    // Decision Studio needs the width: the menu shrinks to icons with short names there
  document.body.classList.toggle("view-decide", name === "decide");

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
  const pills = [$("#ai-status")].filter(Boolean);
  const textNodes = [$("#ai-status-text")].filter(Boolean);

  try {
    state.health = await api.health();
    const live = state.health.live_gemini_available;
    const provider = state.health.gemini_provider === "vertex_ai" ? " · Vertex AI" : "";

    pills.forEach((p) => {
      p.classList.add(live ? "live" : "offline");
      p.title = live
        ? `Decision agent: ${state.health.models?.decision_agent}\nUnderstanding & assistant: ${state.health.models?.understanding_and_assistant}\nSpecialist war room: ${state.health.models?.specialist_war_room}`
        : "Deterministic optimization and simulation active. Gemini API key required for live generative specialists.";
    });

    // the provider part is dropped on phone widths (see .ai-provider in style.css)
    textNodes.forEach((t) => t.replaceChildren(live ? "Gemini Live" : "AI Offline · Rule-based", live && provider ? h("span", { class: "ai-provider" }, provider) : ""));
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

  // The logo goes to the Factory Overview, the app's starting point
  $("#brand-home")?.addEventListener("click", () => showView("control"));

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
    showView(raw || "control");
  });
}

export function setPlantClock(iso) {
  const el = $("#plant-clock");
  // Demo data is built around one fixed moment, so say plainly that this is the plant's time, not today
  if (el) el.replaceChildren("🕗 ", h("span", { class: "clock-label" }, "Plant time"), ` · ${plantTime(iso)}`);
}

async function initDomains() {
  const selects = [$("#domain-select")].filter(Boolean);
  try {
    const { domains, default: fallback, switchable } = await api.domains();
    state.domains = domains;
    // The demo shows one plant unless the server turns the industry switch on; then a viewer's
    // own choice (or a shared link's ?domain=) is used
    const linked = new URLSearchParams(location.hash.split("?")[1] || "").get("domain");
    const wanted = switchable ? (linked || getDomain()) : fallback;
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
        toast(`Switched synthetic pack to ${pack.plant.name} (${pack.label})`);
      });
    });

    state.pack = domains.find((d) => d.key === chosen);
    setOperationNames(state.pack);
    workspace.applyPack(state.pack);
    if (!switchable) selects.forEach((sel) => { sel.closest(".domain-select").hidden = true; });
  } catch {
    selects.forEach((s) => {
      const wrap = s.closest(".domain-select");
      if (wrap) wrap.hidden = true;
    });
  }
}

const DOC_TYPES = {
  engineering_constraint: "Engineering rule", incident_report: "Incident report", machine_manual: "Machine manual",
  maintenance: "Maintenance procedure", production_sop: "Production procedure", quality: "Quality rule",
};

/** "Cited in the current plan" on the documents the analysis on screen relies on. */
function markCitedDocuments() {
  const d = state.decision;
  const cited = new Set([
    ...(d?.engineering_evidence || []).map((e) => e.document_id),
    ...(d?.specialist_assessments || []).flatMap((a) => a.citations || []),
  ]);
  for (const card of $$("#evidence-catalog .catalog-card")) {
    const on = cited.has(card.dataset.doc);
    card.classList.toggle("is-cited", on);
    $(".cited-tag", card).hidden = !on;
  }
}

/** Render full browsable Engineering Evidence catalog */
async function showEvidenceView() {
  const catalog = $("#evidence-catalog");
  if (!catalog) return;
  if (catalog.children.length > 0) { markCitedDocuments(); return; } // already loaded

  try {
    const res = await api.knowledge();
    const docs = res.documents || [];
    if (!docs.length) { clear(catalog, h("p", { class: "empty-note" }, "No documents found.")); return; }
    clear(catalog, docs.map((doc) => h("article", { class: "catalog-card", "data-doc": doc.document_id },
      h("div", { class: "catalog-card-header" },
        h("span", { class: "badge badge-mono" }, doc.document_id),
        h("span", { class: "badge badge-category" }, DOC_TYPES[doc.document_type] || doc.document_type),
        h("span", { class: "badge badge-subtle" }, doc.machine_ids?.length ? `Machines: ${doc.machine_ids.join(", ")}` : "Whole plant"),
        h("span", { class: "badge badge-grounding cited-tag", hidden: true }, "✓ Cited in the current plan"),
      ),
      h("h3", { class: "catalog-title" }, doc.title),
      h("p", { class: "catalog-excerpt" }, workspace.readableExcerpt(doc.excerpt)),
      h("details", { class: "catalog-full" },
        h("summary", {}, "Read the full document"),
        h("div", { class: "catalog-doc prose" }, renderMarkdown(doc.content)),
      ),
    )));
    markCitedDocuments();
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
    prediction: state.alerts?.find((a) => a.kind === "predictive") || null,
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
  // Default to Factory Overview when the link has no view
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
