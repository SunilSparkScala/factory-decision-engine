// Plant map: one row per production line, stations in flow order joined by arrows.
// Used in two moments of the story:
//   1. Control Room, "Where's the trouble?": quiet by default, only problem machines are marked.
//   2. Decide → Impact, "What happens next?": the failed machine, who can and can't take over,
//      and an arrow to the recommended machine. Everything shown comes from the engine's result.

import { h, clear } from "../dom.js";
import { machineTypeLabel, stationOperation } from "../format.js";

const NS = "http://www.w3.org/2000/svg";

/**
 * @param {HTMLElement} container
 * @param {object} opts
 *   lines: overview.lines
 *   atRisk: Set of machine IDs predicted at risk
 *   overlay: optional { failed, target, candidates: { [id]: { ok: bool, note: string } }, focusOperation }
 *   onMachine: (machine, line) => void
 */
export function renderPlantMap(container, { lines, atRisk = new Set(), highlight = new Set(), overlay = null, onMachine = null }) {
  const canvas = h("div", { class: "pm-canvas" });
  for (const line of lines) {
    const stations = groupByStation(line.machines);
    const cells = [];
    stations.forEach((machines, stationName) => {
      if (cells.length) cells.push(h("span", { class: "pm-arrow", "aria-hidden": "true" }, "→"));
      cells.push(stationBox(stationName, machines, line, { atRisk, highlight, overlay, onMachine }));
    });
    canvas.appendChild(h("div", { class: "pm-row" },
      h("div", { class: "pm-line" },
        h("strong", {}, line.name),
        h("span", {}, `${line.order_count} orders · ${line.high_priority_orders} high-priority`)),
      h("div", { class: "pm-stations" }, cells)));
  }
  const scroller = h("div", { class: "pm-scroll" }, canvas);
  clear(container, h("p", { class: "pm-scroll-hint" }, "Swipe sideways to follow each line →"), scroller);
  if (overlay?.failed && overlay?.target) drawMoveArrow(canvas, overlay.failed, overlay.target);
}

function groupByStation(machines) {
  const map = new Map();
  for (const m of machines) {
    if (!map.has(m.station)) map.set(m.station, []);
    map.get(m.station).push(m);
  }
  return map;
}

function problemOf(m, atRisk) {
  if (m.status === "DOWN") return { cls: "down", icon: "✕", text: "down" };
  if (m.status === "MAINTENANCE") return { cls: "maintenance", icon: "🔧", text: "maintenance" };
  if (m.status === "DEGRADED" || atRisk.has(m.machine_id)) return { cls: "risk", icon: "⚠", text: m.status === "DEGRADED" ? "degraded" : "at risk" };
  return null;
}

function stationBox(stationName, machines, line, { atRisk, highlight, overlay, onMachine }) {
  const op = stationOperation(stationName);
  const relevant = overlay && machines.some((m) => m.machine_id === overlay.failed || overlay.candidates?.[m.machine_id]);
  const box = h("div", { class: `pm-station${relevant ? " relevant" : ""}${overlay && !relevant ? " muted" : ""}` },
    h("div", { class: "pm-op" }, op));

  const chip = (m) => {
    const problem = problemOf(m, atRisk);
    let cls = "pm-machine";
    let badge = null;
    let note = `${m.machine_id} · ${machineTypeLabel(m.type)} · ${m.status.toLowerCase()} · health ${m.health}%`;
    if (overlay) {
      const cand = overlay.candidates?.[m.machine_id];
      if (m.machine_id === overlay.failed) { cls += " failed"; badge = "✕ broken"; }
      else if (m.machine_id === overlay.target) { cls += " target"; badge = "★ move here"; }
      else if (cand) { cls += cand.ok ? " can" : " cannot"; badge = cand.ok ? "✓ can help" : "✕ can't"; note += ` · ${cand.note}`; }
    } else if (problem) {
      cls += ` problem ${problem.cls}`;
      badge = `${problem.icon} ${m.reported ? "fault reported" : problem.text}`;
    }
    if (highlight.has(m.machine_id)) cls += " pm-highlight";
    return h("button", {
      class: cls,
      "data-machine": m.machine_id,
      title: note,
      onclick: onMachine ? () => onMachine(m, line) : null,
      disabled: onMachine ? null : true,
    }, h("span", { class: "pm-id" }, m.machine_id), badge ? h("span", { class: "pm-badge" }, badge) : null);
  };

  if (overlay) {
    // Decide view: show every machine where the story happens, keep the rest quiet
    box.appendChild(relevant
      ? h("div", { class: "pm-machines" }, machines.map(chip))
      : h("div", { class: "pm-quiet" }, `${machines.length} machines`));
    return box;
  }

  // Control Room: only problems are visible; the station expands to show all its machines
  const problems = machines.filter((m) => problemOf(m, atRisk) || highlight.has(m.machine_id));
  const all = h("div", { class: "pm-machines", hidden: true }, machines.map(chip));
  const summary = problems.length
    ? h("div", { class: "pm-machines" }, problems.map(chip))
    : h("div", { class: "pm-quiet" }, `✓ ${machines.length} running`);
  if (machines.some((m) => highlight.has(m.machine_id))) box.classList.add("pm-has-highlight");
  const toggle = h("button", {
    class: "pm-expand",
    "aria-expanded": "false",
    onclick: () => {
      const open = all.hidden;
      all.hidden = !open;
      summary.hidden = open;
      toggle.setAttribute("aria-expanded", String(open));
      toggle.textContent = open ? "Hide" : "All machines";
    },
  }, "All machines");
  box.append(summary, all, toggle);
  return box;
}

/** Curved arrow from the broken machine to the recommended one ("move work here"). */
function drawMoveArrow(canvas, fromId, toId) {
  const draw = () => {
    canvas.querySelector(".pm-arrow-layer")?.remove();
    const from = canvas.querySelector(`[data-machine="${fromId}"]`);
    const to = canvas.querySelector(`[data-machine="${toId}"]`);
    if (!from || !to || !canvas.isConnected) return;
    const base = canvas.getBoundingClientRect();
    const a = from.getBoundingClientRect();
    const b = to.getBoundingClientRect();
    if (!base.width) return;
    // Leave the source from the side facing the target (targets can be on a line above or below)
    const down = b.top >= a.top;
    const x1 = a.left + a.width / 2 - base.left, y1 = (down ? a.bottom : a.top) - base.top;
    const x2 = b.left + b.width / 2 - base.left, y2 = (down ? b.top : b.bottom) - base.top;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("class", "pm-arrow-layer");
    svg.setAttribute("width", base.width);
    svg.setAttribute("height", base.height);
    svg.setAttribute("aria-hidden", "true");
    const defs = document.createElementNS(NS, "defs");
    defs.innerHTML = '<marker id="pm-head" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z"></path></marker>';
    svg.appendChild(defs);
    const path = document.createElementNS(NS, "path");
    const midY = (y1 + y2) / 2;
    path.setAttribute("d", `M${x1},${y1} C${x1},${midY} ${x2},${midY} ${x2},${y2 + (down ? -4 : 4)}`);
    path.setAttribute("marker-end", "url(#pm-head)");
    svg.appendChild(path);
    canvas.appendChild(svg);
  };
  requestAnimationFrame(draw);
  const observer = new ResizeObserver(() => draw());
  observer.observe(canvas);
}
