// Plain-language formatting. Raw engine scores stay available in "Engineering details";
// everything here turns them into words a plant manager can act on.

export const STRATEGY = {
  MACHINE_TRANSFER: { label: "Move work", icon: "⇄", noun: "transfer" },
  RESEQUENCE: { label: "Reorder queue", icon: "⇅", noun: "resequencing" },
  OVERTIME: { label: "Overtime", icon: "⏱", noun: "overtime" },
};

export function strategyLabel(type) {
  return (STRATEGY[type] || { label: humanize(type) }).label;
}

export function strategyIcon(type) {
  return (STRATEGY[type] || { icon: "•" }).icon;
}

export function humanize(text) {
  return String(text || "")
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/^\w/, (c) => c.toUpperCase());
}

export function lineName(lineId) {
  return lineId ? lineId.replace("LINE_", "Line ") : "";
}

export function usd(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: value < 100 && value % 1 !== 0 ? 2 : 0,
  }).format(value);
}

export function num(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
}

export function hours(value) {
  if (value === null || value === undefined) return "—";
  return `${num(value, 1)} h`;
}

export function pct(value, digits = 0) {
  if (value === null || value === undefined) return "—";
  return `${num(value * 100, digits)}%`;
}

/** Plain description of a plan, e.g. "Move M17's work to M26 (Line C)". */
export function planText(plan, failedMachine) {
  if (!plan) return "No feasible plan";
  const from = failedMachine ? `${failedMachine}'s` : "the";
  if (plan.strategy_type === "MACHINE_TRANSFER") {
    return `Move ${from} work to ${plan.target_machine_id}${plan.target_line_id ? ` (${lineName(plan.target_line_id)})` : ""}`;
  }
  if (plan.strategy_type === "OVERTIME") {
    const m = /by ([\d.]+) overtime hours/.exec(plan.description || "");
    return m ? `Add ${num(Number(m[1]), 1)} h of overtime on ${failedMachine || "the machine"} after repair` : "Recover with overtime after repair";
  }
  if (plan.strategy_type === "RESEQUENCE") {
    return "Run high-priority orders first; lower-priority orders absorb the delay";
  }
  return plan.description || plan.plan_id;
}

/** Delivery outcome relative to the best achievable among feasible options. */
export function deliveryLabel(score, bestScore) {
  if (score === null || score === undefined) return { text: "—", tone: "" };
  const gap = score - (bestScore ?? score);
  if (gap <= 0.01) return { text: "Best achievable", tone: "ok" };
  if (gap < 0.15) return { text: "Minor extra delay", tone: "warn" };
  return { text: "Significant delays", tone: "danger" };
}

export function qualityLabel(score) {
  if (score === null || score === undefined) return { text: "—", tone: "" };
  if (score < 0.05) return { text: "Very low", tone: "ok" };
  if (score < 0.1) return { text: "Low", tone: "ok" };
  if (score < 0.25) return { text: "Medium", tone: "warn" };
  return { text: "High", tone: "danger" };
}

export function riskLabel(score) {
  if (score === null || score === undefined) return { text: "—", tone: "" };
  if (score < 0.25) return { text: "Low", tone: "ok" };
  if (score < 0.4) return { text: "Medium", tone: "warn" };
  return { text: "High", tone: "danger" };
}

export function deliveryRiskTone(level) {
  return { HIGH: "danger", MEDIUM: "warn", LOW: "ok" }[level] || "";
}

export function riskBadgeClass(level) {
  return {
    CRITICAL: "badge-danger",
    HIGH: "badge-danger",
    ELEVATED: "badge-warn",
    OFFLINE: "badge-info",
    NORMAL: "badge-ok",
    MEDIUM: "badge-warn",
    LOW: "badge-ok",
  }[level] || "";
}

export function plantTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("en-GB", {
    weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
  });
}

export function dateTime(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function stanceInfo(stance) {
  return {
    support: { text: "Supports", cls: "badge-ok" },
    caution: { text: "Supports with conditions", cls: "badge-warn" },
    oppose: { text: "Opposes", cls: "badge-danger" },
  }[stance] || { text: stance, cls: "" };
}

/** Which priority preset matches a set of optimizer weights (or null for custom). */
export function presetForWeights(w) {
  if (!w) return null;
  const v = (k) => w[k] || 0;
  const near = (a, b) => Math.abs(a - b) < 0.02;
  if (["delivery", "cost", "quality", "energy", "risk"].every((k) => near(v(k), 0.2))) return "balanced all objectives";
  if (near(v("delivery"), 0.4) && near(v("cost"), 0.4)) return "delivery cost";
  for (const k of ["delivery", "cost", "risk", "energy"]) if (v(k) >= 0.59) return k;
  return null;
}

/** Operation display names of the active industry pack, e.g. PICK_AND_PLACE -> "Pick-and-place". */
let operationNames = {};
export function setOperationNames(pack) {
  operationNames = Object.fromEntries((pack?.operations || []).map((o) => [o.code, o.display]));
}

/** "PICK_AND_PLACE_Unit_2" -> "Pick-and-place Unit 2" */
export function machineTypeLabel(type) {
  if (!type) return "";
  const [op, unit] = type.split("_Unit_");
  const name = operationNames[op] || humanize(op);
  return unit ? `${name} Unit ${unit}` : name;
}

/** "Station B2 - Pick-and-place" -> "Pick-and-place" */
export function stationOperation(stationName) {
  const parts = String(stationName || "").split(" - ");
  return parts.length > 1 ? parts.slice(1).join(" - ") : stationName;
}
