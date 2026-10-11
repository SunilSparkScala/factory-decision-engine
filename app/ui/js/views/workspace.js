// Decision Workspace: the guided 5-step flow
// 1 What happened → 2 Impact → 3 Options → 4 Recommendation → 5 Approve

import { $, $$, h, clear, toast, setLoading, renderMarkdown, copyText } from "../dom.js";
import { api } from "../api.js";
import { state } from "../state.js";
import {
  STRATEGY, strategyLabel, strategyIcon, planText, usd, num, hours, pct, lineName,
  deliveryLabel, qualityLabel, riskLabel, deliveryRiskTone, stanceInfo, presetForWeights, humanize,
} from "../format.js";

const CANONICAL_PROMPT = "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.";
const STEP_IDS = ["#step-impact", "#step-options", "#step-recommendation", "#step-approve"];
const AGENT_ICONS = { production: "🏭", quality: "🔍", maintenance: "🔧", energy: "⚡" };

let ctx = { showView: () => {} };
let progressTimers = [];
let busy = false;

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
  for (const chip of $$("#step-input .chip-btn[data-prompt]")) {
    chip.addEventListener("click", () => analyze(chip.dataset.prompt, { fresh: true }));
  }
  $("#btn-reset-demo").addEventListener("click", resetDemo);
  for (const btn of $$("#priority-presets button")) {
    btn.addEventListener("click", () => rerank(btn.dataset.priorities));
  }
  $("#ask-form").addEventListener("submit", (e) => {
    e.preventDefault();
    ask($("#ask-input").value);
  });
  for (const chip of $$("#ask-card .chip-btn")) {
    chip.addEventListener("click", () => ask(chip.dataset.question));
  }
  $("#btn-approve").addEventListener("click", openApproval);
  $("#approve-form").addEventListener("submit", submitApproval);
  for (const el of $$("#approve-modal [data-close]")) el.addEventListener("click", closeApproval);
  $("#approve-modal").addEventListener("click", (e) => { if (e.target.id === "approve-modal") closeApproval(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#approve-modal").hidden) closeApproval(); });
}

export function onShow() {}

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

function resetDemo() {
  state.decision = null;
  clear($("#chat-log"));
  $("#approval-result").hidden = true;
  analyze(CANONICAL_PROMPT, { fresh: true });
}

// ------------------------------------------------------------------ analyze

export async function analyze(prompt, { fresh = false } = {}) {
  prompt = (prompt || "").trim();
  if (!prompt) {
    showError("Describe the disruption first, e.g. “M17 will be down for 6 hours”.");
    return;
  }
  if (busy) return;
  busy = true;
  $("#scenario-input").value = prompt;
  state.lastPrompt = prompt;
  hideMessages();
  const btn = $("#btn-analyze");
  setLoading(btn, true);
  startProgress();
  try {
    const previous = fresh ? null : state.decision;
    const decision = await api.analyze(prompt, previous);
    finishProgress();
    render(decision);
  } catch (e) {
    stopProgress();
    showError(e.message);
  } finally {
    setLoading(btn, false);
    busy = false;
  }
}

function startProgress() {
  const box = $("#progress");
  box.hidden = false;
  const steps = $$(".progress-step", box);
  steps.forEach((s) => s.classList.remove("active", "done"));
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
  $$(".progress-step").forEach((s) => { s.classList.remove("active"); s.classList.add("done"); });
  setTimeout(() => { $("#progress").hidden = true; }, 700);
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

  if (!decision.selected_plan) {
    renderClarification(decision);
    if (!decision.impact_summary?.machine_id || decision.impact_summary?.error) {
      showSteps(false);
      return;
    }
  }

  state.decision = decision;
  $("#approval-result").hidden = true;
  $("#btn-approve").disabled = !decision.selected_plan;
  showSteps(true);
  renderImpact(decision);
  renderOptions(decision);
  renderRecommendation(decision);
  reportDegradedAi(decision);
}

function showSteps(visible) {
  $("#empty-state").hidden = visible;
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
  $("#impact-scenario").textContent = d.scenario_summary;

  const node = (label, value, sub, cls = "") =>
    h("div", { class: `ripple-node ${cls}` },
      h("div", { class: "rn-label" }, label),
      h("div", { class: "rn-value" }, value),
      sub ? h("div", { class: "rn-sub" }, sub) : null);
  const arrow = () => h("div", { class: "ripple-arrow", "aria-hidden": "true" }, "→");
  clear($("#ripple-chain"),
    node("Machine down", chain.machine_id, `${(chain.machine_type || "").replace(/_/g, " ")} · ${hours(imp.downtime_hours)}`, "origin"), arrow(),
    node("Station", chain.station_name?.replace(/^Station /, "") || chain.station_id, chain.operation_type ? humanize(chain.operation_type) : ""), arrow(),
    node("Line", chain.line_name || lineName(chain.line_id), `${pct((imp.capacity_loss_percentage || 0) / 100, 1)} capacity lost`), arrow(),
    node("Orders", `${imp.affected_order_count} affected`, `${imp.high_priority_affected_count} high-priority`), arrow(),
    node("Delivery risk", humanize(imp.delivery_risk), (imp.affected_product_models || []).join(", "), "end"),
  );

  const card = (label, value, sub, tone = "") =>
    h("div", { class: `impact-card ${tone ? `tone-${tone}` : ""}` },
      h("div", { class: "impact-label" }, label),
      h("div", { class: "impact-value" }, value),
      h("div", { class: "impact-sub" }, sub));
  clear($("#impact-cards"),
    card("Production lost", `${num(imp.capacity_loss_units)} units`, `${num(imp.capacity_loss_percentage, 1)}% of line capacity`, "danger"),
    card("Orders affected", String(imp.affected_order_count), `${imp.high_priority_affected_count} high-priority`, imp.high_priority_affected_count ? "warn" : ""),
    card("Delivery risk", humanize(imp.delivery_risk), "if nothing is done", deliveryRiskTone(imp.delivery_risk)),
    card("Backup machines", String((imp.alternative_candidates || []).length), "same operation type in the plant"),
  );

  const table = $("#orders-table");
  clear(table,
    h("thead", {}, h("tr", {}, ["Order", "Model", "Qty", "Priority", "Due"].map((t) => h("th", {}, t)))),
    h("tbody", {}, d.affected_orders.map((o) => h("tr", {},
      h("td", { class: "mono" }, o.order_id),
      h("td", {}, o.product_model),
      h("td", { class: "num" }, num(o.quantity)),
      h("td", {}, h("span", { class: `badge ${o.priority === "HIGH" ? "badge-danger" : o.priority === "MEDIUM" ? "badge-warn" : ""}` }, humanize(o.priority))),
      h("td", {}, new Date(o.due_time).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" }))))),
  );
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

  $("#all-candidates-summary").textContent = `Compare all ${d.candidate_plans.length} candidates (${d.feasible_alternatives.length} feasible)`;
  const sorted = d.candidate_plans.slice().sort((a, b) => (b.is_feasible - a.is_feasible) || ((b.weighted_score ?? -1) - (a.weighted_score ?? -1)));
  clear($("#candidates-table"),
    h("thead", {}, h("tr", {}, ["Plan", "Option", "Extra cost", "Delivery score", "Quality", "Risk", "Energy kWh", "Overall score", "Status"].map((t) => h("th", {}, t)))),
    h("tbody", {}, sorted.map((p) => h("tr", { class: `${d.selected_plan?.plan_id === p.plan_id ? "is-selected" : ""}${p.is_feasible ? "" : " infeasible"}` },
      h("td", { class: "mono" }, p.plan_id),
      h("td", {}, planText(p, machine)),
      h("td", { class: "num" }, usd(p.cost_impact_usd)),
      h("td", { class: "num" }, p.delivery_delay_score?.toFixed(2) ?? "—"),
      h("td", { class: "num" }, p.quality_penalty_score?.toFixed(2) ?? "—"),
      h("td", { class: "num" }, p.risk_score?.toFixed(2) ?? "—"),
      h("td", { class: "num" }, num(p.energy_kwh)),
      h("td", { class: "num" }, p.weighted_score?.toFixed(4) ?? "—"),
      h("td", {}, p.is_feasible ? (d.selected_plan?.plan_id === p.plan_id ? "Recommended" : "Feasible") : (p.infeasibility_reasons || []).join(" "))))),
  );
}

async function rerank(priorities) {
  if (!state.decision || busy) return;
  busy = true;
  const seg = $("#priority-presets");
  seg.classList.add("busy");
  try {
    const before = state.decision.selected_plan?.plan_id;
    const res = await api.rerank(state.decision, priorities);
    // Re-ranking is instant and deterministic; the specialists then re-review automatically
    const reReview = Boolean(state.health?.live_gemini_available && res.selected_plan);
    if (reReview) res.ai_components = { ...res.ai_components, war_room: "reviewing" };
    render(res);
    const after = res.selected_plan?.plan_id;
    toast(before === after
      ? "Same plan is still best under these priorities."
      : `New recommendation: ${planText(res.selected_plan, res.impact_summary.machine_id)}`);
    if (reReview) autoReview(res);
  } catch (e) {
    toast(`Re-ranking failed: ${e.message}`, { error: true });
  } finally {
    seg.classList.remove("busy");
    busy = false;
  }
}

/** Run the specialist review for a decision; ignore the result if the user has moved on. */
async function autoReview(decision) {
  try {
    const reviewed = await api.review(decision);
    if (state.decision !== decision) return;
    state.decision = reviewed;
    renderRecommendation(reviewed);
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

function renderRecommendation(d) {
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
  $("#rec-title").textContent = brief?.headline || planText(sp, machine);
  $("#rec-summary").textContent = brief?.summary
    || `${planText(sp, machine)}. Chosen by the optimizer as the best balance of delivery, cost, quality, energy and risk under your priorities.`;
  $("#rec-plan-id").textContent = sp.plan_id;
  $("#rec-strategy").textContent = strategyLabel(sp.strategy_type);

  const grounding = $("#rec-grounding");
  if (brief?.grounding_check) {
    const ok = brief.grounding_check.passed;
    grounding.hidden = false;
    grounding.className = `badge ${ok ? "badge-ok" : "badge-warn"}`;
    grounding.textContent = ok ? "✓ All AI numbers traced to engine data" : `⚠ ${brief.grounding_check.ungrounded_numbers.length} AI number(s) not found in engine data`;
    grounding.title = ok ? "Every number the AI wrote appears in the deterministic engine output or the cited documents." : `Unverified: ${brief.grounding_check.ungrounded_numbers.join(", ")}`;
  } else {
    grounding.hidden = true;
  }

  const fig = (label, value) => h("div", { class: "fig" }, h("div", { class: "fig-label" }, label), h("div", { class: "fig-value" }, value));
  clear($("#rec-figures"),
    fig("Extra cost", usd(sp.cost_impact_usd)),
    fig("Delivery", deliveryLabel(sp.delivery_delay_score, best).text),
    fig("Quality risk", qualityLabel(sp.quality_penalty_score).text),
    fig("Energy", `${num(sp.energy_kwh)} kWh`),
  );

  const why = brief?.why?.length ? brief.why : fallbackWhy(d);
  const watch = brief?.watch_outs?.length ? brief.watch_outs : d.risks;
  const next = brief?.next_steps?.length ? brief.next_steps : [
    "Review the specialist conditions and evidence below.",
    "Approve to record the decision and draft the floor documents.",
  ];
  clear($("#rec-why"), why.map((t) => h("li", {}, h("span", {}, t))));
  clear($("#rec-watch"), watch.map((t) => h("li", {}, h("span", {}, t))));
  clear($("#rec-next"), next.map((t) => h("li", {}, h("span", {}, t))));

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
    const cls = !s.plan_id ? "none" : s.is_recommended ? "rec" : "alt";
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
  const marker = h("div", { class: "robust-marker", style: `left:${pos(rb.base_downtime_hours)}`, "data-label": `${num(rb.base_downtime_hours, 1)} h` });
  bar.appendChild(marker);
  const axis = h("div", { class: "robust-axis", "aria-hidden": "true" },
    [0, 8, 16, 24, 32, 40, 48].filter((t) => t <= maxH).map((t) => h("span", { style: `left:${pos(t)}` }, `${t}h`)));
  const verdict = rb.downtime_range
    ? (rb.holds_for_stated_range ? `✓ Holds across the full ${rb.downtime_range[0]}–${rb.downtime_range[1]} h estimate.` : `⚠ The best plan changes within the ${rb.downtime_range[0]}–${rb.downtime_range[1]} h estimate.`)
    : null;
  clear(box, wrap, axis, h("p", { class: "robust-summary" }, verdict ? h("strong", {}, verdict + " ") : null, rb.summary));
}

function renderWarRoom(d) {
  const box = $("#warroom");
  const status = d.ai_components?.war_room;
  const consensus = $("#warroom-consensus");
  if (status === "reviewing") {
    consensus.textContent = "Four AI specialists review the plan from their domain.";
    clear(box, h("div", { class: "warroom-placeholder" },
      h("span", { class: "reviewing" }, h("span", { class: "spinner inline", "aria-hidden": "true" }),
        "Specialists are re-reviewing the plan for your new priorities…")));
    return;
  }
  if (d.specialist_assessments?.length) {
    consensus.textContent = d.decision_brief?.specialist_consensus || "Four AI specialists reviewed the plan.";
    const cards = d.specialist_assessments.map((a) => {
      const stance = stanceInfo(a.stance);
      return h("article", { class: `agent-card agent-${a.agent}${a.stance === "oppose" ? " opposes" : ""}` },
        h("div", { class: "agent-head" },
          h("span", { class: "agent-avatar", "aria-hidden": "true" }, AGENT_ICONS[a.agent] || "◆"),
          h("span", { class: "agent-name" }, `${a.title} specialist`),
          h("span", { class: `badge ${stance.cls}`, style: "margin-left:auto" }, stance.text)),
        h("div", { class: "agent-headline" }, a.headline),
        h("div", { class: "agent-text" }, a.assessment),
        a.key_concern ? h("div", { class: "agent-concern" }, h("strong", {}, "Condition: "), a.key_concern) : null,
        a.citations?.length ? h("div", { class: "cite-row" }, a.citations.map(citeButton)) : null);
    });
    clear(box, cards, renderConnections(d));
    return;
  }
  consensus.textContent = "Four AI specialists review the plan from their domain.";
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
    h("h5", { class: "mini-head" }, "How the concerns connect"),
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
    const res = await api.review(state.decision);
    state.decision = res;
    renderRecommendation(res);
    if (res.ai_components?.war_room === "failed") toast("Specialists are still busy. Try again in a minute.", { error: true });
  } catch (err) {
    toast(`Review failed: ${err.message}`, { error: true });
    setLoading(btn, false);
  }
}

function citeButton(id) {
  return h("button", {
    class: "cite",
    title: `Show ${id}`,
    onclick: () => {
      const card = document.getElementById(`ev-${id}`);
      if (!card) return;
      $("#evidence-disclosure").open = true;
      card.scrollIntoView({ behavior: "smooth", block: "center" });
      card.classList.remove("flash");
      void card.offsetWidth;
      card.classList.add("flash");
    },
  }, id);
}

function renderEvidence(d) {
  $("#evidence-summary").textContent = `Engineering evidence (${d.engineering_evidence.length} documents)`;
  clear($("#evidence"), d.engineering_evidence.length
    ? d.engineering_evidence.map((ev) => h("article", { class: "evidence-card", id: `ev-${ev.document_id}` },
        h("div", { class: "evidence-top" },
          h("span", { class: "badge badge-mono" }, ev.document_id),
          h("span", { class: "badge" }, humanize(ev.evidence_category || ev.document_type))),
        h("div", { class: "evidence-title" }, ev.title),
        h("div", { class: "evidence-excerpt" }, ev.excerpt)))
    : h("p", { class: "subcard-hint" }, "No supporting engineering document was retrieved for this pattern."));
}

/** "Agent ran: simulate_machine_failure → evaluate_recovery_plans": the tools the ADK agent chose. */
function toolTrace(tools) {
  const items = [];
  tools.forEach((t, i) => {
    if (i) items.push(h("span", { class: "arrow", "aria-hidden": "true" }, "→"));
    items.push(h("span", { class: "tool", title: JSON.stringify(t.args) }, t.tool));
  });
  return h("div", { class: "trace" }, h("span", { class: "fine-print" }, "Agent ran: "), items);
}

function renderDetails(d) {
  clear($("#agent-trace"), h("span", { class: "fine-print" },
    "Numbers come from the deterministic engine. Questions that need new calculations are answered by the " +
    "AI agent in “Ask about this decision”, which shows the tools it ran."));

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
  const pending = h("div", { class: "msg msg-ai msg-pending" }, "Thinking…");
  log.appendChild(pending);
  const btn = $("#btn-ask");
  setLoading(btn, true);
  try {
    const res = await api.ask(question, state.decision);
    pending.replaceWith(h("div", { class: "msg msg-ai" },
      h("div", { class: "prose" }, renderMarkdown(res.answer)),
      res.tools_used?.length ? toolTrace(res.tools_used) : null,
      res.citations?.length ? h("div", { class: "cite-row" }, res.citations.map(citeButton)) : null,
      res.grounding_check && !res.grounding_check.passed
        ? h("div", { class: "fine-print warn-text" }, `⚠ Not found in engine data: ${res.grounding_check.ungrounded_numbers.join(", ")}`)
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
  clear($("#approve-summary"),
    h("div", {}, h("strong", {}, planText(sp, d.impact_summary.machine_id))),
    h("div", {}, `${d.scenario_summary} · extra cost ${usd(sp.cost_impact_usd)}`),
    d.decision_brief?.specialist_consensus ? h("div", {}, d.decision_brief.specialist_consensus) : null,
  );
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
  if (!name) return;
  try { localStorage.setItem("fde-approver", name); } catch { /* storage unavailable */ }
  const btn = $("#btn-confirm-approve");
  setLoading(btn, true);
  try {
    const entry = await api.approve(state.decision, name, $("#approver-note").value.trim() || null);
    closeApproval();
    renderApproval(entry);
    toast(`Decision ${entry.decision_id} approved and logged.`);
  } catch (err) {
    toast(`Approval failed: ${err.message}`, { error: true });
  } finally {
    setLoading(btn, false);
  }
}

export function documentTabs(docs) {
  const tabs = [
    ["supervisor_work_order", "Supervisor work order"],
    ["quality_notice", "Quality notice"],
    ["revised_schedule_summary", "Schedule changes"],
    ["customer_message", "Customer update"],
  ];
  const panel = h("div", { class: "doc-panel", role: "tabpanel" });
  const buttons = tabs.map(([key, label], i) => h("button", {
    class: "doc-tab", role: "tab", "aria-selected": String(i === 0),
    onclick: (ev) => {
      buttons.forEach((b) => b.setAttribute("aria-selected", "false"));
      ev.currentTarget.setAttribute("aria-selected", "true");
      show(key);
    },
  }, label));
  const show = (key) => clear(panel,
    h("button", { class: "btn btn-secondary btn-sm doc-copy", onclick: () => copyText(docs[key] || "") }, "Copy"),
    docs[key] || "—");
  show(tabs[0][0]);
  return h("div", {}, h("div", { class: "doc-tabs", role: "tablist" }, buttons), panel);
}

function renderApproval(entry) {
  const box = $("#approval-result");
  clear(box,
    h("div", { class: "approved-banner" },
      h("span", {}, `✓ Approved by ${entry.approved_by}`),
      h("span", { class: "badge badge-mono" }, entry.decision_id),
      h("span", { class: "fine-print" }, entry.source === "gemini" ? "Documents drafted by Gemini from engine data" : "Documents from templates (AI unavailable)")),
    documentTabs(entry.documents));
  box.hidden = false;
  $("#btn-approve").disabled = true;
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}
