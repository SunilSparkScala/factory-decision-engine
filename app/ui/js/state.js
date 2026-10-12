// Shared client state. Views read from here and call the setters; nothing else mutates it.

export const state = {
  health: null,        // /api/health
  overview: null,      // /api/factory/overview
  watch: [],           // /api/watchlist machines
  alerts: [],          // /api/alerts
  decision: null,      // current DecisionResponse in the workspace
  lastPrompt: "",
  seenAlertIds: new Set(),
};

export function atRiskIds() {
  return new Set(
    state.watch.filter((w) => ["CRITICAL", "HIGH", "ELEVATED"].includes(w.risk_level)).map((w) => w.machine_id)
  );
}
