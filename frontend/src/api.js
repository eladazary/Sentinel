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

// Edits need the server's rejection reason (duplicate, full, unknown symbol),
// so unlike send() this surfaces the error body instead of swallowing it.
async function sendOrThrow(path, method, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new Error(data?.detail || `request failed (${res.status})`);
  return data;
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
export const fetchPositions = () => getJSON("/positions");
export const fetchDecisionCounts = () => getJSON("/decisions/counts");
export const fetchDecisionsFiltered = ({ limit = 60, excludeSkips = false, symbol = null } = {}) => {
  const q = new URLSearchParams({ limit: String(limit) });
  if (excludeSkips) q.set("exclude_skips", "true");
  if (symbol) q.set("symbol", symbol);
  return getJSON(`/decisions?${q}`);
};

export const setRiskFactor = (risk_factor) =>
  send("/risk/factor", "PUT", { risk_factor });
export const killSwitch = (flatten = true) =>
  send(`/kill?flatten=${flatten}`, "POST");
export const addWatchlistTicker = (symbol, name, sector_etf) =>
  sendOrThrow("/watchlist/tickers", "POST", { symbol, name, sector_etf });
export const removeWatchlistTicker = (symbol) =>
  sendOrThrow(`/watchlist/tickers/${encodeURIComponent(symbol)}`, "DELETE");
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
