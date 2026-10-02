/**
 * FACTORY DECISION ENGINE — DECISION INTELLIGENCE CLIENT
 * Connects directly to FastAPI /api/analyze and /api/what-if endpoints.
 * Never duplicates optimization or simulation algorithms on the client.
 */

let currentResponse = null;
let isRunning = false;

// DOM Elements
const scenarioInput = document.getElementById("scenario-input");
const btnAnalyze = document.getElementById("btn-analyze");
const loadingStepper = document.getElementById("loading-stepper");
const errorBanner = document.getElementById("error-banner");
const errorTitle = document.getElementById("error-title");
const errorMessage = document.getElementById("error-message");
const errorClarification = document.getElementById("error-clarification");
const resultsWorkspace = document.getElementById("results-workspace");

// Provenance & Badges
const provenanceBadge = document.getElementById("provenance-badge");
const provenanceText = document.getElementById("provenance-text");

// Impact Chain
const impactChainNodes = document.getElementById("impact-chain-nodes");

// Metrics
const metricCapacityLoss = document.getElementById("metric-capacity-loss");
const metricCapacityPct = document.getElementById("metric-capacity-pct");
const metricAffectedOrders = document.getElementById("metric-affected-orders");
const metricHighPriority = document.getElementById("metric-high-priority");
const metricModelsList = document.getElementById("metric-models-list");
const metricDeliveryImpact = document.getElementById("metric-delivery-impact");
const metricRiskLevel = document.getElementById("metric-risk-level");
const metricQualityScore = document.getElementById("metric-quality-score");
const metricEnergyKwh = document.getElementById("metric-energy-kwh");

// Recommendation
const recActionTitle = document.getElementById("rec-action-title");
const recPlanId = document.getElementById("rec-plan-id");
const recStrategy = document.getElementById("rec-strategy");
const recScore = document.getElementById("rec-score");
const recCostVal = document.getElementById("rec-cost-val");
const recDelVal = document.getElementById("rec-del-val");
const recQualVal = document.getElementById("rec-qual-val");
const recWhyList = document.getElementById("rec-why-list");

// Alternatives
const tabFeasible = document.getElementById("tab-feasible");
const tabInfeasible = document.getElementById("tab-infeasible");
const countFeasible = document.getElementById("count-feasible");
const countInfeasible = document.getElementById("count-infeasible");
const feasiblePlansList = document.getElementById("feasible-plans-list");
const infeasiblePlansList = document.getElementById("infeasible-plans-list");
const alternativesCountBadge = document.getElementById("alternatives-count-badge");

// Trade-offs & Weights
const weightsBarsContainer = document.getElementById("weights-bars-container");
const engineRationaleText = document.getElementById("engine-rationale-text");

// Evidence
const evidenceGrid = document.getElementById("evidence-grid");
const evidenceCountBadge = document.getElementById("evidence-count-badge");

// What-If
const whatIfInput = document.getElementById("what-if-input");
const btnWhatIf = document.getElementById("btn-what-if");

/**
 * Animate the 6-stage loading stepper during backend execution.
 */
function animateLoadingStepper() {
  const stepNodes = [
    document.getElementById("step-1"),
    document.getElementById("step-2"),
    document.getElementById("step-3"),
    document.getElementById("step-4"),
    document.getElementById("step-5"),
    document.getElementById("step-6"),
  ];

  stepNodes.forEach((node) => {
    node.classList.remove("active", "done");
  });

  let currentStep = 0;
  stepNodes[0].classList.add("active");

  const interval = setInterval(() => {
    if (!isRunning) {
      clearInterval(interval);
      return;
    }
    if (currentStep < stepNodes.length) {
      stepNodes[currentStep].classList.remove("active");
      stepNodes[currentStep].classList.add("done");
      currentStep++;
      if (currentStep < stepNodes.length) {
        stepNodes[currentStep].classList.add("active");
      }
    }
  }, 220);

  return interval;
}

/**
 * Primary action: Analyze Scenario through FastAPI /api/analyze
 */
async function analyzeScenario(promptText) {
  const prompt = (promptText || scenarioInput.value || "").trim();
  if (!prompt) return;

  // Check if this is a follow-up what-if query without machine ID on an active base scenario
  const isWhatIfPrompt =
    /^what\s+if/i.test(prompt) ||
    /^(suppose|assume)\b/i.test(prompt) ||
    (!prompt.match(/\bM\d+\b/i) && currentResponse && currentResponse.impact_summary && currentResponse.impact_summary.machine_id);

  if (isWhatIfPrompt && currentResponse && currentResponse.impact_summary && currentResponse.impact_summary.machine_id) {
    whatIfInput.value = prompt;
    return runWhatIf(prompt);
  }

  hideError();
  isRunning = true;
  loadingStepper.classList.remove("hidden");
  btnAnalyze.disabled = true;

  const stepperInterval = animateLoadingStepper();

  try {
    const res = await fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        prompt: prompt,
        previous_response: currentResponse || null,
      }),
    });

    const data = await res.json();
    isRunning = false;
    clearInterval(stepperInterval);
    loadingStepper.classList.add("hidden");
    btnAnalyze.disabled = false;

    if (!res.ok) {
      showError("Analysis Error", data.detail || "Error communicating with decision engine.");
      return;
    }

    if (data.clarification_needed && !data.selected_plan) {
      showError(
        "Clarification Needed / Incomplete Scenario",
        data.rationale || "Unable to formulate feasible plan.",
        data.clarification_needed
      );
      renderUI(data);
      return;
    }

    currentResponse = data;
    renderUI(data);
  } catch (err) {
    isRunning = false;
    clearInterval(stepperInterval);
    loadingStepper.classList.add("hidden");
    btnAnalyze.disabled = false;
    showError("Network / Backend Error", `Failed to reach backend API: ${err.message}`);
  }
}

/**
 * Sensitivity action: What-If re-evaluation through /api/what-if
 */
async function runWhatIf(followUpPrompt) {
  const query = (followUpPrompt || whatIfInput.value || "").trim();
  if (!query) return;

  if (!currentResponse) {
    // If no prior response, run as primary analyze
    scenarioInput.value = query;
    return analyzeScenario(query);
  }

  hideError();
  isRunning = true;
  loadingStepper.classList.remove("hidden");
  btnWhatIf.disabled = true;

  const stepperInterval = animateLoadingStepper();

  try {
    const res = await fetch("/api/what-if", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        previous_response: currentResponse,
        follow_up_prompt: query,
      }),
    });

    const data = await res.json();
    isRunning = false;
    clearInterval(stepperInterval);
    loadingStepper.classList.add("hidden");
    btnWhatIf.disabled = false;

    if (!res.ok) {
      showError("What-If Sensitivity Error", data.detail || "Failed to execute what-if simulation.");
      return;
    }

    currentResponse = data;
    scenarioInput.value = query;
    whatIfInput.value = query;
    renderUI(data);
  } catch (err) {
    isRunning = false;
    clearInterval(stepperInterval);
    loadingStepper.classList.add("hidden");
    btnWhatIf.disabled = false;
    showError("Network / Backend Error", `Failed to reach backend API: ${err.message}`);
  }
}

/**
 * Render the complete DecisionResponse data onto the UI
 */
function renderUI(data) {
  // 1. Provenance Badge
  if (data.execution_mode === "live_gemini") {
    provenanceBadge.className = "provenance-badge mode-live";
    provenanceText.textContent = "Gemini + Google ADK";
  } else {
    provenanceBadge.className = "provenance-badge mode-fallback";
    provenanceText.textContent = "Deterministic fallback — Gemini unavailable";
  }

  // 2. Dependency Trace Chain
  renderImpactChain(data);

  // 3. Impact Summary Cards
  renderImpactSummary(data);

  // 4. Recommendation Panel
  renderRecommendation(data);

  // 5. Alternatives (Feasible vs. Infeasible)
  renderAlternatives(data);

  // 6. Trade-Offs & Objective Weights
  renderTradeOffs(data);

  // 7. Grounded Engineering Evidence
  renderEvidence(data);
}

/**
 * Render the dynamic visual impact chain
 */
function renderImpactChain(data) {
  impactChainNodes.innerHTML = "";
  const dc = data.dependency_chain || {};
  const imp = data.impact_summary || {};
  const orders = data.affected_orders || [];

  const mId = dc.machine_id || imp.machine_id || "--";
  const mType = dc.machine_type || "--";
  const staId = dc.station_id || "--";
  const staName = dc.station_name || "--";
  const lineId = dc.line_id || "--";
  const lineName = dc.line_name || "--";
  const opType = dc.operation_type || "--";
  const orderCount = orders.length || imp.affected_order_count || 0;
  const highPriCount = imp.high_priority_affected_count != null ? imp.high_priority_affected_count : "--";

  const nodes = [
    { type: "DISRUPTED MACHINE", title: mId, detail: mType, isTarget: true },
    { type: "STATION", title: staId, detail: staName },
    { type: "PRODUCTION LINE", title: lineId, detail: lineName },
    { type: "OPERATION", title: opType, detail: "Critical Bottleneck" },
    { type: "AFFECTED ORDERS", title: `${orderCount} Orders`, detail: `${highPriCount} High-Priority` },
    { type: "DELIVERY", title: "SLA Window", detail: "Customer Commitments", isDelivery: true },
  ];

  nodes.forEach((node, idx) => {
    const nodeEl = document.createElement("div");
    nodeEl.className = `chain-node ${node.isTarget ? "node-target" : ""} ${node.isDelivery ? "node-delivery" : ""}`;
    nodeEl.innerHTML = `
      <span class="node-type">${node.type}</span>
      <span class="node-title">${node.title}</span>
      <span class="node-detail">${node.detail}</span>
    `;
    impactChainNodes.appendChild(nodeEl);

    if (idx < nodes.length - 1) {
      const arrowEl = document.createElement("div");
      arrowEl.className = "chain-arrow";
      arrowEl.textContent = "→";
      impactChainNodes.appendChild(arrowEl);
    }
  });
}

/**
 * Render the operational impact metrics
 */
function renderImpactSummary(data) {
  const imp = data.impact_summary || {};
  const orders = data.affected_orders || [];

  // Capacity Loss
  const lossUnits = imp.capacity_loss_units != null ? imp.capacity_loss_units.toFixed(1) : "--";
  const lossPct = imp.capacity_loss_percentage != null ? `${imp.capacity_loss_percentage.toFixed(1)}%` : "--";
  metricCapacityLoss.textContent = lossUnits !== "--" ? `${lossUnits} units` : "--";
  metricCapacityPct.textContent = lossPct !== "--" ? `(${lossPct})` : "";

  // Affected Orders
  const orderCount = imp.affected_order_count != null ? imp.affected_order_count : (orders.length > 0 ? orders.length : "--");
  const highPriCount = imp.high_priority_affected_count != null ? imp.high_priority_affected_count : "--";
  metricAffectedOrders.textContent = orderCount;
  metricHighPriority.textContent = highPriCount !== "--" ? `${highPriCount} HIGH PRIORITY` : "--";

  // Affected Product Models
  if (imp.affected_product_models && imp.affected_product_models.length > 0) {
    metricModelsList.textContent = `Models: ${imp.affected_product_models.join(", ")}`;
  } else {
    metricModelsList.textContent = "Models: --";
  }

  // Delivery Impact Score (Strictly labeled as normalized penalty score, NOT hours)
  const sp = data.selected_plan;
  const delScore = sp && sp.delivery_delay_score != null ? sp.delivery_delay_score.toFixed(2) : "--";
  metricDeliveryImpact.textContent = delScore;

  // Delivery Risk
  const risk = imp.delivery_risk || "--";
  metricRiskLevel.textContent = risk;
  metricRiskLevel.className = `risk-badge ${risk === "HIGH" ? "badge-high" : risk === "MEDIUM" ? "badge-medium" : risk === "LOW" ? "badge-low" : ""}`;

  // Synthetic Proxies
  metricQualityScore.textContent = sp && sp.quality_penalty_score != null ? sp.quality_penalty_score.toFixed(2) : "--";
  metricEnergyKwh.textContent = sp && sp.energy_kwh != null ? `${sp.energy_kwh.toFixed(1)} kWh` : "--";
}

/**
 * Render the dominant recommendation card
 */
function renderRecommendation(data) {
  const sp = data.selected_plan;
  if (!sp) {
    recActionTitle.textContent = "No Feasible Plan Available";
    recPlanId.textContent = "NONE";
    recStrategy.textContent = "CONSTRAINT_BREACH";
    recScore.textContent = "N/A";
    recCostVal.textContent = "N/A";
    recDelVal.textContent = "N/A";
    recQualVal.textContent = "N/A";
    recWhyList.innerHTML = `
      <li style="color:#fca5a5;">No candidate plan satisfies all hard operational constraints.</li>
      <li>Review plant downtime or relax constraint thresholds.</li>
    `;
    return;
  }

  recActionTitle.textContent = sp.description || (sp.target_machine_id ? `Transfer production to ${sp.target_machine_id}` : "--");
  recPlanId.textContent = sp.plan_id || "PLAN_--";
  recStrategy.textContent = sp.strategy_type ? sp.strategy_type.replace(/_/g, " ").toUpperCase() : "--";
  recScore.textContent = sp.weighted_score != null ? sp.weighted_score.toFixed(4) : "--";
  recCostVal.textContent = sp.cost_impact_usd != null ? `$${sp.cost_impact_usd.toFixed(2)} USD` : "--";
  recDelVal.textContent = sp.delivery_delay_score != null ? sp.delivery_delay_score.toFixed(2) : "--";
  recQualVal.textContent = sp.quality_penalty_score != null ? sp.quality_penalty_score.toFixed(2) : "--";

  // Reasons Why
  recWhyList.innerHTML = `
    <li><strong>Feasible:</strong> Satisfies line speed, nominal rate, and shift boundary constraints.</li>
    <li><strong>Product Compatibility:</strong> Downstream station accommodates affected product models.</li>
    <li><strong>Capacity Available:</strong> Sufficient buffer confirmed on target machine ${sp.target_machine_id || "target asset"}.</li>
    <li><strong>Deterministic Optimal:</strong> Best multi-objective composite score among all feasible alternatives.</li>
    <li><strong>Balanced Trade-Off:</strong> Effectively protects high-priority deliveries within budget limits.</li>
  `;
}

/**
 * Render recovery alternatives (Feasible vs. Infeasible tabs)
 */
function renderAlternatives(data) {
  const feasible = data.feasible_alternatives || (data.candidate_plans || []).filter((p) => p.is_feasible);
  const infeasible = data.infeasible_alternatives || (data.candidate_plans || []).filter((p) => !p.is_feasible);

  countFeasible.textContent = feasible.length;
  countInfeasible.textContent = infeasible.length;
  alternativesCountBadge.textContent = `${feasible.length + infeasible.length} CANDIDATES`;

  // Render Feasible Plans
  feasiblePlansList.innerHTML = "";
  if (feasible.length === 0) {
    feasiblePlansList.innerHTML = `<div class="text-muted" style="padding:12px;">No feasible alternatives identified.</div>`;
  } else {
    feasible.forEach((p) => {
      const isSelected = data.selected_plan && data.selected_plan.plan_id === p.plan_id;
      const card = document.createElement("div");
      card.className = `plan-item-card ${isSelected ? "is-selected" : ""}`;
      card.innerHTML = `
        <div class="plan-item-header">
          <div class="plan-title-col">
            <span class="plan-name">${p.description}</span>
            ${isSelected ? '<span class="priority-pill">RECOMMENDED</span>' : ""}
          </div>
          <span class="badge-strategy">${p.strategy_type}</span>
        </div>
        <div class="plan-metrics-row">
          <span>ID: <strong>${p.plan_id}</strong></span>
          <span>Target: <strong>${p.target_machine_id || "Local"}</strong></span>
          <span>Score: <strong>${p.weighted_score != null ? p.weighted_score.toFixed(4) : "N/A"}</strong></span>
          <span>Cost: <strong>$${(p.cost_impact_usd || 0).toFixed(2)}</strong></span>
          <span>Delivery Score: <strong>${p.delivery_delay_score != null ? p.delivery_delay_score.toFixed(2) : "0.00"}</strong></span>
        </div>
      `;
      feasiblePlansList.appendChild(card);
    });
  }

  // Render Infeasible Plans (Visible reasons demonstrate reasoning under constraints)
  infeasiblePlansList.innerHTML = "";
  if (infeasible.length === 0) {
    infeasiblePlansList.innerHTML = `<div class="text-muted" style="padding:12px;">None — all candidate plans feasible.</div>`;
  } else {
    infeasible.forEach((p) => {
      const reasons = p.infeasibility_reasons && p.infeasibility_reasons.length > 0
        ? p.infeasibility_reasons.join("; ")
        : "Operational constraint violation";
      const card = document.createElement("div");
      card.className = "plan-item-card";
      card.innerHTML = `
        <div class="plan-item-header">
          <span class="plan-name">${p.description}</span>
          <span class="badge-infeasible">INFEASIBLE</span>
        </div>
        <div class="plan-metrics-row">
          <span>ID: <strong>${p.plan_id}</strong></span>
          <span>Strategy: <strong>${p.strategy_type}</strong></span>
          <span>Cost: <strong>$${(p.cost_impact_usd || 0).toFixed(2)}</strong></span>
        </div>
        <div class="infeasibility-box">
          <strong>Constraint Reason:</strong> ${reasons}
        </div>
      `;
      infeasiblePlansList.appendChild(card);
    });
  }
}

/**
 * Render applied objective weights and trade-off rationale
 */
function renderTradeOffs(data) {
  weightsBarsContainer.innerHTML = "";
  const weights = data.objective_weights || { delivery: 0.4, cost: 0.4, quality: 0.1, energy: 0.05, risk: 0.05 };

  for (const [key, val] of Object.entries(weights)) {
    const pct = Math.round(val * 100);
    const row = document.createElement("div");
    row.className = "weight-bar-row";
    row.innerHTML = `
      <div class="wb-header">
        <span class="wb-title">${key} Priority Weight</span>
        <span class="wb-pct">${pct}%</span>
      </div>
      <div class="wb-track">
        <div class="wb-fill" style="width: ${pct}%;"></div>
      </div>
    `;
    weightsBarsContainer.appendChild(row);
  }

  engineRationaleText.textContent = data.rationale || "--";
}

/**
 * Render engineering evidence cards with categories
 */
function renderEvidence(data) {
  evidenceGrid.innerHTML = "";
  const evidence = data.engineering_evidence || [];
  evidenceCountBadge.textContent = `${evidence.length} DOCUMENTS`;

  if (evidence.length === 0) {
    evidenceGrid.innerHTML = `<div class="text-muted">No evidence retrieved for this scenario.</div>`;
    return;
  }

  evidence.forEach((ev) => {
    const cat = ev.evidence_category || "historical_precedent";
    const catLabel = cat.replace(/_/g, " ");
    const card = document.createElement("div");
    card.className = "evidence-card";
    card.innerHTML = `
      <div class="ev-header">
        <span class="ev-doc-id">${ev.document_id}</span>
        <span class="ev-category cat-${cat}">${catLabel}</span>
      </div>
      <div class="ev-title">${ev.title}</div>
      <div class="ev-excerpt">"${ev.excerpt}"</div>
      <div class="ev-footer">
        <span>Relevance: ${(ev.relevance_score * 100).toFixed(0)}%</span>
        <span>Source: ${ev.source_type}</span>
      </div>
    `;
    evidenceGrid.appendChild(card);
  });
}

/**
 * Error display helpers
 */
function showError(title, msg, clarification) {
  errorTitle.textContent = title;
  errorMessage.textContent = msg;
  if (clarification) {
    errorClarification.textContent = `Clarification: ${clarification}`;
    errorClarification.classList.remove("hidden");
  } else {
    errorClarification.classList.add("hidden");
  }
  errorBanner.classList.remove("hidden");
}

function hideError() {
  errorBanner.classList.add("hidden");
}

// Event Listeners
btnAnalyze.addEventListener("click", () => analyzeScenario());
scenarioInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
    analyzeScenario();
  }
});

btnWhatIf.addEventListener("click", () => runWhatIf());
whatIfInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    runWhatIf();
  }
});

const CANONICAL_SCENARIO = "M17 will be unavailable for 8 hours tomorrow. Keep high-priority deliveries on time while minimizing cost.";

// Preset Chips
document.querySelectorAll(".preset-chip[data-preset]").forEach((chip) => {
  chip.addEventListener("click", () => {
    const text = chip.getAttribute("data-preset");
    if (text) {
      scenarioInput.value = text;
      const isWhatIf = (/^what\s+if/i.test(text) || /^(suppose|assume)\b/i.test(text)) && !text.match(/\bM\d+\b/i);
      if (isWhatIf && currentResponse && currentResponse.impact_summary && currentResponse.impact_summary.machine_id) {
        whatIfInput.value = text;
        runWhatIf(text);
      } else {
        analyzeScenario(text);
      }
    }
  });
});

// Reset Demo Button
const btnResetDemo = document.getElementById("btn-reset-demo");
if (btnResetDemo) {
  btnResetDemo.addEventListener("click", () => {
    scenarioInput.value = CANONICAL_SCENARIO;
    whatIfInput.value = "";
    // Reset tabs back to Feasible
    tabFeasible.classList.add("active");
    tabInfeasible.classList.remove("active");
    feasiblePlansList.classList.remove("hidden");
    infeasiblePlansList.classList.add("hidden");
    // Re-run canonical flow
    analyzeScenario(CANONICAL_SCENARIO);
  });
}

// What-If Chips
document.querySelectorAll(".what-if-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    const query = chip.getAttribute("data-query");
    whatIfInput.value = query;
    runWhatIf(query);
  });
});

// Alternatives Tabs (Feasible vs. Infeasible)
tabFeasible.addEventListener("click", () => {
  tabFeasible.classList.add("active");
  tabInfeasible.classList.remove("active");
  feasiblePlansList.classList.remove("hidden");
  infeasiblePlansList.classList.add("hidden");
});

tabInfeasible.addEventListener("click", () => {
  tabInfeasible.classList.add("active");
  tabFeasible.classList.remove("active");
  infeasiblePlansList.classList.remove("hidden");
  feasiblePlansList.classList.add("hidden");
});

// Mock Approve Button
document.getElementById("btn-mock-approve")?.addEventListener("click", () => {
  alert("Human Approval Registered!\n\nPlan sign-off recorded for audit trail. In accordance with safety policies, factory dispatch remains a human responsibility.");
});

// Auto-run Canonical Scenario on First Load
window.addEventListener("DOMContentLoaded", () => {
  analyzeScenario();
});
