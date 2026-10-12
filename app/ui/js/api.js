// Every backend call lives here, so the views never build URLs or parse errors themselves.

// Industry pack chosen by this viewer (sent with every request; the server defaults if unset)
let domain = null;
try { domain = localStorage.getItem("fde-domain"); } catch { /* storage unavailable */ }

export function getDomain() { return domain; }

export function setDomain(key) {
  domain = key;
  try { localStorage.setItem("fde-domain", key); } catch { /* storage unavailable */ }
}

async function request(method, url, body) {
  const headers = {};
  if (body) headers["Content-Type"] = "application/json";
  if (domain) headers["X-FDE-Domain"] = domain;
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

export const api = {
  health: () => request("GET", "/api/health"),
  domains: () => request("GET", "/api/domains"),
  overview: () => request("GET", "/api/factory/overview"),
  watchList: () => request("GET", "/api/watchlist"),
  alerts: () => request("GET", "/api/alerts"),
  simulateFault: () => request("POST", "/api/alerts/simulate"),
  analyze: (prompt, previous) => request("POST", "/api/analyze", { prompt, previous_response: previous || null }),
  rerank: (decision, priorities) => request("POST", "/api/rerank", { decision, priorities }),
  review: (decision) => request("POST", "/api/review", { decision }),
  ask: (question, decision) => request("POST", "/api/ask", { question, decision }),
  approve: (decision, approver, note) => request("POST", "/api/approve", { decision, approver, note }),
  decisions: () => request("GET", "/api/decisions"),
};
