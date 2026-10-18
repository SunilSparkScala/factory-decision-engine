// Voice for plant managers on the floor: speak a report or a question (🎤) and hear the
// recommendation (🔊).
// Input: the recording goes to Gemini on the server (/api/transcribe), which understands
// machine IDs, numbers and factory noise better; if that is unavailable or fails, the
// browser's own speech recognition (Web Speech API) takes over. Output: browser speech.
// Spoken text only fills the box; the manager checks it and presses Analyze/Ask themselves.

import { h, toast } from "../dom.js";
import { api } from "../api.js";
import { state } from "../state.js";

const Recognition = window.SpeechRecognition || window.webkitSpeechRecognition;
const LANG = "en-IN"; // Indian English: plant names, accents and number words come out right
const MAX_LISTEN_MS = 60_000; // a report is a sentence or two; never leave the mic open by mistake

const canRecord = Boolean(navigator.mediaDevices?.getUserMedia && window.MediaRecorder);
export const canListen = canRecord || Boolean(Recognition);
export const canSpeak = typeof window.speechSynthesis !== "undefined" && typeof window.SpeechSynthesisUtterance !== "undefined";

// Only one microphone session at a time, across all boxes
let session = null;
// Set when the server says Gemini is not set up, so later clicks go straight to the browser
let geminiOff = false;

function useGemini() {
  return canRecord && !geminiOff && state.health?.live_gemini_available !== false;
}

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
    else if (useGemini()) record(input, btn);
    else listen(input, btn);
  });
  // The "Transcribed by …" label belongs to the words it describes: it goes when they are sent or erased
  input.form?.addEventListener("submit", () => showNote(input, null));
  input.addEventListener("input", () => { if (!input.value.trim()) showNote(input, null); });
  return btn;
}

const MIC_LABELS = {
  listening: ["Listening…", " click to stop"],
  working: ["Gemini is transcribing…", ""],
  idle: ["", ""],
};

function setMicState(btn, mode) {
  btn.classList.toggle("is-listening", mode === "listening");
  btn.classList.toggle("is-working", mode === "working");
  btn.setAttribute("aria-pressed", mode === "listening" ? "true" : "false");
  btn.setAttribute("aria-busy", mode === "working" ? "true" : "false");
  const [text, hint] = MIC_LABELS[mode];
  btn.querySelector(".mic-text").textContent = text;
  btn.querySelector(".mic-hint").textContent = hint;
}

// One small honest label under each box: who turned the speech into text
const notes = new WeakMap();
// What was in the box before the manager spoke, so it can be put back
const previousText = new WeakMap();

function rememberPrevious(input) {
  previousText.set(input, input.value.trim());
}

function showNote(input, source) {
  let note = notes.get(input);
  if (!source) { if (note) note.hidden = true; return; }
  if (!note) {
    note = h("div", { class: "voice-note", role: "status" });
    (input.form || input.closest(".mic-field") || input).insertAdjacentElement("afterend", note);
    notes.set(input, note);
  }
  note.classList.toggle("is-gemini", source === "gemini");
  note.textContent = source === "gemini"
    ? "✦ Transcribed by Gemini. Check the words, then send."
    : "Transcribed by the browser. Check the words, then send.";
  const previous = previousText.get(input);
  if (previous && previous !== input.value.trim()) {
    note.append(" ", h("button", {
      type: "button", class: "link-btn voice-restore",
      onclick: () => { input.value = previous; previousText.delete(input); showNote(input, null); input.focus(); },
    }, "↩ Restore previous text"));
  }
  note.hidden = false;
}

/** Put the recognised words in the box (base is "" because speaking replaces what was there). */
function fill(input, base, words) {
  input.value = base + (base ? words.replace(/\s+/g, " ").trim() : tidy(words));
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
  // Speaking gives a new report: it replaces the box (the note offers to restore the old text)
  rememberPrevious(input);
  const base = "";
  let heard = "";
  let timer = null;

  showNote(input, null);

  const done = () => {
    clearTimeout(timer);
    if (session?.rec === rec) session = null;
    setMicState(btn, "idle");
    if (heard.trim()) {
      fill(input, base, heard);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      showNote(input, "browser");
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
    fill(input, base, heard);
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

/** Record a voice note, then Gemini transcribes it; on any failure the browser listens instead. */
async function record(input, btn) {
  stopSession();
  showNote(input, null);
  const fallBack = (message) => {
    setMicState(btn, "idle");
    if (session?.btn === btn) session = null;
    if (!Recognition) { toast(`${message} Please type instead.`, { error: true }); return; }
    toast(`${message} Please say it again: the browser is listening now.`);
    listen(input, btn);
  };

  // Placeholder session while the browser asks for the microphone, so a second click does nothing
  session = { btn, stop: () => {} };
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
  } catch (e) {
    session = null;
    setMicState(btn, "idle");
    if (e.name === "NotAllowedError" || e.name === "SecurityError") toast(MIC_ERRORS["not-allowed"], { error: true });
    else if (e.name === "NotFoundError") toast(MIC_ERRORS["audio-capture"], { error: true });
    else fallBack("Recording could not start.");
    return;
  }
  const type = ["audio/webm;codecs=opus", "audio/ogg;codecs=opus", "audio/webm", "audio/mp4"]
    .find((t) => MediaRecorder.isTypeSupported?.(t));
  let rec;
  try {
    rec = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
  } catch {
    stream.getTracks().forEach((t) => t.stop());
    fallBack("Recording is not supported here.");
    return;
  }

  rememberPrevious(input);
  const base = ""; // the transcript replaces the box; the note offers to restore the old text
  const chunks = [];
  let timer = null;
  rec.ondataavailable = (e) => { if (e.data?.size) chunks.push(e.data); };
  rec.onstop = async () => {
    clearTimeout(timer);
    stream.getTracks().forEach((t) => t.stop()); // release the mic (the browser's red dot goes away)
    const blob = new Blob(chunks, { type: (rec.mimeType || type || "audio/webm").split(";")[0] });
    if (!blob.size) { fallBack("Nothing was recorded."); return; }
    setMicState(btn, "working");
    session = { btn, stop: () => {} }; // transcribing: clicks wait for the result
    try {
      const res = await api.transcribe(blob);
      session = null;
      setMicState(btn, "idle");
      if (!res.text) { toast(MIC_ERRORS["no-speech"]); return; }
      fill(input, base, res.text);
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
      showNote(input, "gemini");
    } catch (err) {
      if (err.status === 503) geminiOff = true;
      if (err.status === 413) { session = null; setMicState(btn, "idle"); toast(err.message, { error: true }); return; }
      fallBack("Gemini couldn't transcribe that.");
    }
  };

  rec.start();
  session = { btn, stop: () => { if (rec.state !== "inactive") rec.stop(); } };
  setMicState(btn, "listening");
  timer = setTimeout(() => { if (rec.state !== "inactive") rec.stop(); }, MAX_LISTEN_MS);
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
  // The two figures a manager asks first: what it costs and what happens to deliveries
  const figure = (label) => [...card.querySelectorAll("#rec-figures .fig")]
    .find((f) => f.querySelector(".fig-label")?.textContent.trim().toLowerCase() === label)
    ?.querySelector(".fig-value")?.textContent.trim();
  const cost = figure("extra cost");
  const delivery = figure("delivery");
  if (cost || delivery) {
    parts.push([cost ? `Extra cost ${cost}` : null, delivery ? `delivery: ${delivery.toLowerCase()}` : null].filter(Boolean).join(", "));
  }
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
