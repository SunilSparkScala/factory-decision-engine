// First-run guided tour: spotlights one part of the Control Room at a time with a short
// explanation. Shown automatically on the first visit only; the "?" button in the header replays it.

import { h } from "../dom.js";

const DONE_KEY = "fde-tour-done";
const card = (sel) => () => document.querySelector(sel)?.closest(".card");

function steps({ plantName, onTryIt }) {
  return [
    {
      title: "Welcome to the Factory Decision Engine",
      body: `When a machine breaks at ${plantName}, this shows what happens next, the ways to recover, and a recommendation reviewed by AI specialists, so you decide in minutes. AI reasons; every number comes from a deterministic engine.`,
    },
    {
      target: () => document.querySelector("#kpi-row .kpi-health"),
      title: "How the plant is doing",
      body: "Every machine is in exactly one group: running normally, at risk, or stopped. Click a group to see those machines on the plant map.",
    },
    {
      target: card("#alert-list"),
      title: "Needs action now",
      body: "Machine faults arrive here automatically (in this demo, the 🧪 button plays the machine). Urgent predictions land here too. “Analyze impact” is your response.",
    },
    {
      target: card("#watch-list"),
      title: "Machine health trends",
      body: "The early-warning radar: sensor trends heading for an alarm, with a backup plan already built. A machine moves to “Needs action now” once it is predicted to hit the alarm within 36 hours.",
    },
    {
      target: () => document.querySelector("#quick-report"),
      title: "Report a disruption",
      body: "Something the sensors can't see? Type it the way you'd say it, e.g. “M17 is down for about 6 hours, protect high-priority orders”. Gemini works out the machine, the duration and your priorities.",
    },
    {
      target: () => document.querySelector('.nav-tab[data-view="decide"]'),
      title: "Decide",
      body: "Every analysis opens here: the impact on lines and orders, the recovery options, the recommendation with the specialists' review, and documents ready once you approve. Nothing happens without your approval.",
    },
    {
      target: () => document.querySelector(".alert-item.kind-predictive"),
      title: "Try it now",
      body: "This machine still reports “healthy”, but its trend says it will reach an alarm soon. See what the engine recommends before it fails.",
      action: { label: "Analyze it now →", run: onTryIt },
    },
  ];
}

export function tourDone() {
  try { return localStorage.getItem(DONE_KEY) === "1"; } catch { return false; }
}

function markDone() {
  try { localStorage.setItem(DONE_KEY, "1"); } catch { /* storage unavailable */ }
}

let active = null;

/**
 * @param {object} opts
 *   plantName: shown in the welcome step
 *   onTryIt: called by the last step's action button
 */
export function startTour(opts) {
  if (active) return;
  const list = steps(opts);
  const opener = document.activeElement;
  let index = 0;

  const spot = h("div", { class: "tour-spot", "aria-hidden": "true" });
  const title = h("h2", { class: "tour-title", id: "tour-title" });
  const body = h("p", { class: "tour-text" });
  const dots = h("div", { class: "tour-dots", "aria-hidden": "true" });
  const counter = h("span", { class: "tour-count" });
  const back = h("button", { class: "btn btn-ghost btn-sm", onclick: () => go(index - 1) }, "Back");
  const next = h("button", { class: "btn btn-primary btn-sm", onclick: () => (index === list.length - 1 ? close() : go(index + 1)) });
  const actionBtn = h("button", { class: "btn btn-primary btn-sm", hidden: true });
  const skip = h("button", { class: "link-btn tour-skip", onclick: close }, "Skip tour");
  const bubble = h("div", { class: "tour-bubble", role: "dialog", "aria-modal": "true", "aria-labelledby": "tour-title" },
    h("div", { class: "tour-top" }, counter, skip),
    title, body,
    h("div", { class: "tour-foot" }, dots, h("div", { class: "tour-buttons" }, back, actionBtn, next)));
  const layer = h("div", { class: "tour-layer" }, spot, bubble);
  document.body.appendChild(layer);

  function place() {
    const step = list[index];
    const el = step.target?.();
    const vw = window.innerWidth, vh = window.innerHeight, gap = 12, pad = 6;
    const bw = Math.min(380, vw - 32);
    bubble.style.width = `${bw}px`;
    if (!el) {
      spot.classList.add("centered");
      spot.removeAttribute("style");
      bubble.style.left = `${(vw - bw) / 2}px`;
      bubble.style.top = `${Math.max(16, (vh - bubble.offsetHeight) / 2)}px`;
      return;
    }
    spot.classList.remove("centered");
    const r = el.getBoundingClientRect();
    Object.assign(spot.style, {
      left: `${r.left - pad}px`, top: `${r.top - pad}px`, width: `${r.width + pad * 2}px`, height: `${r.height + pad * 2}px`,
    });
    const bh = bubble.offsetHeight;
    const clampTop = (t) => Math.min(vh - bh - 16, Math.max(16, t));
    let top = r.bottom + pad + gap;
    let left = Math.min(Math.max(16, r.left), vw - bw - 16);
    if (top + bh > vh - 16) top = r.top - pad - gap - bh;   // not enough room below: go above
    if (top < 16) {
      // tall target: sit beside it if there is room, otherwise at the bottom of the screen
      if (r.right + pad + gap + bw <= vw - 16) { left = r.right + pad + gap; top = clampTop(r.top); }
      else if (r.left - pad - gap - bw >= 16) { left = r.left - pad - gap - bw; top = clampTop(r.top); }
      else top = vh - bh - 16;
    }
    bubble.style.left = `${left}px`;
    bubble.style.top = `${top}px`;
  }

  function go(i) {
    index = Math.max(0, Math.min(list.length - 1, i));
    const step = list[index];
    title.textContent = step.title;
    body.textContent = step.body;
    counter.textContent = `${index + 1} of ${list.length}`;
    dots.replaceChildren(...list.map((_, j) => h("span", { class: j === index ? "on" : "" })));
    back.hidden = index === 0;
    const last = index === list.length - 1;
    next.textContent = index === 0 ? "Show me around" : last ? "Done" : "Next";
    const target = step.target?.();
    const hasAction = last && step.action && target;
    actionBtn.hidden = !hasAction;
    if (hasAction) {
      actionBtn.textContent = step.action.label;
      actionBtn.onclick = () => { close(); step.action.run(); };
      next.className = "btn btn-ghost btn-sm";
    } else {
      next.className = "btn btn-primary btn-sm";
    }
    if (target) {
      const r = target.getBoundingClientRect();
      const fits = r.top >= 70 && r.bottom <= window.innerHeight - 200;
      if (!fits) {
        // keep clear of the sticky header; centre short targets, top-align tall ones
        const header = document.querySelector(".app-header")?.offsetHeight || 64;
        const offset = r.height > window.innerHeight * 0.45 ? header + 16 : (window.innerHeight - r.height) / 2 - 80;
        window.scrollTo({ top: window.scrollY + r.top - Math.max(header + 16, offset) });
      }
    }
    place();
    (hasAction ? actionBtn : next).focus();
  }

  function onKey(e) {
    if (e.key === "Escape") { e.preventDefault(); close(); return; }
    if (e.key === "ArrowRight" && index < list.length - 1) go(index + 1);
    else if (e.key === "ArrowLeft" && index > 0) go(index - 1);
    else if (e.key === "Tab") {
      // keep focus inside the bubble while the tour is open
      const focusable = [...bubble.querySelectorAll("button")].filter((b) => !b.hidden);
      const i = focusable.indexOf(document.activeElement);
      e.preventDefault();
      focusable[(i + (e.shiftKey ? -1 : 1) + focusable.length) % focusable.length].focus();
    }
  }
  const onMove = () => requestAnimationFrame(place);

  function close() {
    markDone();
    layer.remove();
    document.removeEventListener("keydown", onKey, true);
    window.removeEventListener("resize", onMove);
    window.removeEventListener("scroll", onMove, true);
    active = null;
    opener?.focus?.();
  }

  document.addEventListener("keydown", onKey, true);
  window.addEventListener("resize", onMove);
  window.addEventListener("scroll", onMove, true);
  active = { close };
  go(0);
}
