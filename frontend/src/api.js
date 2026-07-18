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

export const setRiskFactor = (risk_factor) =>
  send("/risk/factor", "PUT", { risk_factor });
export const killSwitch = (flatten = true) =>
  send(`/kill?flatten=${flatten}`, "POST");
