// Factory Decision Engine — UI entry point.
// Wires the header (navigation, AI status, theme) and boots the three views.
// All figures shown in the UI come from the API; nothing here is hard-coded.

import { $, $$, toast } from "./js/dom.js";
import { api, getDomain, setDomain } from "./js/api.js";
import { state } from "./js/state.js";
import { plantTime, setOperationNames } from "./js/format.js";
import * as controlRoom from "./js/views/control-room.js";
import * as workspace from "./js/views/workspace.js";
import * as decisionLog from "./js/views/decision-log.js";

const VIEWS = {
  control: { el: "#view-control", onShow: controlRoom.onShow },
  decide: { el: "#view-decide", onShow: workspace.onShow },
  log: { el: "#view-log", onShow: decisionLog.onShow },
};

export function showView(name) {
  if (!VIEWS[name]) name = "control";
  for (const [key, view] of Object.entries(VIEWS)) {
    $(view.el).hidden = key !== name;
  }
  for (const tab of $$(".nav-tab")) {
    if (tab.dataset.view === name) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  if (location.hash.split("?")[0] !== `#${name}`) history.replaceState(null, "", `#${name}`);
  VIEWS[name].onShow?.();
  window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
}

function initTheme() {
  $("#theme-toggle").addEventListener("click", () => {
    const root = document.documentElement;
    const current = root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem("fde-theme", next); } catch { /* storage unavailable */ }
  });
}

async function initHealth() {
  const pill = $("#ai-status");
  const text = $("#ai-status-text");
  try {
    state.health = await api.health();
    const live = state.health.live_gemini_available;
    pill.classList.add(live ? "live" : "offline");
    const provider = state.health.gemini_provider === "vertex_ai" ? " · Vertex AI" : "";
    text.textContent = live ? `Gemini live${provider}` : "AI offline · rule-based mode";
    pill.title = live
      ? `Decision agent: ${state.health.models?.decision_agent}\nUnderstanding & assistant: ${state.health.models?.understanding_and_assistant}\nSpecialist war room: ${state.health.models?.specialist_war_room}`
      : "No Gemini API key configured. All numbers still come from the deterministic engine.";
  } catch {
    pill.classList.add("offline");
    text.textContent = "Server unreachable";
    toast("Cannot reach the decision engine server.", { error: true });
  }
}

function initNav() {
  for (const tab of $$(".nav-tab")) {
    tab.addEventListener("click", () => showView(tab.dataset.view));
  }
  window.addEventListener("hashchange", () => showView(location.hash.slice(1).split("?")[0]));
}

export function setPlantClock(iso) {
  $("#plant-clock").textContent = `🕗 ${plantTime(iso)}`;
}

/**
 * Industry selector: same engine, different plant. The choice is per viewer (stored locally and
 * sent as a header), so one judge switching industry never changes another judge's screen.
 */
async function initDomains() {
  const select = $("#domain-select");
  try {
    const { domains, default: fallback } = await api.domains();
    state.domains = domains;
    // A shareable link may pin the industry: #decide?q=...&domain=automotive
    const linked = new URLSearchParams(location.hash.split("?")[1] || "").get("domain");
    const wanted = linked || getDomain();
    const chosen = domains.some((d) => d.key === wanted) ? wanted : fallback;
    setDomain(chosen);
    select.replaceChildren(...domains.map((d) => {
      const opt = document.createElement("option");
      opt.value = d.key;
      opt.textContent = d.label;
      return opt;
    }));
    select.value = chosen;
    state.pack = domains.find((d) => d.key === chosen);
    setOperationNames(state.pack);
    workspace.applyPack(state.pack);
    select.addEventListener("change", async () => {
      setDomain(select.value);
      const pack = state.domains.find((d) => d.key === select.value);
      state.pack = pack;
      setOperationNames(pack);
      workspace.applyPack(pack);
      await controlRoom.reload();
      toast(`Switched to ${pack.plant.name} (${pack.label})`);
    });
  } catch {
    select.closest(".domain-select").hidden = true;
  }
}

async function boot() {
  initTheme();
  initNav();
  controlRoom.init({ showView, setPlantClock });
  workspace.init({ showView });
  decisionLog.init();
  await initDomains();
  await initHealth();
  api.overview().then((o) => setPlantClock(o.plant_clock)).catch(() => {});
  // Shareable scenario links: #decide?q=<scenario text> opens the workspace and runs it
  const [view, query] = location.hash.slice(1).split("?");
  showView(view || "control");
  const q = new URLSearchParams(query || "").get("q");
  if (view === "decide" && q) workspace.analyze(q, { fresh: true });
}

boot();
