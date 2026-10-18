// Decision Studio: 1 What happened → 2 Impact → 3 Recommendation → 4 Options, then the human approval.

import { $, $$, h, clear, toast, setLoading, renderMarkdown, copyText } from "../dom.js";
import { api } from "../api.js";
import { state } from "../state.js";
import {
  STRATEGY, strategyLabel, strategyIcon, planText, usd, num, hours, pct, lineName,
  deliveryLabel, qualityLabel, riskLabel, deliveryRiskTone, stanceInfo, presetForWeights, humanize,
  machineTypeLabel, stationOperation,
} from "../format.js";
import { renderPlantMap } from "../components/plant-map.js?v=2.1";
import { attachMic, attachReadAloud, recommendationParts, stopReading } from "../components/voice.js";

const STEP_IDS = ["#step-impact", "#step-options", "#step-recommendation", "#step-approve"];
const AGENT_ICONS = { production: "🏭", quality: "🔍", maintenance: "🔧", energy: "⚡" };

let ctx = { showView: () => {} };
let progressTimers = [];
let busy = false;
// Each analysis gets a number; only the newest one may render (an older one that finishes late is dropped)
let analysisSeq = 0;
// What the last priority change did to the recommendation ({ changed, from }), shown on the card
let priorityChange = null;
// The last few questions and answers about the plan on screen, so follow-ups make sense
let chatHistory = [];
let currentIncidentContext = null;

export function init(context) {
  ctx = context;
  $("#scenario-form").addEventListener("submit", (e) => {
    e.preventDefault();
    analyze($("#scenario-input").value);
  });
  $("#scenario-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      analyze($("#scenario-input").value);
    }
  });

  $("#btn-clear-incident")?.addEventListener("click", detachIncident);
  // Voice: speak the report or question (the manager still presses Analyze/Ask), hear the result
  attachMic($("#scenario-input"), { before: $("#btn-analyze") });
  attachMic($("#ask-input"));
  attachReadAloud($("#step-recommendation .rec-badge-row"), () => recommendationParts($("#step-recommendation")));

  // Example reports fill the box so the user sees exactly what will be sent; they press Analyze
  for (const btn of $$("#step-input .example-btn")) {
    btn.addEventListener("click", () => {
      prefill(btn.dataset.prompt);
      const analyzeBtn = $("#btn-analyze");
      analyzeBtn.classList.remove("pulse");
      void analyzeBtn.offsetWidth;
      analyzeBtn.classList.add("pulse");
    });
  }
  for (const btn of $$("#priority-presets button")) {
    btn.addEventListener("click", () => rerank(btn.dataset.priorities));
  }
  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    ask($("#ask-input").value);
  });
  // The example questions are redrawn for each plan, so listen on their container
  $("#ask-chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".chip-btn");
    if (chip) ask(chip.dataset.question);
  });
  $("#btn-approve").addEventListener("click", openApproval);
  $("#btn-quick-approve")?.addEventListener("click", openApproval);
  $("#approve-form").addEventListener("submit", submitApproval);
  for (const el of $$("#approve-modal [data-close]")) el.addEventListener("click", closeApproval);
  $("#approve-modal").addEventListener("click", (e) => { if (e.target.id === "approve-modal") closeApproval(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#approve-modal").hidden) closeApproval(); });
}

export function onShow() {}

/** Use the industry pack's example sentences and start a clean workspace for the new plant. */
export function applyPack(pack) {
  if (!pack) return;
  for (const btn of $$("#step-input .example-btn[data-example]")) {
    const text = pack.examples?.[btn.dataset.example];
    if (text) {
      btn.dataset.prompt = text;
      const exText = btn.querySelector(".ex-text");
      if (exText) exText.textContent = `“${text}”`;
      btn.title = text;
    }
  }
  if (pack.examples?.placeholder) {
    $("#scenario-input").placeholder = pack.examples.placeholder;
  }
  $("#scenario-input").value = "";
  currentIncidentContext = null;
  const incBanner = $("#attached-incident-banner");
  if (incBanner) incBanner.hidden = true;
  state.decision = null;
  hideMessages();
  $("#interpretation").hidden = true;
  $("#approval-result").hidden = true;
  const approveBtn = $("#btn-approve");
  if (approveBtn) approveBtn.disabled = false;
  const quickApprove = $("#btn-quick-approve");
  if (quickApprove) quickApprove.disabled = false;
  const whatifBanner = $("#whatif-diff-banner");
  if (whatifBanner) whatifBanner.hidden = true;
  clear($("#chat-log"));
  showSteps(false);
}

export function focusInput() {
  const input = $("#scenario-input");
  input.focus();
}

export function prefill(text) {
  const input = $("#scenario-input");
  input.value = text;
  input.focus();
  input.setSelectionRange(text.length, text.length);
}

/** A new report is a new situation: drop the alert it was attached to, if any. */
export function detachIncident() {
  currentIncidentContext = null;
  const banner = $("#attached-incident-banner");
  if (banner) banner.hidden = true;
}

export function analyzeWithIncident(alert) {
  if (!alert) return;
  currentIncidentContext = alert;
  const banner = $("#attached-incident-banner");
  if (banner) {
    $("#aib-incident-id").textContent = alert.alert_id;
    $("#aib-incident-desc").textContent = alert.title;
    banner.hidden = false;
  }
  // The alert's own report goes through the full pipeline: Gemini, engine, specialists
  analyze({ prompt: alert.analysis_prompt, incident_id: alert.alert_id }, { fresh: true });
}

// ------------------------------------------------------------------ analyze

export async function analyze(payload, { fresh = false } = {}) {
  let promptText = "";
  let bodyPayload = null;

  if (typeof payload === "string") {
    promptText = (payload || "").trim();
    if (!promptText) {
      showError("Describe the disruption first, e.g. “M17 will be down for 6 hours”.");
      return;
    }
    bodyPayload = { prompt: promptText };
    if (currentIncidentContext?.alert_id) {
      bodyPayload.incident_id = currentIncidentContext.alert_id;
    }
  } else if (payload && typeof payload === "object") {
    bodyPayload = { ...payload };
    promptText = payload.prompt || `Machine ${payload.machine_id} will be unavailable for ${payload.downtime_hours} hours.`;
    if (currentIncidentContext?.alert_id && !bodyPayload.incident_id) {
      bodyPayload.incident_id = currentIncidentContext.alert_id;
    }
  }

  // A newer request replaces one still running (e.g. the manager went back and opened another alert)
  const seq = ++analysisSeq;
  stopReading(); // the old recommendation is about to be replaced
  priorityChange = null;
  // A new report is a new conversation
  chatHistory = [];
  clear($("#chat-log"));
  $("#whatif-diff-banner").hidden = true;
  busy = true;
  // Until the new answer arrives, the previous one is greyed out and can't be approved
  $("#view-decide").classList.add("is-reanalyzing");
  $("#scenario-input").value = promptText;
  state.lastPrompt = promptText;
  hideMessages();
  const btn = $("#btn-analyze");
  setLoading(btn, true);
  startProgress();
  try {
    const previous = fresh ? null : state.decision;
    const decision = await api.analyze(bodyPayload, previous);
    if (seq !== analysisSeq) return;
    finishProgress();
    render(decision);
  } catch (e) {
    if (seq !== analysisSeq) return;
    stopProgress();
    showError(e.message);
  } finally {
    if (seq === analysisSeq) {
      setLoading(btn, false);
      busy = false;
      $("#view-decide").classList.remove("is-reanalyzing");
    }
  }
}

const PROGRESS_LABELS = ["Gemini reads the report", "Engine calculates the options", "AI specialists review"];

function startProgress() {
  const box = $("#progress");
  box.hidden = false;
  $("#progress-title").textContent = "Working on it";
  const steps = $$(".progress-step", box);
  steps.forEach((s, i) => { s.className = "progress-step"; $(".ps-text", s).textContent = PROGRESS_LABELS[i]; });
  const live = state.health?.live_gemini_available;
  // Estimated timings: the server reports only the final result, so stages advance on a timer
  const schedule = live ? [0, 2000, 3000] : [0, 300, 600];
  progressTimers.forEach(clearTimeout);
  progressTimers = schedule.map((ms, i) => setTimeout(() => {
    steps.forEach((s, j) => {
      s.classList.toggle("done", j < i);
      s.classList.toggle("active", j === i);
    });
  }, ms));
}

function finishProgress() {
  progressTimers.forEach(clearTimeout);
}

/** After a result, the progress row stays as "how this answer was made", showing what really ran. */
function renderProgressSummary(d) {
  const box = $("#progress");
  const comps = d.ai_components || {};
  const candidates = d.scenario_interpretation?.candidate_machine_ids || [];
  if (!d.impact_summary?.machine_id && !candidates.length) { box.hidden = true; return; } // not a disruption report
  const options = (d.feasible_alternatives?.length || 0) + (d.infeasible_alternatives?.length || 0);
  const review = comps.war_room;
  const stages = [
    comps.understanding === "gemini" ? ["done", "Gemini understood the report"] : ["done", "Rules read the report (Gemini unavailable)"],
    !d.impact_summary?.machine_id ? ["waiting", "Engine waits for your answer above"]
      : ["done", d.selected_plan ? `Engine calculated ${options} options` : "Engine found no feasible plan"],
    !d.selected_plan ? ["waiting", "Specialist review follows"]
      : review === "gemini" ? ["done", `${d.specialist_assessments?.length || 0} AI specialists reviewed it`]
      : review === "reviewing" || review === "pending" ? ["active", "AI specialists reviewing…"]
      : review === "failed" ? ["failed", "Specialist review unavailable (retry in the review panel)"]
      : ["waiting", "Specialist review skipped"],
  ];
  $$(".progress-step", box).forEach((s, i) => {
    const [cls, text] = stages[i];
    s.className = `progress-step ${cls}`;
    $(".ps-text", s).textContent = text;
  });
  $("#progress-title").textContent = "How this answer was made";
  box.hidden = false;
}

function stopProgress() {
  progressTimers.forEach(clearTimeout);
  $("#progress").hidden = true;
}

function hideMessages() {
  $("#clarification").hidden = true;
  $("#error-banner").hidden = true;
}

function showError(message) {
  const box = $("#error-banner");
  clear(box, h("div", { class: "callout-title" }, "Could not analyze this scenario"), h("div", {}, message));
  box.hidden = false;
}

// ------------------------------------------------------------------ render

function render(decision) {
  renderInterpretation(decision);
  renderProgressSummary(decision);

  if (!decision.selected_plan) {
    renderClarification(decision);
    if (!decision.impact_summary?.machine_id || decision.impact_summary?.error) {
      showSteps(false);
      $("#empty-state").hidden = true;
      return;
    }
  }

  state.decision = decision;
  $("#approval-result").hidden = true;
  $("#btn-approve").disabled = !decision.selected_plan;
  const quickApprove = $("#btn-quick-approve");
  if (quickApprove) quickApprove.disabled = !decision.selected_plan;
  showSteps(true);
  renderImpact(decision);
  renderImpactMap(decision);
  renderOptions(decision);
  renderRecommendation(decision);
  reportDegradedAi(decision);
}

function showSteps(visible) {
  $("#empty-state").hidden = visible;
  $("#rail-placeholder").hidden = visible;
  for (const id of STEP_IDS) $(id).hidden = !visible;
}

function renderInterpretation(d) {
  const box = $("#interpretation");
  const interp = d.scenario_interpretation;
  if (!interp) { box.hidden = true; return; }
  const chips = [];
  if (interp.machine_id) chips.push(["Machine", interp.machine_id]);
  if (interp.downtime_range) chips.push(["Downtime", `${interp.downtime_range[0]}–${interp.downtime_range[1]} h (planning ${num(interp.downtime_hours, 1)} h)`]);
  else if (interp.downtime_hours) chips.push(["Downtime", hours(interp.downtime_hours)]);
  if (interp.priority_focus?.length) chips.push(["Priority", interp.priority_focus.map(humanize).join(" + ")]);
  for (const [k, v] of Object.entries(interp.constraints || {})) {
    if (v) chips.push(["Rule", humanize(k.replace("disallow_", "no_"))]);
  }
  const source = interp.source === "gemini" ? "Gemini understood" : "Rule-based parser read";
  clear(box,
    h("div", { class: "interp-head" }, h("span", { "aria-hidden": "true" }, "✦"), source),
    chips.length ? h("div", { class: "interp-chips" }, chips.map(([k, v]) => h("span", { class: "interp-chip" }, `${k}: `, h("b", {}, v)))) : null,
    interp.summary ? h("div", { class: "interp-summary" }, interp.summary) : null,
    interp.assumptions?.length ? h("div", { class: "interp-assume" }, "Notes: ", interp.assumptions.join(" ")) : null,
    d.assistant_message ? h("div", { class: "assistant-bubble" }, h("span", { class: "avatar", "aria-hidden": "true" }, "✦"), h("div", {}, d.assistant_message)) : null,
  );
  box.hidden = false;
}

function renderClarification(d) {
  const box = $("#clarification");
  const msg = d.clarification_needed || d.rationale;
  if (!msg) return;
  const candidates = d.scenario_interpretation?.candidate_machine_ids || [];
  if (d.assistant_message && !candidates.length && !d.impact_summary?.machine_id) return;
  clear(box,
    h("div", { class: "callout-title" }, "I need one more detail"),
    h("div", {}, msg),
    candidates.length ? h("div", { class: "chips" },
      h("span", { class: "chips-label" }, "Pick the machine:"),
      candidates.map((id) => h("button", {
        class: "chip-btn",
        onclick: () => analyze(`${id}: ${state.lastPrompt}`, { fresh: true }),
      }, id))) : null,
  );
  box.hidden = false;
}

function reportDegradedAi(d) {
  const failed = Object.entries(d.ai_components || {}).filter(([, v]) => v === "failed").map(([k]) => humanize(k));
  if (failed.length) {
    toast(`Some AI steps were busy (${failed.join(", ")}). The engine's numbers are complete; try again in a minute for the full AI review.`);
  }
}

// ------------------------------------------------------------------ step 2: impact

function renderImpact(d) {
  const imp = d.impact_summary;
  const chain = d.dependency_chain;
  $("#impact-scenario").textContent = `${chain.machine_id} down for ${hours(imp.downtime_hours)}`;

  const headerCtx = $("#header-scenario-context");
  if (headerCtx) {
    headerCtx.textContent = `${chain.machine_id} · ${hours(imp.downtime_hours)} outage`;
    headerCtx.hidden = false;
  }

  const node = (label, value, sub, cls = "") =>
    h("div", { class: `ripple-node ${cls}` },
      h("div", { class: "rn-label" }, label),
      h("div", { class: "rn-value" }, value),
      sub ? h("div", { class: "rn-sub" }, sub) : null);
  const arrow = () => h("div", { class: "ripple-arrow", "aria-hidden": "true" }, "→");

  clear($("#ripple-chain"),
    node("Machine", chain.machine_id, `${machineTypeLabel(chain.machine_type)}`, "origin"), arrow(),
    node("Station", chain.station_id?.replace("STA_", "Station ") || "Station", stationOperation(chain.station_name)), arrow(),
    node("Line", chain.line_name || lineName(chain.line_id), `${pct((imp.capacity_loss_percentage || 0) / 100, 1)} capacity loss`), arrow(),
    node("Customer orders", `${imp.affected_order_count} affected`, `${imp.high_priority_affected_count} high-priority`, imp.high_priority_affected_count ? "end" : ""),
  );

  const card = (label, value, sub, tone = "") =>
    h("div", { class: `impact-card ${tone ? `tone-${tone}` : ""}` },
      h("div", { class: "impact-label" }, label),
      h("div", { class: "impact-value" }, value),
      h("div", { class: "impact-sub" }, sub));
  clear($("#impact-cards"),
    card("Downtime", hours(imp.downtime_hours), "Unplanned outage"),
    card("Capacity loss", `${num(imp.capacity_loss_units)} units`, `${num(imp.capacity_loss_percentage, 1)}% of line`, "danger"),
    card("Affected orders", String(imp.affected_order_count), `${(imp.affected_product_models || []).join(", ") || "Active models"}`),
    card("High-priority orders", String(imp.high_priority_affected_count), imp.high_priority_affected_count ? "Critical delivery risk" : "Zero high-priority delay", imp.high_priority_affected_count ? "warn" : "ok"),
  );

  const orderCountLabel = $("#orders-count-label");
  if (orderCountLabel) {
    orderCountLabel.textContent = `${imp.affected_order_count}, of which ${imp.high_priority_affected_count} high-priority`;
  }

  const table = $("#orders-table");
  clear(table,
    h("thead", {}, h("tr", {}, ["Order", "Model", "Qty", "Priority", "Due"].map((t) => h("th", {}, t)))),
    // Most urgent deadline first: that is the order a manager checks first
    h("tbody", {}, [...d.affected_orders].sort((a, b) => new Date(a.due_time) - new Date(b.due_time)).map((o) => h("tr", {},
      h("td", { class: "mono" }, o.order_id),
      h("td", {}, o.product_model),
      h("td", { class: "num" }, num(o.quantity)),
      h("td", {}, h("span", { class: `badge ${o.priority === "HIGH" ? "badge-danger" : o.priority === "MEDIUM" ? "badge-warn" : ""}` }, humanize(o.priority))),
      h("td", {}, new Date(o.due_time).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }))))),
  );
}

/** Plain-language reason a backup machine can't take over (from the engine's feasibility check). */
function cantHelpReason(reasons) {
  const r = (reasons || [])[0] || "";
  const models = /incompatible with affected order model\(s\): \[(.*?)\]/.exec(r);
  const line = /Target line (LINE_[A-Z])/.exec(r);
  if (models && line) return `${lineName(line[1])} doesn't build ${models[1].replace(/'/g, "")}`;
  if (/unavailable with status/i.test(r)) return "machine is offline";
  if (/schedule conflicts/i.test(r)) return "already fully booked";
  return r || "not feasible";
}

export function redrawMap() {
  if (state.decision) {
    renderImpactMap(state.decision);
  }
}

/** "What happens next on the plant floor": the broken machine, who can/can't help, where work moves. */
/** "Line C is also qualified for NW-Speaker" when work moves to a line named after another product. */
function otherLineQualified(d, targetLineId) {
  const failedLine = d.dependency_chain?.line_id;
  if (!targetLineId || targetLineId === failedLine) return null;
  const models = d.impact_summary?.affected_product_models || [];
  const line = (state.overview?.lines || []).find((l) => l.line_id === targetLineId);
  if (!models.length || !line || !models.every((m) => (line.supported_models || []).includes(m))) return null;
  return `${line.name.replace(/\s*\(.*\)$/, "")} is also qualified for ${models.join(", ")}`;
}

/** One line under the recommendation when work moves to a line named after another product. */
function renderQualifiedNote(d) {
  const sp = d.selected_plan;
  const qualified = sp?.strategy_type === "MACHINE_TRANSFER" ? otherLineQualified(d, sp.target_line_id) : null;
  let note = $("#rec-qualified");
  if (!note) {
    note = h("p", { class: "rec-qualified", id: "rec-qualified" });
    $("#rec-summary").after(note);
  }
  note.textContent = qualified ? `✓ ${qualified}, so the move is allowed.` : "";
  note.hidden = !qualified;
}

async function renderImpactMap(d) {
  const box = $("#impact-map");
  const hint = $("#impact-map-card .subcard-hint");
  try {
    if (!state.overview) state.overview = await api.overview();
  } catch {
    $("#impact-map-card").hidden = true;
    return;
  }
  if (state.decision !== d) return;
  $("#impact-map-card").hidden = false;
  const failed = d.impact_summary.machine_id;
  const candidates = {};
  for (const p of d.candidate_plans) {
    if (p.strategy_type !== "MACHINE_TRANSFER" || !p.target_machine_id) continue;
    const qualified = otherLineQualified(d, p.target_line_id);
    candidates[p.target_machine_id] = p.is_feasible
      ? { ok: true, note: `can take over${qualified ? ` · ${qualified}` : ""} · extra cost ${usd(p.cost_impact_usd)}` }
      : { ok: false, note: cantHelpReason(p.infeasibility_reasons) };
  }
  const sp = d.selected_plan;
  const target = sp?.strategy_type === "MACHINE_TRANSFER" ? sp.target_machine_id : null;
  hint.textContent = target
    ? `${failed} is down. ✓ marks machines that can take over its work, ✕ those that can't (hover for why). The arrow shows the recommended move to ${target}.`
    : `${failed} is down. The recommended plan keeps the work where it is: ${planText(sp, failed)}.`;
  renderPlantMap(box, {
    lines: state.overview.lines,
    overlay: { failed, target, candidates },
  });
  renderQualifiedNote(d); // the plant layout may have arrived after the recommendation was drawn
}

// ------------------------------------------------------------------ step 3: options

function bestPerStrategy(d) {
  const out = {};
  for (const type of Object.keys(STRATEGY)) {
    const plans = d.candidate_plans.filter((p) => p.strategy_type === type);
    if (!plans.length) continue;
    const feasible = plans.filter((p) => p.is_feasible);
    const pool = feasible.length ? feasible : plans;
    const chosen = d.selected_plan && pool.find((p) => p.plan_id === d.selected_plan.plan_id);
    out[type] = chosen || pool.slice().sort((a, b) => (b.weighted_score ?? -1) - (a.weighted_score ?? -1))[0];
  }
  return out;
}

function renderOptions(d) {
  const machine = d.impact_summary.machine_id;
  const best = bestPerStrategy(d);
  const feasibleCards = Object.values(best).filter((p) => p.is_feasible);
  const minCost = Math.min(...feasibleCards.map((p) => p.cost_impact_usd ?? Infinity));
  const minRisk = Math.min(...feasibleCards.map((p) => p.risk_score ?? Infinity));
  const minEnergy = Math.min(...feasibleCards.map((p) => p.energy_kwh ?? Infinity));
  const bestDelivery = Math.min(...d.feasible_alternatives.map((p) => p.delivery_delay_score ?? Infinity));

  // Priority preset state
  const preset = presetForWeights(d.objective_weights);
  for (const btn of $$("#priority-presets button")) {
    btn.setAttribute("aria-checked", String(btn.dataset.priorities === preset));
  }

  clear($("#option-cards"), Object.entries(best).map(([type, p]) => {
    const recommended = d.selected_plan && p.plan_id === d.selected_plan.plan_id;
    const tags = [];
    if (recommended) tags.push(["Recommended", "badge-primary"]);
    if (p.is_feasible && p.cost_impact_usd === minCost) tags.push(["Cheapest", "badge-ok"]);
    if (p.is_feasible && p.risk_score === minRisk) tags.push(["Lowest risk", "badge-info"]);
    if (p.is_feasible && p.energy_kwh === minEnergy) tags.push(["Least energy", "badge-violet"]);
    if (!p.is_feasible) tags.push(["Not possible", "badge-danger"]);
    const del = deliveryLabel(p.delivery_delay_score, bestDelivery);
    const q = qualityLabel(p.quality_penalty_score);
    const r = riskLabel(p.risk_score);
    const metric = (label, value, tone) => [h("dt", {}, label), h("dd", { class: tone ? `text-${tone}` : "" }, value)];
    return h("article", { class: `option-card${recommended ? " recommended" : ""}${p.is_feasible ? "" : " infeasible"}` },
      h("div", { class: "option-head" },
        h("span", { class: "option-icon", "aria-hidden": "true" }, strategyIcon(type)),
        h("span", { class: "option-title" }, strategyLabel(type))),
      h("div", { class: "option-desc" }, planText(p, machine)),
      h("div", { class: "option-tags" }, tags.map(([t, c]) => h("span", { class: `badge ${c}` }, t))),
      p.is_feasible
        ? h("dl", { class: "option-metrics" },
            metric("Extra cost", usd(p.cost_impact_usd)),
            metric("Delivery", del.text),
            metric("Quality risk", q.text),
            metric("Operational risk", r.text),
            metric("Energy", `${num(p.energy_kwh)} kWh`))
        : h("div", { class: "option-reason" }, (p.infeasibility_reasons || []).join(" ")),
    );
  }));

  const candCount = $("#all-candidates-count");
  if (candCount) candCount.textContent = String(d.candidate_plans.length);
  const candSummary = $("#all-candidates-summary");
  if (candSummary) candSummary.textContent = `Compare all ${d.candidate_plans.length} candidates (${d.feasible_alternatives.length} feasible)`;

  const sorted = d.candidate_plans.slice().sort((a, b) => (b.is_feasible - a.is_feasible) || ((b.weighted_score ?? -1) - (a.weighted_score ?? -1)));
  clear($("#candidates-table"),
    // Plain columns only: lower is better for delivery, quality and risk; the score decides. The status sits
    // under each option's name so the table fits a laptop screen.
    h("thead", {}, h("tr", {}, [["Option"], ["Cost"], ["Delivery", "Delay penalty: lower is better"], ["Quality", "Quality risk: lower is better"],
      ["Risk", "Operational risk: lower is better"], ["kWh", "Energy used"], ["Score", "Overall score weighted by your priorities: highest wins"]]
      .map(([t, tip]) => h("th", tip ? { title: tip } : {}, t)))),
    h("tbody", {}, sorted.map((p) => h("tr", { class: `${d.selected_plan?.plan_id === p.plan_id ? "is-selected" : ""}${p.is_feasible ? "" : " infeasible"}`, title: p.plan_id },
      h("td", { class: "opt-name" }, planText(p, machine),
        h("span", { class: "opt-status" }, p.is_feasible ? (d.selected_plan?.plan_id === p.plan_id ? "★ Recommended" : "Possible")
          : `Not possible: ${cantHelpReason(p.infeasibility_reasons)}`)),
      h("td", { class: "num" }, usd(p.cost_impact_usd)),
      h("td", { class: "num" }, p.delivery_delay_score?.toFixed(2) ?? "—"),
      h("td", { class: "num" }, p.quality_penalty_score?.toFixed(2) ?? "—"),
      h("td", { class: "num" }, p.risk_score?.toFixed(2) ?? "—"),
      h("td", { class: "num" }, num(p.energy_kwh)),
      h("td", { class: "num" }, p.weighted_score?.toFixed(3) ?? "—")))),
  );
}

async function rerank(priorities) {
  if (!state.decision || busy) return;
  busy = true;
  const seq = analysisSeq;
  const seg = $("#priority-presets");
  seg.classList.add("busy");
  try {
    const beforePlan = state.decision.selected_plan;
    const before = beforePlan?.plan_id;
    const res = await api.rerank(state.decision, priorities);
    if (seq !== analysisSeq) return; // a new analysis started meanwhile; it owns the screen now
    priorityChange = {
      changed: before !== res.selected_plan?.plan_id,
      from: beforePlan ? planText(beforePlan, res.impact_summary.machine_id) : null,
    };
    // Re-ranking is instant and deterministic; the specialists then re-review automatically
    const reReview = Boolean(state.health?.live_gemini_available && res.selected_plan);
    if (reReview) res.ai_components = { ...res.ai_components, war_room: "reviewing" };
    render(res);
    const after = res.selected_plan?.plan_id;
    toast(before === after
      ? "Same plan is still best under these priorities."
      : `New recommendation: ${planText(res.selected_plan, res.impact_summary.machine_id)}`);
    // Make the link visible: the card that answers the priority change lights up
    const card = $("#step-recommendation");
    card.classList.remove("rec-updated");
    void card.offsetWidth;
    card.classList.add("rec-updated");
    if (reReview) autoReview(res);
  } catch (e) {
    if (seq === analysisSeq) toast(`Re-ranking failed: ${e.message}`, { error: true });
  } finally {
    seg.classList.remove("busy");
    if (seq === analysisSeq) busy = false;
  }
}

/** Run the specialist review for a decision; ignore the result if the user has moved on. */
async function autoReview(decision) {
  try {
    const reviewed = await api.review(decision);
    if (state.decision !== decision) return;
    state.decision = reviewed;
    renderRecommendation(reviewed);
    renderProgressSummary(reviewed);
    if (reviewed.ai_components?.war_room === "failed") {
      toast("Specialists are busy right now. The engine's numbers are complete; you can retry the review.");
    }
  } catch (err) {
    if (state.decision !== decision) return;
    decision.ai_components = { ...decision.ai_components, war_room: "failed" };
    renderWarRoom(decision);
  }
}

// ------------------------------------------------------------------ step 4: recommendation

/** "Based on: Lowest risk · Changed from: …", so the card says which priorities it answers. */
function renderBasis(d) {
  const preset = presetForWeights(d.objective_weights);
  const button = [...$$("#priority-presets button")].find((b) => b.dataset.priorities === preset);
  const top = Object.entries(d.objective_weights || {}).filter(([, v]) => v >= 0.3).sort((a, b) => b[1] - a[1]);
  const basis = button ? button.textContent.trim() : top.map(([k]) => humanize(k)).join(" + ") || "Balanced";
  const change = priorityChange;
  clear($("#rec-basis"),
    h("span", { class: "rec-basis-label" }, "Based on: ", h("b", {}, basis)),
    change ? h("span", { class: `rec-basis-change${change.changed ? " changed" : ""}` },
      change.changed ? `Changed from: ${change.from}` : "Still the best plan for these priorities") : null);
}

/** Example questions in plain words that fit the plan on screen. */
const STRATEGY_PHRASE = { MACHINE_TRANSFER: "move the work to another machine", OVERTIME: "use overtime", RESEQUENCE: "re-order the queue" };
function renderAskChips(d) {
  const sp = d.selected_plan;
  const box = $("#ask-chips");
  if (!sp) { clear(box); return; }
  const hrs = d.impact_summary?.downtime_hours;
  const chip = (label, question, title = "") => h("button", { type: "button", class: "chip-btn", "data-question": question, title }, label);
  const agentTag = () => h("span", { class: "chip-agent" }, " ⚙ new simulation");
  const runnerUp = (d.feasible_alternatives || [])
    .filter((p) => p.strategy_type !== sp.strategy_type)
    .sort((a, b) => (b.weighted_score || 0) - (a.weighted_score || 0))[0];
  const agentTitle = "A Google ADK agent runs new simulations with the engine to answer this";
  const target = sp.strategy_type === "MACHINE_TRANSFER" ? sp.target_machine_id : null;
  clear(box,
    d.applied_constraints?.disallow_overtime ? null : chip("What if overtime isn't allowed?", "What if overtime is not allowed?"),
    runnerUp ? chip(`Why not ${STRATEGY_PHRASE[runnerUp.strategy_type] || "use the other option"} instead?`,
      `Why not ${STRATEGY_PHRASE[runnerUp.strategy_type] || "use the other option"} instead of the recommended plan?`) : null,
    target
      ? h("button", { type: "button", class: "chip-btn", "data-question": `What if ${target} also breaks down for ${num(hrs, 0)} hours?`, title: agentTitle },
          `What if ${target} breaks down too?`, agentTag())
      : h("button", { type: "button", class: "chip-btn", "data-question": `What if the repair takes ${num(hrs * 2, 0)} hours instead of ${num(hrs, 0)}?`, title: agentTitle },
          `What if the repair takes ${num(hrs * 2, 0)} h?`, agentTag()),
    chip("Explain this to my director", "Explain this decision to my plant director in 3 short lines."),
  );
}

function renderRecommendation(d) {
  renderBasis(d);
  renderAskChips(d);
  const sp = d.selected_plan;
  const brief = d.decision_brief;
  const machine = d.impact_summary.machine_id;
  const best = Math.min(...d.feasible_alternatives.map((p) => p.delivery_delay_score ?? Infinity));

  if (!sp) {
    $("#rec-title").textContent = "No feasible plan — supervisor intervention required";
    $("#rec-summary").textContent = d.rationale;
    clear($("#rec-figures"));
    return;
  }

  renderObjections(d);
  // Who wrote the explanation, with the real number of reviews (one can time out)
  const reviews = d.specialist_assessments?.length || 0;
  const reviewingNow = ["reviewing", "pending"].includes(d.ai_components?.war_room);
  const leadTag = $("#rec-lead-tag");
  leadTag.hidden = false;
  leadTag.classList.toggle("muted", !brief);
  leadTag.textContent = brief
    ? `✦ Summary by the Decision Lead (Gemini), combining ${reviews} specialist review${reviews === 1 ? "" : "s"}`
    : reviewingNow ? "✦ The Decision Lead (Gemini) is reviewing the new plan…"
    : "Explanation from the engine's rules (AI review not available for this result)";
  $("#rec-title").textContent = brief?.headline || planText(sp, machine);
  $("#rec-summary").textContent = brief?.summary
    || `${planText(sp, machine)}. Chosen by the optimizer as the best balance of delivery, cost, quality, energy and risk under your priorities.`;
  $("#rec-plan-id").textContent = sp.plan_id;
  $("#rec-title").title = `${strategyLabel(sp.strategy_type)} · ${sp.plan_id}`; // plan code on hover
  $("#rec-strategy").textContent = strategyLabel(sp.strategy_type);

  renderQualifiedNote(d);

  const routeTag = $("#rec-route");
  if (routeTag) {
    if (sp.strategy_type === "MACHINE_TRANSFER" && sp.target_machine_id) {
      routeTag.textContent = `${machine} → ${sp.target_machine_id}`;
      routeTag.hidden = false;
    } else {
      routeTag.hidden = true;
    }
  }

  const grounding = $("#rec-grounding");
  if (brief?.grounding_check) {
    const ok = brief.grounding_check.passed;
    grounding.hidden = false;
    grounding.className = `badge ${ok ? "badge-ok" : "badge-warn"}`;
    const nums = brief.grounding_check.ungrounded_numbers || [];
    const causes = brief.grounding_check.unsupported_causes || [];
    grounding.textContent = ok ? "✓ All AI numbers traced to engine data"
      : nums.length ? `⚠ Check: ${nums.length === 1 ? "1 number" : `${nums.length} numbers`} not in the plant data` : "⚠ AI stated a cause that was not reported";
    grounding.title = ok ? "Every number the AI wrote appears in the deterministic engine output or the cited documents, and it states no cause that was not reported."
      : [nums.length ? `Unverified numbers: ${nums.join(", ")}` : null, causes.length ? `Cause not in the report: ${causes.join("; ")}` : null].filter(Boolean).join("\n");
  } else {
    grounding.hidden = true;
  }

  const fig = (label, value, sub = "", tooltip = "") =>
    h("div", { class: "fig", title: tooltip },
      h("div", { class: "fig-label" }, label),
      h("div", { class: "fig-value" }, value),
      sub ? h("div", { class: "fig-sub" }, sub) : null);

  clear($("#rec-figures"),
    fig("Extra cost", usd(sp.cost_impact_usd), "Calculated by the engine",
      "Extra spend for this plan: setup, overtime and any production that is not recovered."),
    fig("Delivery", deliveryLabel(sp.delivery_delay_score, best).text, "Compared with the other options",
      "How close this plan gets to the best delivery result of all options for the affected orders."),
    fig("Quality risk", qualityLabel(sp.quality_penalty_score).text, "",
      "Chance the change causes defects, based on the health of the machine taking over the work. Lower is better."),
    fig("Operational risk", riskLabel(sp.risk_score).text, "",
      "Chance the plan does not go as planned on the floor: the kind of plan and how much production stays unrecovered. Lower is better."),
    fig("Energy", `${num(sp.energy_kwh)} kWh`, "", "Energy the plan uses to recover the lost production."),
  );

  // Say honestly who wrote the explanation: Gemini, or the rule-based fallback
  const aiTag = $("#rec-ai-source");
  const reviewing = ["reviewing", "pending"].includes(d.ai_components?.war_room);
  if (aiTag) aiTag.textContent = brief ? "Reasoning: Gemini" : reviewing ? "Reasoning: Gemini reviewing…" : "Reasoning: rule-based (AI not used)";

  const why = brief?.why?.length ? brief.why : fallbackWhy(d);
  const watch = brief?.watch_outs?.length ? brief.watch_outs : d.risks;
  const next = brief?.next_steps?.length ? brief.next_steps : [
    "Review the specialist conditions and evidence below.",
    "Approve to record the decision and draft the floor documents.",
  ];
  // Three reasons are enough; the specialists' conditions live in their cards below, so the separate
  // watch-out list is only shown when there is no specialist review (rule-based risks then)
  clear($("#rec-why"), why.slice(0, 3).map((t) => h("li", {}, h("span", {}, t))));
  const reviewed = Boolean(d.specialist_assessments?.length);
  $("#rec-watch-col").hidden = reviewed || !watch?.length;
  $(".rec-rationale-columns").classList.toggle("single", reviewed || !watch?.length);
  clear($("#rec-watch"), (watch || []).map((t) => h("li", {}, h("span", {}, t))));
  // Approval is the button itself, so it is not repeated as a step
  const steps = next.filter((t) => !/\bapprov/i.test(t));
  clear($("#rec-next"), (steps.length ? steps : next).map((t) => h("li", {}, h("span", {}, t))));

  renderRobustness(d);
  renderWarRoom(d);
  renderEvidence(d);
  renderDetails(d);
}

/** Specialists who oppose the plan, decided by their own stance (not by the lead's wording). */
function objectionsOf(d) {
  return (d.specialist_assessments || [])
    .filter((a) => a.stance === "oppose")
    .map((a) => ({ agent: a.agent, title: a.title, reason: a.key_concern || a.headline || "" }));
}

function renderObjections(d) {
  const box = $("#rec-objections");
  const objections = objectionsOf(d);
  if (!objections.length) { box.hidden = true; clear(box); return; }
  clear(box,
    h("div", { class: "callout-title" },
      `⚠ ${objections.map((o) => o.title).join(" and ")} ${objections.length > 1 ? "oppose" : "opposes"} this plan. Review before approving.`),
    objections.map((o) => h("div", {}, h("strong", {}, `${o.title}: `), o.reason)));
  box.hidden = false;
}

function fallbackWhy(d) {
  const sp = d.selected_plan;
  const imp = d.impact_summary;
  const out = ["Passes every hard constraint: machine status, operation type, product compatibility and capacity."];
  if (imp.high_priority_affected_count) out.push(`Protects the ${imp.high_priority_affected_count} high-priority orders exposed by the outage.`);
  out.push(`Extra cost of ${usd(sp.cost_impact_usd)} under your priorities.`);
  if (d.engineering_evidence.length) out.push(`Supported by ${d.engineering_evidence[0].document_id}: ${d.engineering_evidence[0].title}.`);
  return out;
}

function renderRobustness(d) {
  const box = $("#robustness");
  const rb = d.robustness;
  if (!rb) { clear(box, h("p", { class: "subcard-hint" }, "Not available for this scenario.")); return; }
  const maxH = Math.max(48, rb.base_downtime_hours);
  const pos = (hrs) => `${Math.min(100, (hrs / maxH) * 100)}%`;
  const segs = rb.segments.map((s, i) => {
    const next = rb.segments[i + 1];
    const start = i === 0 ? 0 : s.from_hours;
    const end = next ? next.from_hours : maxH;
    const cls = !s.plan_id ? "none" : s.is_recommended ? "rec" : `alt alt-${(s.strategy || "").toLowerCase()}`;
    const label = !s.plan_id ? "No feasible plan" : s.is_recommended ? `This plan` : strategyLabel(s.strategy);
    const widthPct = ((end - start) / maxH) * 100;
    const range = s.from_hours === s.to_hours ? `${s.from_hours} h` : `${s.from_hours}–${s.to_hours} h`;
    return h("div", {
      class: `robust-seg ${cls}`,
      style: `width:${widthPct}%`,
      title: `${range}: ${label} (${s.description || "no feasible plan"})`,
    }, widthPct >= 9 ? label : "");
  });
  const bar = h("div", { class: "robust-bar", role: "img", "aria-label": rb.summary }, segs);
  const wrap = h("div", { class: "robust-wrap" }, bar);
  if (rb.downtime_range) {
    bar.appendChild(h("div", { class: "robust-range", style: `left:${pos(rb.downtime_range[0])};width:calc(${pos(rb.downtime_range[1])} - ${pos(rb.downtime_range[0])})`, title: "Stated downtime range" }));
  }
  // The marker sits outside the bar so its label is not clipped
  wrap.appendChild(h("div", { class: "robust-marker", style: `left:${pos(rb.base_downtime_hours)}`, "data-label": `Your estimate: ${num(rb.base_downtime_hours, 1)} h` }));
  const axis = h("div", { class: "robust-axis", "aria-hidden": "true" },
    [0, 8, 16, 24, 32, 40, 48].filter((t) => t <= maxH).map((t) => h("span", { style: `left:${pos(t)}` }, `${t}h`)));
  const verdict = rb.downtime_range
    ? (rb.holds_for_stated_range ? `✓ Holds across the full ${rb.downtime_range[0]}–${rb.downtime_range[1]} h estimate.` : `⚠ The best plan changes within the ${rb.downtime_range[0]}–${rb.downtime_range[1]} h estimate.`)
    : null;
  // Legend: one entry per kind of plan that wins somewhere, so every colour has a name
  const machine = d.impact_summary.machine_id;
  const kinds = [];
  for (const s of rb.segments) {
    const key = !s.plan_id ? "none" : s.is_recommended ? "rec" : `alt alt-${(s.strategy || "").toLowerCase()}`;
    if (kinds.some((k) => k.key === key)) continue;
    kinds.push({ key, text: !s.plan_id ? "No feasible plan" : s.is_recommended ? `This plan: ${planText(d.selected_plan, machine)}` : strategyLabel(s.strategy) });
  }
  const legend = h("div", { class: "robust-legend" }, kinds.map((k) => h("span", {}, h("i", { class: `robust-seg ${k.key}` }), k.text)));
  clear(box, wrap, axis, legend, h("p", { class: "robust-summary" }, verdict ? h("strong", {}, verdict + " ") : null, robustSentence(d)));
}

/** The robustness result in plain words, e.g. "… stays the best plan for any repair from 3 to 48 h, including your 8 h estimate." */
function robustSentence(d) {
  const rb = d.robustness;
  const machine = d.impact_summary.machine_id;
  const span = ([a, b]) => (a === b ? `${a} h` : `${a}–${b} h`);
  const join = (rs) => rs.map(span).join(" and ");
  const best = rb.segments.filter((s) => s.is_recommended).map((s) => [s.from_hours, s.to_hours]);
  const est = rb.base_downtime_hours;
  const atEstimate = best.some(([a, b]) => a <= est && est <= b);
  const plan = planText(d.selected_plan, machine);
  const parts = [best.length
    ? `“${plan}” is the best plan for a repair of ${join(best)}${atEstimate ? `, including your ${num(est, 1)} h estimate` : ""}.`
    : `“${plan}” is not the top choice at other downtimes.`];
  const others = {};
  for (const s of rb.segments) {
    if (s.is_recommended) continue;
    const name = s.plan_id ? strategyLabel(s.strategy).toLowerCase() : "no plan is feasible";
    (others[name] ||= []).push([s.from_hours, s.to_hours]);
  }
  for (const [name, rs] of Object.entries(others)) {
    parts.push(name === "no plan is feasible" ? `At ${join(rs)}, no plan is feasible.` : `At ${join(rs)}, ${name} is better.`);
  }
  if (!atEstimate && best.length) parts.unshift("⚠ At your estimate a different plan scores higher.");
  return parts.join(" ");
}

function renderWarRoom(d) {
  const box = $("#warroom");
  const status = d.ai_components?.war_room;
  const consensus = $("#warroom-consensus");
  if (status === "reviewing") {
    consensus.className = "warroom-verdict"; consensus.textContent = "";
    clear(box, h("div", { class: "warroom-placeholder" },
      h("span", { class: "reviewing" }, h("span", { class: "spinner inline", "aria-hidden": "true" }),
        "Specialists are re-reviewing the plan for your new priorities…")));
    return;
  }
  if (d.specialist_assessments?.length) {
    // Counted from the specialists' own stances, never from AI wording
    const all = d.specialist_assessments;
    const n = (s) => all.filter((a) => a.stance === s).length;
    const opposes = n("oppose"), cautions = n("caution"), supports = all.length - opposes;
    consensus.className = `warroom-verdict${opposes ? " oppose" : ""}`;
    consensus.textContent = opposes
      ? `⚠ ${opposes} of ${all.length} oppose${opposes === 1 ? "s" : ""} this plan`
      : `✓ ${supports} of ${all.length} support${cautions ? (cautions === all.length ? ", all with conditions" : ` · ${cautions} with conditions`) : ""}`;
    const cards = d.specialist_assessments.map((a) => {
      const stance = stanceInfo(a.stance);
      return h("article", { class: `agent-card agent-${a.agent}${a.stance === "oppose" ? " opposes" : ""}` },
        h("div", { class: "agent-head" },
          h("span", { class: "agent-avatar", "aria-hidden": "true" }, AGENT_ICONS[a.agent] || "◆"),
          h("span", { class: "agent-name" }, `${a.title} specialist`),
          h("span", { class: `badge ${stance.cls}`, style: "margin-left:auto" }, stance.text)),
        h("div", { class: "agent-headline" }, a.headline),
        a.key_concern ? h("div", { class: "agent-concern" }, h("strong", {}, "Condition: "), a.key_concern) : null,
        h("details", { class: "agent-more" }, h("summary", {}, "Read full review"), h("div", { class: "agent-text" }, a.assessment)),
        a.citations?.length ? h("div", { class: "cite-row" }, h("span", { class: "cite-label" }, "Sources:"), a.citations.map(citeButton)) : null);
    });
    clear(box, cards, renderConnections(d));
    return;
  }
  consensus.className = "warroom-verdict"; consensus.textContent = "";
  const messages = {
    pending: "Priorities changed, so the ranking was recalculated instantly. Run the specialist review for this new ranking.",
    failed: "The specialist agents are busy right now (Gemini rate limit). The engine's numbers above are complete.",
    skipped: state.health?.live_gemini_available
      ? "Specialist review was not run for this result."
      : "AI specialists need a Gemini API key. The deterministic engine result above is complete.",
  };
  const canRun = state.health?.live_gemini_available && d.selected_plan;
  clear(box, h("div", { class: "warroom-placeholder" },
    h("span", {}, messages[status] || messages.skipped),
    canRun ? h("button", { class: "btn btn-secondary btn-sm", onclick: runReview }, "Run specialist review") : null));
}

/** "How the concerns connect": dependencies and disagreements found by the Decision Lead. */
function renderConnections(d) {
  const brief = d.decision_brief || {};
  const deps = brief.dependencies || [];
  const conflicts = brief.conflicts || [];
  if (!deps.length && !conflicts.length) return null;
  const titleOf = (key) => (d.specialist_assessments.find((a) => a.agent === key) || {}).title || humanize(key);
  return h("div", { class: "connections" },
    h("h5", { class: "mini-head" }, "How the concerns connect ", h("span", { class: "lead-note" }, "· found by the Decision Lead")),
    deps.map((dep) => h("div", { class: "connection" },
      h("span", { class: "conn-path" },
        h("span", { "aria-hidden": "true" }, AGENT_ICONS[dep.from_agent] || "◆"), ` ${titleOf(dep.from_agent)} `,
        h("span", { class: "conn-arrow", "aria-label": "affects" }, "→"),
        h("span", { "aria-hidden": "true" }, ` ${AGENT_ICONS[dep.to_agent] || "◆"}`), ` ${titleOf(dep.to_agent)}`),
      h("span", { class: "conn-note" }, dep.note))),
    conflicts.map((c) => h("div", { class: "connection conflict" },
      h("span", { class: "conn-path" }, "⚖ Disagreement"), h("span", { class: "conn-note" }, c))));
}

async function runReview(e) {
  const btn = e.currentTarget;
  setLoading(btn, true);
  try {
    const asked = state.decision;
    const res = await api.review(asked);
    if (state.decision !== asked) return; // the manager moved on to another analysis
    state.decision = res;
    renderRecommendation(res);
    renderProgressSummary(res);
    if (res.ai_components?.war_room === "failed") toast("Specialists are still busy. Try again in a minute.", { error: true });
  } catch (err) {
    toast(`Review failed: ${err.message}`, { error: true });
    setLoading(btn, false);
  }
}

// Titles and excerpts of all plant documents, loaded once, so a citation can say what it is
let docIndex = null;
let docIndexLoading = null;
function loadDocIndex() {
  docIndexLoading ||= api.knowledge()
    .then((res) => { docIndex = Object.fromEntries((res.documents || []).map((doc) => [doc.document_id, doc])); })
    .catch(() => { docIndexLoading = null; });
  return docIndexLoading;
}

function shortTitle(title, max = 42) {
  return title.length > max ? `${title.slice(0, max - 1).trimEnd()}…` : title;
}

/** A source chip: the document code plus a short name; clicking opens its excerpt right under the card. */
function citeButton(id) {
  const doc = docIndex?.[id];
  return h("button", {
    class: "cite",
    title: doc ? `${id} · ${doc.title} (click to read the excerpt)` : `Show ${id}`,
    "aria-expanded": "false",
    onclick: (e) => toggleSourcePreview(e.currentTarget, id),
  }, h("span", { class: "cite-id" }, `📄 ${id}`), doc ? h("span", { class: "cite-name" }, ` · ${shortTitle(doc.title)}`) : null);
}

function toggleSourcePreview(btn, id) {
  const row = btn.closest(".cite-row");
  if (!row) { jumpToEvidence(id); return; }
  const open = row.nextElementSibling?.classList.contains("cite-preview") ? row.nextElementSibling : null;
  const sameDoc = open?.dataset.doc === id;
  row.querySelectorAll(".cite").forEach((b) => b.setAttribute("aria-expanded", "false"));
  open?.remove();
  if (sameDoc) return; // a second click closes it
  const doc = docIndex?.[id];
  const ev = state.decision?.engineering_evidence?.find((e) => e.document_id === id);
  const excerpt = readableExcerpt(ev?.excerpt || doc?.excerpt);
  row.after(h("div", { class: "cite-preview", "data-doc": id },
    h("div", { class: "cite-preview-title" }, `${id} · ${doc?.title || ev?.title || ""}`),
    excerpt ? h("blockquote", {}, `“${excerpt}”`) : null,
    h("button", { class: "link-btn", onclick: () => jumpToEvidence(id) }, "Show in Engineering Evidence ↓")));
  btn.setAttribute("aria-expanded", "true");
}

/** Document text without markdown headings or markers, cut at a sentence end (about 320 characters). */
export function readableExcerpt(text) {
  if (!text) return "";
  const plain = text.split(String.fromCharCode(10))
    .filter((line) => !line.trim().startsWith("#"))
    .join(" ")
    .replace(/[*`>]+/g, "") // markdown marks; underscores stay (PICK_AND_PLACE)
    .replace(/(^|\s)-\s+/g, "$1") // list dashes
    .replace(/\s+/g, " ")
    .replace(/\s\d+\.$/, "") // a dangling list number at the end of the excerpt
    .trim();
  const truncated = /(\.\.\.|…)$/.test(plain);
  if (plain.length <= 320 && !truncated) return plain;
  const cut = plain.slice(0, 320);
  const end = cut.lastIndexOf(". ");
  return end > 120 ? cut.slice(0, end + 1).replace(/\s\d+\.$/, "") : `${cut.replace(/[.…\s]+$/, "")}…`;
}

function jumpToEvidence(id) {
  const card = document.getElementById(`ev-${id}`);
  if (!card) return;
  $("#evidence-disclosure").open = true;
  card.scrollIntoView({ behavior: "smooth", block: "center" });
  card.classList.remove("flash");
  void card.offsetWidth;
  card.classList.add("flash");
}

function renderEvidence(d) {
  // Titles arrive once; then redraw so citation hovers and specialist-only sources are complete
  if (!docIndex) {
    loadDocIndex().then(() => { if (state.decision === d && docIndex) { renderWarRoom(d); renderEvidence(d); } });
  }
  // Every document a specialist cited is listed, so its "Sources:" link always has somewhere to go
  const listed = new Set(d.engineering_evidence.map((ev) => ev.document_id));
  const citedOnly = [...new Set((d.specialist_assessments || []).flatMap((a) => a.citations || []))]
    .filter((id) => !listed.has(id) && docIndex?.[id])
    .map((id) => ({ ...docIndex[id], evidence_category: "Cited by a specialist" }));
  const docs = [...d.engineering_evidence, ...citedOnly];
  $("#evidence-summary").textContent = `Engineering Evidence (${docs.length} documents cited)`;
  clear($("#evidence"), docs.length
    ? docs.map((ev) => {
        // Procedures and quality rules list no machines: they apply to the whole plant, not to the failed machine
        const appliesTo = ev.machine_ids?.length ? ev.machine_ids.join(", ") : "whole plant";
        return h("article", { class: "evidence-card", id: `ev-${ev.document_id}` },
          h("div", { class: "evidence-top" },
            h("div", { class: "evidence-id-group" },
              h("span", { class: "badge badge-mono" }, ev.document_id),
              h("span", { class: "badge badge-doc-type" }, humanize(ev.evidence_category || ev.document_type)),
            ),
            h("span", { class: "badge badge-asset" }, `Applies to: ${appliesTo}`)
          ),
          h("div", { class: "evidence-title" }, ev.title),
          h("blockquote", { class: "evidence-excerpt" }, `“${readableExcerpt(ev.excerpt)}”`),
        );
      })
    : h("p", { class: "subcard-hint" }, "No supporting engineering document was retrieved for this pattern."));
}

/** "Agent ran: simulate_machine_failure → evaluate_recovery_plans": the tools the ADK agent chose. */
/** What a tool call did, in plain words ("simulated M26 down for 8 h"); the raw call is on hover. */
function toolStep(t) {
  const a = t.args || {};
  const hrs = a.downtime_hours != null ? ` for ${num(a.downtime_hours, 0)} h` : "";
  switch (t.tool) {
    case "simulate_machine_failure": return `simulated ${a.machine_id || "a machine"} down${hrs}`;
    case "generate_recovery_plans": return `listed the recovery options${a.machine_id ? ` for ${a.machine_id}` : ""}`;
    case "evaluate_recovery_plans": return "compared the recovery plans";
    case "search_engineering_knowledge": return "searched the plant documents";
    case "get_machine_status": return `checked ${a.machine_id || "a machine"}'s status`;
    case "get_factory_state": return "checked the plant status";
    default: return humanize(t.tool);
  }
}

function toolTrace(tools) {
  const items = [];
  tools.forEach((t, i) => {
    if (i) items.push(h("span", { class: "arrow", "aria-hidden": "true" }, "→"));
    items.push(h("span", { class: "tool", title: `${t.tool}(${JSON.stringify(t.args)})` }, toolStep(t)));
  });
  return h("div", { class: "trace" }, h("span", { class: "fine-print" }, "⚙ The agent ran new calculations: "), items);
}

function renderDetails(d) {
  clear($("#agent-trace"), h("p", { class: "calc-note" },
    "Every number comes from the deterministic engine. Gemini understands the report and explains and reviews " +
    "the plan; questions that need new calculations are answered by the Google ADK agent in “Ask about this plan”, " +
    "which shows the tools it ran."));

  clear($("#weights"), Object.entries(d.objective_weights || {}).map(([k, v]) =>
    h("div", { class: "weight-row" },
      h("span", {}, humanize(k)),
      h("div", { class: "weight-track" }, h("div", { class: "weight-fill", style: `width:${v * 100}%` })),
      h("span", { class: "weight-val" }, pct(v)))));

  clear($("#rationale"), renderMarkdown(d.rationale));
  clear($("#assumptions"), (d.assumptions || []).map((a) => h("li", {}, a)));
  clear($("#constraints"), (d.hard_constraints_checked || []).map((c) => h("li", {}, humanize(c))));
}

// ------------------------------------------------------------------ ask

async function ask(question) {
  question = (question || "").trim();
  if (!question || !state.decision) return;
  const log = $("#chat-log");
  const input = $("#ask-input");
  input.value = "";
  log.appendChild(h("div", { class: "msg msg-user" }, question));
  const pending = h("div", { class: "msg msg-ai msg-pending" }, "Thinking… (the agent may run new simulations)");
  log.appendChild(pending);
  const btn = $("#btn-ask");
  setLoading(btn, true);

  // Check if this is a what-if constraint question
  if (/what if.*overtime.*not allowed|no overtime|disallow overtime/i.test(question)) {
    const beforeFeasible = state.decision.feasible_alternatives?.length || 0;
    const beforeInfeasible = state.decision.infeasible_alternatives?.length || 0;
    const beforeSelected = state.decision.selected_plan?.plan_id;
    try {
      const asked = state.decision;
      const updatedDecision = await api.whatIf(asked, question);
      if (state.decision !== asked) { pending.remove(); setLoading(btn, false); return; }
      const afterFeasible = updatedDecision.feasible_alternatives?.length || 0;
      const afterInfeasible = updatedDecision.infeasible_alternatives?.length || 0;
      const afterSelected = updatedDecision.selected_plan?.plan_id;

      const banner = $("#whatif-diff-banner");
      const diffText = $("#whatif-diff-text");
      if (banner && diffText) {
        diffText.textContent = `Feasible plans: ${beforeFeasible} → ${afterFeasible} · Infeasible: ${beforeInfeasible} → ${afterInfeasible} (Overtime strictly disallowed)`;
        banner.hidden = false;
      }

      render(updatedDecision);
      pending.replaceWith(h("div", { class: "msg msg-ai" },
        h("div", { class: "prose" },
          renderMarkdown(
            `**Sensitivity What-If Applied:** Overtime disallowed across plant lines.\n\n` +
            `- **Feasible alternatives:** reduced from ${beforeFeasible} to **${afterFeasible}**\n` +
            `- **Infeasible alternatives:** increased from ${beforeInfeasible} to **${afterInfeasible}** *(Overtime plans violated constraint)*\n` +
            `- **Recommendation:** ${afterSelected === beforeSelected
              ? `still **${planText(updatedDecision.selected_plan, updatedDecision.impact_summary.machine_id)}**, which does not rely on overtime.`
              : `now **${planText(updatedDecision.selected_plan, updatedDecision.impact_summary.machine_id)}**.`}`
          )
        )
      ));
      setLoading(btn, false);
      return;
    } catch (err) {
      // Fall through to regular ask
    }
  }

  try {
    const res = await api.ask(question, state.decision, chatHistory);
    chatHistory = [...chatHistory, { question, answer: res.answer || "" }].slice(-3);
    pending.replaceWith(h("div", { class: "msg msg-ai" },
      h("div", { class: "prose" }, renderMarkdown(res.answer)),
      res.tools_used?.length ? toolTrace(res.tools_used) : null,
      res.citations?.length ? h("div", { class: "cite-row" }, res.citations.map(citeButton)) : null,
      res.grounding_check && !res.grounding_check.passed
        ? h("div", { class: "fine-print warn-text" }, `⚠ Check this answer: ${res.grounding_check.ungrounded_numbers.join(", ")} ${res.grounding_check.ungrounded_numbers.length === 1 ? "does" : "do"} not appear in the plant data.`)
        : null,
      res.source === "rules" ? h("div", { class: "fine-print" }, "Rule-based answer (AI unavailable).") : null));
  } catch (e) {
    pending.replaceWith(h("div", { class: "msg msg-ai" }, `Sorry, I couldn't answer: ${e.message}`));
  } finally {
    setLoading(btn, false);
  }
}

// ------------------------------------------------------------------ step 5: approve

function openApproval() {
  const d = state.decision;
  if (!d?.selected_plan) return;
  const sp = d.selected_plan;
  const machine = d.impact_summary.machine_id;
  // What happened, in plain words: the fault report if the analysis came from an alert
  const what = currentIncidentContext?.kind === "fault" ? currentIncidentContext.title : `${machine} down for ${hours(d.impact_summary.downtime_hours)}`;
  const verdict = $("#warroom-consensus")?.textContent.trim();
  clear($("#approve-summary"),
    h("div", {}, h("strong", {}, planText(sp, machine))),
    h("div", {}, `${what} · extra cost ${usd(sp.cost_impact_usd)}`),
    verdict ? h("div", {}, `Specialists: ${verdict.replace(/^[✓⚠]\s*/, "")}`) : null,
  );
  // What the approver commits to: each specialist's condition
  const conditions = (d.specialist_assessments || []).filter((a) => (a.key_concern || "").trim());
  clear($("#approve-conditions"), conditions.length
    ? h("div", { class: "approve-conditions" },
        h("div", { class: "approve-conditions-title" }, "You approve with these conditions:"),
        h("ul", {}, conditions.map((a) => h("li", {}, h("strong", {}, `${AGENT_ICONS[a.agent] || "◆"} ${a.title}: `), a.key_concern))))
    : null);
  $("#approver-ack-text").textContent = conditions.length
    ? "I have reviewed the plan, its impact and the conditions above."
    : "I have reviewed the plan and its impact.";
  $("#approve-error").hidden = true;
  // Each objection must be acknowledged explicitly before the plan can be approved
  clear($("#approve-objections"), objectionsOf(d).map((o) =>
    h("label", { class: "check check-warn" },
      h("input", { type: "checkbox", required: true, class: "objection-ack" }),
      h("span", {}, h("strong", {}, `I have reviewed the ${o.title} objection: `), o.reason))));
  try { $("#approver-name").value = localStorage.getItem("fde-approver") || ""; } catch { /* storage unavailable */ }
  $("#approver-ack").checked = false;
  $("#approve-modal").hidden = false;
  ($("#approver-name").value ? $("#approver-ack") : $("#approver-name")).focus();
}

function closeApproval() {
  $("#approve-modal").hidden = true;
  $("#btn-approve").focus();
}

async function submitApproval(e) {
  e.preventDefault();
  const name = $("#approver-name").value.trim();
  // Say plainly what is missing instead of relying on the browser's tooltip
  const missing = [
    !name ? "your name and role" : null,
    !$("#approver-ack").checked ? "the confirmation tick" : null,
    [...$$(".objection-ack")].some((b) => !b.checked) ? "the objection tick" : null,
  ].filter(Boolean);
  const err = $("#approve-error");
  if (missing.length) {
    err.textContent = `To approve, please add ${missing.join(" and ")}.`;
    err.hidden = false;
    (!name ? $("#approver-name") : $("#approver-ack")).focus();
    return;
  }
  err.hidden = true;
  try { localStorage.setItem("fde-approver", name); } catch { /* storage unavailable */ }
  const btn = $("#btn-confirm-approve");
  setLoading(btn, true);
  try {
    const entry = await api.approve(state.decision, name, $("#approver-note").value.trim() || null, currentIncidentContext?.alert_id);
    closeApproval();
    renderApproval(entry);
    if (entry.resolved_incident) {
      // the fault is handled: it leaves "Needs action now"
      toast(`✓ ${entry.resolved_incident.title.split(" ")[0]} fault handled. Plan approved and logged as ${entry.decision_id}.`);
      window.dispatchEvent(new CustomEvent("fde:alerts-changed"));
    } else {
      toast(`Decision ${entry.decision_id} approved and logged.`);
    }
  } catch (err) {
    toast(`Approval failed: ${err.message}`, { error: true });
  } finally {
    setLoading(btn, false);
  }
}

/** "1. Halt… 2. Transfer…" on one line becomes one step per line ("8.0 hours" is left alone). */
function docLines(text) {
  return (text || "").replace(/\s+(?=\d{1,2}\.\s)/g, String.fromCharCode(10)).replace(/\s+(?=-\s[A-Z0-9])/g, String.fromCharCode(10)).trim();
}

export function documentTabs(docs) {
  const tabs = [
    ["supervisor_work_order", "Supervisor work order", "For the shift supervisor: the steps to carry out, in order"],
    ["quality_notice", "Quality notice", "For the quality team: inspections and holds required"],
    ["revised_schedule_summary", "Schedule changes", "For production planning: what changes for each affected order"],
    ["customer_message", "Customer update", "For the account manager to send to the customer"],
  ];
  const panel = h("div", { class: "doc-panel", role: "tabpanel" });
  const buttons = tabs.map(([key, label, tip], i) => h("button", {
    class: "doc-tab", role: "tab", "aria-selected": String(i === 0), title: tip,
    onclick: (ev) => {
      buttons.forEach((b) => b.setAttribute("aria-selected", "false"));
      ev.currentTarget.setAttribute("aria-selected", "true");
      show(key);
    },
  }, label));
  const show = (key) => clear(panel,
    h("button", { class: "btn btn-secondary btn-sm doc-copy", onclick: () => copyText(docLines(docs[key])) }, "Copy"),
    docLines(docs[key]) || "—");
  show(tabs[0][0]);
  return h("div", {},
    h("p", { class: "doc-intro" }, "Drafted by Gemini for each team: what the supervisor does, what quality checks, what changes per order, and a message for the customer."),
    h("div", { class: "doc-tabs", role: "tablist" }, buttons), panel);
}

function renderApproval(entry) {
  const box = $("#approval-result");
  clear(box,
    h("div", { class: "approved-banner" },
      h("span", {}, `✓ Approved by ${entry.approved_by}`),
      h("span", { class: "badge badge-mono" }, entry.decision_id),
      h("span", { class: "fine-print" }, entry.source === "gemini" ? "Documents drafted by Gemini from engine data" : "Documents from templates (AI unavailable)"),
      entry.grounding_check ? h("span", { class: `badge ${entry.grounding_check.passed ? "badge-grounding" : "badge-warn"}`, title: entry.grounding_check.passed
        ? "Every number in these documents appears in the engine data, the documents or your note."
        : `Not found in the data: ${entry.grounding_check.ungrounded_numbers.join(", ")}` },
        entry.grounding_check.passed ? "✓ Numbers traced to engine data" : "⚠ Check the numbers") : null),
    documentTabs(entry.documents));
  box.hidden = false;
  $("#btn-approve").disabled = true;
  const quickApprove = $("#btn-quick-approve");
  if (quickApprove) quickApprove.disabled = true;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}
