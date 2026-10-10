// Small DOM helpers. All text goes through textContent; the only HTML we generate is from
// the markdown renderer below, which escapes its input first.

export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/**
 * Create an element: h("div", { class: "x", onclick: fn, dataset: {a: 1} }, child, "text", ...)
 * Children may be nodes, strings, numbers, arrays, null/false (skipped).
 */
export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "text") el.textContent = value;
    else if (key === "dataset") Object.assign(el.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (value === true) el.setAttribute(key, "");
    else el.setAttribute(key, value);
  }
  append(el, children);
  return el;
}

function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.appendChild(child instanceof Node ? child : document.createTextNode(String(child)));
  }
}

export function clear(el, ...children) {
  el.replaceChildren();
  append(el, children);
  return el;
}

export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** Minimal, safe markdown: headings, bold, italics, inline code, lists, rules, paragraphs. */
export function renderMarkdown(md) {
  const lines = escapeHtml(md || "").split(/\r?\n/);
  const out = [];
  let list = null;
  const inline = (s) =>
    s
      .replace(/`([^`]+)`/g, "<code>$1</code>")
      .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>");
  const closeList = () => {
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (const raw of lines) {
    const line = raw.trimEnd();
    let m;
    if (!line.trim()) { closeList(); continue; }
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { closeList(); out.push(`<h4>${inline(m[2])}</h4>`); continue; }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { closeList(); out.push("<hr>"); continue; }
    if ((m = line.match(/^\s*[-*•]\s+(.*)$/))) {
      if (list !== "ul") { closeList(); out.push("<ul>"); list = "ul"; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    if ((m = line.match(/^\s*\d+[.)]\s+(.*)$/))) {
      if (list !== "ol") { closeList(); out.push("<ol>"); list = "ol"; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    if ((m = line.match(/^&gt;\s?(.*)$/))) { closeList(); out.push(`<p><em>${inline(m[1])}</em></p>`); continue; }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  const wrap = document.createElement("div");
  wrap.innerHTML = out.join("");
  return wrap;
}

export function toast(message, { error = false, timeout = 3800 } = {}) {
  const box = document.getElementById("toasts");
  const t = h("div", { class: `toast${error ? " error" : ""}`, role: error ? "alert" : "status" }, message);
  box.appendChild(t);
  setTimeout(() => t.remove(), timeout);
}

export function setLoading(button, loading) {
  if (!button) return;
  button.classList.toggle("loading", loading);
  button.disabled = loading;
}

export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Copied to clipboard");
  } catch {
    toast("Copy failed — select the text and copy manually", { error: true });
  }
}
