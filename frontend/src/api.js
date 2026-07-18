// Thin API client. All requests go through /api/* which is proxied to the
// backend by Vite (dev) or nginx (Docker).

const BASE = "/api";

async function getJSON(path) {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
  });
  const body = await res.json().catch(() => null);
  if (body === null && !res.ok) throw new Error(`bad response from ${path}`);
  return body;
}

async function send(path, method, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  return res.json().catch(() => null);
}

export const fetchWatchlist = () => getJSON("/watchlist");
export const fetchHealth = () => getJSON("/health");
export const fetchRiskProfiles = () => getJSON("/risk/profiles");
export const fetchAccount = () => getJSON("/account");
export const fetchDecisions = (limit = 40) => getJSON(`/decisions?limit=${limit}`);
export const fetchLatestBacktest = () => getJSON("/backtest/latest");
export const fetchNews = (limit = 30) => getJSON(`/news?limit=${limit}`);
export const fetchTrackers = () => getJSON("/trackers");
export const fetchPerformance = () => getJSON("/performance");

export const setRiskFactor = (risk_factor) =>
  send("/risk/factor", "PUT", { risk_factor });
export const killSwitch = (flatten = true) =>
  send(`/kill?flatten=${flatten}`, "POST");
export const addTracker = (handle, source) =>
  send("/trackers", "POST", { handle, source });
export const removeTracker = (source, handle) =>
  send(`/trackers/${source}/${encodeURIComponent(handle)}`, "DELETE");

// Phase 3: go-live gate, mode switch, review.
export const fetchGolive = () => getJSON("/golive");
export const fetchMode = () => getJSON("/mode");
export const fetchBreakers = () => getJSON("/breakers");
export const sampleDecisions = (n = 20) => getJSON(`/decisions/sample?n=${n}`);
export const reviewDecision = (id, ok = true, note = null) =>
  send(`/decisions/${id}/review`, "POST", { ok, note });
export const ackBreaker = (id) => send(`/breakers/${id}/ack`, "POST");
export const unlockLive = (confirmation) =>
  send("/mode/unlock-live", "POST", { confirmation });
