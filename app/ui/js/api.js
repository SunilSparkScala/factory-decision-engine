// Every backend call lives here, so the views never build URLs or parse errors themselves.

// Industry pack chosen by this viewer (sent with every request; the server defaults if unset)
let domain = null;
try { domain = localStorage.getItem("fde-domain"); } catch { /* storage unavailable */ }

// A random id per browser, so demo faults you raise are yours only (other viewers never see them)
let viewer = null;
try {
  viewer = localStorage.getItem("fde-viewer");
  if (!viewer) {
    viewer = (crypto.randomUUID ? crypto.randomUUID() : `v-${Date.now()}-${Math.random().toString(16).slice(2)}`);
    localStorage.setItem("fde-viewer", viewer);
  }
} catch { viewer = null; /* storage unavailable: faults are shared */ }

export function getDomain() { return domain; }

export function setDomain(key) {
  domain = key;
  try { localStorage.setItem("fde-domain", key); } catch { /* storage unavailable */ }
}

async function request(method, url, body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (domain) headers["X-FDE-Domain"] = domain;
  if (viewer) headers["X-FDE-Viewer"] = viewer;
  const res = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok) {
    const detail = data && data.detail;
    const message = Array.isArray(detail)
      ? detail.map((d) => d.msg).join("; ")
      : detail || `Request failed (${res.status})`;
    throw new Error(message);
  }
  return data;
}

/** Send a recorded voice note (raw audio body) for Gemini to transcribe. Errors carry the HTTP status. */
async function transcribe(blob) {
  const res = await fetch("/api/transcribe", { method: "POST", headers: { "Content-Type": blob.type || "audio/webm" }, body: blob });
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON error */ }
  if (!res.ok) throw Object.assign(new Error((data && data.detail) || `Transcription failed (${res.status})`), { status: res.status });
  return data;
}

export const api = {
  transcribe,
  health: () => request("GET", "/api/health"),
  domains: () => request("GET", "/api/domains"),
  overview: () => request("GET", "/api/factory/overview"),
  watchList: () => request("GET", "/api/watchlist"),
  alerts: () => request("GET", "/api/alerts"),
  simulateFault: () => request("POST", "/api/alerts/simulate"),
  clearFaults: () => request("POST", "/api/alerts/clear"),
  knowledge: () => request("GET", "/api/knowledge"),
  machineDetail: (machineId) => request("GET", `/api/machines/${encodeURIComponent(machineId)}`),
  machineTelemetry: (machineId) => request("GET", `/api/machines/${encodeURIComponent(machineId)}/telemetry`),
  analyze: (payload, previous) => {
    const body = typeof payload === "string" ? { prompt: payload } : { ...payload };
    if (previous) body.previous_response = previous;
    return request("POST", "/api/analyze", body);
  },
  whatIf: (previous, followUpPrompt) => request("POST", "/api/what-if", { previous_response: previous, follow_up_prompt: followUpPrompt }),
  rerank: (decision, priorities) => request("POST", "/api/rerank", { decision, priorities }),
  review: (decision) => request("POST", "/api/review", { decision }),
  ask: (question, decision, history = []) => request("POST", "/api/ask", { question, decision, history }),
  approve: (decision, approver, note, incidentId) => request("POST", "/api/approve", { decision, approver, note, incident_id: incidentId || null }),
  decisions: () => request("GET", "/api/decisions"),
};
