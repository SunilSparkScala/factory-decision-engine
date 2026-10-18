// Voice for plant managers on the floor: speak a report or a question (🎤) and hear the
// recommendation (🔊). Uses the browser's own speech features (Web Speech API).
// Spoken text only fills the box; the manager checks it and presses Analyze/Ask themselves.

import { h, toast } from "../dom.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const LANG = "en-IN"; // Indian English: plant names, accents and number words come out right
const MAX_LISTEN_MS = 60_000; // a report is a sentence or two; never leave the mic open by mistake

export const canListen = Boolean(Recognition);
export const canSpeak = typeof window.speechSynthesis !== "undefined" && typeof window.SpeechSynthesisUtterance !== "undefined";

// Only one microphone session at a time, across all boxes
let session = null;

// ------------------------------------------------------------------ voice input

/**
 * Add a 🎤 button for a text box. By default the box and button share a row (the box is
 * wrapped); pass `before` to put the button in front of another element instead.
 * Returns the button, or null when this browser cannot listen.
 */
export function attachMic(input, { before = null, label = "Speak instead of typing" } = {}) {
  if (!input || !canListen) return null;
  const btn = h("button", { type: "button", class: "mic-btn", title: label, "aria-label": label, "aria-pressed": "false" },
    h("span", { class: "mic-icon", "aria-hidden": "true" }, "🎤"),
    h("span", { class: "mic-text" }),
    h("span", { class: "mic-hint" }));
  if (before) {
    // Grouped so the mic and the button it sits beside wrap onto a new line together
    const group = h("span", { class: "mic-actions" });
    before.replaceWith(group);
    group.append(btn, before);
  } else {
    const field = h("span", { class: "mic-field" });
    input.replaceWith(field);
    field.append(input, btn);
  }
  btn.addEventListener("click", () => {
    if (session?.btn === btn) session.stop();
    else listen(input, btn);
  });
  return btn;
}

function setMicState(btn, mode) {
  btn.classList.toggle("is-listening", mode === "listening");
  btn.setAttribute("aria-pressed", mode === "listening" ? "true" : "false");
  btn.querySelector(".mic-text").textContent = mode === "listening" ? "Listening…" : "";
  btn.querySelector(".mic-hint").textContent = mode === "listening" ? " click to stop" : "";
}

function stopSession() {
  if (session) session.stop();
}

const MIC_ERRORS = {
  "no-speech": "I didn't hear anything. Click 🎤 and speak again.",
  "audio-capture": "No microphone found. Check that one is connected.",
  "not-allowed": "The microphone is blocked. Allow microphone access for this site, then try again.",
  "service-not-allowed": "The microphone is blocked. Allow microphone access for this site, then try again.",
  network: "Speech recognition needs an internet connection. Please type instead.",
  "language-not-supported": "This browser can't recognise Indian English. Please type instead.",
};

/** Browser speech recognition: interim words appear in the box as the manager speaks. */
function listen(input, btn) {
  stopSession();
  const rec = new Recognition();
  rec.lang = LANG;
  rec.interimResults = true;
  rec.continuous = true; // a pause to think must not end the report; the manager clicks to stop
  // Keep what was already typed (e.g. "M17 will be down for ") and add the spoken words after it
  const base = input.value.trim() ? `${input.value.trimEnd()} ` : "";
  let heard = "";
  let timer = null;

  const done = () => {
    clearTimeout(timer);
    if (session?.rec === rec) session = null;
    setMicState(btn, "idle");
    if (heard.trim()) {
      input.value = base + tidy(heard);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    }
  };

  rec.onresult = (e) => {
    let finalText = "";
    let interim = "";
    for (let i = 0; i < e.results.length; i++) {
      if (e.results[i].isFinal) finalText += ` ${e.results[i][0].transcript}`;
      else interim += ` ${e.results[i][0].transcript}`;
    }
    heard = finalText + interim;
    input.value = base + tidy(heard);
    input.scrollLeft = input.scrollWidth; // a one-line box shows the newest words
  };
  rec.onerror = (e) => {
    if (e.error !== "aborted") toast(MIC_ERRORS[e.error] || "Voice input stopped. Please try again or type instead.", { error: true });
  };
  rec.onend = done;

  try {
    rec.start();
  } catch {
    toast("Voice input could not start. Please try again or type instead.", { error: true });
    return;
  }
  session = { btn, rec, stop: () => rec.stop() };
  setMicState(btn, "listening");
  timer = setTimeout(() => rec.stop(), MAX_LISTEN_MS);
}

function tidy(text) {
  const t = text.replace(/\s+/g, " ").trim();
  return t ? t[0].toUpperCase() + t.slice(1) : t;
}

// ------------------------------------------------------------------ voice output

let reading = null; // { btn, token } while the recommendation is being read

/**
 * Add a 🔊 "Read aloud" button to `container`; `getParts()` returns the sentences to read.
 * Clicking while reading stops. Returns the button, or null when this browser cannot speak.
 */
export function attachReadAloud(container, getParts) {
  if (!container || !canSpeak) return null;
  const btn = h("button", { type: "button", class: "read-aloud-btn", title: "Read the recommendation aloud", "aria-pressed": "false" });
  setReadState(btn, false);
  btn.addEventListener("click", () => {
    if (reading?.btn === btn) { stopReading(); return; }
    const parts = getParts();
    if (!parts.length) { toast("Nothing to read yet."); return; }
    readAloud(parts, btn);
  });
  container.appendChild(btn);
  return btn;
}

function setReadState(btn, on) {
  btn.classList.toggle("is-reading", on);
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.replaceChildren(
    h("span", { "aria-hidden": "true" }, on ? "■" : "🔊"),
    h("span", {}, on ? "Stop reading" : "Read aloud"));
}

/** Speak each part as its own utterance (long single utterances get cut off in Chrome). */
export function readAloud(parts, btn = null) {
  if (!canSpeak) return;
  stopReading();
  const token = {};
  reading = { btn, token };
  if (btn) setReadState(btn, true);
  const voice = pickVoice();
  const sentences = parts.map(speakable).filter(Boolean);
  sentences.forEach((text, i) => {
    const u = new SpeechSynthesisUtterance(text);
    u.lang = voice?.lang || LANG;
    if (voice) u.voice = voice;
    u.rate = 0.98;
    if (i === sentences.length - 1) u.onend = () => finishReading(token);
    u.onerror = (e) => {
      if (e.error !== "interrupted" && e.error !== "canceled") toast("Reading aloud stopped.", { error: true });
      finishReading(token);
    };
    window.speechSynthesis.speak(u);
  });
}

export function stopReading() {
  if (!canSpeak) return;
  const r = reading;
  reading = null;
  window.speechSynthesis.cancel();
  if (r?.btn) setReadState(r.btn, false);
}

function finishReading(token) {
  if (reading?.token !== token) return; // a newer reading (or a stop) already took over
  const { btn } = reading;
  reading = null;
  if (btn) setReadState(btn, false);
}

function pickVoice() {
  const voices = window.speechSynthesis.getVoices() || [];
  return voices.find((v) => v.lang === LANG)
    || voices.find((v) => /^en[-_]GB/i.test(v.lang))
    || voices.find((v) => /^en/i.test(v.lang))
    || null;
}

/** Turn on-screen shorthand into words a speech voice reads naturally. */
export function speakable(text) {
  let t = String(text || "")
    .replace(/[✓✔⚠✦⚙★✕▲▼•◆]/gu, " ")
    .replace(/\p{Extended_Pictographic}/gu, " ")
    .replace(/(\d)\s*[–—-]\s*(\d)/g, "$1 to $2")
    .replace(/\s*→\s*/g, " to ")
    .replace(/\s*·\s*/g, ", ")
    .replace(/\s+[–—]\s+/g, ", ")
    .replace(/[≈~]\s*/g, "about ")
    .replace(/\b1\s?h\b/g, "1 hour")
    .replace(/(\d)\s?h\b/g, "$1 hours")
    .replace(/(\d)\s?min\b/g, "$1 minutes")
    .replace(/\bvs\.?\s/gi, "versus ")
    .replace(/\s&\s/g, " and ")
    .replace(/\s+/g, " ")
    .trim();
  if (t && !/[.!?]$/.test(t)) t += ".";
  return t;
}

/** What "Read aloud" says for the recommendation card: the plan, the verdict, each condition. */
export function recommendationParts(card) {
  const text = (sel) => card.querySelector(sel)?.textContent.trim() || "";
  const parts = [];
  const title = text("#rec-title");
  if (title) parts.push(`Recommendation: ${title}`);
  const verdict = text("#warroom-consensus");
  if (verdict) parts.push(`Specialist review: ${verdict}`);
  for (const agent of card.querySelectorAll(".agent-card")) {
    const concern = agent.querySelector(".agent-concern");
    if (!concern) continue;
    const name = agent.querySelector(".agent-name")?.textContent.trim() || "A specialist";
    const condition = concern.textContent.replace(/^\s*Condition:\s*/i, "").trim();
    if (condition) parts.push(`${name}'s condition: ${condition}`);
  }
  return parts;
}
