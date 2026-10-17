// First-run guided tour: spotlights one part of the Control Room at a time with a short
// explanation. Shown automatically on the first visit only; the "?" button in the header replays it.

import { h } from "../dom.js";

const DONE_KEY = "fde-tour-done";
const el = (sel) => () => document.querySelector(sel);

function steps({ plantName, prediction, onTryIt }) {
  const machine = prediction?.machine_id || "This machine";
  const hrs = prediction?.hours_to_alarm != null ? ` in about ${Math.round(prediction.hours_to_alarm)} hours` : " soon";
  return [
    {
      title: "Welcome to the Factory Decision Engine",
      lead: "Before you change the factory, understand what happens next.",
      body: `When a machine breaks at ${plantName}, this app shows which orders are hit, every way to recover, and a recommendation checked by AI specialists. The final decision is always yours.`,
      note: "This is a made-up demo factory. The AI explains and advises, but every number (lost units, cost, delays) is worked out by exact calculations, never guessed by the AI.",
    },
    {
      target: el("#quick-report"),
      title: "Report a disruption",
      body: "Type what happened in your own words, e.g. “pick-and-place on Line B stopped, maybe 6 hours, protect urgent orders.” Gemini reads it and finds which machine, for how long, and what to protect.",
    },
    {
      target: el("#sec-alerts"),
      title: "Needs action now",
      body: "Machine breakdowns and machines about to fail show up here. “Demo: simulate a machine fault” adds a pretend breakdown so you can see how the app handles one (in a real plant, machines report this themselves). “Analyze impact” on any alert shows what that problem affects and the best way to recover.",
    },
    {
      target: el("#cockpit-fleet-card"),
      title: "How the plant is doing",
      body: "A quick health check of all 36 machines: running normally, at risk (showing warning signs), or stopped. After the tour, click any of the three numbers to see where those machines are on the plant map.",
    },
    {
      target: el("#sec-watch"),
      title: "Machine health trends",
      body: "The early warning: machines whose sensors show they're wearing out, with a backup plan ready for each. “~17 h” means the machine is expected to hit its alarm level in about 17 hours; once that's under 36 hours, it moves up to “Needs action now”.",
    },
    {
      target: el('.sidebar-tab[data-view="decide"]'),
      title: "Decision Studio",
      body: "Every analysis opens here: what the problem affects, the ways to recover, and a recommendation reviewed by 4 AI specialists. You can also ask an AI agent “what if…?” questions. Nothing changes until you approve, and then the work orders are drafted for you.",
    },
    {
      target: el(".alert-item.kind-predictive"),
      title: "Try it now",
      body: `${machine} still reports “healthy”, but its sensors say it will hit its alarm level${hrs}. See what the app recommends before it fails.`,
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
    body.replaceChildren(
      ...(step.lead ? [h("strong", { class: "tour-lead" }, step.lead), " "] : []),
      step.body,
      ...(step.note ? [h("span", { class: "tour-note" }, step.note)] : []));
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
        // scrollIntoView scrolls whichever container holds the target (the app scrolls its main
        // area, not the window); centre short targets, top-align tall ones
        target.scrollIntoView({ block: r.height > window.innerHeight * 0.45 ? "start" : "center" });
        // if the window itself scrolled (narrow screens), keep the target clear of the sticky header
        const header = document.querySelector(".app-header")?.offsetHeight || 64;
        const top = target.getBoundingClientRect().top;
        if (top < header + 12) window.scrollBy(0, top - header - 12);
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
